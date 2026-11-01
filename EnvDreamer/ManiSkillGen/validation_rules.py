"""Structured static checks. Unknown runtime checks always remain null."""
from __future__ import annotations

import math
from typing import Mapping

from .scene_spec import normalize_scene_spec
from .validator import (_schema_errors, _task_feasibility, _portal_task_feasibility,
                        _aabb, _overlap, _is_number, load_asset_registry,
                        SUPPORTED_TASKS, SUPPORTED_ROBOTS, SUPPORTED_CONTROLS, SUPPORTED_TEMPLATES)

GEOMETRY_CODES = {"bounds", "below_table", "above_workspace", "unsupported", "overlap"}
REPAIRABLE_CODES = GEOMETRY_CODES | {"robot_reach"}


def marker(obj):
    return obj.get("asset") == "primitive_target" or obj.get("shape") == "target"


def supported(obj, objects, table):
    """Conservative full-footprint support; markers never support rigid bodies."""
    box = _aabb(obj)
    if abs(box[4] - table) <= .003:
        return True
    for other in objects:
        if other["id"] == obj["id"] or marker(other):
            continue
        support = _aabb(other)
        # Curved primitives cannot provide the planar support assumed here.
        if other["shape"] == "sphere":
            continue
        if (abs(box[4] - support[5]) <= .003 and
                box[0] >= support[0] - 1e-6 and box[1] <= support[1] + 1e-6 and
                box[2] >= support[2] - 1e-6 and box[3] <= support[3] + 1e-6):
            return True
    return False


