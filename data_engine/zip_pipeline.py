from __future__ import annotations

"""ZIP ingestion and source discovery for the validated trajectory adapters."""

import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ZipSource:
    kind: str
    root: Path
    name: str
    details: dict[str, Any]


def _relative_names(archive: zipfile.ZipFile) -> list[str]:
    names: list[str] = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"ZIP contains an unsafe path: {info.filename}")
        names.append(name)
    return names


def inspect_zip(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = _relative_names(archive)
    sources = _discover_names(names)
    return {
        "format": "zip",
        "members": len(names),
        "files": names[:200],
        "sources": [{"kind": item.kind, "name": item.name, **item.details} for item in sources],
        "unsupported": [name for name in names if Path(name).suffix.lower() not in {".jsonl", ".json", ".csv", ".gz", ".xml", ".md", ".py", ".mp4", ".h5", ".hdf5"}],
    }


def extract_zip(path: Path, destination: Path) -> Path:
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(path) as archive:
        names = _relative_names(archive)
        total_uncompressed = sum(info.file_size for info in archive.infolist() if not info.is_dir())
        if len(names) > 10000:
            raise ValueError("ZIP contains too many files (maximum 10000)")
        if total_uncompressed > 20 * 1024**3:
            raise ValueError("ZIP expands beyond the 20 GiB limit")
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            target = destination / Path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
    return destination


def discover_sources(root: Path) -> list[ZipSource]:
    names = [str(path.relative_to(root)).replace("\\", "/") for path in root.rglob("*") if path.is_file()]
    return _discover_names(names, root=root)


def _discover_names(names: list[str], root: Path | None = None) -> list[ZipSource]:
    normalized = {name.replace("\\", "/") for name in names}
    sources: list[ZipSource] = []

    # A package is identified by its schema-bearing files, so an arbitrary ZIP
    # prefix (for example ``my_upload/``) does not matter.
    pipeline_roots = set()
    for name in normalized:
        path = Path(name)
        if path.parts[-2:] == ("data", "control_records.jsonl"):
            pipeline_roots.add(path.parent.parent.as_posix())
    for prefix in sorted(pipeline_roots):
        if (Path(prefix) / "scene/task.json").as_posix() not in normalized:
            continue
        package = Path(prefix) if root is None else root / prefix
        sources.append(ZipSource("pipeline_confined_reach_v1", package, Path(prefix).name or "pipeline_sample", {"control_rows": 300}))

    # Table30 episodes have a stable three-part signature.
    for name in sorted(normalized):
        path = Path(name)
        if path.parts[-2:] != ("states", "states.jsonl"):
            continue
        prefix = path.parent.parent
        required = [
            (prefix / "meta/episode_meta.json").as_posix(),
            (prefix / "videos/cam_global_rgb.mp4").as_posix(),
            (prefix / "videos/cam_side_rgb.mp4").as_posix(),
            (prefix / "videos/cam_arm_rgb.mp4").as_posix(),
        ]
        if all(item in normalized for item in required):
            episode = Path(prefix) if root is None else root / prefix
            sources.append(ZipSource("robochallenge_table30_v2_arx5", episode, Path(prefix).name or "table30_episode", {"videos": 3}))

    # Dexmal JSONL rows refer to shared top/wrist MP4 files. Search the nearest
    # common directory, supporting both a flat sample and nested HF snapshots.
    for name in sorted(normalized):
        path = Path(name)
        if path.suffix.lower() != ".jsonl" or not path.name.startswith("episode_"):
            continue
        candidates: list[Path] = []
        for count in range(len(path.parts) - 1, -1, -1):
            prefix = Path(*path.parts[:count])
            if (prefix / "top.mp4").as_posix() in normalized and (prefix / "wrist.mp4").as_posix() in normalized:
                candidates.append(prefix)
        if candidates:
            prefix = candidates[0]
            episode = path if root is None else root / path
            video_root = Path(prefix) if root is None else root / prefix
            sources.append(ZipSource("dexmal_jsonl_video_v1", episode, path.stem, {"top_video": str(video_root / "top.mp4"), "wrist_video": str(video_root / "wrist.mp4")}))

    # Prevent duplicate signatures from making a package look like multiple
    # datasets when the discovery root is itself one of the source folders.
    unique: dict[tuple[str, str], ZipSource] = {(item.kind, str(item.root)): item for item in sources}
    return list(unique.values())


def source_to_dict(item: ZipSource) -> dict[str, Any]:
    return {"kind": item.kind, "name": item.name, "root": str(item.root), **item.details}
