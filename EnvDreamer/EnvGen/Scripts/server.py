#!/usr/bin/env python3
"""
EnvGen Simple Builder Server

Minimal routes:
- GET /                        → simple UI (web_simple.html)
- GET /shots/latest.png        → latest screenshot
- POST /api/simple/build-from-prompt  → prompt → layout JSON → write → build
- POST /api/simple/build-from-image   → image(+prompt) → layout JSON → write → build
"""

import asyncio
import json
import os
import sys
import uuid
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel

import uvicorn
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# Windows event loop policy (safe no-op elsewhere)
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Scripts.core.config import PUBLIC_IMG_DIR, SERVER_HOST, SERVER_PORT
from Scripts.utils.image_utils import (
    copy_latest_screenshot_to_public,
    find_latest_screenshot,
    get_latest_image_path,
)
from Scripts.api.layout_planner import (
    generate_layout_from_prompt,
    generate_layout_from_image,
    write_layout_file,
)
from Scripts.api.layout_sync import apply_layout_update
from Scripts.api.rc_client import rc_call
from Scripts.panda_demo import run_demo, call as panda_call, READY
from Scripts.core.config import RC_BASE, RC_OBJECT_PATH, OPENAI_API_KEY

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
SIMPLE_TEMPLATE_PATH = os.path.join(TEMPLATE_DIR, "web_simple.html")

# Ensure public image directory exists
try:
    os.makedirs(PUBLIC_IMG_DIR, exist_ok=True)
except Exception:
    pass

app = FastAPI()
from Scripts import grasp_jobs
app.include_router(grasp_jobs.router)
scene_request_lock = asyncio.Lock()


@app.middleware('http')
async def serialize_scene_updates(request: Request, call_next):
    # Keep status/download reads responsive, while protecting the shared UE
    # scene across robot, layout, screenshot and long-running capture jobs.
    if request.method == 'POST' and request.url.path.startswith('/api/simple/'):
        if scene_request_lock.locked() or grasp_jobs.active_job():
            return JSONResponse({'error':'场景正在执行任务或渲染，请等待完成后再修改。'}, status_code=409)
        async with scene_request_lock:
            # Grasp jobs take their own screenshot after they have finished
            # constructing the task scene.  Requesting a ``latest`` snapshot
            # here races that first scene snapshot in UE's editor and can
            # leave Web Remote Control with two queued high-resolution
            # captures.  The race is especially visible in UE 5.6, where it
            # can trigger the Remote Control Map assertion or delay the PNG
            # for several minutes.  Let the worker own the screenshot timing.
            return await call_next(request)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(SIMPLE_TEMPLATE_PATH, media_type="text/html")


