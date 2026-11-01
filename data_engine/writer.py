from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .adapters import CanonicalDataset


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_lerobot(dataset: CanonicalDataset, output: Path, repo_id: str, source: Path | None = None,
                  preserve_source: bool = True) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    data_dir = output / "data" / "chunk-000"
    meta_dir = output / "meta"
    data_dir.mkdir(parents=True)
    meta_dir.mkdir(parents=True)
    episodes: dict[str, int] = {}
    task_ids: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    for index, row in enumerate(dataset.rows):
        episode_key = row["episode_id"]
        episode_index = episodes.setdefault(episode_key, len(episodes))
        task_index = task_ids.setdefault(row["task"], len(task_ids))
        records.append({
            "observation.state": [float(x) for x in row["observation"]],
            "action": [float(x) for x in row["action"]],
            "timestamp": float(row["timestamp"]),
            "frame_index": index,
            "episode_index": episode_index,
            "index": index,
            "task_index": task_index,
        })
    table = pa.Table.from_pylist(records)
    pq.write_table(table, data_dir / "file-000.parquet")
    tasks = pa.table({"task_index": list(task_ids.values()), "task": list(task_ids.keys())})
    pq.write_table(tasks, meta_dir / "tasks.parquet")
    episode_rows = [{"episode_index": i, "tasks": [task_ids[t] for t in task_ids], "length": sum(r["episode_index"] == i for r in records)} for i in episodes.values()]
    episodes_dir = meta_dir / "episodes" / "chunk-000"
    episodes_dir.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(episode_rows), episodes_dir / "file-000.parquet")
    info = {
        "codebase_version": "v3.0",
        "robot_type": "unknown",
        "fps": _infer_fps(dataset.rows),
        "total_episodes": len(episodes),
        "total_frames": len(records),
        "features": dataset.features,
    }
    (meta_dir / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "schema": "data-engine-manifest-v1",
        "repo_id": repo_id,
        "source_format": dataset.source_format,
        "rows": len(records),
        "episodes": len(episodes),
        "features": dataset.features,
        "source_metadata": dataset.source_metadata,
    }
    if source and preserve_source:
        archive = output / "extras" / "source"
        archive.mkdir(parents=True)
        target = archive / source.name
        shutil.copy2(source, target)
        manifest["source"] = {"name": source.name, "sha256": sha256(source), "path": str(target.relative_to(output))}
    (meta_dir / "semantic_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _infer_fps(rows: list[dict[str, Any]]) -> float | None:
    if len(rows) < 2:
        return None
    delta = float(rows[1]["timestamp"]) - float(rows[0]["timestamp"])
    return round(1 / delta, 6) if delta > 0 else None
