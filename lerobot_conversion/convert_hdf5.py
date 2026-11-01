"""Convert embodied_gt_v1 HDF5 to a LeRobot v3 training view plus lossless archive.

Run with the Python environment documented in README.md. Never overwrites output.
The archive is an extension, not a modality automatically loaded by LeRobot.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
from pathlib import Path

import h5py
import numpy as np


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def convert(source: Path, output: Path, repo_id: str, task: str | None = None) -> None:
    from lerobot.configs import RGBEncoderConfig
    from lerobot.datasets import LeRobotDataset

    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    with h5py.File(source, "r") as h5:
        metadata = json.loads(text(h5["metadata_json"][()]))
        # Older UE5 recorder revisions omitted the explicit coordinate block.
        # Keep the adapter strict about the schema, but provide the canonical
        # embodied-data convention for those recordings so they remain
        # convertible instead of failing at manifest generation time.
        metadata.setdefault("coordinates", {
            "world": "UE5 world, metres after cm->m conversion; Z up",
            "quaternion": "wxyz",
            "camera": "OpenCV convention",
        })
        if metadata.get("schema_version") != "embodied_gt_v1":
            raise ValueError("This adapter requires embodied_gt_v1; other HDF5 schemas need an adapter.")
        if not bool(h5.attrs.get("complete", False)):
            raise ValueError("Source episode is incomplete")
        if metadata["control_mode"] != "pd_joint_pos":
            raise ValueError("This action mapping only supports pd_joint_pos")
        actions = h5["actions"][:]
        n = len(actions)
        ts = h5["timestamps"][:]
        ats = h5["action_timestamps"][:]
        dt = float(metadata["control_dt"])
        fps = int(round(1 / dt))
        state_width = int(h5["observations/qpos"].shape[1])
        action_width = int(actions.shape[1])
        if n < 1 or len(ts) != n + 1 or action_width < 2 or action_width > state_width:
            raise ValueError("Expected N joint actions and N+1 observations")
        if h5["observations/qpos"].shape != (n + 1, state_width):
            raise ValueError("Invalid joint observation shape")
        np.testing.assert_allclose(dt, 1 / fps, atol=1e-10, rtol=0)
        np.testing.assert_allclose(np.diff(ts), dt, atol=1e-8, rtol=0)
        np.testing.assert_allclose(ats, ts[:-1], atol=1e-8, rtol=0)
        joints = metadata["joint_names"]
        if len(joints) != state_width:
            raise ValueError(f"Expected {state_width} joint names")
        task_spec = metadata["scene_spec"]["task"]
        if task is None:
            if task_spec["template"] in {"pick_and_place", "stack"}:
                task = f"Pick up {task_spec['object_id']} and place it on {task_spec['target_id']}."
            else:
                description = metadata.get("scene_spec", {}).get("description")
                if not description:
                    raise ValueError("Pass --task for an unsupported task template")
                task = str(description)

        features: dict = {}
        mappings: list[dict] = []

        def mapped(key: str, path: str, *, offset: int = 0, dtype: str | None = None,
                   names: list | None = None, info: dict | None = None) -> None:
            src = h5[path]
            expected_length = n if path in ("actions", "action_timestamps", "stages") else n + 1
            if len(src) != expected_length:
                raise ValueError(f"Unexpected length for {path}: {len(src)}")
            dtype = dtype or ("string" if src.dtype.kind in "OSU" else str(src.dtype))
            shape = src.shape[1:] or (1,)
            features[key] = {"dtype": dtype, "shape": shape, "names": names,
                             "info": {"source_path": path, "source_offset": offset, **(info or {})}}
            mappings.append({"target": key, "source": path, "offset": offset,
                             "dtype": dtype, "shape": list(shape)})

        mapped("observation.state", "observations/qpos", dtype="float32", names=joints,
               info={"units": ["rad"] * max(0, state_width - 2) + ["m"] * min(2, state_width),
                     "description": "Measured arm and gripper joint positions; not action targets"})
        mapped("action", "actions", names=joints[:action_width - 1] + ["gripper_target"],
               info={"control_mode": metadata["control_mode"],
                     "description": metadata["action_definition"],
                     "units": ["rad"] * (action_width - 1) + ["normalized"],
                     "gripper_range": [-1, 1]})
        # Keep original float64 measurements as well as a float32 policy view.
        for field in ("qpos", "qvel", "tcp_pose", "robot_base_pose"):
            mapped(f"source.{field}", f"observations/{field}")
            mapped(f"next.{field}", f"observations/{field}", offset=1)
        mapped("source.timestamp", "timestamps")
        mapped("source.action_timestamp", "action_timestamps")
        mapped("next.timestamp", "timestamps", offset=1)
        mapped("source.success", "observations/success")
        mapped("next.success", "observations/success", offset=1)
        mapped("source.stage", "stages")

        for camera in h5["cameras"]:
            base = f"cameras/{camera}"
            mapped(f"observation.images.{camera}", f"{base}/rgb", dtype="video",
                   names=["height", "width", "channels"])
            # Keep dense ground truth as native LeRobot numeric features too.
            # The lossless source archive remains available for readers that
            # need the original HDF5 layout, but training/data tooling can now
            # access these arrays directly through LeRobotDataset.
            mapped(f"observation.depth.{camera}", f"{base}/depth_m", dtype="float32",
                   names=["height", "width"], info={"units": "metres", "background": 0})
            mapped(f"observation.normal.{camera}", f"{base}/normal_cv", dtype="float32",
                   names=["height", "width", "channels"], info={"frame": "OpenCV camera"})
            mapped(f"observation.instance_id.{camera}", f"{base}/instance_id", dtype="uint32",
                   names=["height", "width"])
            mapped(f"observation.semantic_id.{camera}", f"{base}/semantic_id", dtype="uint16",
                   names=["height", "width"])
            mapped(f"observation.valid.{camera}", f"{base}/valid", dtype="bool",
                   names=["height", "width"])
            for field in ("intrinsic_cv", "camera_to_world_cv", "world_to_camera_cv", "visible_pixels"):
                mapped(f"source.camera.{camera}.{field}", f"{base}/{field}")

        for obj in h5["objects"]:
            mapped(f"source.objects.{obj}.state", f"objects/{obj}/state")
            mapped(f"next.objects.{obj}.state", f"objects/{obj}/state", offset=1)
        def engine_visit(name: str, obj: object) -> None:
            if isinstance(obj, h5py.Dataset):
                mapped("source.engine." + name.replace("/", "."), "engine/" + name)
        h5["engine"].visititems(engine_visit)

        dataset = LeRobotDataset.create(
            repo_id=repo_id, root=output, robot_type=str(metadata.get("robot_type", "franka_panda")).lower(), fps=fps,
            features=features, video_backend="pyav", streaming_encoding=True,
            rgb_encoder=RGBEncoderConfig(vcodec="h264", crf=18, preset="fast", g=10),
            encoder_threads=2,
        )
        for i in range(n):
            frame: dict = {"task": task}
            for mapping in mappings:
                value = h5[mapping["source"]][i + mapping["offset"]]
                if mapping["dtype"] == "string":
                    value = text(value)
                elif mapping["dtype"] != "video":
                    value = np.asarray(value, dtype=mapping["dtype"]).reshape(mapping["shape"])
                    if value.dtype.kind == "f" and not np.isfinite(value).all():
                        raise ValueError(f"Nonfinite values at row {i}, {mapping['source']}")
                frame[mapping["target"]] = value
            dataset.add_frame(frame)
            if i % 50 == 0:
                print(f"Wrote frame {i}/{n}", flush=True)
        dataset.save_episode(parallel_encoding=False)
        dataset.finalize()

        inventory = []
        def inventory_visit(name: str, obj: object) -> None:
            if isinstance(obj, h5py.Dataset):
                inventory.append({"path": name, "shape": list(obj.shape), "dtype": str(obj.dtype),
                                  "native_targets": [m["target"] for m in mappings if m["source"] == name],
                                  "lossless_archive": True})
        h5.visititems(inventory_visit)

    archive = output / "extras" / "source_episode.h5"
    archive.parent.mkdir()
    shutil.copy2(source, archive)
    digest = sha256(source)
    if sha256(archive) != digest:
        raise ValueError("Source archive checksum mismatch")
    dump(output / "meta" / "source_metadata.json", metadata)
    dump(output / "meta" / "semantic_manifest.json", {
        "adapter": "embodied_gt_v1_to_lerobot_v3", "adapter_version": "1.0.0",
        "source_file": source.name, "source_sha256": digest,
        "lerobot_commit": "dd5313e305ff7aaa5b6c7b4769d1b26650ce250b",
        "lerobot_package_version": importlib.metadata.version("lerobot"),
        "repo_id": repo_id, "fps": fps, "training_rows": n, "source_observations": n + 1,
        "alignment": "row i: observation[i], action[i], next.* from observation[i+1]",
        "terminal_observation": "No fabricated terminal action. Final low-dimensional state is in last row next.*; all final images/GT remain in extras/source_episode.h5.",
        "task": task, "task_origin": "--task override or rendered from scene_spec.task; not the planner user_prompt",
        "coordinates": metadata["coordinates"], "control_mode": metadata["control_mode"],
        "source_config_disagreement": {"requested": metadata["scene_spec"]["robot"]["control_mode"],
                                         "runtime": metadata["control_mode"]},
        "state_action_dimensions": {"state": state_width, "action": action_width},
        "image_encoding": "H.264 yuv420p CRF18; lossy. Exact RGB is preserved in the archive.",
        "lossless_archive": "extras/source_episode.h5",
        "archive_scope": "All source datasets, root/dataset attributes and metadata, including the native dense GT features and the original depth, normals, segmentation, valid masks and observation N.",
        "native_dense_ground_truth": ["observation.depth.*", "observation.normal.*", "observation.instance_id.*", "observation.semantic_id.*", "observation.valid.*"],
        "omitted_inventions": ["reward", "terminal_vs_truncated", "end_effector_action", "gripper_calibration"],
        "mappings": mappings, "source_inventory": inventory,
    })
    print(f"Created {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repo-id", default="local/panda_pick_place_example")
    parser.add_argument("--task")
    args = parser.parse_args()
    convert(args.source.resolve(), args.output.resolve(), args.repo_id, args.task)
