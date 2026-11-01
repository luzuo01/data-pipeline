"""PORTAL-Trace compatibility backend.

PORTAL-Trace is a MuJoCo/robosuite benchmark, while the primary generator is
ManiSkill3/SAPIEN.  This module keeps that boundary explicit: it validates a
small JSON SceneSpec in the main process and runs robosuite in a separate
worker process.  The worker is intentionally isolated because MuJoCo and the
Windows Anaconda OpenMP stack can conflict with SAPIEN/Torch in one process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping


PORTAL_ROBOTS = ("Panda", "Sawyer", "IIWA", "Kinova3", "XArm7", "UR5e")

PORTAL_UE_ASSETS = {
    robot: f"/Game/Robots/PORTALTrace/{robot}/{robot}.{robot}"
    for robot in PORTAL_ROBOTS
}

PORTAL_TASKS: dict[str, dict[str, Any]] = {
    "NutAssemblySquareTight-v0": {
        "env_name": "NutAssemblySquareTight-v0",
        "env_kwargs": {"single_object_mode": 2, "nut_type": "square"},
        "aliases": ("nutassemblysquare", "square nut", "方形螺母", "方螺母"),
    },
    "NutAssemblyRoundTight-v0": {
        "env_name": "NutAssemblyRoundTight-v0",
        "env_kwargs": {"single_object_mode": 2, "nut_type": "round"},
        "aliases": ("nutassemblyround", "round nut", "圆形螺母", "圆螺母", "nut assembly", "螺母", "拧螺母"),
    },
    "ToolHang": {
        "env_name": "ToolHang",
        "env_kwargs": {},
        "aliases": ("tool hang", "hang the tool", "挂工具", "工具挂载"),
    },
    "Stack": {
        "env_name": "Stack",
        "env_kwargs": {},
        "aliases": ("stack", "stack cubes", "堆叠", "叠方块"),
    },
    "Door": {
        "env_name": "Door",
        "env_kwargs": {},
        "aliases": ("door", "open door", "开门", "打开门"),
    },
    "Wipe": {
        "env_name": "Wipe",
        "env_kwargs": {},
        "aliases": ("wipe", "擦拭", "擦桌子", "擦桌面"),
    },
}

PORTAL_TASK_ALIASES = {
    alias.lower(): task_id
    for task_id, entry in PORTAL_TASKS.items()
    for alias in (task_id, *entry["aliases"])
}


class PortalTraceRuntimeError(RuntimeError):
    """Raised when the isolated MuJoCo/robosuite worker cannot run."""


def resolve_portal_trace_root() -> Path | None:
    candidates: list[Path] = []
    configured = os.getenv("PORTAL_TRACE_ROOT")
    if configured:
        candidates.append(Path(configured))
    project_root = Path(__file__).resolve().parents[1].parent
    candidates.extend(
        [
            project_root / "PORTAL-Trace",
            project_root / "_external_PORTAL_Trace_20261025",
            Path.cwd() / "PORTAL-Trace",
        ]
    )
    for candidate in candidates:
        if (candidate / "portal_trace" / "registry" / "tasks.py").exists():
            return candidate.resolve()
    return None


def portal_trace_python_candidates() -> list[Path]:
    """Return configured and bundled runtimes in preference order.

    A stale ``PORTAL_TRACE_PYTHON`` is common after recreating a uv
    environment.  Keep it in the diagnostic list, but never let it shadow a
    working project-local environment.
    """
    candidates: list[Path] = []
    configured = os.getenv("PORTAL_TRACE_PYTHON")
    if configured:
        candidates.append(Path(configured).expanduser())
    root = Path(__file__).resolve().parents[1]
    if os.name == "nt":
        candidates.extend([root / ".portal_uv_env" / "Scripts" / "python.exe", root / ".portal_env" / "Scripts" / "python.exe"])
    else:
        candidates.extend([root / ".portal_uv_env" / "bin" / "python", root / ".portal_env" / "bin" / "python"])
    return candidates


def portal_trace_python() -> Path:
    candidates = portal_trace_python_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    # Preserve a useful path for the caller's diagnostic when nothing exists.
    return candidates[0]


def portal_trace_task(task_id: str) -> dict[str, Any]:
    try:
        return PORTAL_TASKS[task_id]
    except KeyError:
        raise KeyError(f"unknown PORTAL-Trace task {task_id!r}; known: {sorted(PORTAL_TASKS)}") from None


def default_portal_trace_spec(task_id: str = "Stack", robot: str = "Panda", seed: int = 0) -> dict[str, Any]:
    task = portal_trace_task(task_id)
    return {
        "schema_version": "scene_spec_v1",
        "backend": "portal_trace",
        "seed": int(seed),
        "scene": {
            "template": "portal_trace",
            "workspace": {"size": [1.0, 1.0, 0.8], "table_height": 0.82},
            "objects": [],
        },
        "robot": {"type": robot, "base_pose": [0.0, 0.0, 0.0], "control_mode": "robosuite_default"},
        "task": {
            "template": task_id,
            "env_name": task["env_name"],
            "env_kwargs": dict(task["env_kwargs"]),
        },
        "success": [{"predicate": "portal_trace_success", "task_id": task_id}],
        "cameras": {
            "agentview": {
                "position": [0.8, 0.0, 0.8],
                "look_at": [0.0, 0.0, 0.8],
                "resolution": [640, 480],
            }
        },
        "portal_trace": {
            "task_id": task_id,
            "robot_family": robot,
            "source": "liu-yang-maker/PORTAL-Trace",
            "ue_asset": PORTAL_UE_ASSETS[robot],
            "ue_asset_kind": "StaticMesh",
            "ue_articulated": False,
        },
    }


def validate_portal_trace_spec(spec: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if spec.get("backend") != "portal_trace":
        errors.append("backend must be 'portal_trace' for PORTAL-Trace scenes")
    robot = spec.get("robot", {}).get("type") if isinstance(spec.get("robot"), Mapping) else None
    if robot not in PORTAL_ROBOTS:
        errors.append(f"robot.type {robot!r} is not a PORTAL-Trace family; expected one of {PORTAL_ROBOTS}")
    task = spec.get("task", {})
    task_id = task.get("template") if isinstance(task, Mapping) else None
    if task_id not in PORTAL_TASKS:
        errors.append(f"task.template {task_id!r} is not a PORTAL-Trace core task; expected one of {tuple(PORTAL_TASKS)}")
    if isinstance(task, Mapping) and task_id in PORTAL_TASKS:
        expected = PORTAL_TASKS[task_id]["env_name"]
        if task.get("env_name", expected) != expected:
            errors.append(f"task.env_name must be {expected!r} for {task_id!r}")
    if task_id in PORTAL_TASKS and robot in PORTAL_ROBOTS:
        reference = default_portal_trace_spec(task_id, robot)
        for key in ("scene", "robot", "task", "success"):
            if spec.get(key) != reference[key]:
                errors.append(f"PORTAL {key} customization is not implemented; use the registered task layout")
        cameras = spec.get("cameras", {})
        if set(cameras) != {"agentview"}:
            errors.append("PORTAL only supports the registered agentview camera")
        else:
            camera = cameras["agentview"]
            if not isinstance(camera, Mapping) or any(camera.get(k) != reference["cameras"]["agentview"][k] for k in ("position", "look_at")):
                errors.append("PORTAL camera extrinsics customization is not implemented")
        if any(spec.get("randomization", {}).get(k, 0) != 0 for k in ("object_pose_jitter", "color_jitter")):
            errors.append("PORTAL custom randomization is not implemented; only the registered seeded reset is supported")
    return errors


def portal_prompt_match(prompt: str) -> tuple[str | None, str | None]:
    text = (prompt or "").lower()
    task_id = next((task_id for alias, task_id in PORTAL_TASK_ALIASES.items() if alias in text), None)
    robot = next((name for name in PORTAL_ROBOTS if name.lower() in text), None)
    return task_id, robot


class PortalTraceBackend:
    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.preview_dir = self.output_dir / "previews"

    def generate(self, spec: Mapping[str, Any], *, seed: int, render: bool = True) -> dict[str, Any]:
        errors = validate_portal_trace_spec(spec)
        if errors:
            raise ValueError("PORTAL-Trace SceneSpec validation failed: " + "; ".join(errors))
        root = resolve_portal_trace_root()
        python = portal_trace_python()
        if root is None:
            raise PortalTraceRuntimeError("PORTAL_TRACE_ROOT is not configured and the PORTAL-Trace checkout was not found")
        if not python.is_file():
            checked = ", ".join(str(path) for path in portal_trace_python_candidates())
            raise PortalTraceRuntimeError(
                "PORTAL-Trace Python runtime not found. Checked: " + checked
                + ". Create it with `uv venv --python 3.11.14 .portal_uv_env` and "
                + "`uv pip install --python .portal_uv_env\\Scripts\\python.exe -r ManiSkillGen\\requirements-portal-trace.txt`, "
                + "or set PORTAL_TRACE_PYTHON to an existing Python with mujoco==3.3.2 and robosuite==1.5.2."
            )
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        preview_dir = self.preview_dir.resolve()
        instance_id = uuid.uuid4().hex[:12]
        frame_path = preview_dir / f"{instance_id}.png"
        request = {
            "portal_trace_root": str(root),
            "task_id": spec["task"]["template"],
            "robot": spec["robot"]["type"],
            "seed": int(seed),
            "render": bool(render),
            "resolution": spec.get("cameras", {}).get("agentview", {}).get("resolution", [640, 480]),
            "frame_path": str(frame_path),
        }
        with tempfile.TemporaryDirectory(prefix="portal_trace_") as temp:
            request_path = Path(temp) / "request.json"
            response_path = Path(temp) / "response.json"
            request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            worker_path = Path(__file__).with_name("portal_trace_worker.py")
            if not worker_path.exists():
                raise PortalTraceRuntimeError("portal_trace_worker.py is missing")
            env = os.environ.copy()
            env["PORTAL_TRACE_ROOT"] = str(root)
            env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
            completed = subprocess.run(
                [str(python), "-X", "faulthandler", str(worker_path), str(request_path), str(response_path)],
                cwd=str(root), env=env, capture_output=True, text=True, timeout=180,
            )
            if not response_path.exists():
                detail = (completed.stderr or completed.stdout or "worker exited without a response").strip()
                raise PortalTraceRuntimeError(f"PORTAL-Trace worker failed with exit code {completed.returncode}: {detail[-2000:]}")
            result = json.loads(response_path.read_text(encoding="utf-8"))
            if result.get("status") != "ok":
                raise PortalTraceRuntimeError(result.get("error", "PORTAL-Trace worker failed"))
            if completed.returncode != 0:
                raise PortalTraceRuntimeError(f"PORTAL-Trace worker exited with {completed.returncode}: {completed.stderr[-1000:]}")
        preview_path = str(frame_path) if render and frame_path.exists() else None
        return {"runtime": result, "preview_path": preview_path, "instance_id": instance_id}
