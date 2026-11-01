from fastapi.testclient import TestClient

from ManiSkillGen import app as web
from ManiSkillGen.generator import ManiSkillSceneGenerator
from ManiSkillGen.scene_spec import default_scene_spec


def test_validation_and_repair_api(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "generator", ManiSkillSceneGenerator(tmp_path, prefer_maniskill=False))
    client = TestClient(web.app)
    spec = default_scene_spec()
    spec["scene"]["objects"][0]["pose"] = [1, 0, 1.2]
    validation = client.post("/api/maniskill/scene/validate", json={"spec": spec}).json()
    assert not validation["ok"]
    response = client.post("/api/maniskill/scene/repair", json={"spec": spec})
    assert response.status_code == 200
    assert response.json()["validation"]["repair"]["status"] == "repaired"
    rejected = client.post("/api/maniskill/scene/generate", json={"spec": spec, "auto_repair": False})
    assert rejected.status_code == 422
    assert rejected.json()["validation"]["audit_path"]
    assert client.post("/api/maniskill/scene/generate", json={"spec": spec, "max_repair_attempts": 99}).status_code == 400
    assert client.post("/api/maniskill/scene/validate", json=[]).status_code == 400


def test_ui_and_success_payload_expose_checks_and_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "generator", ManiSkillSceneGenerator(tmp_path, prefer_maniskill=False))
    monkeypatch.setattr(web, "_state", {"instance": None})
    client = TestClient(web.app)
    response = client.post("/api/maniskill/scene/generate", json={"spec": default_scene_spec(), "render": False})
    assert response.status_code == 200
    report = response.json()["validation"]
    assert report["runtime"]["status"] == "not_tested"
    assert report["checks"]["physics_stability"] is None
    assert report["repair"]["history"]
    assert client.get("/shots/maniskill/latest.png").status_code == 404
    assert 'id="auto-repair"' in client.get("/").text
