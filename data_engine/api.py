from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .models import ConversionRequest, Hdf5ConversionRequest, ImportRequest, JobResponse, LerobotValidationRequest, ZipConversionRequest
from .service import DataEngineService


def create_app(storage_root: str | Path | None = None) -> FastAPI:
    root = Path(storage_root or os.getenv("DATA_ENGINE_STORAGE", "./.data_engine"))
    service = DataEngineService(root)
    app = FastAPI(title="Data Engine", version="0.1.0", description="Import robot trajectories and convert them to LeRobot v3.")
    app.state.service = service

    @app.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/formats")
    def formats() -> dict:
        return {
            "generic_import": ["jsonl", "csv", "zip"],
            "validated_profiles": [
                {"id": "dexmal_so101_v1", "source": "Dexmal/so101_pick_cube", "modalities": ["state", "action", "top_rgb", "wrist_rgb"]},
                {"id": "table30_v2_arx5", "source": "RoboChallenge/Table30v2", "modalities": ["state", "derived_next_pose_action", "global_rgb", "side_rgb", "wrist_rgb"]},
                {"id": "pipeline_confined_reach_v1", "source": "local package", "modalities": ["state", "policy_action", "quality"]},
            ],
            "zip_profiles": ["dexmal_jsonl_video_v1", "robochallenge_table30_v2_arx5", "pipeline_confined_reach_v1"],
            "unsupported_profiles": [
                {"name": "LIBERO", "reason": "no source adapter registered"},
                {"name": "ManiSkill2", "reason": "no source adapter registered"},
                {"name": "RoboDojo", "reason": "no source adapter registered"},
                {"name": "RoboTwin", "reason": "no source adapter registered"},
                {"name": "RoboTwin2-full", "reason": "no source adapter registered"},
                {"name": "Simpler", "reason": "no source adapter registered"},
                {"name": "CALVIN", "reason": "no source adapter registered"},
                {"name": "UniNaVid", "reason": "no source adapter registered"},
                {"name": "VLA-Arena", "reason": "no source adapter registered"},
                {"name": "official PushT", "reason": "no source adapter registered"},
            ],
            "planned": ["generic_hdf5", "parquet", "rosbag", "lerobot_repack"],
        }

    @app.post("/v1/imports", status_code=201)
    async def create_import(file: UploadFile = File(...), dataset_name: str = Form(...), source_type: str = Form("upload")) -> dict:
        if source_type != "upload":
            raise HTTPException(400, "file upload endpoint only accepts source_type=upload")
        content = await file.read()
        if not content:
            raise HTTPException(400, "uploaded file is empty")
        try:
            request = ImportRequest(dataset_name=dataset_name)
            return service.register_upload(file.filename or "upload", content, request)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/hdf5/sample", status_code=201)
    def sample_hdf5() -> dict:
        try:
            return service.register_sample_hdf5()
        except FileNotFoundError as exc:
            raise HTTPException(404, f"内置样例不存在: {exc}") from exc

    @app.post("/v1/hdf5/conversions", response_model=JobResponse, status_code=202)
    def create_hdf5_conversion(request: Hdf5ConversionRequest) -> JobResponse:
        try:
            return service.create_hdf5_job(request)
        except KeyError as exc:
            raise HTTPException(404, f"未知的 import_id: {exc.args[0]}") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/zip/conversions", response_model=JobResponse, status_code=202)
    def create_zip_conversion(request: ZipConversionRequest) -> JobResponse:
        try:
            return service.create_zip_job(request)
        except KeyError as exc:
            raise HTTPException(404, f"未知的 import_id: {exc.args[0]}") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/validate/lerobot")
    def validate_lerobot(request: LerobotValidationRequest) -> dict:
        try:
            return service.validate_lerobot(request.path)
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/conversions", response_model=JobResponse, status_code=202)
    def create_conversion(request: ConversionRequest) -> JobResponse:
        try:
            return service.create_job(request)
        except KeyError as exc:
            raise HTTPException(404, f"unknown import_id: {exc.args[0]}") from exc

    @app.get("/v1/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str) -> JobResponse:
        try:
            return service.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown job_id: {exc.args[0]}") from exc

    return app


app = create_app()
