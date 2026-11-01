"""Create and validate a LeRobot v3 dataset from one internal GT episode."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_ROOT = ROOT.parent
CONVERTER = PIPELINE_ROOT.parent / "lerobot_conversion" / "convert_hdf5.py"
DEFAULT_PYTHON = PIPELINE_ROOT.parent / "lerobot_conversion" / ".venv" / "Scripts" / "python.exe"
POSIX_PYTHON = PIPELINE_ROOT.parent / "lerobot_conversion" / ".venv" / "bin" / "python"


def lerobot_python() -> Path:
    configured = os.getenv("LEROBOT_PYTHON")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_file():
            raise RuntimeError(f"LEROBOT_PYTHON does not exist: {candidate}")
        return candidate.resolve()
    candidates = [DEFAULT_PYTHON, POSIX_PYTHON, Path(sys.executable)]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("LeRobot Python runtime not found; set LEROBOT_PYTHON")


def convert_hdf5(source: Path, output: Path, *, repo_id: str, task: str | None = None) -> dict:
    source = Path(source).resolve()
    output = Path(output).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.exists():
        raise FileExistsError(output)
    if not CONVERTER.is_file():
        raise FileNotFoundError(f"LeRobot converter is missing: {CONVERTER}")
    command = [str(lerobot_python()), str(CONVERTER), str(source), str(output), "--repo-id", repo_id]
    if task:
        command.extend(["--task", task])
    completed = subprocess.run(command, cwd=str(CONVERTER.parent), capture_output=True, text=True, timeout=900)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "conversion failed").strip()
        raise RuntimeError(f"LeRobot v3 conversion failed ({completed.returncode}): {detail[-4000:]}")
    info_path = output / "meta" / "info.json"
    manifest_path = output / "meta" / "semantic_manifest.json"
    if not info_path.is_file() or not manifest_path.is_file():
        raise RuntimeError("LeRobot conversion finished without meta/info.json and semantic_manifest.json")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if info.get("codebase_version") != "v3.0":
        raise RuntimeError(f"unexpected LeRobot codebase version: {info.get('codebase_version')!r}")
    return {"root": str(output), "repo_id": repo_id, "info": info, "manifest": manifest,
            "stdout": completed.stdout[-2000:]}


def zip_dataset(dataset_root: Path, archive: Path) -> Path:
    dataset_root = Path(dataset_root).resolve()
    archive = Path(archive).resolve()
    if not (dataset_root / "meta" / "info.json").is_file():
        raise FileNotFoundError(dataset_root / "meta" / "info.json")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        raise FileExistsError(archive)
    base_name = archive.with_suffix("")
    created = Path(shutil.make_archive(str(base_name), "zip", root_dir=str(dataset_root.parent), base_dir=dataset_root.name))
    if created != archive:
        created.replace(archive)
    return archive
