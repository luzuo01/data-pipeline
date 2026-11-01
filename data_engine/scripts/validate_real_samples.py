from __future__ import annotations

import json
from pathlib import Path

import av
import cv2
import numpy as np
import pyarrow.parquet as pq
from lerobot.datasets import LeRobotDataset


ROOT = Path(__file__).resolve().parents[1] / "_samples"


def video_frames(dataset_root: Path) -> dict[str, int]:
    result = {}
    for path in (dataset_root / "videos").glob("**/*.mp4"):
        with av.open(str(path)) as container:
            result[str(path.relative_to(dataset_root))] = sum(1 for _ in container.decode(video=0))
    return result


def parquet_rows(dataset_root: Path) -> list[dict]:
    rows = []
    for path in sorted((dataset_root / "data").glob("*/*.parquet")):
        rows.extend(pq.read_table(path).to_pylist())
    return rows


def sampled_psnr(source_video: Path, output_video: Path, pairs: list[tuple[int, int]]) -> dict[str, float]:
    source = cv2.VideoCapture(str(source_video))
    output = cv2.VideoCapture(str(output_video))
    scores = []
    try:
        for source_index, output_index in pairs:
            source.set(cv2.CAP_PROP_POS_FRAMES, source_index)
            output.set(cv2.CAP_PROP_POS_FRAMES, output_index)
            source_ok, source_frame = source.read()
            output_ok, output_frame = output.read()
            assert source_ok and output_ok
            assert source_frame.shape == output_frame.shape
            mse = float(np.mean((source_frame.astype(np.float32) - output_frame.astype(np.float32)) ** 2))
            scores.append(float("inf") if mse == 0 else float(10 * np.log10(255**2 / mse)))
    finally:
        source.release()
        output.release()
    assert min(scores) > 25
    return {"minimum_psnr_db": min(scores), "mean_psnr_db": float(np.mean(scores))}


def validate_dexmal() -> dict:
    source = [json.loads(line) for line in (ROOT / "dexmal_so101" / "episode_00000.jsonl").read_text().splitlines()]
    root = ROOT / "outputs" / "dexmal_so101_episode0"
    rows = parquet_rows(root)
    assert len(rows) == len(source) == 234
    for actual, expected in zip(rows, source, strict=True):
        np.testing.assert_array_equal(np.asarray(actual["observation.state"], np.float32), np.asarray(expected["state"], np.float32))
        np.testing.assert_array_equal(np.asarray(actual["action"], np.float32), np.asarray(expected["action"], np.float32))
    videos = video_frames(root)
    assert set(videos.values()) == {234}
    alignment = {}
    for camera, filename in (("top", "top.mp4"), ("wrist", "wrist.mp4")):
        source_indices = [int(source[i]["images_1" if camera == "top" else "images_2"]["frame_idx"]) for i in (0, 117, 233)]
        alignment[camera] = sampled_psnr(
            ROOT / "dexmal_so101" / filename,
            root / "videos" / f"observation.images.{camera}" / "chunk-000" / "file-000.mp4",
            list(zip(source_indices, (0, 117, 233), strict=True)),
        )
    dataset = LeRobotDataset("local/dexmal_so101_sample", root=root, video_backend="pyav")
    for index in (0, 117, 233):
        sample = dataset[index]
        assert sample["observation.images.top"].shape == (3, 480, 640)
        assert sample["observation.images.wrist"].shape == (3, 480, 640)
    return {"status": "passed", "rows": len(rows), "videos": videos, "video_alignment": alignment, "reader_indices": [0, 117, 233]}


def validate_table30() -> dict:
    source_root = ROOT / "table30_arrange_flowers" / "episode_000188"
    source = [json.loads(line) for line in (source_root / "states" / "states.jsonl").read_text().splitlines()]
    root = ROOT / "outputs" / "table30_arrange_flowers_episode188_full"
    rows = parquet_rows(root)
    assert len(source) == 2839 and len(rows) == 2838
    for index, actual in enumerate(rows):
        current, following = source[index], source[index + 1]
        expected_state = np.asarray([*current["joint_positions"], current["gripper_width"]], np.float32)
        expected_action = np.asarray([*following["ee_positions"], following["gripper_width"]], np.float32)
        np.testing.assert_array_equal(np.asarray(actual["observation.state"], np.float32), expected_state)
        np.testing.assert_array_equal(np.asarray(actual["action"], np.float32), expected_action)
        np.testing.assert_array_equal(np.asarray(actual["source.timestamp"], np.float64), [current["timestamp"]])
    videos = video_frames(root)
    assert set(videos.values()) == {2838}
    alignment = {}
    for camera, filename in (("global", "cam_global_rgb.mp4"), ("side", "cam_side_rgb.mp4"), ("wrist", "cam_arm_rgb.mp4")):
        alignment[camera] = sampled_psnr(
            source_root / "videos" / filename,
            root / "videos" / f"observation.images.{camera}" / "chunk-000" / "file-000.mp4",
            [(0, 0), (1419, 1419), (2837, 2837)],
        )
    dataset = LeRobotDataset("local/table30_arrange_flowers_sample", root=root, video_backend="pyav")
    for index in (0, 1419, 2837):
        sample = dataset[index]
        for camera in ("global", "side", "wrist"):
            assert sample[f"observation.images.{camera}"].shape == (3, 720, 1280)
    return {"status": "passed", "source_rows": len(source), "training_rows": len(rows), "videos": videos, "video_alignment": alignment, "reader_indices": [0, 1419, 2837]}


def validate_pipeline_sample() -> dict:
    source_root = Path(r"F:\PKU\数据管线数据sample（质量闭环-抵达受限空间）\pipeline_sample_20261027_v1")
    source = [json.loads(line) for line in (source_root / "data" / "control_records.jsonl").read_text(encoding="utf-8").splitlines()]
    root = ROOT / "outputs" / "confined_reach_sample"
    rows = parquet_rows(root)
    assert len(source) == len(rows) == 300
    for actual, expected in zip(rows, source, strict=True):
        decision, outcome = expected["decision"], expected["outcome"]
        np.testing.assert_array_equal(np.asarray(actual["observation.state"], np.float32), np.asarray(decision["q_before"], np.float32))
        np.testing.assert_array_equal(np.asarray(actual["action"], np.float32), np.asarray(decision["action_task"], np.float32))
        assert bool(actual["source.collision_free"]) == bool(outcome["collision_free"])
    dataset = LeRobotDataset("local/confined_reach_sample", root=root)
    for index in (0, 150, 299):
        assert dataset[index]["observation.state"].shape == (8,)
    return {"status": "passed", "rows": len(rows), "videos": {}, "reader_indices": [0, 150, 299]}


if __name__ == "__main__":
    report = {
        "dexmal_so101_pick_cube": validate_dexmal(),
        "robochallenge_table30v2": validate_table30(),
        "local_confined_reach": validate_pipeline_sample(),
    }
    path = ROOT / "validation_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
