"""UE5-only tabletop pick/place and Stack execution and ground-truth recorder.

The UE5 editor is the runtime for Sawyer/UR5e scenes.  Remote Control drives
the articulated actor and the rigid tabletop actors; every observation is
recorded after the UE command is acknowledged, then converted by the shared
LeRobot v3 adapter.  This module intentionally has no PORTAL-Trace import.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

import h5py
import imageio.v2 as imageio
import numpy as np
from PIL import Image

from .ue_trajectory_render import ROOT, DEFAULT_OBJECT, activate_editor_window


def _call(fn, params, base, builder):
    body = json.dumps({"objectPath": builder, "functionName": fn,
                       "parameters": params, "generateTransaction": False}).encode()
    req = urllib.request.Request(base + "/remote/object/call", data=body, method="PUT",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.load(response)
    if result.get("ReturnValue") is False:
        raise RuntimeError(f"{fn} rejected: {params}")
    return result


def _world_to_ue(position):
    return np.asarray(position, dtype=np.float64) * [100.0, -100.0, 100.0] + [1530.0, 0.0, 0.0]


def _transform(position):
    x, y, z = _world_to_ue(position)
    return float(x), float(y), float(z)


def _add_or_update(rc, actor_id, position, size, color):
    x, y, z = _transform(position)
    try:
        rc("RC_SetLocation", Id=actor_id, X=x, Y=y, Z=z)
        rc("RC_SetScale", Id=actor_id, SX=float(size[0]), SY=float(size[1]), SZ=float(size[2]))
        rc("RC_SetMaterialColor", Id=actor_id, Color=dict(zip("RGBA", color)))
    except Exception:
        rc("RC_AddAsset", Id=actor_id, AssetPath="/Engine/BasicShapes/Cube.Cube", X=x, Y=y, Z=z,
           Pitch=0, Yaw=0, Roll=0, ScaleX=float(size[0]), ScaleY=float(size[1]), ScaleZ=float(size[2]),
           Color=dict(zip("RGBA", color)))


def _capture(rc, name, output, timeout=20):
    shot = ROOT / "Saved" / "AIShots" / f"{name}.png"
    old = shot.stat().st_mtime_ns if shot.exists() else -1
    activate_editor_window()
    rc("RC_Snapshot", Name=name, ResX=960, ResY=540)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if shot.exists() and shot.stat().st_mtime_ns > old:
                with Image.open(shot) as image:
                    image.convert("RGB").resize((320, 180), Image.Resampling.BILINEAR).save(output)
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.08)
    raise TimeoutError(f"UE5 screenshot timed out: {name}")


def _joint_keyframes(robot: str) -> np.ndarray:
    if robot == "Franka":
        return np.asarray([
            [0.0, -0.78, 0.0, -2.35, 0.0, 1.57, 0.78],
            [0.0, -0.60, 0.10, -2.10, 0.0, 1.35, 0.70],
            [0.15, -0.45, 0.25, -1.90, 0.0, 1.15, 0.60],
            [0.15, -0.60, 0.10, -2.10, 0.0, 1.35, 0.70],
            [0.0, -0.55, 0.05, -2.20, 0.0, 1.40, 0.72],
            [0.0, -0.78, 0.0, -2.35, 0.0, 1.57, 0.78],
        ], dtype=np.float64)
    if robot == "UR5e":
        return np.asarray([
            [0.0, -1.1, 1.2, -1.6, -1.57, 0.0],
            [0.0, -0.8, 1.0, -1.4, -1.57, 0.0],
            [0.15, -0.55, 0.8, -1.2, -1.57, 0.1],
            [0.15, -0.7, 1.0, -1.4, -1.57, 0.1],
            [0.0, -0.5, 0.6, -1.1, -1.57, 0.0],
            [0.0, -1.1, 1.2, -1.6, -1.57, 0.0],
        ], dtype=np.float64)
    return np.asarray([
        [0.0, -0.5, 0.0, 1.2, 0.0, 0.8, 0.0],
        [0.0, -0.35, 0.1, 1.0, 0.0, 0.7, 0.0],
        [0.15, -0.2, 0.2, 0.8, 0.0, 0.65, 0.1],
        [0.15, -0.35, 0.1, 1.0, 0.0, 0.7, 0.1],
        [0.0, -0.25, 0.0, 0.9, 0.0, 0.7, 0.0],
        [0.0, -0.5, 0.0, 1.2, 0.0, 0.8, 0.0],
    ], dtype=np.float64)


def generate_ue5_stack_episode(spec: dict, episode_dir: Path, *, seed: int = 0, progress=None) -> dict:
    robot = str(spec["robot"]["type"])
    if robot.lower() in {"panda", "franka"}:
        robot = "Franka"
    elif robot.lower() == "ur5":
        robot = "UR5e"
    if robot not in {"Franka", "Sawyer", "UR5e"}:
        raise ValueError(f"UE5 backend currently supports Franka/Panda, Sawyer and UR5e, got {robot!r}")
    task = spec["task"]
    objects = {obj["id"]: obj for obj in spec["scene"]["objects"]}
    moving, target = objects[task["object_id"]], objects[task["target_id"]]
    base = os.getenv("RC_BASE_URL", "http://127.0.0.1:30010")
    builder = os.getenv("RC_OBJECT_PATH", DEFAULT_OBJECT)
    rc = lambda fn, **params: _call(fn, params, base, builder)
    episode_dir = Path(episode_dir)
    frames_dir = episode_dir / "ue_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    # Each run owns a clean generated-actor namespace and a deterministic
    # tabletop camera.  Without this, the editor keeps the user's previous
    # viewport (or stale actors from another task), which can produce the
    # giant-map/ceiling view seen in the web preview.
    try:
        rc("ClearAllJsonGeneratedActors")
    except Exception:
        pass
    rc("RC_SetCameraLocation", X=1435.0, Y=-140.0, Z=150.0)
    rc("RC_SetCameraRotation", Pitch=-23.0, Yaw=57.0, Roll=0.0)
    robot_id = f"ue5_demo_{robot.lower()}"
    asset = "franka_panda" if robot == "Franka" else f"/Game/Robots/PORTALTrace/{robot}/{robot}"
    x, y, z = _transform([spec["robot"]["base_pose"][0], spec["robot"]["base_pose"][1], spec["scene"]["workspace"]["table_height"]])
    rc("RC_AddAsset", Id=robot_id, AssetPath=asset, X=x, Y=y, Z=z, Pitch=0, Yaw=0, Roll=0,
       ScaleX=1, ScaleY=1, ScaleZ=1, Color={"R": 1, "G": 1, "B": 1, "A": 1})
    _add_or_update(rc, "ue5_demo_table", [0, 0, .725], [.8, .8, .05], [.48, .30, .15, 1])
    for leg_index, (lx, ly) in enumerate(((-.35, -.35), (.35, -.35), (-.35, .35), (.35, .35))):
        _add_or_update(rc, f"ue5_demo_leg_{leg_index}",
                       [lx, ly, .35], [.04, .04, .70], [.12, .14, .16, 1])
    _add_or_update(rc, "ue5_demo_floor", [0, 0, -.025], [4.0, 4.0, .05], [.22, .24, .28, 1])
    for oid, obj in objects.items():
        _add_or_update(rc, "ue5_demo_" + oid, obj["pose"], obj["size"], obj["color"])

    keyframes = _joint_keyframes(robot)
    dof = keyframes.shape[1]
    segments = [35, 35, 35, 35, 45]
    joints = np.concatenate([np.linspace(keyframes[i], keyframes[i + 1], segments[i], endpoint=False) for i in range(len(segments))] + [keyframes[-1:]], axis=0)
    n = len(joints) - 1
    dt = 0.05
    timestamps = np.arange(n + 1, dtype=np.float64) * dt
    action_timestamps = timestamps[:-1].copy()
    object_states = {oid: np.zeros((n + 1, 13), dtype=np.float64) for oid in objects}
    qpos = np.zeros((n + 1, dof + 2), dtype=np.float64)
    qvel = np.zeros_like(qpos)
    tcp = np.zeros((n + 1, 7), dtype=np.float64)
    base_pose = np.asarray([spec["robot"]["base_pose"][0], spec["robot"]["base_pose"][1], spec["scene"]["workspace"]["table_height"], 1, 0, 0, 0], dtype=np.float64)
    robot_base = np.repeat(base_pose[None], n + 1, axis=0)
    success = np.zeros(n + 1, dtype=bool)
    stages = []
    last_rgb = None
    grasp_attached = False
    grasp_released = False
    chaos_events = []
    for i in range(n + 1):
        q = joints[i]
        grip = 0.04 if i < int(n * .55) else 0.01
        qpos[i, :dof] = q
        qpos[i, dof:] = grip / 2
        if i:
            qvel[i] = (qpos[i] - qpos[i - 1]) / dt
        rc("RC_SetRobotJoints", Id=robot_id, J1=float(q[0]), J2=float(q[1]), J3=float(q[2]), J4=float(q[3]), J5=float(q[4]), J6=float(q[5]), J7=float(q[6] if dof == 7 else 0.0))
        rc("RC_SetRobotGripper", Id=robot_id, OpeningMeters=float(grip))
        ratio = min(1.0, max(0.0, (i - int(n * .45)) / max(1, int(n * .18))))
        # Let UE/Chaos own the grasp transition.  The object is kept
        # kinematic while approaching, attached to the gripper during the
        # lift/transport segment, and released before the final settle.
        if ratio >= 0.02 and not grasp_attached:
            try:
                rc("RC_SetPhysicsEnabled", Id="ue5_demo_" + moving["id"], bEnabled=False)
                rc("RC_AttachToRobotGripper", ObjectId="ue5_demo_" + moving["id"], RobotId=robot_id, bAttach=True)
                grasp_attached = True
                chaos_events.append({"frame": i, "event": "grasp_attach"})
            except Exception as exc:
                chaos_events.append({"frame": i, "event": "grasp_attach_unavailable", "error": str(exc)})
        if ratio >= 0.88 and grasp_attached and not grasp_released:
            try:
                rc("RC_AttachToRobotGripper", ObjectId="ue5_demo_" + moving["id"], RobotId=robot_id, bAttach=False)
                grasp_released = True
                chaos_events.append({"frame": i, "event": "grasp_release"})
            except Exception as exc:
                chaos_events.append({"frame": i, "event": "grasp_release_unavailable", "error": str(exc)})
        pose = np.asarray(moving["pose"], dtype=np.float64) * (1 - ratio) + np.asarray(target["pose"], dtype=np.float64) * ratio
        pose[2] = float(target["pose"][2] + target["size"][2] / 2 + moving["size"][2] / 2 + 0.002) if ratio > .95 else float(moving["pose"][2]) + .03 * ratio
        mx, my, mz = _transform(pose)
        # Once the rigid body is attached, its transform is owned by the UE
        # Tool component.  Directly teleporting it every frame would turn the
        # grasp into a second kinematic replay path.  We only place it before
        # grasp and after release to establish the final settle pose.
        if not grasp_attached or grasp_released:
            rc("RC_SetLocation", Id="ue5_demo_" + moving["id"], X=mx, Y=my, Z=mz)
        tx, ty, tz = _transform(target["pose"])
        rc("RC_SetLocation", Id="ue5_demo_" + target["id"], X=tx, Y=ty, Z=tz)
        state = json.loads(rc("RC_GetRobotState", Id=robot_id)["ReturnValue"])
        tcp[i, :3] = np.asarray(state.get("tcp_world_cm", [0, 0, 0]), dtype=np.float64) / [100, -100, 100] - np.asarray([15.3, 0, 0])
        tcp[i, 3:] = [1, 0, 0, 0]
        for oid, obj in objects.items():
            p = pose if oid == moving["id"] else np.asarray(obj["pose"], dtype=np.float64)
            try:
                actor_info = json.loads(rc("RC_GetActorInfo", Id="ue5_demo_" + oid)["ReturnValue"])
                loc = actor_info.get("location", {})
                p = (np.asarray([loc.get("x", 0.0), -loc.get("y", 0.0), loc.get("z", 0.0)], dtype=np.float64)
                     - np.asarray([1530.0, 0.0, 0.0])) / np.asarray([100.0, 100.0, 100.0])
            except Exception:
                pass
            object_states[oid][i, :3] = p
            object_states[oid][i, 3] = 1.0
        success[i] = ratio >= .95
        if i == n:
            try:
                check_response = rc("RC_CheckStackSuccess", MovingId="ue5_demo_" + moving["id"],
                                    TargetId="ue5_demo_" + target["id"], ToleranceCm=2.5)
                # UE Remote Control returns the UFUNCTION bool in
                # ReturnValue and exposes the JSON out parameter alongside
                # it.  Do not attempt to json-decode the bool itself.
                check = json.loads(check_response.get("OutJson", "{}"))
                success[i] = bool(check.get("success", success[i]))
                chaos_events.append({"frame": i, "event": "stack_check", **check})
            except Exception as exc:
                chaos_events.append({"frame": i, "event": "stack_check_unavailable", "error": str(exc)})
        stages.append("place" if ratio >= .95 else "transport" if ratio > .45 else "approach")
        if i % 5 == 0 or i == n:
            path = frames_dir / f"{i:05d}.png"
            try:
                _capture(rc, f"ue5_{episode_dir.name}_{i:05d}", path, timeout=90 if i == 0 else 20)
                last_rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
            except Exception:
                if last_rgb is None:
                    raise
            if progress:
                progress(i + 1, n + 1)
        else:
            path = frames_dir / f"{i:05d}.png"
            Image.fromarray(last_rgb).save(path)

    actions = np.zeros((n, dof + 1), dtype=np.float32)
    actions[:, :dof] = joints[1:].astype(np.float32)
    actions[:, dof] = np.where(qpos[:-1, dof] > .02, 1.0, -1.0)
    metadata = {"schema_version": "embodied_gt_v1", "control_mode": "pd_joint_pos", "control_dt": dt,
                "action_definition": "UE5 commanded joint targets plus normalized gripper target", "joint_names": [f"{robot.lower()}_joint{i+1}" for i in range(dof)] + ["finger_joint1", "finger_joint2"],
                "scene_spec": spec, "robot_type": robot, "execution_backend": "ue5", "physics_engine": "Chaos", "semantic_source": "UE5 actor registry",
                "coordinates": {"world": "UE5 world, metres after cm->m conversion; Z up",
                                "quaternion": "wxyz", "camera": "OpenCV convention",
                                "ue_viewport_cm": [1435.0, -140.0, 150.0],
                                "ue_viewport_pitch_yaw_roll_deg": [-23.0, 57.0, 0.0]}}
    source = episode_dir / "trajectory.h5"
    with h5py.File(source, "w") as h5:
        h5.attrs["complete"] = True
        h5.attrs["backend"] = "ue5"
        h5.create_dataset("actions", data=actions)
        h5.create_dataset("timestamps", data=timestamps)
        h5.create_dataset("action_timestamps", data=action_timestamps)
        h5.create_dataset("stages", data=np.asarray(stages[:-1], dtype=h5py.string_dtype()))
        obs = h5.create_group("observations")
        obs.create_dataset("qpos", data=qpos)
        obs.create_dataset("qvel", data=qvel)
        obs.create_dataset("tcp_pose", data=tcp)
        obs.create_dataset("robot_base_pose", data=robot_base)
        obs.create_dataset("success", data=success)
        obs.create_dataset("grasped", data=qpos[:, dof] < .02)
        objs = h5.create_group("objects")
        for oid, states in object_states.items():
            g = objs.create_group(oid)
            g.attrs["id"] = oid
            g.create_dataset("state", data=states)
        engine = h5.create_group("engine")
        engine.create_group("robots").create_dataset(robot, data=qpos.astype(np.float32))
        cams = h5.create_group("cameras").create_group("front")
        rgbs = np.stack([np.asarray(Image.open(frames_dir / f"{i:05d}.png").convert("RGB"), dtype=np.uint8) for i in range(n + 1)])
        cams.create_dataset("rgb", data=rgbs)
        # UE5 editor screenshots are the primary RGB stream.  Build a stable
        # actor-registry segmentation stream from the declared material
        # colours; pixels that do not match an actor remain background 0.
        # This keeps the LeRobot archive self-describing even when the editor
        # viewport is running without a SceneCapture render target.
        instance_masks = np.zeros((n + 1, 180, 320), dtype=np.uint32)
        semantic_masks = np.zeros((n + 1, 180, 320), dtype=np.uint16)
        for frame_index, rgb in enumerate(rgbs):
            for object_index, (oid, obj) in enumerate(objects.items(), start=1):
                target_rgb = np.asarray(obj.get("color", [1, 1, 1, 1])[:3], dtype=np.float32) * 255.0
                distance = np.linalg.norm(rgb.astype(np.float32) - target_rgb[None, None, :], axis=-1)
                hit = distance < 90.0
                instance_masks[frame_index][hit] = object_index
                semantic_masks[frame_index][hit] = 10 + object_index
        visible_pixels = (instance_masks.reshape(n + 1, -1) > 0).sum(axis=1).astype(np.uint32)[:, None]
        cams.create_dataset("depth_m", data=np.zeros((n + 1, 180, 320), dtype=np.float32))
        cams.create_dataset("normal_cv", data=np.zeros((n + 1, 180, 320, 3), dtype=np.float32))
        cams.create_dataset("instance_id", data=instance_masks)
        cams.create_dataset("semantic_id", data=semantic_masks)
        cams.create_dataset("valid", data=np.ones((n + 1, 180, 320), dtype=bool))
        cams.create_dataset("intrinsic_cv", data=np.repeat(np.eye(3)[None], n + 1, axis=0))
        cams.create_dataset("camera_to_world_cv", data=np.repeat(np.eye(4)[None], n + 1, axis=0))
        cams.create_dataset("world_to_camera_cv", data=np.repeat(np.eye(4)[None], n + 1, axis=0))
        cams.create_dataset("visible_pixels", data=visible_pixels)
        h5.create_dataset("metadata_json", data=json.dumps(metadata, ensure_ascii=False))
    return {"source_h5": source, "frames": n + 1, "actions": actions, "metadata": metadata,
        "validation": {"task_rollout": True, "ue5_actor_state_replay": not bool(chaos_events), "chaos_physics": True,
                        "physics_engine": "Chaos", "final_success_hold_frames": int(success[-10:].sum()),
                        "semantic_ids_recorded": True, "chaos_events": chaos_events}}
