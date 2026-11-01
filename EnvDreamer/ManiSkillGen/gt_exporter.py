"""Synchronized engine ground truth export: T actions and T+1 observations."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
from urllib.parse import quote

import h5py
import numpy as np
from PIL import Image

from .kinematics import pose_matrix
from .runtime_validation import array


def flatten_tree(tree, prefix=()):
    for key, value in tree.items():
        if isinstance(value, dict):
            yield from flatten_tree(value, prefix+(key,))
        else:
            yield prefix+(key,), value


def torch_tree(tree, device):
    import torch
    return {key: torch_tree(value, device) if isinstance(value, dict) else torch.as_tensor(value, device=device)
            for key, value in tree.items()}


def decode_camera(raw, params):
    """Keep float32 metre depth; avoid ManiSkill's optional int16/mm conversion."""
    required = {"Color", "Position", "Normal", "Segmentation"}
    if not required <= set(raw):
        raise ValueError("GT export requires the default shader's raw Color/Position/Normal/Segmentation")
    rgb = np.clip(array(raw["Color"])[0,:,:,:3]*255, 0, 255).astype(np.uint8)
    gl = array(raw["Position"])[0,:,:,:3]
    seg = array(raw["Segmentation"])[0,:,:,1].astype(np.uint32)
    valid = (seg != 0) & np.isfinite(gl).all(-1) & (-gl[...,2] > 0)
    depth = np.where(valid, -gl[...,2], 0).astype(np.float32)
    normal = array(raw["Normal"])[0,:,:,:3] * np.array([1.,-1.,-1.])
    normal[~valid] = 0
    normal = normal.astype(np.float32)
    intrinsic = array(params["intrinsic_cv"])[0]
    extrinsic = np.eye(4)
    extrinsic[:3,:] = array(params["extrinsic_cv"])[0]
    cv_position = gl*np.array([1.,-1.,-1.])
    yy, xx = np.indices(depth.shape)
    projected = cv_position @ intrinsic.T
    projected_xy = projected[...,:2] / np.maximum(projected[...,2:3], 1e-12)
    error = np.linalg.norm(projected_xy-np.stack([xx+.5, yy+.5],axis=-1), axis=-1)
    max_error = float(error[valid].max()) if valid.any() else 0.
    if not valid.any() or max_error > .02:
        raise ValueError(f"camera projection consistency failed ({max_error:.5f} pixels)")
    lengths = np.linalg.norm(normal[valid], axis=-1)
    if not np.isfinite(normal).all() or np.max(np.abs(lengths-1)) > .02:
        raise ValueError("rendered surface normals are not finite unit vectors")
    return {"rgb": rgb, "depth_m": depth, "normal_cv": normal, "instance_id": seg,
            "valid": valid, "intrinsic_cv": intrinsic, "world_to_camera_cv": extrinsic,
            "camera_to_world_cv": np.linalg.inv(extrinsic)}, cv_position, max_error


def segmentation_catalog(env, spec):
    entries = {0: {"name": "background", "semantic_id": 0, "kind": "background"}}
    def add(entity, name, semantic, kind):
        for entity_id in array(entity.per_scene_id).reshape(-1):
            entries[int(entity_id)] = {"name": name, "semantic_id": semantic, "kind": kind}
    add(env.table, "table", 2, "table")
    for link in env.agent.robot.links:
        add(link, link.name, 1, "robot_link")
    for name, obj in env._object_handles.items():
        semantic = 3 if name == spec["task"]["object_id"] else 4 if name == spec["task"]["target_id"] else 5
        add(obj, name, semantic, "scene_object")
    return entries


