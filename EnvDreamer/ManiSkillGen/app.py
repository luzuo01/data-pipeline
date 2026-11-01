"""Standalone ManiSkillGen web API.

Run with ``python -m ManiSkillGen.app`` from the EnvDreamer directory.  The
module is separate from the UE server so the two backends never share layout
files or screenshot state.
"""

from __future__ import annotations

import json
import time
import asyncio
import uuid
import re
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

try:  # FastAPI is optional for offline validation and library use.
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
except ImportError:  # pragma: no cover - exercised on minimal developer machines
    FastAPI = None  # type: ignore[assignment]
    Request = Any  # type: ignore[assignment,misc]
    FileResponse = HTMLResponse = JSONResponse = None  # type: ignore[assignment]

from .generator import ManiSkillSceneGenerator
from .planner import SceneSpecPlanner
from .portal_trace_backend import PORTAL_ROBOTS, PORTAL_TASKS, PORTAL_UE_ASSETS
from .validator import SceneValidationError, validate_scene_spec
from .repair import repair_scene


ROOT = Path(__file__).resolve().parent
TEMPLATE_PATH = ROOT / "templates" / "index.html"
OUTPUT_DIR = ROOT / "outputs"
# The web endpoint must never present a schematic fallback as a ManiSkill3
# result. Install ManiSkill3/SAPIEN to generate a real preview in the UI.
generator = ManiSkillSceneGenerator(OUTPUT_DIR, allow_software_fallback=False)
planner = SceneSpecPlanner()
_state: dict[str, Any] = {"instance": None, "last_prompt": None, "updated_at": None}
# Keep all SAPIEN operations on the same worker thread and serialize compilation.
_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scene-runtime")
_generation_lock = asyncio.Lock()
_pipeline_jobs: dict[str, Any] = {}
_pipeline_tasks: set = set()


async def _request_object(req):
    try:
        body = await req.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "request must contain valid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "request must be a JSON object")
    return body


def _seed(body):
    seed = body.get("seed", 0)
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    return seed


