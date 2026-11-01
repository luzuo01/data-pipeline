from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np


CAMERAS = {
    "global": "cam_global_rgb.mp4",
    "side": "cam_side_rgb.mp4",
    "wrist": "cam_arm_rgb.mp4",
}


def convert_arx5_episode(
    episode: Path,
    output: Path,
    repo_id: str,
    prompt: str,
    max_frames: int | None = None,
) -> dict:
    """Convert a Table30 v2 ARX5 episode using next measured EE pose as action."""
    from lerobot.configs import RGBEncoderConfig
    from lerobot.datasets import LeRobotDataset

    if output.exists():
        raise FileExistsError(output)
    states_path = episode / "states" / "states.jsonl"
    meta_path = episode / "meta" / "episode_meta.json"
    states = [json.loads(line) for line in states_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if len(states) != int(meta["frames"]):
        raise ValueError(f"state count {len(states)} != metadata frames {meta['frames']}")
    captures: dict[str, cv2.VideoCapture] = {}
    shape: tuple[int, int, int] | None = None
    try:
        for name, filename in CAMERAS.items():
            capture = cv2.VideoCapture(str(episode / "videos" / filename))
            if not capture.isOpened():
                raise ValueError(f"cannot open {filename}")
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            camera_shape = (int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), 3)
            if frame_count != len(states) or not np.isclose(fps, 30.0):
                raise ValueError(f"{filename}: frames={frame_count}, fps={fps}; expected {len(states)}, 30")
            if shape is not None and camera_shape != shape:
                raise ValueError("camera resolutions differ")
            shape = camera_shape
            captures[name] = capture

        assert shape is not None
        row_count = len(states) - 1
        if max_frames is not None:
            row_count = min(row_count, max_frames)
        features = {
            "observation.state": {"dtype": "float32", "shape": (7,), "names": [*[f"joint_{i}" for i in range(6)], "gripper_width"]},
            "action": {"dtype": "float32", "shape": (8,), "names": ["x", "y", "z", "qx", "qy", "qz", "qw", "gripper_width"]},
            "source.timestamp": {"dtype": "float64", "shape": (1,), "names": ["unix_time_s"]},
        }
        for name in CAMERAS:
            features[f"observation.images.{name}"] = {"dtype": "video", "shape": shape, "names": ["height", "width", "channels"]}
        dataset = LeRobotDataset.create(
            repo_id=repo_id,
            root=output,
            robot_type="ARX5",
            fps=30,
            features=features,
            video_backend="pyav",
            streaming_encoding=True,
            rgb_encoder=RGBEncoderConfig(vcodec="h264", crf=18, preset="fast", g=30),
            encoder_threads=2,
        )
        for index in range(row_count):
            images = {}
            for name, capture in captures.items():
                ok, bgr = capture.read()
                if not ok:
                    raise ValueError(f"camera {name} ended at frame {index}")
                images[f"observation.images.{name}"] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            current, following = states[index], states[index + 1]
            state = np.asarray([*current["joint_positions"], current["gripper_width"]], dtype=np.float32)
            action = np.asarray([*following["ee_positions"], following["gripper_width"]], dtype=np.float32)
            dataset.add_frame({
                "observation.state": state,
                "action": action,
                "source.timestamp": np.asarray([current["timestamp"]], dtype=np.float64),
                "task": prompt,
                **images,
            })
        dataset.save_episode(parallel_encoding=False)
        dataset.finalize()
    finally:
        for capture in captures.values():
            capture.release()

    manifest = {
        "adapter": "robochallenge_table30_v2_arx5_v1",
        "source_repo": "RoboChallenge/Table30v2",
        "source_revision": "c58ad2cc76ce722ea54de51f9fae03012a698f47",
        "source_episode": episode.name,
        "source_frames": len(states),
        "training_rows": row_count,
        "fps": 30,
        "alignment": "row t uses measured robot state and all camera frames at t; action is measured EE pose and gripper width at t+1",
        "action_semantics": "Derived next measured pose for behavior cloning. The source does not expose the original command, so this is not labeled as a commanded action.",
        "terminal_observation": "Source final observation is not assigned a fabricated action and is omitted from the training rows.",
        "source_state_fields_preserved_in_training_view": ["joint_positions", "gripper_width", "timestamp"],
        "source_sidecar_required_for": ["joint_velocities", "effort", "gripper_velocity", "camera calibration", "episode wall-clock metadata"],
    }
    path = output / "meta" / "semantic_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