def check_object_alignment(env, spec, seg, points_cv, camera_to_world):
    """Compare rendered visible object points against current engine geometry."""
    maximum = 0.
    for definition in spec["scene"]["objects"]:
        obj = env._object_handles[definition["id"]]
        mask = np.isin(seg, array(obj.per_scene_id).astype(int))
        points = points_cv[mask][::max(1,int(mask.sum())//2048)]
        if not len(points):
            continue  # Occlusion is recorded separately through pixel counts.
        transform = np.linalg.inv(pose_matrix(array(obj.pose.raw_pose)[0])) @ camera_to_world
        local = points @ transform[:3,:3].T + transform[:3,3]
        size = np.asarray(definition["size"])
        if definition["shape"] == "sphere":
            violation = np.abs(np.linalg.norm(local,axis=-1)-size[0]/2)
        elif definition["shape"] == "cylinder":
            violation = np.maximum(np.linalg.norm(local[:,1:],axis=-1)-size[0]/2, np.abs(local[:,0])-size[2]/2)
        else:
            violation = np.max(np.abs(local)-size/2,axis=-1)
        maximum = max(maximum, float(np.max(violation)))
    if maximum > .002:
        raise ValueError(f"render/pose alignment failed: object surface outside engine geometry by {maximum:.6f} m")
    return maximum


class GTExporter:
    def export(self, instance, trajectory, output_path, *, on_progress=None):
        env = instance["env"].unwrapped
        spec = instance["scene_spec"]
        states, actions = trajectory["states"], trajectory["actions"]
        if trajectory["scene_spec"] != spec:
            raise ValueError("trajectory and scene specification do not match")
        required_hold=max(10,int(trajectory.get('success_hold_frames',10)))
        if not trajectory.get("success") or len(states) != len(actions)+1 or not all(s["success"] for s in states[-required_hold:]):
            raise ValueError("only verified successful T-action/T+1-state trajectories can be exported")
        if not np.isfinite(actions).all():
            raise ValueError("non-finite trajectory actions")
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            raise FileExistsError(path)
        temporary = path.with_suffix(".partial.h5")
        catalog = segmentation_catalog(env,spec)
        frames = len(states)
        spec_json = json.dumps(spec,sort_keys=True,ensure_ascii=False)
        metadata = {"schema_version": "embodied_gt_v1", "scene_spec": spec,
                    "scene_sha256": hashlib.sha256(spec_json.encode()).hexdigest(),
                    "source_validation": instance["validation"],
                    "success": True, "num_actions": len(actions), "num_observations": frames,
                    "control_dt": trajectory["control_dt"], "control_mode": trajectory["control_mode"],
                    "action_definition": "7 absolute Panda arm joint targets (rad) + 1 normalized gripper target [-1,1]",
                    "alignment": "observation[t], action[t], observation[t+1]; image and pose share observation timestamp",
                    "coordinates": {"world": "SAPIEN world, metres; Z up", "quaternion": "wxyz",
                                    "camera": "OpenCV: X right, Y down, Z forward; pixel centers at u+0.5,v+0.5",
                                    "normal_cv": "renderer surface normals in OpenCV camera frame; background zero",
                                    "depth_m": "optical Z depth in metres; background zero, see valid mask"},
                    "segmentation_catalog": catalog, "trajectory_method": trajectory["method"],
                    "trajectory_validation": trajectory["validation"],
                    "joint_names": [joint.name for joint in env.agent.robot.get_active_joints()],
                    "robot_urdf_sha256": hashlib.sha256(Path(env.agent.urdf_path).read_bytes()).hexdigest(),
                    "phases": trajectory["phases"], "versions": {}}
        for package in ("mani_skill","sapien","torch","numpy","h5py","scipy"):
            try: metadata["versions"][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError: pass
        max_projection, max_geometry, max_replay = 0., 0., 0.
        gif_frames = []
        gif_stride = max(1, int(np.ceil(frames/100)))
        last_state = env.get_state_dict()
        try:
            with h5py.File(temporary,"w") as h5:
                h5.attrs["schema_version"] = "embodied_gt_v1"
                h5.attrs["complete"] = False
                h5.create_dataset("actions",data=actions.astype(np.float32),compression="gzip")
                h5.create_dataset("timestamps",data=np.arange(frames)*trajectory["control_dt"])
                h5.create_dataset("action_timestamps",data=np.arange(len(actions))*trajectory["control_dt"])
                h5.create_dataset("stages",data=trajectory["stages"],dtype=h5py.string_dtype("utf-8"))
                for key in ("qpos","qvel","tcp_pose","robot_base_pose","success","grasped"):
                    h5.create_dataset("observations/"+key,data=np.stack([s[key] for s in states]),compression="gzip")
                for index, name in enumerate(env._object_handles):
                    group=h5.create_group(f"objects/{index}")
                    group.attrs["id"]=name
                    group.attrs["columns"]="position_xyz,quaternion_wxyz,linear_velocity_xyz,angular_velocity_xyz"
                    group.create_dataset("state",data=np.stack([s["objects"][name] for s in states]),compression="gzip")
                # Persist the engine's state dictionary as well as human-readable poses.
                engine_keys=list(flatten_tree(states[0]["engine"]))
                for keys, _ in engine_keys:
                    values=[]
                    for state in states:
                        value=state["engine"]
                        for key in keys: value=value[key]
                        values.append(value)
                    dataset=h5.create_dataset("engine/"+"/".join(quote(k,safe="") for k in keys),data=np.stack(values),compression="gzip")
                    dataset.attrs["key_path_json"]=json.dumps(keys)
                datasets={}
                for frame_index,state in enumerate(states):
                    env.set_state_dict(torch_tree(state["engine"],env.device))
                    replay_error=float(np.max(np.abs(array(env.agent.robot.qpos)[0]-state["qpos"])))
                    for oid, obj in env._object_handles.items():
                        replay_error=max(replay_error,float(np.max(np.abs(array(obj.pose.raw_pose)[0]-state["objects"][oid][:7]))))
                    max_replay=max(max_replay,replay_error)
                    if replay_error > 1e-5:
                        raise ValueError(f"engine state replay mismatch: {replay_error}")
                    env.scene.update_render(update_sensors=True,update_human_render_cameras=False)
                    for camera_index,(name,camera) in enumerate(env._sensors.items()):
                        camera.capture()
                        raw=camera.get_obs(rgb=True,depth=True,position=True,segmentation=True,normal=True,apply_texture_transforms=False)
                        data,points,projection_error=decode_camera(raw,camera.get_params())
                        max_projection=max(max_projection,projection_error)
                        max_geometry=max(max_geometry,check_object_alignment(env,spec,data["instance_id"],points,data["camera_to_world_cv"]))
                        observed_ids=np.unique(data["instance_id"])
                        unknown=set(int(x) for x in observed_ids)-set(catalog)
                        if unknown:
                            raise ValueError(f"unmapped segmentation IDs: {sorted(unknown)}")
                        semantic=np.zeros_like(data["instance_id"],dtype=np.uint16)
                        for entity_id in observed_ids:
                            semantic[data["instance_id"]==entity_id]=catalog[int(entity_id)]["semantic_id"]
                        data["semantic_id"]=semantic
                        data["visible_pixels"]=np.array([(data["instance_id"]==key).sum() for key in sorted(catalog)],dtype=np.uint32)
                        for key,value in data.items():
                            entry=(name,key)
                            if entry not in datasets:
                                datasets[entry]=h5.create_dataset(f"cameras/{quote(name,safe='')}/{key}",shape=(frames,*value.shape),dtype=value.dtype,
                                                                chunks=(1,*value.shape),compression="gzip",compression_opts=1)
                            datasets[entry][frame_index]=value
                        if camera_index==0 and (frame_index%gif_stride==0 or frame_index==frames-1):
                            gif_frames.append(Image.fromarray(data["rgb"]).resize((320,240)))
                        if camera_index==0 and frame_index in (0,frames-1):
                            Image.fromarray(data["rgb"]).save(path.with_name(path.stem+("_initial.png" if frame_index==0 else "_final.png")))
                    if on_progress and (frame_index%20==0 or frame_index==frames-1):
                        on_progress(frame_index+1,frames)
                metadata["gt_validation"]={"max_projection_error_px":max_projection,"max_object_geometry_violation_m":max_geometry,
                                           "max_state_replay_error":max_replay,"temporal_alignment":True,
                                           "normal_unit_vectors":True,"segmentation_ids_mapped":True}
                metadata["visible_pixel_columns"]=sorted(catalog)
                h5.create_dataset("metadata_json",data=json.dumps(metadata,ensure_ascii=False),dtype=h5py.string_dtype("utf-8"))
                h5.attrs["complete"]=True
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            env.set_state_dict(last_state)
        preview=path.with_suffix(".gif")
        gif_frames[0].save(preview,save_all=True,append_images=gif_frames[1:],duration=round(trajectory["control_dt"]*gif_stride*1000),loop=0)
        manifest=path.with_suffix(".json")
        metadata.update(hdf5_path=str(path),preview_path=str(preview),file_size_bytes=path.stat().st_size)
        manifest.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
        return metadata
