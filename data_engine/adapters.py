from __future__ import annotations

import csv
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .models import ImportFormat, MappingConfig


@dataclass(frozen=True)
class ProbeResult:
    format: ImportFormat
    confidence: float
    records: int | None
    warnings: list[str]


@dataclass
class CanonicalDataset:
    rows: list[dict[str, Any]]
    features: dict[str, dict[str, Any]]
    source_format: str
    source_metadata: dict[str, Any]


def get_path(value: Any, path: str) -> Any:
    """Read dotted paths and array indexes without evaluating user code."""
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise KeyError(path)
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            raise KeyError(path)
    return current


def flatten_paths(record: dict[str, Any], paths: str | list[str]) -> list[Any]:
    names = [paths] if isinstance(paths, str) else paths
    values: list[Any] = []
    for name in names:
        value = get_path(record, name)
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    return values


class Adapter(ABC):
    format: ImportFormat

    @abstractmethod
    def probe(self, path: Path) -> ProbeResult: ...

    @abstractmethod
    def read(self, path: Path, mapping: MappingConfig) -> CanonicalDataset: ...


class JsonlAdapter(Adapter):
    format = ImportFormat.jsonl

    def probe(self, path: Path) -> ProbeResult:
        try:
            with path.open(encoding="utf-8") as stream:
                first = next((line for line in stream if line.strip()), "")
            json.loads(first)
            return ProbeResult(self.format, 0.99, sum(1 for _ in path.open(encoding="utf-8")), [])
        except (StopIteration, json.JSONDecodeError, UnicodeDecodeError):
            return ProbeResult(self.format, 0.0, None, ["file is not UTF-8 JSONL"])

    def read(self, path: Path, mapping: MappingConfig) -> CanonicalDataset:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return _canonicalize(rows, mapping, self.format.value)


class CsvAdapter(Adapter):
    format = ImportFormat.csv

    def probe(self, path: Path) -> ProbeResult:
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                sample = stream.read(4096)
            dialect = csv.Sniffer().sniff(sample)
            return ProbeResult(self.format, 0.85 if dialect.delimiter == "," else 0.6, None, [])
        except (OSError, csv.Error, UnicodeDecodeError):
            return ProbeResult(self.format, 0.0, None, ["file is not a delimited UTF-8 table"])

    def read(self, path: Path, mapping: MappingConfig) -> CanonicalDataset:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        return _canonicalize(rows, mapping, self.format.value)


def _canonicalize(records: Iterable[dict[str, Any]], mapping: MappingConfig, source_format: str) -> CanonicalDataset:
    result: list[dict[str, Any]] = []
    last_timestamp = 0.0
    for index, record in enumerate(records):
        if mapping.timestamp:
            timestamp = float(get_path(record, mapping.timestamp))
        elif mapping.fps:
            timestamp = index / mapping.fps
        else:
            timestamp = last_timestamp if index == 0 else last_timestamp + 1.0
        last_timestamp = timestamp
        task = get_path(record, mapping.task) if mapping.task else ""
        episode = get_path(record, mapping.episode) if mapping.episode else 0
        row = {
            "observation": flatten_paths(record, mapping.observation),
            "action": flatten_paths(record, mapping.action),
            "timestamp": timestamp,
            "task": str(task),
            "episode_id": str(episode),
            "source_frame_index": int(get_path(record, mapping.source_frame_index)) if mapping.source_frame_index else index,
        }
        for target in mapping.include_fields:
            row[f"source.{target}"] = get_path(record, target)
        result.append(row)
    if not result:
        raise ValueError("input contains no records")
    features = {
        "observation.state": {"dtype": "float32", "shape": [len(result[0]["observation"])]},
        "action": {"dtype": "float32", "shape": [len(result[0]["action"])]},
    }
    return CanonicalDataset(result, features, source_format, {"records": len(result)})


ADAPTERS: dict[ImportFormat, Adapter] = {
    ImportFormat.jsonl: JsonlAdapter(),
    ImportFormat.csv: CsvAdapter(),
}
