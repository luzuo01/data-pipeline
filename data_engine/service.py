from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters import ADAPTERS, ProbeResult
from .models import ConversionRequest, Hdf5ConversionRequest, ImportFormat, ImportRequest, JobResponse, JobState, ZipConversionRequest
from .writer import write_lerobot
from .zip_pipeline import discover_sources, extract_zip, inspect_zip, source_to_dict


class DataEngineService:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.uploads = self.root / "uploads"
        self.outputs = self.root / "outputs"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.outputs.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, JobResponse] = {}
        self._imports: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="data-engine")

    def register_upload(self, filename: str, content: bytes, request: ImportRequest) -> dict[str, Any]:
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("invalid filename")
        import_id = f"imp_{uuid.uuid4().hex[:16]}"
        target = self.uploads / f"{import_id}_{safe_name}"
        target.write_bytes(content)
        record = {"import_id": import_id, "path": target, "request": request.model_dump(mode="json")}
        with self._lock:
            self._imports[import_id] = record
        detected = self.detect(target)
        result = {"import_id": import_id, "filename": safe_name, "bytes": len(content), "format": detected.format.value}
        if detected.format == ImportFormat.hdf5:
            result["inspection"] = inspect_hdf5(target)
        elif detected.format == ImportFormat.zip:
            result["inspection"] = inspect_zip(target)
        return result

    def register_sample_hdf5(self) -> dict[str, Any]:
        sample = Path(__file__).resolve().parent.parent / "lerobot_conversion" / "panda_pick_place" / "extras" / "source_episode.h5"
        if not sample.exists():
            raise FileNotFoundError(sample)
        import_id = f"imp_sample_{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._imports[import_id] = {"import_id": import_id, "path": sample, "request": {"dataset_name": "panda_pick_place_sample", "source_type": "upload"}}
        return {"import_id": import_id, "filename": sample.name, "bytes": sample.stat().st_size, "format": "hdf5", "inspection": inspect_hdf5(sample), "sample": True}

    def detect(self, path: Path) -> ProbeResult:
        requested = ImportFormat.auto
        ext = path.suffix.lower()
        if ext == ".jsonl":
            requested = ImportFormat.jsonl
        elif ext in {".csv", ".tsv"}:
            requested = ImportFormat.csv
        elif ext in {".h5", ".hdf5"}:
            requested = ImportFormat.hdf5
        elif ext == ".zip":
            requested = ImportFormat.zip
        if requested in ADAPTERS:
            return ADAPTERS[requested].probe(path)
        if requested == ImportFormat.hdf5:
            try:
                return ProbeResult(ImportFormat.hdf5, 1.0, inspect_hdf5(path).get("action_shape", [0])[0], [])
            except Exception as exc:
                return ProbeResult(ImportFormat.hdf5, 0.0, None, [str(exc)])
        if requested == ImportFormat.zip:
            try:
                inspection = inspect_zip(path)
                if not inspection["sources"]:
                    return ProbeResult(ImportFormat.zip, 0.0, 0, ["ZIP 中没有识别到已验证的数据源"])
                return ProbeResult(ImportFormat.zip, 1.0, len(inspection["sources"]), [])
            except Exception as exc:
                return ProbeResult(ImportFormat.zip, 0.0, None, [str(exc)])
        return ProbeResult(ImportFormat.auto, 0.0, None, [f"no adapter registered for {ext or 'extensionless input'}"])

    def create_hdf5_job(self, request: Hdf5ConversionRequest) -> JobResponse:
        with self._lock:
            imported = self._imports.get(request.import_id)
        if not imported:
            raise KeyError(request.import_id)
        if self.detect(imported["path"]).format != ImportFormat.hdf5:
            raise ValueError("import_id does not reference an HDF5 file")
        now = datetime.now(timezone.utc)
        job = JobResponse(job_id=f"job_{uuid.uuid4().hex[:16]}", state=JobState.queued, progress=0, stage="queued", created_at=now, updated_at=now)
        with self._lock:
            self._jobs[job.job_id] = job
        self._executor.submit(self._run_hdf5, job.job_id, imported, request)
        return job

    def create_job(self, request: ConversionRequest) -> JobResponse:
        with self._lock:
            imported = self._imports.get(request.import_id)
        if not imported:
            raise KeyError(request.import_id)
        now = datetime.now(timezone.utc)
        job = JobResponse(job_id=f"job_{uuid.uuid4().hex[:16]}", state=JobState.queued, progress=0, stage="queued", created_at=now, updated_at=now)
        with self._lock:
            self._jobs[job.job_id] = job
        self._executor.submit(self._run, job.job_id, imported, request)
        return job

    def create_zip_job(self, request: ZipConversionRequest) -> JobResponse:
        with self._lock:
            imported = self._imports.get(request.import_id)
        if not imported:
            raise KeyError(request.import_id)
        if self.detect(imported["path"]).format != ImportFormat.zip:
            raise ValueError("import_id does not reference a ZIP file")
        now = datetime.now(timezone.utc)
        job = JobResponse(job_id=f"job_{uuid.uuid4().hex[:16]}", state=JobState.queued, progress=0, stage="queued", created_at=now, updated_at=now)
        with self._lock:
            self._jobs[job.job_id] = job
        self._executor.submit(self._run_zip, job.job_id, imported, request)
        return job

    def get_job(self, job_id: str) -> JobResponse:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return self._jobs[job_id]

    def _run(self, job_id: str, imported: dict[str, Any], request: ConversionRequest) -> None:
        self._update(job_id, state=JobState.running, progress=10, stage="detecting")
        source: Path = imported["path"]
        try:
            result = self.detect(source)
            adapter = ADAPTERS.get(result.format)
            if not adapter:
                raise ValueError(f"unsupported input format: {result.format.value}")
            self._update(job_id, progress=30, stage="normalizing")
            canonical = adapter.read(source, request.mapping)
            canonical.source_metadata["mapping"] = request.mapping.model_dump(mode="json")
            canonical.source_metadata["import_id"] = request.import_id
            self._update(job_id, progress=60, stage="writing_lerobot")
            output = self.outputs / request.output_repo_id.replace("/", "__")
            manifest = write_lerobot(canonical, output, request.output_repo_id, source, request.preserve_source)
            manifest_path = output / "meta" / "semantic_manifest.json"
            self._update(job_id, state=JobState.succeeded, progress=100, stage="completed", output_uri=str(output), manifest_uri=str(manifest_path))
        except Exception as exc:
            self._update(job_id, state=JobState.failed, progress=100, stage="failed", error={"code": type(exc).__name__, "message": str(exc)})

    def _run_hdf5(self, job_id: str, imported: dict[str, Any], request: Hdf5ConversionRequest) -> None:
        source: Path = imported["path"]
        output = self.outputs / request.output_repo_id.replace("/", "__")
        converter = Path(__file__).resolve().parent.parent / "lerobot_conversion" / "convert_hdf5.py"
        validator = Path(__file__).resolve().parent.parent / "lerobot_conversion" / "validate_conversion.py"
        python = Path(__file__).resolve().parent.parent / "lerobot_conversion" / ".venv" / "Scripts" / "python.exe"
        if not python.exists():
            python = Path(sys.executable)
        try:
            self._update(job_id, state=JobState.running, progress=10, stage="检查 HDF5 schema")
            inspection = inspect_hdf5(source)
            if inspection["schema_version"] != "embodied_gt_v1":
                raise ValueError(f"当前 HDF5 适配器只支持 embodied_gt_v1，收到 {inspection['schema_version']}")
            self._update(job_id, progress=25, stage="转换为 LeRobot v3")
            command = [str(python), str(converter), str(source), str(output), "--repo-id", request.output_repo_id]
            if request.task:
                command.extend(["--task", request.task])
            subprocess.run(command, cwd=str(converter.parent), check=True, capture_output=True, text=True)
            self._update(job_id, progress=85, stage="验证 LeRobot 输出")
            subprocess.run([str(python), str(validator), str(output)], cwd=str(converter.parent), check=True, capture_output=True, text=True)
            report_path = output / "validation_report.json"
            validation = json.loads(report_path.read_text(encoding="utf-8"))
            self._update(job_id, state=JobState.succeeded, progress=100, stage="完成", output_uri=str(output), manifest_uri=str(output / "meta" / "semantic_manifest.json"), validation=validation)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            self._update(job_id, state=JobState.failed, progress=100, stage="失败", error={"code": "ConversionProcessError", "message": detail[-6000:]})
        except Exception as exc:
            self._update(job_id, state=JobState.failed, progress=100, stage="失败", error={"code": type(exc).__name__, "message": str(exc)})

    def _run_zip(self, job_id: str, imported: dict[str, Any], request: ZipConversionRequest) -> None:
        source: Path = imported["path"]
        output = self.outputs / request.output_repo_id.replace("/", "__")
        extraction = self.root / "extracted" / job_id
        try:
            self._update(job_id, state=JobState.running, progress=5, stage="检查 ZIP 内容")
            inspection = inspect_zip(source)
            if not inspection["sources"]:
                raise ValueError("ZIP 中没有识别到 Dexmal、Table30v2 或质量闭环数据包")
            self._update(job_id, progress=15, stage="解压并识别数据源")
            extract_zip(source, extraction)
            sources = discover_sources(extraction)
            if not sources:
                raise ValueError("ZIP 解压后没有识别到已验证的数据源")
            if output.exists():
                if not request.overwrite:
                    raise FileExistsError(f"output already exists: {output}")
                shutil.rmtree(output)
            output.mkdir(parents=True)
            results: list[dict[str, Any]] = []
            total = len(sources)
            for index, item in enumerate(sources):
                child_name = f"{index:02d}_{item.name}".replace(" ", "_")
                child_output = output / child_name
                repo_id = f"{request.output_repo_id}/{child_name}"
                self._update(job_id, progress=20 + int(index * 65 / total), stage=f"转换 {item.kind}: {item.name}")
                project_root = Path(__file__).resolve().parent.parent
                python = project_root / "lerobot_conversion" / ".venv" / "Scripts" / "python.exe"
                if not python.exists():
                    python = Path(sys.executable)
                if item.kind == "dexmal_jsonl_video_v1":
                    command = [str(python), "-m", "data_engine.scripts.convert_dexmal_sample", str(item.root), item.details["top_video"], item.details["wrist_video"], str(child_output), "--repo-id", repo_id]
                elif item.kind == "robochallenge_table30_v2_arx5":
                    command = [str(python), "-m", "data_engine.scripts.convert_table30_sample", str(item.root), str(child_output), "--repo-id", repo_id, "--prompt", request.task or "Table30v2 robot manipulation task"]
                elif item.kind == "pipeline_confined_reach_v1":
                    command = [str(python), "-m", "data_engine.scripts.convert_pipeline_sample", str(item.root), str(child_output), "--repo-id", repo_id]
                else:
                    raise ValueError(f"unsupported ZIP source: {item.kind}")
                try:
                    subprocess.run(command, cwd=str(project_root), check=True, capture_output=True, text=True)
                except subprocess.CalledProcessError as exc:
                    detail = (exc.stderr or exc.stdout or str(exc)).strip()
                    raise RuntimeError(f"{item.kind} 转换失败: {detail[-6000:]}") from exc
                manifest_path = child_output / "meta" / "semantic_manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                validation_process = subprocess.run(
                    [str(python), "data_engine/scripts/validate_lerobot_cli.py", str(child_output)],
                    cwd=str(project_root), check=True, capture_output=True, text=True,
                )
                validation = json.loads(validation_process.stdout)
                if validation.get("official_reader") != "passed":
                    raise RuntimeError(f"{item.kind} 输出未通过 LeRobot 官方 reader: {validation}")
                results.append({"source": source_to_dict(item), "output_uri": str(child_output), "manifest": manifest, "validation": validation})
            archive = output / "extras" / "source_upload.zip"
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, archive)
            report = {"status": "passed", "source_zip": str(source), "sources": results, "source_count": len(results), "output_root": str(output)}
            (output / "zip_conversion_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            self._update(job_id, state=JobState.succeeded, progress=100, stage="完成", output_uri=str(output), manifest_uri=str(output / "zip_conversion_report.json"), validation=report)
        except Exception as exc:
            self._update(job_id, state=JobState.failed, progress=100, stage="失败", error={"code": type(exc).__name__, "message": str(exc)})

    def validate_lerobot(self, path: str) -> dict[str, Any]:
        root = Path(path).expanduser().resolve()
        project_root = Path(__file__).resolve().parent.parent
        python = project_root / "lerobot_conversion" / ".venv" / "Scripts" / "python.exe"
        script = project_root / "data_engine" / "scripts" / "validate_lerobot_cli.py"
        if python.exists() and script.exists():
            result = subprocess.run([str(python), str(script), str(root)], cwd=str(project_root), capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        return validate_lerobot_root(root)

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            current = self._jobs[job_id]
            self._jobs[job_id] = current.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})


