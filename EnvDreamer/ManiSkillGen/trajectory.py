"""Bounded, physically executed Panda tabletop skills with success verification."""
from __future__ import annotations

import copy
import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from .kinematics import URDFKinematics, pose_matrix
from .runtime_validation import array


class TrajectoryError(RuntimeError):
    pass


def numpy_tree(value):
    if isinstance(value, dict):
        return {key: numpy_tree(child) for key, child in value.items()}
    return array(value).astype(np.float32).copy()


def read_state(env):
    return {"engine": numpy_tree(env.get_state_dict()),
            "grasped": bool(array(env.agent.is_grasping(env._object_handles[env.scene_spec['task']['object_id']])).all()),
            "qpos": array(env.agent.robot.qpos)[0].copy(), "qvel": array(env.agent.robot.qvel)[0].copy(),
            "tcp_pose": array(env.agent.tcp.pose.raw_pose)[0].copy(),
            "robot_base_pose": array(env.agent.robot.pose.raw_pose)[0].copy(),
            "objects": {key: np.r_[array(obj.pose.raw_pose)[0], array(obj.linear_velocity)[0], array(obj.angular_velocity)[0]]
                        for key, obj in env._object_handles.items()},
            "success": bool(array(env.evaluate()["success"]).all())}


class TrajectoryGenerator:
    """Cartesian waypoint skills, numerical IK, and bounded joint interpolation.

    This is not a general collision-free motion planner. Unsupported geometry,
    failed grasps, IK failures and unsolved goals are explicit failures.
    """
    def __init__(self, max_steps=650):
        self.max_steps = max_steps

    def generate(self, instance, *, on_progress=None):
        if instance["manifest"]["runtime_backend"] != "maniskill3" or instance.get("env") is None:
            raise TrajectoryError("TrajectoryGen currently requires a live ManiSkill Panda scene")
        env = instance["env"].unwrapped
        spec = instance["scene_spec"]
        task = spec["task"]
        template = {"tabletop_pick_place": "pick_and_place", "tabletop_push": "push", "tabletop_stack": "stack"}.get(task["template"], task["template"])
        if template not in {"pick_and_place", "stack", "push"}:
            raise TrajectoryError("no trajectory skill for this task")
        definitions = {o["id"]: o for o in spec["scene"]["objects"]}
        obj_def, target_def = definitions[task["object_id"]], definitions[task["target_id"]]
        if obj_def["shape"] not in ({"box", "cylinder"} if template == "push" else {"box"}) or max(obj_def["size"][:2]) > .065:
            raise TrajectoryError("physical skills require XY dimensions <= 6.5 cm; pick/stack require a box, push supports boxes and upright cylinders")
        if template == "stack" and target_def["shape"] != "box":
            raise TrajectoryError("stack skill requires a flat box support")
        env.agent.set_control_mode("pd_joint_pos")
        env.agent.controller.reset()
        kinematics = URDFKinematics(env.agent.urdf_path, env.agent.arm_joint_names, env.agent.ee_link_name)
        base_inverse = np.linalg.inv(pose_matrix(array(env.agent.robot.pose.raw_pose)[0]))
        measured = pose_matrix(array(env.agent.tcp.pose.raw_pose)[0])
        calculated = np.linalg.inv(base_inverse) @ kinematics.forward(array(env.agent.robot.qpos)[0, :7])
        fk_error = float(np.max(np.abs(measured-calculated)))
        if fk_error > 1e-4:
            raise TrajectoryError(f"URDF FK does not match engine TCP: {fk_error}")
        states, actions, stages = [read_state(env)], [], []
        if states[0]["success"]:
            raise TrajectoryError("task is already solved at initialization; no nontrivial successful demonstration")
        objects = env._object_handles
        moving = objects[task["object_id"]]
        goal = objects[task["target_id"]]
        phases = []
        actor_names = {entity.name for obj in objects.values() for entity in obj._objs}
        max_penetration = 0.

        def step(qpos, gripper, phase):
            nonlocal max_penetration
            if len(actions) >= self.max_steps:
                raise TrajectoryError("trajectory control-step budget exceeded")
            action = np.r_[qpos, gripper].astype(np.float32)
            if not np.isfinite(action).all():
                raise TrajectoryError("non-finite action")
            env.step({"control_mode": "pd_joint_pos", "action": action[None]})
            state = read_state(env)
            if not np.isfinite(state["qpos"]).all():
                raise TrajectoryError("non-finite simulator state")
            if template == "push":
                moving_state = state["objects"][task["object_id"]]
                initial_z = states[0]["objects"][task["object_id"]][2]
                if state["grasped"] or moving_state[2] > initial_z + .008:
                    raise TrajectoryError("push must not grasp or lift the object off the table")
            for oid, obj_state in state["objects"].items():
                if not np.isfinite(obj_state).all():
                    raise TrajectoryError(f"non-finite object state: {oid}")
                if obj_state[2] < spec["scene"]["workspace"]["table_height"]-.02:
                    raise TrajectoryError(f"object fell through/off table: {oid}")
                if oid != task["object_id"] and np.linalg.norm(obj_state[:3]-states[0]["objects"][oid][:3]) > .015:
                    raise TrajectoryError(f"non-task object displaced more than 1.5 cm: {oid}")
            for contact in env.scene.get_contacts():
                if any(body.entity.name in actor_names for body in contact.bodies):
                    max_penetration=max(max_penetration,max([max(0.,-p.separation) for p in contact.points] or [0.]))
            if max_penetration > .01:
                raise TrajectoryError("trajectory caused object contact penetration >1 cm")
            actions.append(action)
            stages.append(phase)
            states.append(state)

        def hold(gripper, phase, count=12):
            q = array(env.agent.robot.qpos)[0, :7]
            for _ in range(count):
                step(q, gripper, phase)

        def move(position, gripper, phase, rotation=None):
            if on_progress:
                on_progress(phase, len(actions))
            phases.append({"phase": phase, "start_step": len(actions), "target_position": list(position)})
            current = pose_matrix(array(env.agent.tcp.pose.raw_pose)[0])
            desired_rotation = np.diag([1., -1., -1.]) if rotation is None else rotation
            count = max(2, int(np.ceil(np.linalg.norm(np.asarray(position)-current[:3,3])/.012)))
            rotations = Slerp([0, 1], Rotation.from_matrix(np.stack([current[:3,:3], desired_rotation])))
            q = array(env.agent.robot.qpos)[0, :7]
            for t in np.linspace(0, 1, count+1)[1:]:
                pose = np.eye(4)
                pose[:3,3] = (1-t)*current[:3,3]+t*np.asarray(position)
                pose[:3,:3] = rotations(t).as_matrix()
                try:
                    goal_q = kinematics.solve(base_inverse @ pose, q)
                except ValueError as exc:
                    raise TrajectoryError(f"{phase}: {exc}") from exc
                n = max(2, int(np.ceil(np.max(np.abs(goal_q-q))/.035)))
                for ratio in np.linspace(0, 1, n+1)[1:]:
                    step((1-ratio)*q+ratio*goal_q, gripper, phase)
                q = goal_q
            for _ in range(5):
                step(q, gripper, phase)
            error = np.linalg.norm(array(env.agent.tcp.pose.p)[0]-position)
            if error > .025:
                raise TrajectoryError(f"{phase}: executed TCP missed waypoint by {error:.3f} m")

        table = spec["scene"]["workspace"]["table_height"]
        object_pos = array(moving.pose.p)[0]
        target_pos = array(goal.pose.p)[0]
        safe_z = max(table+.20, object_pos[2]+.15, target_pos[2]+target_def["size"][2]/2+.15)
        if template in {"pick_and_place", "stack"}:
            # TCP is 25 mm below the top face at grasp, as in the stock Panda skills.
            grasp = object_pos.copy()
            grasp[2] += obj_def["size"][2]/2 - .025
            move([*grasp[:2], safe_z], 1, "approach")
            move(grasp, 1, "descend")
            hold(-1, "grasp", 18)
            if not bool(array(env.agent.is_grasping(moving)).all()):
                raise TrajectoryError("physical grasp failed; no attachment/teleport fallback is used")
            move([*grasp[:2], safe_z], -1, "lift")
            if float(array(moving.pose.p)[0,2]) < table+.075:
                raise TrajectoryError("object did not lift with the gripper")
            move([*target_pos[:2], safe_z], -1, "transport")
            offset = array(env.agent.tcp.pose.p)[0] - array(moving.pose.p)[0]
            desired = target_pos.copy()
            desired[2] = (target_pos[2]+target_def["size"][2]/2 if template == "stack" else table) + obj_def["size"][2]/2 + .002
            release_tcp = desired + offset
            move(release_tcp, -1, "place")
            hold(1, "release", 15)
            move([*release_tcp[:2], safe_z], 1, "retreat")
        else:
            delta = target_pos[:2]-object_pos[:2]
            length = np.linalg.norm(delta)
            if length < .05:
                raise TrajectoryError("push displacement must be at least 5 cm")
            direction = delta/length
            # Closed fingers push with their side; grasping/lifting is never used.
            extent = obj_def["size"][0]/2 if obj_def["shape"] == "cylinder" else .5*np.dot(np.abs(direction), obj_def["size"][:2])
            start = object_pos.copy()
            start[:2] -= direction*(extent+.045)
            start[2] = table+obj_def["size"][2]*(.35 if obj_def["shape"] == "cylinder" else .65)
            rot = np.column_stack(([direction[0],direction[1],0], [direction[1],-direction[0],0], [0,0,-1]))
            move([*start[:2], safe_z], -1, "push_approach", rot)
            move(start, -1, "push_descend", rot)
            end = start.copy()
            end[:2] += direction*(length+.018)
            move(end, -1, "push", rot)
            for _ in range(3):
                residual = target_pos[:2]-array(moving.pose.p)[0,:2]
                if np.linalg.norm(residual) < .008 or np.dot(residual,direction) <= 0:
                    break
                end[:2] += residual*.8
                move(end, -1, "push_adjust", rot)
            back = end.copy(); back[:2] -= direction*.06
            move(back, -1, "push_release", rot)
            move([*back[:2],safe_z], 1, "retreat", rot)
        hold_frames = max(10, int(np.ceil(float(spec.get('stability_seconds',.5))/float(env.control_timestep))))
        hold(1, "settle", max(20,hold_frames+10))
        final_success = all(state["success"] for state in states[-hold_frames:])
        if not final_success:
            raise TrajectoryError(f"task success predicate did not hold for final {hold_frames} frames; object={array(moving.pose.p)[0].tolist()}")
        return {"schema_version": "trajectory_v1", "scene_spec": copy.deepcopy(spec),
                "states": states, "actions": np.stack(actions), "stages": stages, "phases": phases,
                "success": True, "control_mode": "pd_joint_pos", "control_dt": float(env.control_timestep),
                "fk_max_error": fk_error, "success_hold_frames": hold_frames,
                "validation": {"task_rollout": True, "final_success_hold_frames": hold_frames, "max_object_contact_penetration_m": max_penetration,
                               "non_task_object_displacement_limit_m": .015, "collision_free_planning": None, "robot_self_collision": None},
                "method": "URDF numerical IK + physical waypoint skills; no collision-free planning guarantee"}
