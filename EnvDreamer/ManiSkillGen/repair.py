"""Bounded deterministic pose-only repair, with validation after every round."""
from __future__ import annotations

import copy
import random

from .validator import validate_scene_spec, _aabb, _overlap
from .validation_rules import marker, supported


def pose_locked(spec, object_id):
    policy = spec.get("repair", {})
    return not policy.get("allow_pose_changes", True) or object_id in policy.get("locked_object_ids", [])


def repair_scene(spec, *, seed=0, enabled=True, max_attempts=3, asset_registry=None):
    if type(max_attempts) is not int or not 0 <= max_attempts <= 5:
        raise ValueError("max_repair_attempts must be an integer in [0, 5]")
    report = validate_scene_spec(spec, asset_registry=asset_registry)
    original = copy.deepcopy(report["normalized_spec"])
    current = copy.deepcopy(original)
    history = [{"attempt": 0, "ok": report["ok"], "issues": copy.deepcopy(report["issues"]), "patches": []}]
    rng = random.Random(seed)
    stop = "valid" if report["ok"] else "disabled" if not enabled else "attempt_limit"
    for attempt in range(1, max_attempts + 1 if enabled else 1):
        if report["ok"]:
            break
        if not current or any(not i["repairable"] for i in report["issues"]):
            stop = "unrepairable_issue"
            break
        objects = current["scene"]["objects"]
        by_id = {o["id"]: o for o in objects}
        movable = set()
        for problem in report["issues"]:
            candidates = [by_id[x] for x in problem["objects"] if x in by_id and not pose_locked(current, x)]
            if problem["code"] == "overlap":
                candidates.sort(key=lambda o: not o["dynamic"])
                candidates = candidates[:1]
            movable.update(o["id"] for o in candidates)
        if not movable:
            stop = "locked"
            break
        workspace = current["scene"]["workspace"]
        wx, wy, wz = workspace["size"]
        table = workspace["table_height"]
        patches = []
        for index, obj in enumerate(objects):
            if obj["id"] not in movable:
                continue
            before = list(obj["pose"])
            sx, sy, sz = obj["size"]
            if sx > wx or sy > wy or sz > wz:
                continue  # Changing workspace or object size would change the task.
            low_x, high_x = -wx/2 + sx/2, wx/2 - sx/2
            low_y, high_y = -wy/2 + sy/2, wy/2 - sy/2
            projected = [max(low_x, min(high_x, before[0])), max(low_y, min(high_y, before[1])), before[2]]
            obj["pose"] = projected
            if not supported(obj, objects, table) or _aabb(obj)[4] < table - .003:
                projected[2] = table + sz/2
            candidates = [list(projected)] + [[rng.uniform(low_x, high_x), rng.uniform(low_y, high_y), table + sz/2] for _ in range(96)]
            # Prefer small changes; the same seed always tries the same candidates.
            candidates.sort(key=lambda p: sum((p[i]-before[i])**2 for i in range(3)))
            selected = None
            for position in candidates:
                obj["pose"] = position
                if _aabb(obj)[5] > table + wz + .003 or not supported(obj, objects, table):
                    continue
                if any(o["id"] != obj["id"] and not marker(obj) and not marker(o) and _overlap(_aabb(obj), _aabb(o), .001) for o in objects):
                    continue
                if obj["id"] in (current["task"].get("object_id"), current["task"].get("target_id")):
                    base = current["robot"]["base_pose"]
                    if sum((position[i] - base[i])**2 for i in (0, 1)) > .9**2:
                        continue
                selected = list(position)
                break
            obj["pose"] = before if selected is None else selected
            if obj["pose"] != before:
                patches.append({"op": "replace", "path": f"/scene/objects/{index}/pose", "object_id": obj["id"],
                                "before": before, "after": list(obj["pose"]), "reason": "resolve geometric validation errors"})
        report = validate_scene_spec(current, asset_registry=asset_registry)
        history.append({"attempt": attempt, "ok": report["ok"], "issues": copy.deepcopy(report["issues"]), "patches": patches})
        if report["ok"]:
            stop = "valid"
            break
        if not patches:
            stop = "no_safe_pose_found"
            break
    report["repair"] = {"enabled": bool(enabled), "max_attempts": max_attempts, "attempts": len(history)-1,
                        "status": "repaired" if report["ok"] and len(history)>1 else "not_needed" if report["ok"] else "failed",
                        "stop_reason": stop, "history": history, "original_spec": original}
    return report


def propose_runtime_repair(spec, runtime_report, *, seed, asset_registry=None):
    """One bounded proposal; caller must rebuild and probe a fresh environment."""
    issues = runtime_report.get("issues", [])
    if not issues or any(not i.get("repairable") for i in issues):
        return None, []
    current = copy.deepcopy(spec)
    ids = {oid for issue in issues for oid in issue["objects"] if not pose_locked(current, oid)}
    if not ids:
        return None, []
    rng = random.Random(seed)
    wx, wy, _ = current["scene"]["workspace"]["size"]
    table = current["scene"]["workspace"]["table_height"]
    for _ in range(64):
        patches = []
        for index, obj in enumerate(current["scene"]["objects"]):
            if obj["id"] not in ids:
                continue
            before = list(spec["scene"]["objects"][index]["pose"])
            sx, sy, sz = obj["size"]
            obj["pose"] = [rng.uniform(-wx/2+sx/2, wx/2-sx/2), rng.uniform(-wy/2+sy/2, wy/2-sy/2), table+sz/2]
            patches.append({"op": "replace", "path": f"/scene/objects/{index}/pose", "object_id": obj["id"],
                            "before": before, "after": list(obj["pose"]), "reason": "resample after failed settling probe"})
        if validate_scene_spec(current, asset_registry=asset_registry)["ok"]:
            return current, patches
    return None, []
