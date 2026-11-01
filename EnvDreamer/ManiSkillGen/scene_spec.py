"""SceneSpec v1 data model and deterministic normalization helpers."""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, Iterable, Mapping


SCHEMA_VERSION = "scene_spec_v1"
BACKEND = "maniskill3"


def validate_seed(seed: Any) -> int:
    """Use NumPy/ManiSkill's uint32 seed domain without lossy coercion."""
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be an integer in [0, 4294967295]")
    return seed


def default_scene_spec(seed: int = 0) -> Dict[str, Any]:
    """Return a complete, valid Franka tabletop pick-and-place specification."""
    return {
        "schema_version": SCHEMA_VERSION,
        "backend": BACKEND,
        "seed": int(seed),
        "scene": {
            "template": "tabletop",
            "workspace": {"size": [0.8, 0.8, 0.5], "table_height": 0.75},
            "objects": [
                {
                    "id": "cube",
                    "asset": "primitive_cube",
                    "shape": "box",
                    "size": [0.04, 0.04, 0.04],
                    "pose": [0.0, 0.0, 0.77],
                    "color": [0.85, 0.1, 0.1, 1.0],
                    "dynamic": True,
                },
                {
                    "id": "target",
                    "asset": "primitive_target",
                    "shape": "box",
                    "size": [0.08, 0.08, 0.01],
                    "pose": [0.2, 0.1, 0.755],
                    "color": [0.1, 0.2, 0.9, 1.0],
                    "dynamic": False,
                },
            ],
        },
        "robot": {
            "type": "franka",
            # ManiSkill's Panda is mounted beside the tabletop so the arm does
            # not occlude the object at the table centre in the preview.
            "base_pose": [-0.4, 0.0, 0.0],
            "control_mode": "pd_ee_delta_pose",
        },
        "task": {
            "template": "pick_and_place",
            "object_id": "cube",
            "target_id": "target",
        },
        "success": [
            {
                "predicate": "object_in_target",
                "object_id": "cube",
                "target_id": "target",
                "position_tolerance": 0.03,
            }
        ],
        "cameras": {
            "front": {
                "position": [1.1, 0.8, 1.35],
                "look_at": [0.0, 0.0, 0.85],
                "resolution": [640, 480],
            }
        },
        "randomization": {"object_pose_jitter": 0.02, "color_jitter": 0.05},
    }


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _as_float_list(value: Any, length: int, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{name} must be a list of {length} numbers")
    try:
        if any(isinstance(x, bool) for x in value):
            raise ValueError
        numbers = [float(x) for x in value]
        if not all(math.isfinite(x) for x in numbers):
            raise ValueError
        return numbers
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain only numbers") from exc


def normalize_scene_spec(spec: Mapping[str, Any] | None, seed: int | None = None) -> Dict[str, Any]:
    """Fill defaults and coerce numeric vectors without changing scene intent.

    Normalization is deliberately conservative: unknown fields are preserved so
    future schema versions can round-trip through this frontend.
    """
    if spec is None:
        spec = default_scene_spec(0 if seed is None else seed)
    if not isinstance(spec, Mapping):
        raise TypeError("SceneSpec must be a JSON object")
    result: Dict[str, Any] = _copy(dict(spec))
    validate_seed(result.get("seed", 0))
    defaults = default_scene_spec(validate_seed(result.get("seed", 0) if seed is None else seed))
    if result.get("backend") == "portal_trace":
        defaults["randomization"] = {}
    for key, value in defaults.items():
        result.setdefault(key, _copy(value))
    if seed is not None:
        result["seed"] = int(seed)
    result["schema_version"] = str(result.get("schema_version", SCHEMA_VERSION))
    result["backend"] = str(result.get("backend", BACKEND))
    result["seed"] = int(result.get("seed", 0))

    scene = result.setdefault("scene", {})
    if not isinstance(scene, dict):
        raise TypeError("scene must be a JSON object")
    scene.setdefault("template", "tabletop")
    workspace = scene.setdefault("workspace", _copy(defaults["scene"]["workspace"]))
    if not isinstance(workspace, dict):
        raise TypeError("scene.workspace must be a JSON object")
    workspace["size"] = _as_float_list(workspace.get("size", [0.8, 0.8, 0.5]), 3, "scene.workspace.size")
    raw_height = workspace.get("table_height", 0.75)
    if isinstance(raw_height, bool):
        raise ValueError("scene.workspace.table_height must be a finite number")
    workspace["table_height"] = float(raw_height)
    objects = scene.setdefault("objects", [])
    if not isinstance(objects, list):
        raise TypeError("scene.objects must be a list")
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        obj.setdefault("asset", "primitive_cube")
        obj.setdefault("shape", "box")
        obj.setdefault("size", [0.04, 0.04, 0.04])
        obj.setdefault("pose", [0.0, 0.0, workspace["table_height"] + 0.02])
        obj.setdefault("color", [0.7, 0.7, 0.7, 1.0])
        obj.setdefault("dynamic", True)
        obj["size"] = _as_float_list(obj["size"], 3, f"object {obj.get('id', '?')}.size")
        obj["pose"] = _as_float_list(obj["pose"], 3, f"object {obj.get('id', '?')}.pose")
        obj["color"] = _as_float_list(obj["color"], 4, f"object {obj.get('id', '?')}.color")

    robot = result.setdefault("robot", _copy(defaults["robot"]))
    if not isinstance(robot, dict):
        raise TypeError("robot must be a JSON object")
    robot.setdefault("type", "franka")
    robot.setdefault("base_pose", [0.0, 0.0, 0.0])
    robot.setdefault("control_mode", "pd_ee_delta_pose")
    robot["base_pose"] = _as_float_list(robot["base_pose"], 3, "robot.base_pose")

    task = result.setdefault("task", _copy(defaults["task"]))
    if not isinstance(task, dict):
        raise TypeError("task must be a JSON object")
    task.setdefault("template", "pick_and_place")
    result.setdefault("success", _copy(defaults["success"]))
    result.setdefault("cameras", _copy(defaults["cameras"]))
    result.setdefault("randomization", _copy(defaults["randomization"]))
    return result


def iter_objects(spec: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    return spec.get("scene", {}).get("objects", [])
