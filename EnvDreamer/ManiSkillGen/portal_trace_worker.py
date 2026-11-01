"""Subprocess entry point for the PORTAL-Trace robosuite adapter.

The main process launches this file with a request and response JSON path. It
is kept separate so a MuJoCo/OpenMP failure cannot take down the ManiSkill3
FastAPI process.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def inactive_nut_dofs(env, task_id):
    """Exclude only nuts explicitly disabled by the benchmark's single-object mode.

    robosuite.clear_objects teleports these to (10,10,10), where they free-fall.
    Their velocity is not evidence that the active task scene is unstable.
    Keep all state finite checks; report the exclusion instead of moving bodies.
    """
    excluded, names = [], []
    if task_id in ("NutAssemblySquareTight-v0", "NutAssemblyRoundTight-v0") and env.single_object_mode == 2:
        for nut in env.nuts:
            if nut.name != env.obj_to_use:
                names.append(nut.name)
                for joint in nut.joints:
                    address = env.sim.model.get_joint_qvel_addr(joint)
                    excluded.extend(range(*address) if isinstance(address, tuple) else [address])
    return excluded, names


def main() -> int:
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    root = Path(request["portal_trace_root"])
    sys.path.insert(0, str(root))
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    def write(value: dict) -> None:
        response_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        import numpy as np
        import robosuite as suite
        from robosuite.controllers import load_composite_controller_config
        from portal_trace.registry.tasks import task as registry_task

        task_id = request["task_id"]
        if task_id in ("NutAssemblySquareTight-v0", "NutAssemblyRoundTight-v0"):
            from portal_trace.registry.tight import ensure_registered

            ensure_registered(registry_task(task_id))
        task_spec = registry_task(task_id)
        robot = request["robot"]
        kwargs = dict(task_spec.env_kwargs)
        kwargs.pop("horizon", None)
        render = bool(request.get("render", True))
        resolution = request.get("resolution", [640, 480])
        np.random.seed(int(request.get("seed", 0)))
        env = suite.make(
            task_spec.env_name,
            robots=robot,
            controller_configs=load_composite_controller_config(controller=None, robot=robot),
            has_renderer=False,
            has_offscreen_renderer=render,
            use_camera_obs=render,
            camera_names=["agentview"] if render else None,
            control_freq=20,
            horizon=400,
            ignore_done=True,
            seed=int(request.get("seed", 0)),
            **kwargs,
        )
        try:
            env.reset()
            success_at_reset = bool(env._check_success()) if hasattr(env, "_check_success") else None
            probe = {"ok": True, "status": "passed", "steps": 0, "issues": [],
                     "checks": {"finite_state": None, "physics_stability": None, "contact_penetration": None,
                                "runtime_bounds": None, "collision_free_ik": None, "task_rollout": None},
                     "scope": "20 zero-action initialization steps; registered benchmark only; no layout repair or task policy",
                     "limits": {"max_final_generalized_speed": .5, "deep_contact_m": .01}}
            max_speed = 0.0
            excluded, inactive = inactive_nut_dofs(env, task_id)
            active_dofs = np.ones(len(env.sim.data.qvel), dtype=bool)
            active_dofs[excluded] = False
            probe["inactive_objects_excluded_from_speed"] = inactive
            for step in range(20):
                env.step(np.zeros(env.action_dim))
                probe["steps"] = step + 1
                if not np.isfinite(env.sim.data.qpos).all() or not np.isfinite(env.sim.data.qvel).all():
                    probe["issues"].append({"code": "nonfinite_runtime", "message": "PORTAL simulator state is non-finite", "severity": "error", "repairable": False, "objects": [], "path": "runtime"})
                    break
                speed = float(np.max(np.abs(env.sim.data.qvel[active_dofs])))
                if step >= 15:
                    max_speed = max(max_speed, speed)
            if max_speed > .5:
                probe["issues"].append({"code": "runtime_unstable", "message": "PORTAL generalized velocity exceeds 0.5 in the final 5 samples", "severity": "error", "repairable": False, "objects": [], "path": "runtime"})
            # Some PORTAL robot XMLs place the two gripper collision shells in
            # a tiny resting overlap.  This is a robot self-collision baseline,
            # not penetration of the task objects or table, so keep it in the
            # report but exclude it from scene-contact validation.  Object and
            # table contacts remain strict.
            ignored_self_contacts = 0
            contact_depths = []
            for contact_index in range(env.sim.data.ncon):
                contact = env.sim.data.contact[contact_index]
                geom_a = env.sim.model.geom_id2name(contact.geom1) or ""
                geom_b = env.sim.model.geom_id2name(contact.geom2) or ""
                if "gripper" in geom_a.lower() and "gripper" in geom_b.lower():
                    ignored_self_contacts += 1
                    continue
                contact_depths.append(max(0.0, -float(contact.dist)))
            depth = max(contact_depths or [0.])
            probe["ignored_robot_self_contacts"] = ignored_self_contacts
            if depth > .01:
                probe["issues"].append({"code": "runtime_penetration", "message": "PORTAL reset contact penetration exceeds 1 cm", "severity": "error", "repairable": False, "objects": [], "path": "runtime"})
            codes = {issue["code"] for issue in probe["issues"]}
            probe["checks"].update(finite_state="nonfinite_runtime" not in codes,
                                   physics_stability=not codes and probe["steps"] == 20,
                                   contact_penetration="runtime_penetration" not in codes)
            probe.update(ok=not codes, status="failed" if codes else "passed", max_final_speed=max_speed,
                         final_max_penetration=depth,
                         state={"qpos": env.sim.data.qpos.tolist(), "qvel": env.sim.data.qvel.tolist()})
            # Never serialize NaN/Inf into the public JSON report.
            if "nonfinite_runtime" in codes:
                probe["state"] = None
            frame_path = None
            if render and probe["ok"]:
                from PIL import Image

                frame = env.sim.render(
                    camera_name="agentview", width=int(resolution[0]), height=int(resolution[1])
                )
                frame_path = request["frame_path"]
                Image.fromarray(frame[::-1]).save(frame_path, format="PNG")
            write(
                {
                    "status": "ok",
                    "runtime_backend": "portal_trace_robosuite",
                    "robosuite_version": getattr(suite, "__version__", "unknown"),
                    "task_id": task_id,
                    "env_name": task_spec.env_name,
                    "robot": robot,
                    "frame_path": frame_path,
                    "success_at_reset": success_at_reset,
                    "validation_probe": probe,
                    "checks": {"environment_reset": True, "preview_render": (frame_path is not None) if render else None, **probe["checks"]},
                }
            )
        finally:
            env.close()
    except Exception as exc:
        write({"status": "error", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
