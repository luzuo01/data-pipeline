from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np


def _read_rows(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("Dexmal episode is empty")
    required = {"prompt", "state", "action", "extra", "images_1", "images_2"}
    if missing := required - rows[0].keys():
        raise ValueError(f"Dexmal row is missing fields: {sorted(missing)}")
    return rows


def _frames(path: Path, indices: list[int]) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"cannot open video: {path}")
    wanted = set(indices)
    result: dict[int, np.ndarray] = {}
    frame_index = 0
    while wanted:
        ok, bgr = capture.read()
        if not ok:
            break
        if frame_index in wanted:
            result[frame_index] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            wanted.remove(frame_index)
        frame_index += 1
    capture.release()
    if wanted:
        raise ValueError(f"video ended before referenced frames: {sorted(wanted)[:5]}")
    return [result[index] for index in indices]


def convert_episode(
    jsonl: Path,
    top_video: Path,
    wrist_video: Path,
    output: Path,
    repo_id: str,
) -> dict:
    """Convert one Dexmal JSONL episode and its referenced shared videos."""
    from lerobot.configs import RGBEncoderConfig
    from lerobot.datasets import LeRobotDataset

    if output.exists():
        raise FileExistsError(output)
    rows = _read_rows(jsonl)
    timestamps = np.asarray([row["extra"]["timestamp"] for row in rows], dtype=np.float64)
    deltas = np.diff(timestamps)
    if len(deltas) and not np.allclose(deltas, 1 / 30, atol=2e-6, rtol=0):
        raise ValueError("Dexmal sample is not uniformly sampled at 30 Hz")
    top_indices = [int(row["images_1"]["frame_idx"]) for row in rows]
    wrist_indices = [int(row["images_2"]["frame_idx"]) for row in rows]
    top_frames = _frames(top_video, top_indices)
    wrist_frames = _frames(wrist_video, wrist_indices)
    height, width, channels = top_frames[0].shape
    if wrist_frames[0].shape != (height, width, channels):
        raise ValueError("Dexmal camera resolutions differ; explicit feature shapes are required")
    state_size = len(rows[0]["state"])
    action_size = len(rows[0]["action"])
    if any(len(row["state"]) != state_size or len(row["action"]) != action_size for row in rows):
        raise ValueError("state/action dimensions change within the episode")

    features = {
        "observation.state": {"dtype": "float32", "shape": (state_size,), "names": [f"motor_{i}" for i in range(state_size)]},
        "action": {"dtype": "float32", "shape": (action_size,), "names": [f"motor_target_{i}" for i in range(action_size)]},
        "observation.images.top": {"dtype": "video", "shape": (height, width, 3), "names": ["height", "width", "channels"]},
        "observation.images.wrist": {"dtype": "video", "shape": (height, width, 3), "names": ["height", "width", "channels"]},
    }
    dataset = LeRobotDataset.create(
        repo_id=repo_id,
        root=output,
        robot_type="so101",
        fps=30,
        features=features,
        video_backend="pyav",
        streaming_encoding=True,
        rgb_encoder=RGBEncoderConfig(vcodec="h264", crf=18, preset="fast", g=30),
        encoder_threads=2,
    )
    for row, top, wrist in zip(rows, top_frames, wrist_frames, strict=True):
        dataset.add_frame({
            "observation.state": np.asarray(row["state"], dtype=np.float32),
            "action": np.asarray(row["action"], dtype=np.float32),
            "observation.images.top": top,
            "observation.images.wrist": wrist,
            "task": row["prompt"],
        })
    dataset.save_episode(parallel_encoding=False)
    dataset.finalize()
    manifest = {
        "adapter": "dexmal_jsonl_video_v1",
        "source_repo": "Dexmal/so101_pick_cube",
        "source_revision": "86e706e38bb67bcf8f866cc45f5ac8a598547438",
        "source_episode": jsonl.name,
        "rows": len(rows),
        "source_video_frame_range": {"top": [min(top_indices), max(top_indices)], "wrist": [min(wrist_indices), max(wrist_indices)]},
        "alignment": "Each JSONL row selects the exact shared MP4 frame_idx for both cameras.",
        "state_semantics": "Six SO-101 motor positions as published by Dexmal; source units are not asserted by the dataset card.",
        "action_semantics": "Six SO-101 target values as published by Dexmal; no delta/absolute reinterpretation applied.",
    }
    manifest_path = output / "meta" / "semantic_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
