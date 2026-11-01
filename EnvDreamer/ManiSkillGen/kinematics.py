"""URDF-based numerical IK for CPU Panda; no simulator state mutation."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation


def pose_matrix(pose):
    values = np.asarray(pose, dtype=float).reshape(7)
    matrix = np.eye(4)
    matrix[:3, 3] = values[:3]
    matrix[:3, :3] = Rotation.from_quat(values[[4, 5, 6, 3]]).as_matrix()
    return matrix


class URDFKinematics:
    def __init__(self, path, joint_names, end_link):
        root = ET.parse(path).getroot()
        parents = {j.find("child").get("link"): j for j in root.findall("joint")}
        chain = []
        while end_link in parents:
            joint = parents[end_link]
            origin = joint.find("origin")
            xyz = np.fromstring(origin.get("xyz", "0 0 0"), sep=" ") if origin is not None else np.zeros(3)
            rpy = np.fromstring(origin.get("rpy", "0 0 0"), sep=" ") if origin is not None else np.zeros(3)
            transform = np.eye(4)
            transform[:3, :3] = Rotation.from_euler("xyz", rpy).as_matrix()
            transform[:3, 3] = xyz
            axis = joint.find("axis")
            vector = np.fromstring(axis.get("xyz"), sep=" ") if axis is not None else np.array([1., 0, 0])
            name = joint.get("name")
            index = joint_names.index(name) if name in joint_names else None
            if joint.get("type") != "fixed" and index is None:
                raise ValueError(f"unmapped non-fixed joint in IK chain: {name}")
            chain.append((transform, vector, index))
            end_link = joint.find("parent").get("link")
        self.chain = chain[::-1]
        limits = {j.get("name"): j.find("limit") for j in root.findall("joint")}
        self.lower = np.array([float(limits[n].get("lower")) for n in joint_names]) + 1e-4
        self.upper = np.array([float(limits[n].get("upper")) for n in joint_names]) - 1e-4

    def forward(self, qpos):
        pose = np.eye(4)
        for origin, axis, index in self.chain:
            pose = pose @ origin
            if index is not None:
                rotation = np.eye(4)
                rotation[:3, :3] = Rotation.from_rotvec(axis*qpos[index]).as_matrix()
                pose = pose @ rotation
        return pose

    def solve(self, target, initial):
        initial = np.clip(np.asarray(initial, dtype=float), self.lower, self.upper)
        def residual(q):
            current = self.forward(q)
            rot = Rotation.from_matrix(target[:3,:3] @ current[:3,:3].T).as_rotvec()
            return np.r_[current[:3,3]-target[:3,3], .2*rot, .0001*(q-initial)]
        solution = least_squares(residual, initial, bounds=(self.lower,self.upper), max_nfev=150,
                                 ftol=1e-8, xtol=1e-8, gtol=1e-8)
        actual = self.forward(solution.x)
        position_error = float(np.linalg.norm(actual[:3,3]-target[:3,3]))
        angle_error = float(Rotation.from_matrix(target[:3,:3]@actual[:3,:3].T).magnitude())
        if position_error > .002 or angle_error > .04:
            raise ValueError(f"IK unreachable: position residual {position_error:.4f} m, angle {angle_error:.4f} rad")
        return solution.x
