"""Static and optional runtime validation for ManiSkill SceneSpec documents."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from .scene_spec import normalize_scene_spec


SUPPORTED_TEMPLATES = {"tabletop", "tabletop_pick_place", "tabletop_push", "tabletop_stack"}
SUPPORTED_TASKS = {"pick_and_place", "push", "stack", "tabletop_pick_place", "tabletop_push", "tabletop_stack"}
SUPPORTED_ROBOTS = {"franka", "panda", "Sawyer", "UR5e"}
SUPPORTED_CONTROLS = {"pd_ee_delta_pose", "pd_joint_pos", "pd_joint_delta_pos", "pd_ee_pose"}
SUPPORTED_PREDICATES = {"object_in_target", "object_on_object", "object_displaced"}
ROBOT_REACH_METERS = {"panda": 0.90, "franka": 0.90}


def load_asset_registry(path: str | Path | None = None) -> Dict[str, Dict[str, Any]]:
    if path is None:
        path = Path(__file__).with_name("assets") / "registry.json"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _schema_errors(spec: Mapping[str, Any]) -> list[str]:
    try:
        import jsonschema

        schema_path = Path(__file__).with_name("schemas") / "scene_spec_v1.json"
        with open(schema_path, "r", encoding="utf-8") as handle:
            schema = json.load(handle)
        validator = jsonschema.Draft202012Validator(schema)
        return [f"{'.'.join(str(part) for part in error.path) or '$'}: {error.message}" for error in validator.iter_errors(spec)]
    except ImportError:
        return ["schema: jsonschema dependency is required"]
    except (OSError, json.JSONDecodeError) as exc:
        return [f"schema: unable to load schema ({exc})"]


def _is_number(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _aabb(obj: Mapping[str, Any]) -> tuple[float, float, float, float, float, float]:
    x, y, z = [float(v) for v in obj["pose"]]
    sx, sy, sz = [float(v) for v in obj["size"]]
    return (x - sx / 2, x + sx / 2, y - sy / 2, y + sy / 2, z - sz / 2, z + sz / 2)


def _overlap(a: Iterable[float], b: Iterable[float], tolerance: float = 1e-7) -> bool:
    av = list(a)
    bv = list(b)
    return av[0] < bv[1] - tolerance and bv[0] < av[1] - tolerance and av[2] < bv[3] - tolerance and bv[2] < av[3] - tolerance and av[4] < bv[5] - tolerance and bv[4] < av[5] - tolerance


def _task_feasibility(
    spec: Mapping[str, Any],
    *,
    by_id: Mapping[str, Mapping[str, Any]],
    table_height: float,
) -> Dict[str, Any]:
    """Analyze whether the declared task has a physically plausible solution.

    This is deliberately a conservative, explainable preflight check.  It
    proves necessary conditions (object references, support/goal geometry,
    table placement and approximate Panda reach), but it does not claim that a
    controller has solved the task.  A later runtime probe records reset-time
    information separately.
    """
    task = spec.get("task", {}) if isinstance(spec.get("task"), Mapping) else {}
    template = str(task.get("template", ""))
    canonical = {
        "tabletop_pick_place": "pick_and_place",
        "tabletop_push": "push",
        "tabletop_stack": "stack",
    }.get(template, template)
    result: Dict[str, Any] = {
        "status": "unknown",
        "confidence": "static_geometry",
        "task": canonical,
        "checks": [],
        "hard_failures": [],
        "warnings": [],
        "assumptions": [
            "Panda reach is approximated by a horizontal radius; collision-free IK and controller success require runtime probing",
            "A valid geometric goal is necessary but not sufficient for a successful trajectory",
        ],
    }

    def check(name: str, status: str, message: str, *, hard: bool = False) -> None:
        result["checks"].append({"name": name, "status": status, "message": message})
        if status == "fail" and hard:
            result["hard_failures"].append(message)
        elif status == "warn":
            result["warnings"].append(message)

    object_id = task.get("object_id")
    target_id = task.get("target_id")
    obj = by_id.get(object_id) if isinstance(object_id, str) else None
    target = by_id.get(target_id) if isinstance(target_id, str) else None
    if obj is None or target is None:
        check("task_references", "fail", "task object_id and target_id must reference existing objects", hard=True)
    else:
        check("task_references", "pass", f"task references '{object_id}' and '{target_id}'")
        if object_id == target_id:
            check("distinct_roles", "fail", "task object and target/support must be different objects", hard=True)
        if not bool(obj.get("dynamic", True)):
            check("movable_object", "fail", f"task object '{object_id}' is static and cannot be moved", hard=True)
        else:
            check("movable_object", "pass", f"task object '{object_id}' is dynamic")
        if bool(target.get("dynamic", True)) and canonical in {"pick_and_place", "push"}:
            check("stable_target", "fail", f"target '{target_id}' is dynamic; a goal region must be static", hard=True)
        else:
            check("stable_target", "pass", f"target/support '{target_id}' is stable")

        obj_pose = [float(v) for v in obj.get("pose", [0, 0, table_height])]
        target_pose = [float(v) for v in target.get("pose", [0, 0, table_height])]
        obj_size = [float(v) for v in obj.get("size", [0, 0, 0])]
        target_size = [float(v) for v in target.get("size", [0, 0, 0])]
        tolerance = 0.03
        for success in spec.get("success", []):
            if isinstance(success, Mapping) and success.get("position_tolerance") is not None:
                try:
                    tolerance = max(0.001, float(success["position_tolerance"]))
                except (TypeError, ValueError):
                    pass

        if canonical in {"pick_and_place", "push"}:
            # ``position_tolerance`` is a success threshold, not a required
            # border around the object.  Reserve a small geometric margin so
            # the default 8 cm target can contain a 4 cm cube.
            clearance = min(0.005, tolerance / 3)
            fits = target_size[0] >= obj_size[0] + 2 * clearance and target_size[1] >= obj_size[1] + 2 * clearance
            check(
                "goal_fit",
                "pass" if fits else "fail",
                f"goal region XY {target_size[:2]} {'contains' if fits else 'does not contain'} object footprint {obj_size[:2]} with {clearance:g} m geometric margin",
                hard=not fits,
            )
            on_table = target_pose[2] - target_size[2] / 2 >= table_height - 0.02
            check("goal_on_table", "pass" if on_table else "fail", f"target '{target_id}' is {'on' if on_table else 'below'} the table surface", hard=not on_table)
            distance = math.hypot(obj_pose[0] - target_pose[0], obj_pose[1] - target_pose[1])
            if distance < tolerance:
                check("nontrivial_motion", "warn", f"object '{object_id}' is already near target '{target_id}'; task may be solved at reset")
            else:
                check("nontrivial_motion", "pass", f"object-target displacement is {distance:.3f} m")
        elif canonical == "stack":
            support_top = target_pose[2] + target_size[2] / 2
            expected_z = support_top + obj_size[2] / 2
            xy_fit = target_size[0] >= obj_size[0] and target_size[1] >= obj_size[1]
            check("support_fit", "pass" if xy_fit else "fail", f"support footprint {target_size[:2]} {'supports' if xy_fit else 'cannot support'} object footprint {obj_size[:2]}", hard=not xy_fit)
            if abs(obj_pose[2] - expected_z) <= tolerance:
                check("stack_height", "pass", "object is initially at a valid stack height")
            else:
                check("stack_height", "pass", f"stack target height is {expected_z:.3f} m and can be reached from {obj_pose[2]:.3f} m")
        else:
            check("task_adapter", "unknown", f"no static feasibility adapter is registered for task '{template}'")

        robot = spec.get("robot", {}) if isinstance(spec.get("robot"), Mapping) else {}
        robot_type = str(robot.get("type", ""))
        base = robot.get("base_pose", [0, 0, 0])
        try:
            base_x, base_y = float(base[0]), float(base[1])
            reach = ROBOT_REACH_METERS.get(robot_type)
            required = [(object_id, obj_pose), (target_id, target_pose)]
            if reach is None:
                check("robot_reach", "unknown", f"no reach model is registered for robot '{robot_type}'")
            else:
                distances = {name: math.hypot(pose[0] - base_x, pose[1] - base_y) for name, pose in required}
                out = [f"{name} at {distance:.3f} m" for name, distance in distances.items() if distance > reach]
                check("robot_reach", "fail" if out else "pass", f"required points are within Panda reach {reach:.2f} m" if not out else "; ".join(out) + f" exceed estimated reach {reach:.2f} m", hard=bool(out))
                near = [f"{name} is only {distance:.3f} m from robot base" for name, distance in distances.items() if distance < 0.08]
                if near:
                    check("robot_workspace_margin", "warn", "; ".join(near) + "; wrist singularity or collision may reduce controllability")
        except (TypeError, ValueError, IndexError):
            check("robot_reach", "unknown", "robot base_pose is unavailable for reach analysis")

    hard_count = len(result["hard_failures"])
    unknown_count = sum(item["status"] == "unknown" for item in result["checks"])
    if hard_count:
        result["status"] = "infeasible"
    elif unknown_count:
        result["status"] = "unknown"
    else:
        result["status"] = "feasible"
    total = len(result["checks"])
    passed = sum(item["status"] == "pass" for item in result["checks"])
    result["score"] = round(passed / max(total, 1), 3)
    result["proof_level"] = "necessary_conditions_only"
    return result


def _portal_task_feasibility(spec: Mapping[str, Any], *, valid: bool) -> Dict[str, Any]:
    """Return benchmark-level feasibility for an isolated PORTAL-Trace task."""
    task = spec.get("task", {}) if isinstance(spec.get("task"), Mapping) else {}
    robot = spec.get("robot", {}) if isinstance(spec.get("robot"), Mapping) else {}
    result: Dict[str, Any] = {
        "status": "unknown" if valid else "infeasible",
        "confidence": "registry",
        "task": task.get("template"),
        "robot": robot.get("type"),
        "checks": [],
        "hard_failures": [] if valid else ["PORTAL-Trace task or robot is not registered"],
        "warnings": ["registry validation proves environment support; it does not prove a controller completes the task"],
        "proof_level": "registered_environment_only",
    }
    result["checks"].append({"name": "benchmark_registration", "status": "pass" if valid else "fail", "message": "task and robot family are registered" if valid else "task or robot is not registered"})
    return result


def validate_scene_spec(spec: Mapping[str, Any] | None, *, asset_registry: Mapping[str, Any] | None = None, check_runtime: bool = False) -> Dict[str, Any]:
    from .validation_rules import validate
    return validate(spec, asset_registry=asset_registry, check_runtime=check_runtime)


class SceneValidationError(ValueError):
    """Raised by the generator when a SceneSpec cannot be instantiated."""

    def __init__(self, report: Mapping[str, Any]):
        self.report = dict(report)
        super().__init__("SceneSpec validation failed: " + "; ".join(report.get("errors", [])))