def inspect_hdf5(path: Path) -> dict[str, Any]:
    import h5py
    with h5py.File(path, "r") as h5:
        metadata = json.loads(h5["metadata_json"][()].decode() if isinstance(h5["metadata_json"][()], bytes) else h5["metadata_json"][()])
        datasets: list[dict[str, Any]] = []
        def visit(name: str, obj: Any) -> None:
            if isinstance(obj, h5py.Dataset):
                datasets.append({"path": name, "shape": list(obj.shape), "dtype": str(obj.dtype)})
        h5.visititems(visit)
        actions = h5["actions"]
        observations = h5["observations/qpos"]
        return {
            "schema_version": str(metadata.get("schema_version", "")),
            "complete": bool(h5.attrs.get("complete", False)),
            "control_mode": metadata.get("control_mode"),
            "fps": round(1 / float(metadata["control_dt"]), 6) if metadata.get("control_dt") else None,
            "action_shape": list(actions.shape),
            "observation_state_shape": list(observations.shape),
            "camera_names": list(h5.get("cameras", {}).keys()) if "cameras" in h5 else [],
            "dataset_count": len(datasets),
            "datasets": datasets[:100],
        }


def validate_lerobot_root(root: Path) -> dict[str, Any]:
    required = [
        "meta/info.json",
        "meta/stats.json",
        "meta/tasks.parquet",
        "meta/episodes/chunk-000/file-000.parquet",
    ]
    missing = [item for item in required if not (root / item).exists()]
    data_files = list((root / "data").glob("**/*.parquet")) if (root / "data").exists() else []
    report: dict[str, Any] = {"path": str(root), "format": "LeRobot v3", "required_files": required, "missing_files": missing, "data_files": len(data_files), "valid": not missing and bool(data_files)}
    if report["valid"]:
        try:
            from lerobot.datasets import LeRobotDataset
            info = json.loads((root / "meta/info.json").read_text(encoding="utf-8"))
            dataset = LeRobotDataset(info.get("repo_id", "local/validation"), root=root, video_backend="pyav")
            sample = dataset[0]
            report.update({"total_frames": len(dataset), "fps": info.get("fps"), "features": sorted(sample.keys()), "official_reader": "passed"})
        except Exception as exc:
            report.update({"official_reader": "unavailable", "reader_error": str(exc), "reader_note": "当前 API 进程环境没有安装 LeRobot；转换任务使用项目 .venv 已完成官方 reader 验证。"})
    return report