@app.get("/shots/latest.png")
async def latest_png():
    try:
        try:
            copy_latest_screenshot_to_public()
        except Exception:
            pass
        path = get_latest_image_path()
        if not (path and os.path.exists(path)):
            return JSONResponse({"error": "Screenshot not available"}, status_code=404)
        file_size = os.path.getsize(path)
        return FileResponse(
            path,
            media_type="image/png",
            headers={
                "Content-Length": str(file_size),
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    except Exception as e:
        return JSONResponse({"error": f"Unable to serve screenshot: {str(e)}"}, status_code=500)


@app.post("/api/simple/snapshot")
def simple_snapshot():
    """Request a fresh UE editor screenshot and publish it for the viewer."""
    previous_mtime = None
    previous_path = find_latest_screenshot()
    if previous_path:
        try:
            previous_mtime = os.path.getmtime(previous_path)
        except OSError:
            previous_mtime = None

    if not _request_scene_snapshot():
        return JSONResponse(
            {"status": "error", "error": "Unable to request UE snapshot"},
            status_code=502,
        )

    copied = copy_latest_screenshot_to_public(
        wait_for_new=True,
        previous_mtime=previous_mtime,
    )
    fresh = bool(copied)
    if copied and previous_mtime is not None:
        try:
            fresh = os.path.getmtime(copied) > previous_mtime
        except OSError:
            fresh = False
    if not fresh:
        return JSONResponse(
            {"status": "error", "error": "UE did not produce a new screenshot in time"},
            status_code=504,
        )
    return JSONResponse({
        "status": "OK",
        "copied": True,
        "fresh": True,
        "path": get_latest_image_path(),
    })


robot_lock = threading.Lock()


@app.get("/api/simple/capabilities")
def capabilities():
    return {"engine": "UE5", "llm_configured": bool(OPENAI_API_KEY)}


@app.post("/api/simple/robot/demo")
def robot_demo():
    if not robot_lock.acquire(blocking=False):
        return JSONResponse({"error": "机械臂正在更新，请稍后重试"}, status_code=409)
    try:
        report = run_demo(base_url=RC_BASE, object_path=RC_OBJECT_PATH)
        copy_latest_screenshot_to_public()
        return {"status": "OK", "state": report["state"], "message": "Panda 桌面场景已生成，截图已更新。"}
    except Exception as exc:
        return JSONResponse({"error": f"UE 场景生成失败：{exc}。请确认编辑器已打开且没有阻塞弹窗。"}, status_code=502)
    finally:
        robot_lock.release()


class RobotAction(BaseModel):
    action: Literal["ready", "pose", "open", "close"]


@app.post("/api/simple/robot/control")
def robot_control(body: RobotAction):
    if not robot_lock.acquire(blocking=False):
        return JSONResponse({"error": "机械臂正在更新，请稍后重试"}, status_code=409)
    try:
        robot_id = "panda_demo_panda_robot"
        def call(fn, **params):
            return panda_call(fn, params, RC_BASE, RC_OBJECT_PATH)
        state = json.loads(call("RC_GetRobotState", Id=robot_id)["ReturnValue"])
        if not state:
            return JSONResponse({"error": "请先点击生成机械臂场景"}, status_code=409)
        if body.action in ("ready", "pose"):
            joints = READY if body.action == "ready" else [.2, -.3, .1, -1.8, .2, 1.4, .3]
            call("RC_SetRobotJoints", Id=robot_id, **{f"J{i+1}": v for i, v in enumerate(joints)})
        else:
            call("RC_SetRobotGripper", Id=robot_id, OpeningMeters=.08 if body.action == "open" else 0.)
        snapshot = simple_snapshot()
        if snapshot.status_code != 200:
            return snapshot
        return {"status": "OK", "state": json.loads(call("RC_GetRobotState", Id=robot_id)["ReturnValue"])}
    except Exception as exc:
        return JSONResponse({"error": f"机械臂更新失败：{exc}"}, status_code=502)
    finally:
        robot_lock.release()


# Register the static directory after the dynamic latest-screenshot route.  If
# mounted first, StaticFiles handles /shots/latest.png before latest_png() can
# copy the newest UE screenshot into the public directory.
app.mount("/shots", StaticFiles(directory=PUBLIC_IMG_DIR), name="shots")

# Seed the public directory at startup so the embedded viewer has an image on
# its first request, even before UE produces another snapshot.
try:
    copy_latest_screenshot_to_public()
except Exception:
    pass


def _default_layout_path() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "Config", "home_layout.json")


LAYOUT_HISTORY_DIR = os.path.join(os.path.dirname(_default_layout_path()), "LayoutHistory")
LAYOUT_HISTORY_INDEX = os.path.join(LAYOUT_HISTORY_DIR, "history.json")
MAX_HISTORY_ENTRIES = 50


def _request_scene_snapshot() -> bool:
    """Ask UE for a fresh background-rendered preview image."""
    try:
        return bool(rc_call("RC_Snapshot", {
            "Name": "latest",
            "ResX": 1280,
            "ResY": 720,
        }))
    except Exception:
        return False


def _load_layout(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    except Exception:
        return None


def _read_history() -> Dict[str, Any]:
    try:
        with open(LAYOUT_HISTORY_INDEX, "r", encoding="utf-8") as f:
            data = json.load(f)
            versions = data.get("versions", [])
            if not isinstance(versions, list):
                versions = []
            return {
                "versions": versions,
                "current": data.get("current"),
            }
    except FileNotFoundError:
        return {"versions": [], "current": None}
    except Exception:
        return {"versions": [], "current": None}


def _write_history(history: Dict[str, Any]) -> None:
    os.makedirs(LAYOUT_HISTORY_DIR, exist_ok=True)
    with open(LAYOUT_HISTORY_INDEX, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def _truncate(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _trim_history(history: Dict[str, Any]) -> None:
    versions = history.get("versions", [])
    if len(versions) <= MAX_HISTORY_ENTRIES:
        return
    excess = len(versions) - MAX_HISTORY_ENTRIES
    removed = versions[:excess]
    history["versions"] = versions[excess:]
    current_id = history.get("current")
    removed_ids = {entry.get("id") for entry in removed}
    for entry in removed:
        filename = entry.get("filename")
        if filename:
            try:
                os.remove(os.path.join(LAYOUT_HISTORY_DIR, filename))
            except FileNotFoundError:
                pass
            except Exception:
                pass
    if current_id in removed_ids:
        history["current"] = history["versions"][-1]["id"] if history["versions"] else None


def _create_checkpoint(layout: Optional[Dict[str, Any]], source: str, note: Optional[str] = None) -> Dict[str, Any]:
    os.makedirs(LAYOUT_HISTORY_DIR, exist_ok=True)
    history = _read_history()
    timestamp = datetime.utcnow()
    version_id = timestamp.strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
    entry: Dict[str, Any] = {
        "id": version_id,
        "timestamp": timestamp.isoformat(timespec="seconds") + "Z",
        "source": source,
    }
    if note:
        entry["note"] = _truncate(note)

    if layout is not None:
        filename = f"{version_id}.json"
        path = os.path.join(LAYOUT_HISTORY_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(layout, f, indent=2)
        entry["filename"] = filename
    else:
        entry["filename"] = None

    versions: List[Dict[str, Any]] = history.get("versions", [])
    versions.append(entry)
    history["versions"] = versions
    history["current"] = entry["id"]
    _trim_history(history)
    _write_history(history)
    return entry


def _ensure_history_initialized(current_layout: Optional[Dict[str, Any]]) -> None:
    if not current_layout:
        return
    history = _read_history()
    if history.get("versions"):
        return
    _create_checkpoint(current_layout, source="initial", note="Initial layout snapshot")


def _load_checkpoint(version_id: str) -> Optional[Dict[str, Any]]:
    history = _read_history()
    for entry in history.get("versions", []):
        if entry.get("id") == version_id:
            filename = entry.get("filename")
            if not filename:
                return None
            path = os.path.join(LAYOUT_HISTORY_DIR, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except FileNotFoundError:
                raise RuntimeError("Checkpoint file missing on disk")
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Checkpoint file invalid JSON: {e}")
    raise RuntimeError("Checkpoint not found")


def _update_history_current(version_id: Optional[str]) -> Dict[str, Any]:
    history = _read_history()
    history["current"] = version_id
    _write_history(history)
    return history


def _layout_state_payload() -> Dict[str, Any]:
    history = _read_history()
    current_layout = _load_layout(_default_layout_path())
    return {
        "current": history.get("current"),
        "history": history.get("versions", []),
        "layout": current_layout,
    }


@app.post("/api/simple/build-from-prompt")
async def simple_build_from_prompt(req: Request):
    body = await req.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"error": "No prompt provided"}, status_code=400)

    target_path = _default_layout_path()
    current_layout = _load_layout(target_path)
    _ensure_history_initialized(current_layout)
    try:
        layout = generate_layout_from_prompt(prompt, current_layout=current_layout, validate_output=False)
        write_layout_file(target_path, layout)
        _create_checkpoint(layout, source="prompt", note=prompt)
        update_summary = apply_layout_update(current_layout, layout, target_path)
        # UE editor sessions do not persist JsonGenerated actors reliably. If
        # the JSON is unchanged after an editor restart, the diff is a noop but
        # the viewport can still be empty; rebuild once to resynchronise UE.
        if update_summary.get("mode") == "noop":
            update_summary = apply_layout_update(None, layout, target_path)
        _request_scene_snapshot()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    state = _layout_state_payload()
    return JSONResponse({
        "status": "OK",
        "path": target_path,
        "layout": layout,
        "rebuilt": update_summary.get("mode") != "noop",
        "update": update_summary,
        "history": state["history"],
        "current": state["current"],
    })


@app.post("/api/simple/build-from-image")
async def simple_build_from_image(image: UploadFile = File(...), prompt: str = Form("")):
    target_path = _default_layout_path()
    current_layout = _load_layout(target_path)
    _ensure_history_initialized(current_layout)
    data = await image.read()
    try:
        layout = generate_layout_from_image(prompt or "", data, current_layout=current_layout, validate_output=False)
        write_layout_file(target_path, layout)
        note = f"{image.filename or 'image'} {prompt}".strip()
        _create_checkpoint(layout, source="image", note=note)
        update_summary = apply_layout_update(current_layout, layout, target_path)
        if update_summary.get("mode") == "noop":
            update_summary = apply_layout_update(None, layout, target_path)
        _request_scene_snapshot()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    state = _layout_state_payload()
    return JSONResponse({
        "status": "OK",
        "path": target_path,
        "layout": layout,
        "rebuilt": update_summary.get("mode") != "noop",
        "update": update_summary,
        "filename": image.filename,
        "history": state["history"],
        "current": state["current"],
    })


@app.get("/api/simple/layout/state")
async def layout_state():
    return JSONResponse(_layout_state_payload())


def _normalize_color(value: Any) -> List[float]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("color must be a list or tuple")
    if len(value) < 3:
        raise ValueError("color must contain at least RGB components")
    floats: List[float] = []
    for idx, component in enumerate(value):
        try:
            f = float(component)
        except Exception as exc:
            raise ValueError(f"color component {idx} is not numeric") from exc
        f = max(0.0, min(1.0, f))
        floats.append(f)
        if len(floats) == 4:
            break
    while len(floats) < 4:
        floats.append(1.0)
    return floats


@app.post("/api/simple/layout/set-wall-color")
async def set_wall_color(req: Request):
    body = await req.json()
    room = (body.get("room") or "").strip()
    color_value = body.get("color")
    if not room:
        return JSONResponse({"error": "room is required"}, status_code=400)
    try:
        color = _normalize_color(color_value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    target_path = _default_layout_path()
    previous_layout = _load_layout(target_path)
    if not previous_layout or "rooms" not in previous_layout:
        return JSONResponse({"error": "Layout not available"}, status_code=404)
    if room not in previous_layout["rooms"]:
        return JSONResponse({"error": f"Room '{room}' not found"}, status_code=404)

    _ensure_history_initialized(previous_layout)
    new_layout = json.loads(json.dumps(previous_layout))
    new_layout["rooms"][room]["wall_color"] = color

    try:
        write_layout_file(target_path, new_layout)
    except Exception as exc:
        return JSONResponse({"error": f"Unable to write layout: {exc}"}, status_code=500)

    try:
        _create_checkpoint(new_layout, source="wall-color", note=f"{room} walls")
    except Exception:
        pass

    try:
        # Use apply_layout_update to rebuild with new color (HomeAgent handles it)
        update_summary = apply_layout_update(previous_layout, new_layout, target_path)
        _request_scene_snapshot()
        color_summary = {
            "updated": True,
            "mode": update_summary.get("mode", "unknown"),
            "rebuilt_rooms": update_summary.get("rebuilt_rooms", [])
        }
    except Exception as exc:
        color_summary = {"error": str(exc)}

    state = _layout_state_payload()
    return JSONResponse({
        "status": "OK",
        "room": room,
        "color": color,
        "summary": color_summary,
        "history": state["history"],
        "current": state["current"],
        "layout": state["layout"],
    })


@app.post("/api/simple/layout/restore")
async def restore_layout(req: Request):
    body = await req.json()
    version_id = (body.get("version_id") or "").strip()
    if not version_id:
        return JSONResponse({"error": "version_id required"}, status_code=400)

    try:
        layout = _load_checkpoint(version_id)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    target_path = _default_layout_path()
    previous_layout = _load_layout(target_path)
    if layout is None:
        try:
            os.remove(target_path)
        except FileNotFoundError:
            pass
        except Exception as e:
            return JSONResponse({"error": f"Unable to clear layout: {e}"}, status_code=500)
        update_summary = apply_layout_update(previous_layout, None, target_path)
    else:
        try:
            write_layout_file(target_path, layout)
        except Exception as e:
            return JSONResponse({"error": f"Unable to write layout: {e}"}, status_code=500)
        update_summary = apply_layout_update(previous_layout, layout, target_path)
        _request_scene_snapshot()

    history = _update_history_current(version_id)
    current_layout = _load_layout(target_path)
    return JSONResponse({
        "status": "OK",
        "restored": version_id,
        "layout": current_layout,
        "update": update_summary,
        "history": history.get("versions", []),
        "current": history.get("current"),
    })


@app.post("/api/simple/layout/clear")
async def clear_layout():
    target_path = _default_layout_path()
    current_layout = _load_layout(target_path)
    if current_layout:
        _ensure_history_initialized(current_layout)
        _create_checkpoint(current_layout, source="snapshot", note="Snapshot before clear")

    try:
        os.remove(target_path)
    except FileNotFoundError:
        pass
    except Exception as e:
        return JSONResponse({"error": f"Unable to remove layout: {e}"}, status_code=500)

    update_summary = apply_layout_update(current_layout, None, target_path)
    _request_scene_snapshot()
    _create_checkpoint(None, source="clear", note="Layout cleared")
    state = _layout_state_payload()
    return JSONResponse({
        "status": "cleared",
        "update": update_summary,
        "history": state["history"],
        "current": state["current"],
        "layout": state["layout"],
    })


if __name__ == "__main__":
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