def validate(spec, *, asset_registry=None, check_runtime=False):
    report = {"ok": False, "errors": [], "warnings": [], "issues": [], "normalized_spec": {},
              "checks": {key: None for key in ("schema", "assets", "geometry", "task_feasibility",
                         "environment_reset", "physics_stability", "preview_render", "collision_free_ik", "task_rollout")},
              "task_feasibility": {"status": "unknown", "proof_level": "not_tested", "checks": []}}

    def issue(code, message, path="$", objects=(), repairable=False):
        report["errors"].append(message)
        report["issues"].append({"code": code, "severity": "error", "message": message,
                                 "path": path, "objects": list(objects), "repairable": repairable})

    # Reject non-JSON finite numbers before normalization can coerce/disguise them.
    def finite(value, path):
        if isinstance(value, float) and not math.isfinite(value):
            issue("schema", "non-finite number at " + path, path)
        elif isinstance(value, Mapping):
            for key, child in value.items():
                finite(child, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                finite(child, f"{path}[{index}]")
    finite(spec, "$")
    if report["errors"]:
        report["checks"]["schema"] = False
        return report
    try:
        normalized = normalize_scene_spec(spec)
    except (TypeError, ValueError, OverflowError, AttributeError) as exc:
        issue("schema", str(exc))
        report["checks"]["schema"] = False
        return report
    finite(normalized, "$")
    if report["errors"]:
        report["checks"]["schema"] = False
        return report
    report["normalized_spec"] = normalized
    for error in _schema_errors(normalized):
        issue("schema", error)
    report["checks"]["schema"] = not report["errors"]
    if report["errors"]:
        return report

    scene, robot, task = normalized["scene"], normalized["robot"], normalized["task"]
    objects = scene["objects"]
    policy = normalized.get("repair", {})
    if not isinstance(policy, dict) or set(policy) - {"locked_object_ids", "allow_pose_changes"}:
        issue("schema", "repair accepts only locked_object_ids and allow_pose_changes", "repair")
    else:
        locks = policy.get("locked_object_ids", [])
        if not isinstance(locks, list) or any(not isinstance(x, str) for x in locks):
            issue("schema", "repair.locked_object_ids must be a list of object IDs", "repair")
        elif any(x not in [o["id"] for o in objects] for x in locks):
            issue("schema", "repair.locked_object_ids references a missing object", "repair")
        if not isinstance(policy.get("allow_pose_changes", True), bool):
            issue("schema", "repair.allow_pose_changes must be boolean", "repair")
    # Arbitrary unimplemented constraints must not be silently discarded during repair.
    if normalized.get("constraints"):
        issue("unsupported_constraints", "constraints are not implemented; use repair.locked_object_ids or disable pose changes", "constraints")
    randomization = normalized["randomization"]
    if set(randomization) - {"object_pose_jitter", "color_jitter"}:
        issue("schema", "unsupported randomization property", "randomization")
    for key in ("object_pose_jitter", "color_jitter"):
        value = randomization.get(key, 0)
        if not _is_number(value) or value < 0:
            issue("schema", f"randomization.{key} must be finite and non-negative", "randomization." + key)

    cameras = normalized["cameras"]
    for name, camera in cameras.items():
        if not isinstance(camera, dict):
            issue("schema", f"cameras.{name} must be an object")
            continue
        for key in ("position", "look_at"):
            value = camera.get(key)
            if not isinstance(value, list) or len(value) != 3 or not all(_is_number(v) for v in value):
                issue("schema", f"cameras.{name}.{key} must be three finite numbers")
        resolution = camera.get("resolution", [640, 480])
        if not isinstance(resolution, list) or len(resolution) != 2 or any(type(v) is not int or not 16 <= v <= 2048 for v in resolution):
            issue("schema", f"cameras.{name}.resolution must be two integers in [16, 2048]")
        if camera.get("position") == camera.get("look_at"):
            issue("schema", f"cameras.{name} position and look_at must differ")

    if normalized["backend"] == "portal_trace":
        from .portal_trace_backend import validate_portal_trace_spec
        for error in validate_portal_trace_spec(normalized):
            issue("portal_contract", error)
        report["task_feasibility"] = _portal_task_feasibility(normalized, valid=not report["errors"])
        report["warnings"].append("PORTAL uses registered layouts; geometry/IK/task completion are not certified by registration")
        report["checks"]["assets"] = not report["errors"]
    else:
        registry = load_asset_registry() if asset_registry is None else asset_registry
        if scene["template"] not in SUPPORTED_TEMPLATES:
            issue("task", "scene.template is not supported by the tabletop UE5/ManiSkill adapters", "scene.template")
        if normalized["backend"] not in {"maniskill3", "ue5"}:
            issue("asset", f"unknown backend '{normalized['backend']}'", "backend")
        robot_ok = robot["type"] in SUPPORTED_ROBOTS
        control_ok = robot["control_mode"] in SUPPORTED_CONTROLS or (normalized["backend"] == "ue5" and robot["control_mode"] == "ue5_joint_pos")
        if not robot_ok or not control_ok:
            issue("asset", "robot.type or robot.control_mode is not supported", "robot")
        if normalized["backend"] == "ue5" and robot["type"] not in {"franka", "panda", "Sawyer", "UR5e"}:
            issue("asset", "UE5 runtime currently exposes Franka/Panda, Sawyer and UR5e articulated actors", "robot")
        if task["template"] not in SUPPORTED_TASKS:
            issue("task", "task.template is not supported", "task.template")
        ids = [obj["id"] for obj in objects]
        if len(ids) != len(set(ids)):
            issue("schema", "duplicate object id", "scene.objects")
        for obj in objects:
            oid = obj["id"]
            if obj["asset"] not in registry:
                issue("asset", f"asset '{obj['asset']}' is not registered", "scene.objects", [oid])
            elif registry[obj["asset"]].get("kind") != "primitive":
                issue("asset", f"'{oid}' requires a primitive object asset", objects=[oid])
            elif registry[obj["asset"]].get("shape") != obj["shape"] and not (obj["asset"] == "primitive_target" and obj["shape"] == "box"):
                issue("shape", f"'{oid}' shape does not match its registered primitive asset", objects=[oid])
            if set(obj) - {"id", "asset", "shape", "size", "pose", "color", "dynamic"}:
                issue("shape", f"'{oid}' contains unsupported object properties; rotation/physics customization is not implemented", objects=[oid])
            if obj["shape"] == "sphere" and max(obj["size"]) - min(obj["size"]) > 1e-6:
                issue("shape", f"sphere '{oid}' must have equal XYZ diameters", objects=[oid])
            if obj["shape"] == "cylinder" and abs(obj["size"][0] - obj["size"][1]) > 1e-6:
                issue("shape", f"cylinder '{oid}' must have equal XY diameters", objects=[oid])
            if marker(obj) and obj["dynamic"]:
                issue("shape", f"target marker '{oid}' must be static", objects=[oid])
        workspace = scene["workspace"]
        wx, wy, wz = workspace["size"]
        table = workspace["table_height"]
        for index, obj in enumerate(objects):
            path = f"scene.objects[{index}].pose"
            oid, box = obj["id"], _aabb(obj)
            if box[0] < -wx/2-1e-6 or box[1] > wx/2+1e-6 or box[2] < -wy/2-1e-6 or box[3] > wy/2+1e-6:
                issue("bounds", f"'{oid}' lies outside workspace bounds", path, [oid], True)
            if box[4] < table - .003:
                issue("below_table", f"'{oid}' is below the table surface", path, [oid], True)
            elif not supported(obj, objects, table):
                issue("unsupported", f"'{oid}' is floating or lacks full footprint support", path, [oid], True)
            if box[5] > table + wz + .003:
                issue("above_workspace", f"'{oid}' is above the workspace height", path, [oid], True)
            for other in objects[index+1:]:
                if not marker(obj) and not marker(other) and _overlap(box, _aabb(other), tolerance=.001):
                    issue("overlap", f"initial collision/overlap between '{oid}' and '{other['id']}'", path, [oid, other["id"]], True)
        by_id = {obj["id"]: obj for obj in objects}
        # Semantic constraints cannot be repaired by moving objects or relaxing tolerance.
        expected = {"pick_and_place": "object_in_target", "tabletop_pick_place": "object_in_target",
                    "push": "object_displaced", "tabletop_push": "object_displaced",
                    "stack": "object_on_object", "tabletop_stack": "object_on_object"}.get(task["template"])
        if not normalized["success"]:
            issue("task", "success must contain at least one predicate", "success")
        for index, success in enumerate(normalized["success"]):
            if success.get("predicate") != expected:
                issue("task", f"success[{index}].predicate does not match task", "success")
            for key in ("object_id", "target_id"):
                if not isinstance(success.get(key), str) or success.get(key) != task.get(key) or success.get(key) not in by_id:
                    issue("task", f"success[{index}].{key} must match an existing task object", "success")
            value = success.get("position_tolerance", .03)
            if not _is_number(value) or value <= 0:
                issue("task", f"success[{index}].position_tolerance must be positive", "success")
        feasibility = _task_feasibility(normalized, by_id=by_id, table_height=table)
        report["task_feasibility"] = feasibility
        for check in feasibility["checks"]:
            if check["status"] == "fail":
                code = {"robot_reach": "robot_reach", "goal_on_table": "below_table"}.get(check["name"], "task")
                issue(code, "task feasibility: " + check["message"], "task",
                      [x for x in (task.get("object_id"), task.get("target_id")) if isinstance(x, str)], code in REPAIRABLE_CODES)
        report["warnings"].extend(feasibility["warnings"])
        report["checks"].update(assets=not any(i["code"] == "asset" for i in report["issues"]),
                                geometry=not any(i["code"] in GEOMETRY_CODES | {"shape"} for i in report["issues"]),
                                task_feasibility=feasibility["status"] == "feasible")
        report["warnings"].append("Overlap/support use conservative axis-aligned primitive bounds; IK and task rollout are not tested")
    if check_runtime:
        report["warnings"].append("Static validate never resets a simulator; use generate for runtime checks")
    report["checks"]["schema"] = not any(i["code"] == "schema" for i in report["issues"])
    report["ok"] = not report["errors"]
    return report
