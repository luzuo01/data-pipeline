import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from data_engine.api import create_app
from data_engine.zip_pipeline import inspect_zip


def test_import_and_conversion(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    client = TestClient(app)
    payload = "{\"observation\":{\"qpos\":[1,2]},\"action\":[0.1],\"timestamp\":0,\"task\":\"reach\",\"episode_id\":\"e0\"}\n"
    imported = client.post("/v1/imports", files={"file": ("episode.jsonl", payload, "application/jsonl")}, data={"dataset_name": "demo"})
    assert imported.status_code == 201, imported.text
    import_id = imported.json()["import_id"]
    job = client.post("/v1/conversions", json={"import_id": import_id, "output_repo_id": "demo/reach", "mapping": {"observation": "observation.qpos", "action": "action", "timestamp": "timestamp", "task": "task", "episode": "episode_id"}})
    assert job.status_code == 202, job.text
    job_id = job.json()["job_id"]
    for _ in range(20):
        state = client.get(f"/v1/jobs/{job_id}").json()
        if state["state"] in {"succeeded", "failed"}:
            break
        time.sleep(0.05)
    assert state["state"] == "succeeded", state
    manifest = Path(state["manifest_uri"])
    assert json.loads(manifest.read_text(encoding="utf-8"))["rows"] == 1


def test_zip_inspection_recognizes_validated_sources(tmp_path: Path) -> None:
    import zipfile

    source = tmp_path / "pipeline_sample_20261027_v1"
    (source / "data").mkdir(parents=True)
    (source / "scene").mkdir()
    (source / "data" / "control_records.jsonl").write_text("{}\n", encoding="utf-8")
    (source / "scene" / "task.json").write_text("{}", encoding="utf-8")
    archive_path = tmp_path / "upload.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in source.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(tmp_path).as_posix())
    inspection = inspect_zip(archive_path)
    assert inspection["format"] == "zip"
    assert [item["kind"] for item in inspection["sources"]] == ["pipeline_confined_reach_v1"]