def _save_job(job):
    # Separate from run.json: planning can fail before an episode directory
    # exists. Atomic replacement prevents a restart from reading partial JSON.
    path = OUTPUT_DIR / "datasets" / (job["run_id"] + ".job.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _pipeline_public(result):
    public = dict(result)
    public["episodes"] = []
    for episode in result.get("episodes", []):
        entry = dict(episode)
        stem = f"episode_{entry['index']:03d}"
        base = f"/api/maniskill/pipeline/{result['run_id']}/files/"
        entry.update(download_url=base+stem+"/lerobot_v3.zip",
                     preview_url=base+stem+("/ue_white.mp4" if entry.get("white_model_video_path") else "/trajectory.gif"),
                     metadata_url=base+stem+"/episode.json",
                     white_model_video_url=(base+stem+"/ue_white.mp4") if entry.get("white_model_video_path") else None)
        public["episodes"].append(entry)
    return public


def _public_instance(instance: dict[str, Any] | None) -> dict[str, Any] | None:
    if instance is None:
        return None
    manifest = instance.get("manifest", {})
    validation = instance.get("validation", {})
    preview = instance.get("preview_path")
    return {
        "scene_spec": instance.get("scene_spec"),
        "planner": instance.get("scene_spec", {}).get("planner", {}),
        "task": instance.get("task"),
        "success": instance.get("success"),
        "manifest": manifest,
        "validation": validation,
        "preview_url": "/shots/maniskill/latest.png" if preview else None,
    }


def create_app() -> Any:
    if FastAPI is None:
        raise RuntimeError("FastAPI is required for the ManiSkillGen web API; install fastapi and uvicorn")
    api = FastAPI(title="EnvDreamer UE5 SceneGenerator", version="0.2.0")

    @api.exception_handler(HTTPException)
    async def request_error(req, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @api.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(TEMPLATE_PATH.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @api.get("/shots/maniskill/latest.png")
    async def latest_png():
        current = _state.get("instance")
        current_manifest = current.get("manifest", {}) if isinstance(current, dict) else {}
        # Serve the accepted immutable frame, even if another request is finishing.
        path = Path(current["preview_path"]) if isinstance(current, dict) and current.get("preview_path") else generator.preview_dir / "missing.png"
        if not path.exists() or current_manifest.get("runtime_backend") not in {"maniskill3", "portal_trace_robosuite"}:
            return JSONResponse({"error": "ManiSkill preview not available"}, status_code=404)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache, no-store"})

    @api.post("/api/maniskill/scene/validate")
    async def validate(req: Request):
        body = await _request_object(req)
        if not isinstance(body, dict):
            return JSONResponse({"error": "request must be a JSON object"}, status_code=400)
        spec = body.get("spec", body)
        return JSONResponse(validate_scene_spec(spec, asset_registry=generator.registry))

    @api.post("/api/maniskill/scene/repair")
    async def repair(req: Request):
        body = await _request_object(req)
        if not isinstance(body, dict):
            return JSONResponse({"error": "request must be a JSON object"}, status_code=400)
        try:
            report = repair_scene(body.get("spec"), seed=_seed(body),
                                  max_attempts=body.get("max_repair_attempts", 3), asset_registry=generator.registry)
            return JSONResponse({"status": "ok" if report["ok"] else "invalid", "validation": report,
                                 "scene_spec": report["normalized_spec"]}, status_code=200 if report["ok"] else 422)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @api.post("/api/maniskill/scene/generate")
    async def generate(req: Request):
        body = await _request_object(req)
        if not isinstance(body, dict):
            return JSONResponse({"error": "request must be a JSON object"}, status_code=400)
        if _generation_lock.locked():
            return JSONResponse({"status": "busy", "error": "已有场景正在生成，请稍后再试"}, status_code=409)
        spec = None
        try:
            prompt = str(body.get("prompt") or "").strip()
            seed = _seed(body)
            render = body.get("render", True)
            num_envs = body.get("num_envs", 1)
            auto_repair = body.get("auto_repair", True)
            attempts = body.get("max_repair_attempts", 3)
            if type(render) is not bool or type(auto_repair) is not bool or type(num_envs) is not int or num_envs != 1 or type(attempts) is not int or not 0 <= attempts <= 5:
                raise ValueError("render/auto_repair must be boolean, num_envs must be 1, max_repair_attempts must be 0..5")
            if "spec" in body and not isinstance(body["spec"], dict):
                raise ValueError("spec must be a JSON object")
            started = time.perf_counter()
            async with _generation_lock:
                loop = asyncio.get_running_loop()
                spec = body.get("spec") if "spec" in body else await loop.run_in_executor(_worker, partial(planner.plan, prompt or "Franka tabletop pick and place", seed=seed))
                if isinstance(spec, dict) and spec.get("backend") == "ue5":
                    # UE5 owns scene construction for native robot actors. Do
                    # the same schema/geometry preflight here, but never send
                    # a UE5 SceneSpec through the ManiSkill/SAPIEN renderer.
                    validation = validate_scene_spec(spec, asset_registry=generator.registry)
                    if not validation["ok"]:
                        raise SceneValidationError(validation)
                    if render:
                        return JSONResponse({"status": "unsupported", "error": "UE-only 场景生成尚未实现；请选择 ManiSkill3/Panda 生成场景，或调用 scene/validate 仅做静态检查。",
                                             "scene_spec": validation["normalized_spec"], "validation": validation}, status_code=422)
                    instance = {
                        "scene_spec": validation.get("normalized_spec", spec),
                        "task": spec.get("task"),
                        "success": spec.get("success", []),
                        "manifest": {"backend": "ue5", "runtime_backend": "ue5",
                                     "execution_backend": "ue5", "physics_engine": "Chaos"},
                        "validation": validation,
                        "preview_path": None,
                    }
                    return JSONResponse({"status": "validated", "generation_ms": round((time.perf_counter() - started) * 1000, 2),
                                         "warning": "仅完成静态检查，未构造 UE 场景或执行仿真。", **(_public_instance(instance) or {})})
                else:
                    instance = await loop.run_in_executor(_worker, partial(generator.generate, spec, seed=seed, num_envs=num_envs, render=render,
                                                                           auto_repair=auto_repair, max_repair_attempts=attempts))
            _state.update({"instance": instance, "last_prompt": prompt, "updated_at": time.time()})
            payload = _public_instance(instance) or {}
            payload["generation_ms"] = round((time.perf_counter() - started) * 1000, 2)
            return JSONResponse({"status": "ok", **payload})
        except SceneValidationError as exc:
            return JSONResponse({"status": "invalid", "error": str(exc), "scene_spec": exc.report.get("normalized_spec", {}), "planner": (spec or {}).get("planner", {}), "validation": exc.report}, status_code=422)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"status": "error", "error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"status": "error", "error": str(exc), "scene_spec": spec, "planner": (spec or {}).get("planner", {})}, status_code=500)

    @api.get("/api/maniskill/scene/state")
    async def state():
        return JSONResponse({"status": "ok", "updated_at": _state.get("updated_at"), "last_prompt": _state.get("last_prompt"), "instance": _public_instance(_state.get("instance"))})

    @api.get("/api/maniskill/catalog")
    async def catalog():
        return JSONResponse(
            {
                "status": "ok",
                "backends": {
                    "maniskill3": {
                        "robots": ["panda"],
                        "tasks": ["pick_and_place", "push", "stack"],
                    },
                    "ue5": {
                        "robots": ["Franka/Panda", "Sawyer", "UR5e"],
                        "tasks": ["pick_and_place", "stack"],
                        "runtime": "Unreal Engine 5.6.1 + Chaos + Remote Control",
                        "assets": "/Game/Robots/PORTALTrace/{Sawyer,UR5e}",
                    },
                    "portal_trace": {
                        "robots": list(PORTAL_ROBOTS),
                        "tasks": list(PORTAL_TASKS),
                        "runtime": "MuJoCo 3.3.2 + robosuite 1.5.2",
                        "ue_assets": PORTAL_UE_ASSETS,
                        "ue_asset_kind": "StaticMesh preview; joint dynamics remain in robosuite",
                    },
                },
            }
        )

    @api.post("/api/maniskill/pipeline/start")
    async def start_pipeline(req: Request):
        body = await _request_object(req)
        if not isinstance(body, dict):
            return JSONResponse({"error": "request must be a JSON object"}, status_code=400)
        if _generation_lock.locked():
            return JSONResponse({"error": "已有生成任务正在运行"}, status_code=409)
        try:
            count = body.get("num_episodes", 1)
            seed = body.get("seed", 0)
            auto_repair = body.get("auto_repair", True)
            attempts = body.get("max_repair_attempts", 3)
            render_ue = body.get("render_ue", False)
            if type(count) is not int or not 1 <= count <= 10 or type(seed) is not int:
                raise ValueError("num_episodes must be 1..10 and seed must be an integer")
            if type(auto_repair) is not bool or type(render_ue) is not bool or type(attempts) is not int or not 0 <= attempts <= 5:
                raise ValueError("invalid repair settings")
            if "spec" in body and not isinstance(body["spec"], dict):
                raise ValueError("spec must be a JSON object")
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        run_id = uuid.uuid4().hex[:12]
        _pipeline_jobs[run_id] = {"run_id": run_id, "status": "running", "progress": {"phase": "planning"},
                                  "requested_episodes": count, "successful_episodes": 0,
                                  "episodes": [], "failed_episodes": [], "started_at": time.time()}
        _save_job(_pipeline_jobs[run_id])
        await _generation_lock.acquire()
        loop = asyncio.get_running_loop()
        def work():
            from .pipeline import DataPipeline
            spec = body["spec"] if "spec" in body else planner.plan(str(body.get("prompt") or "Franka pick and place a cube"), seed=seed)
            return DataPipeline(OUTPUT_DIR/"datasets").run(spec,seed=seed,num_episodes=count,auto_repair=auto_repair,
                         max_repair_attempts=attempts,reference_scene=body.get("reference_scene") is True,run_id=run_id,
                         render_ue=render_ue,
                         on_progress=lambda update:_pipeline_jobs[run_id].update(progress=update))
        async def run_job():
            try:
                result = await loop.run_in_executor(_worker, work)
                _pipeline_jobs[run_id].update(_pipeline_public(result))
            except Exception as exc:
                _pipeline_jobs[run_id].update(status="failed", error=f"{type(exc).__name__}: {exc}")
            finally:
                try:
                    _pipeline_jobs[run_id]["finished_at"] = time.time()
                    _save_job(_pipeline_jobs[run_id])
                finally:
                    _generation_lock.release()
        task = asyncio.create_task(run_job())
        _pipeline_tasks.add(task)
        task.add_done_callback(_pipeline_tasks.discard)
        return JSONResponse({"run_id": run_id, "status": "running"}, status_code=202)

    @api.get("/api/maniskill/pipeline/{run_id}")
    async def pipeline_status(run_id: str):
        if not re.fullmatch(r"[0-9a-f]{12}", run_id):
            return JSONResponse({"error": "unknown run"}, status_code=404)
        if run_id in _pipeline_jobs:
            return JSONResponse(_pipeline_jobs[run_id])
        path = OUTPUT_DIR/"datasets"/run_id/"run.json"
        job_path = OUTPUT_DIR/"datasets"/(run_id+".job.json")
        if job_path.is_file():
            saved = json.loads(job_path.read_text(encoding="utf-8"))
            if saved["status"] != "running":
                return JSONResponse(_pipeline_public(saved))
            if not path.is_file():
                path = job_path
        if path.is_file():
            result = json.loads(path.read_text(encoding="utf-8"))
            if result["status"] == "running":
                result.update(status="interrupted", error="server stopped before this run finished")
            return JSONResponse(_pipeline_public(result))
        return JSONResponse({"error": "unknown run"}, status_code=404)

    # ``filename`` intentionally spans one nested episode directory, e.g.
    # ``episode_000/lerobot_v3.zip``.  FastAPI's default string path parameter
    # stops at the slash; the ``:path`` converter is required for these public
    # download URLs to work.
    @api.get("/api/maniskill/pipeline/{run_id}/files/{filename:path}")
    async def pipeline_file(run_id: str, filename: str):
        if not re.fullmatch(r"[0-9a-f]{12}", run_id) or not re.fullmatch(r"episode_\d{3}/(lerobot_v3\.zip|ue_white\.mp4|trajectory\.gif|episode\.json)", filename):
            return JSONResponse({"error": "unknown file"}, status_code=404)
        path = OUTPUT_DIR/"datasets"/run_id/filename
        # Publish only episodes which made it into the run's successful episode list.
        report_path = OUTPUT_DIR/"datasets"/run_id/"run.json"
        if not path.is_file() or not report_path.is_file():
            return JSONResponse({"error": "file not ready"}, status_code=404)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if int(filename[8:11]) not in {e["index"] for e in report["episodes"]}:
            return JSONResponse({"error": "episode not accepted"}, status_code=404)
        media = "video/mp4" if path.suffix == ".mp4" else "image/gif" if path.suffix == ".gif" else "application/json" if path.suffix == ".json" else "application/zip"
        return FileResponse(path, media_type=media, filename=path.name if path.suffix == ".zip" else None)

    return api


app = create_app() if FastAPI is not None else None


if __name__ == "__main__":  # pragma: no cover
    if app is None:
        raise SystemExit("Install fastapi and uvicorn to run the web API")
    import uvicorn

    uvicorn.run("ManiSkillGen.app:app", host="127.0.0.1", port=8090, reload=False)
