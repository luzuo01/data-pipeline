"""A small, spec-driven ManiSkill3 environment.

This module is imported lazily by :mod:`generator` so importing ManiSkillGen
does not require SAPIEN.  It uses ManiSkill3's public ``BaseEnv`` and actor
builders; the generated tabletop, objects, Franka and camera therefore live in
the actual SAPIEN scene rather than in the software preview renderer.
"""

from __future__ import annotations

from typing import Any, Mapping


def register_spec_env() -> str:
    """Register and return the environment id, importing ManiSkill lazily."""
    import numpy as np
    import sapien
    import torch
    from mani_skill.envs.sapien_env import BaseEnv
    from mani_skill.sensors.camera import CameraConfig
    from mani_skill.utils import sapien_utils
    from mani_skill.utils.building import actors
    from mani_skill.utils.registration import register_env

    env_id = "EnvDreamerTabletop-v1"
    try:
        # Registration is idempotent for our process in normal use.  A second
        # decorator registration is harmlessly avoided by checking gym's spec.
        import gymnasium as gym
        if gym.spec(env_id) is not None:
            return env_id
    except Exception:
        pass

    @register_env(env_id, max_episode_steps=100)
    class EnvDreamerTabletopEnv(BaseEnv):
        SUPPORTED_ROBOTS = ["panda"]

        def __init__(self, *args, scene_spec: Mapping[str, Any] | None = None, **kwargs):
            self.scene_spec = dict(scene_spec or {})
            self._object_handles: dict[str, Any] = {}
            self._target_handles: dict[str, Any] = {}
            super().__init__(*args, robot_uids="panda", **kwargs)

        def _load_agent(self, options: dict):
            base = self.scene_spec.get("robot", {}).get("base_pose", [0.0, 0.0, 0.0])
            base = [float(v) for v in base]
            table_height = float(self.scene_spec.get("scene", {}).get("workspace", {}).get("table_height", 0.75))
            # SceneSpec stores robot base z relative to the tabletop by
            # convention.  ManiSkill/SAPIEN uses world coordinates, so place a
            # zero z base on the actual tabletop surface.
            if abs(base[2]) < 1e-8:
                base[2] = table_height
            super()._load_agent(options, sapien.Pose(p=base))

        def _load_scene(self, options: dict):
            scene = self.scene_spec.get("scene", {})
            workspace = scene.get("workspace", {})
            size = [float(v) for v in workspace.get("size", [0.8, 0.8, 0.5])]
            table_height = float(workspace.get("table_height", 0.75))
            # A static box is sufficient for the tabletop and keeps the scene
            # independent from ManiSkill's task-specific table dimensions.
            self.table = actors.build_box(
                self.scene,
                half_sizes=[size[0] / 2, size[1] / 2, 0.025],
                color=[0.55, 0.38, 0.20, 1.0],
                name="table",
                body_type="static",
                initial_pose=sapien.Pose(p=[0.0, 0.0, table_height - 0.025]),
            )
            for obj in scene.get("objects", []):
                if not isinstance(obj, Mapping):
                    continue
                obj_id = str(obj.get("id", "object"))
                shape = str(obj.get("shape", "box"))
                dims = [float(v) for v in obj.get("size", [0.04, 0.04, 0.04])]
                pose = sapien.Pose(p=[float(v) for v in obj.get("pose", [0.0, 0.0, table_height + dims[2] / 2])])
                color = [float(v) for v in obj.get("color", [0.7, 0.7, 0.7, 1.0])]
                is_target = shape == "target" or obj.get("asset") == "primitive_target"
                body_type = "kinematic" if is_target or not bool(obj.get("dynamic", True)) else "dynamic"
                if shape == "sphere":
                    handle = actors.build_sphere(self.scene, radius=dims[0] / 2, color=color, name=obj_id, body_type=body_type, initial_pose=pose)
                elif shape == "cylinder":
                    # SAPIEN cylinders are built along local X; SceneSpec height is Z.
                    pose = sapien.Pose(p=pose.p, q=[2**-.5, 0, 2**-.5, 0])
                    handle = actors.build_cylinder(self.scene, radius=dims[0] / 2, half_length=dims[2] / 2, color=color, name=obj_id, body_type=body_type, initial_pose=pose)
                else:
                    handle = actors.build_box(self.scene, half_sizes=[d / 2 for d in dims], color=color, name=obj_id, body_type=body_type, add_collision=not is_target, initial_pose=pose)
                self._object_handles[obj_id] = handle
                if is_target:
                    self._target_handles[obj_id] = handle

        def _camera_vectors(self):
            camera = next(iter(self.scene_spec.get("cameras", {}).values()), {})
            eye = [float(v) for v in camera.get("position", [0.8, 0.0, 0.65])]
            target = [float(v) for v in camera.get("look_at", [0.0, 0.0, 0.78])]
            return eye, target, camera.get("resolution", [640, 480])

        @property
        def _default_sensor_configs(self):
            configs = []
            for name, camera in self.scene_spec.get("cameras", {}).items():
                pose = sapien_utils.look_at(eye=camera["position"], target=camera["look_at"])
                width, height = camera.get("resolution", [640,480])
                configs.append(CameraConfig(name, pose, width, height, 1.2, .01, 100, shader_pack="default"))
            return configs

        @property
        def _default_human_render_camera_configs(self):
            eye, target, resolution = self._camera_vectors()
            pose = sapien_utils.look_at(eye=eye, target=target)
            return CameraConfig("render_camera", pose, int(resolution[0]), int(resolution[1]), 1.2, 0.01, 100)

        def _initialize_episode(self, env_idx: torch.Tensor, options: dict):
            # Actor initial poses are provided through their builders. BaseEnv
            # restores them on reset, so no task-specific randomization is
            # needed here for a fixed SceneSpec.
            self.agent.reset(self.agent.keyframes["rest"].qpos)

        def evaluate(self):
            task = self.scene_spec.get("task", {})
            obj = self._object_handles.get(task.get("object_id"))
            target = self._object_handles.get(task.get("target_id"))
            if obj is None or target is None:
                return {"success": torch.zeros(self.num_envs, device=self.device, dtype=torch.bool)}
            definitions = {o["id"]: o for o in self.scene_spec["scene"]["objects"]}
            obj_size = torch.tensor(definitions[task["object_id"]]["size"], device=self.device)
            target_size = torch.tensor(definitions[task["target_id"]]["size"], device=self.device)
            delta = obj.pose.p - target.pose.p
            footprint = torch.all(torch.abs(delta[:, :2]) + obj_size[:2]/2 <= target_size[:2]/2 + 1e-5, dim=1)
            stable = obj.is_static(lin_thresh=.03, ang_thresh=.3)
            released = ~self.agent.is_grasping(obj)
            success = torch.ones(self.num_envs, device=self.device, dtype=torch.bool)
            for predicate in self.scene_spec["success"]:
                tolerance = float(predicate.get("position_tolerance", .03))
                if predicate["predicate"] == "object_on_object":
                    expected_z = target.pose.p[:, 2] + target_size[2]/2 + obj_size[2]/2
                    # Equal-sized boxes cannot satisfy exact full-footprint containment
                    # under numerical physics. Require >=50% support area AND contact,
                    # plus the declared position tolerance and settled/released state.
                    left = torch.maximum(obj.pose.p[:,:2]-obj_size[:2]/2, target.pose.p[:,:2]-target_size[:2]/2)
                    right = torch.minimum(obj.pose.p[:,:2]+obj_size[:2]/2, target.pose.p[:,:2]+target_size[:2]/2)
                    area = torch.prod(torch.clamp(right-left, min=0), dim=1)
                    contact = torch.linalg.norm(self.scene.get_pairwise_contact_forces(obj, target), dim=1) > 1e-4
                    footprint = (area >= .5*torch.prod(obj_size[:2])) & contact
                    stable = stable & target.is_static(lin_thresh=.03, ang_thresh=.3)
                else:
                    expected_z = self.scene_spec["scene"]["workspace"]["table_height"] + obj_size[2]/2
                success &= (footprint & (torch.linalg.norm(delta[:, :2], dim=1) <= tolerance)
                            & (torch.abs(obj.pose.p[:, 2]-expected_z) <= min(tolerance, .005)) & stable & released)
            return {"success": success, "object_target_distance": torch.linalg.norm(delta, dim=1)}

        def _get_obs_extra(self, info: dict):
            return {}

    return env_id
