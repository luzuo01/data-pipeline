"""Short passive settling probes, deliberately separate from task solvability."""
from __future__ import annotations

import numpy as np


def array(value):
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    return np.asarray(value, dtype=float)


def probe_maniskill(env, spec, steps=20):
    report = {"status": "not_tested", "ok": None, "steps": 0, "issues": [], "objects": {},
              "checks": {"finite_state": None, "physics_stability": None, "runtime_bounds": None,
                         "contact_penetration": None, "collision_free_ik": None, "task_rollout": None},
              "limits": {"linear_speed_m_s": .03, "angular_speed_rad_s": .3, "displacement_m": .05,
                         "penetration_m": .005, "settling_control_steps": steps, "robot_joint_speed": .5},
              "scope": "passive settling with held robot control targets; no IK or task policy"}
    if env is None:
        report["reason"] = "simulator unavailable; software preview is not a physical validation"
        return report
    target = getattr(env, "unwrapped", env)

    def fail(code, message, ids=(), repairable=False):
        report["issues"].append({"code": code, "severity": "error", "message": message,
                                 "objects": list(ids), "path": "runtime", "repairable": repairable})
    try:
        handles = target._object_handles
        entity_names = {entity.name: oid for oid, handle in handles.items() for entity in getattr(handle, "_objs", [])}
        objects = spec["scene"]["objects"]
        table = spec["scene"]["workspace"]["table_height"]
        workspace = spec["scene"]["workspace"]["size"]
        initial = {o["id"]: array(handles[o["id"]].pose.p).copy() for o in objects}
        # PD controller.reset() updates its cached target but does not necessarily
        # send it to PhysX until the first action. Explicitly hold the reset pose.
        controller = getattr(target.agent, "controller", None)
        controllers = getattr(controller, "controllers", {"robot": controller})
        for control in controllers.values():
            if callable(getattr(control, "set_drive_targets", None)):
                control.set_drive_targets(control.qpos)
        robot_speeds = []
        max_motion = {o["id"]: 0.0 for o in objects}
        speeds = {o["id"]: [] for o in objects}
        contacts_available = True
        for step in range(steps):
            env.step(None)  # ManiSkill holds the existing controller target for None.
            report["steps"] = step + 1
            if not np.isfinite(array(target.agent.robot.qpos)).all() or not np.isfinite(array(target.agent.robot.qvel)).all():
                fail("nonfinite_runtime", "robot state is non-finite")
                break
            robot_speeds.append(float(np.abs(array(target.agent.robot.qvel)).max()))
            for obj in objects:
                oid, handle = obj["id"], handles[obj["id"]]
                pos, quat = array(handle.pose.p), array(handle.pose.q)
                velocity, angular = array(handle.linear_velocity), array(handle.angular_velocity)
                if not all(np.isfinite(x).all() for x in (pos, quat, velocity, angular)):
                    fail("nonfinite_runtime", f"'{oid}' has non-finite state", [oid])
                    continue
                motion = float(np.linalg.norm(pos-initial[oid], axis=-1).max())
                max_motion[oid] = max(max_motion[oid], motion)
                speed = float(np.linalg.norm(velocity, axis=-1).max())
                spin = float(np.linalg.norm(angular, axis=-1).max())
                speeds[oid].append((speed, spin))
                # Bounding sphere under-approximations would miss tilted boxes:
                # transform the local half extents by the absolute rotation matrix.
                w, x, y, z = quat.reshape(-1, 4)[0]
                rotation = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
                dims = np.array(obj["size"])
                if obj["shape"] == "cylinder":
                    dims = dims[[2, 1, 0]]  # SAPIEN cylinder's local axis is X.
                half = dims/2 if obj["shape"] == "sphere" else np.abs(rotation) @ (dims/2)
                p = pos.reshape(-1, 3)[0]
                if p[2]-half[2] < table-.005 or any(abs(p[i])+half[i] > workspace[i]/2+.005 for i in (0, 1)) or p[2]+half[2] > table+workspace[2]+.005:
                    fail("runtime_bounds", f"'{oid}' left the workspace or penetrated the table", [oid], True)
                report["objects"][oid] = {"position": p.tolist(), "quaternion": quat.reshape(-1,4)[0].tolist(),
                                           "linear_speed": speed, "angular_speed": spin, "max_displacement": max_motion[oid]}
            if report["issues"]:
                break
            try:
                for contact in target.scene.get_contacts():
                    names = [body.entity.name for body in contact.bodies]
                    involved = [entity_names.get(name, name) for name in names if name in entity_names or name in handles]
                    if involved and any(point.separation < -.005 for point in contact.points):
                        fail("runtime_penetration", "deep contact between " + " and ".join(names), involved, True)
            except (AttributeError, NotImplementedError):
                contacts_available = False
            if report["issues"]:
                break
        if not report["issues"]:
            if max(robot_speeds[-5:], default=0) > .5:
                fail("robot_unstable", "robot failed to hold its initialization pose")
            for obj in objects:
                oid = obj["id"]
                if obj["dynamic"] and (max_motion[oid] > .05 or any(v>.03 or w>.3 for v,w in speeds[oid][-5:])):
                    fail("runtime_unstable", f"'{oid}' moved excessively or did not settle in the final 5 samples", [oid], True)
        codes = {i["code"] for i in report["issues"]}
        report["checks"].update(finite_state="nonfinite_runtime" not in codes,
                                runtime_bounds="runtime_bounds" not in codes,
                                physics_stability=not codes and report["steps"] == steps,
                                contact_penetration=("runtime_penetration" not in codes) if contacts_available else None)
        report["checks"]["robot_self_collision"] = None
        report["robot_final_max_joint_speed"] = max(robot_speeds[-5:], default=0)
        if "nonfinite_runtime" not in codes:
            report["robot_state"] = {"qpos": array(target.agent.robot.qpos).tolist(), "qvel": array(target.agent.robot.qvel).tolist()}
        report["ok"] = not report["issues"]
        report["status"] = "passed" if report["ok"] else "failed"
    except Exception as exc:
        fail("runtime_probe_error", f"settling probe could not complete: {type(exc).__name__}: {exc}")
        report.update(ok=False, status="failed")
    return report
