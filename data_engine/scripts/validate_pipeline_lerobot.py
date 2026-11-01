from __future__ import annotations

"""End-to-end validation for the confined-reach LeRobot v3 export.

The validator deliberately checks both the public LeRobot representation and
the copied source package.  Float fields are compared with a tight tolerance
because LeRobot stores the training state/action as float32.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from lerobot.datasets import LeRobotDataset


FLOAT_FEATURES = {
    "observation.state",
    "action",
    "source.tcp_position_before_m",
    "source.position_error_m",
    "source.executed_joint_target",
    "source.ik_joint_target",
    "source.tcp_position_target_m",
    "source.tcp_position_end_m",
    "source.grip_channel",
}
EXACT_FEATURES = {
    "source.collision_free",
    "source.grip_command",
    "source.control_step",
    "source.physics_slice",
}


def _rows(root: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted((root / "data").glob("**/*.parquet")):
        rows.extend(pq.read_table(path).to_pylist())
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_source_manifest(source_root: Path) -> dict:
    manifest_path = source_root / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    verified = 0
    for item in manifest["files"]:
        path = source_root / item["path"]
        if not path.is_file():
            failures.append(f"missing: {item['path']}")
            continue
        if path.stat().st_size != item["bytes"]:
            failures.append(f"size: {item['path']}")
            continue
        if _sha256(path) != item["sha256"]:
            failures.append(f"sha256: {item['path']}")
            continue
        verified += 1
    if failures:
        raise AssertionError(f"source MANIFEST verification failed: {failures[:5]}")
    return {"status": "passed", "manifest_files": len(manifest["files"]), "verified_files": verified}


def validate(source_root: Path, output_root: Path, repo_id: str) -> dict:
    required = [
        output_root / "meta" / "info.json",
        output_root / "meta" / "stats.json",
        output_root / "meta" / "tasks.parquet",
        output_root / "meta" / "semantic_manifest.json",
        output_root / "data" / "chunk-000" / "file-000.parquet",
        output_root / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
        output_root / "extras" / "source_package" / "MANIFEST.json",
        output_root / "extras" / "source_package" / "data" / "physics.jsonl",
        output_root / "extras" / "source_package" / "data" / "contact_observations.jsonl",
    ]
    missing = [str(path.relative_to(output_root)) for path in required if not path.is_file()]
    if missing:
        raise AssertionError(f"missing LeRobot v3 files: {missing}")

    source_records = [
        json.loads(line)
        for line in (source_root / "data" / "control_records.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    physics = [
        json.loads(line)
        for line in (source_root / "data" / "physics.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = _rows(output_root)
    assert len(source_records) == 300, len(source_records)
    assert len(physics) == 7500, len(physics)
    assert len(rows) == 300, len(rows)

    # This is the official LeRobot reader check, rather than a parquet-only check.
    dataset = LeRobotDataset(repo_id, root=output_root, video_backend="pyav")
    assert len(dataset) == 300, len(dataset)
    assert dataset.num_episodes == 1, dataset.num_episodes
    expected_features = set(FLOAT_FEATURES | EXACT_FEATURES | {"task", "timestamp", "frame_index", "episode_index", "index"})
    assert FLOAT_FEATURES | EXACT_FEATURES <= set(dataset.features), dataset.features.keys()

    for index, (actual, expected) in enumerate(zip(rows, source_records, strict=True)):
        decision, outcome = expected["decision"], expected["outcome"]
        expected_values = {
            "observation.state": np.asarray(decision["q_before"], dtype=np.float32),
            "action": np.asarray(decision["action_task"], dtype=np.float32),
            "source.tcp_position_before_m": np.asarray(decision["tcp_position_before_m"], dtype=np.float64),
            "source.position_error_m": np.asarray([outcome["position_error_m"]], dtype=np.float64),
            "source.collision_free": np.asarray([outcome["collision_free"]], dtype=np.bool_),
            "source.executed_joint_target": np.asarray(decision["executed_joint_target"], dtype=np.float64),
            "source.ik_joint_target": np.asarray(decision["ik_joint_target"], dtype=np.float64),
            "source.tcp_position_target_m": np.asarray(decision["tcp_position_target_m"], dtype=np.float64),
            "source.tcp_position_end_m": np.asarray(physics[outcome["physics_end_index"] - 1]["state_end"]["tcp_position_m"], dtype=np.float64),
            "source.grip_channel": np.asarray([decision["grip_channel"]], dtype=np.float64),
            "source.grip_command": np.asarray([decision["grip_command"]], dtype=np.int64),
            "source.control_step": np.asarray([outcome["control_step"]], dtype=np.int64),
            "source.physics_slice": np.asarray([outcome["physics_start_index"], outcome["physics_end_index"]], dtype=np.int64),
        }
        for key, expected_value in expected_values.items():
            actual_value = np.asarray(actual[key])
            if key in FLOAT_FEATURES:
                np.testing.assert_allclose(actual_value, expected_value, rtol=1e-6, atol=1e-6, err_msg=f"{key} at row {index}")
            else:
                np.testing.assert_array_equal(actual_value, expected_value, err_msg=f"{key} at row {index}")
        assert actual["task"] == expected["task"] if "task" in expected else True
        assert actual["source.physics_slice"][1] - actual["source.physics_slice"][0] == 25

    # Check the official reader at the boundaries too, including tensor shapes.
    reader_checks = []
    for index in (0, 150, 299):
        sample = dataset[index]
        assert tuple(sample["observation.state"].shape) == (8,)
        assert tuple(sample["action"].shape) == (7,)
        reader_checks.append(index)

    manifest = json.loads((output_root / "meta" / "semantic_manifest.json").read_text(encoding="utf-8"))
    assert manifest["training_rows"] == 300
    assert manifest["fps"] == 20
    assert manifest["physics_sidecar"]["rows"] == 7500
    assert manifest["quality"]["kind"] == "failed_policy_diagnostic_not_expert_demonstration"
    assert "rgb" in manifest["missing_modalities"]

    source_manifest = _assert_source_manifest(output_root / "extras" / "source_package")
    report = {
        "status": "passed",
        "output_root": str(output_root),
        "repo_id": repo_id,
        "official_reader": {"status": "passed", "rows": len(dataset), "num_episodes": dataset.num_episodes, "reader_indices": reader_checks},
        "row_alignment": {"status": "passed", "control_rows": len(rows), "float_tolerance": {"rtol": 1e-6, "atol": 1e-6}, "physics_slice_length": 25},
        "sidecars": {"physics_rows": len(physics), "contact_observations": sum(1 for _ in (output_root / "extras" / "source_package" / "data" / "contact_observations.jsonl").open(encoding="utf-8"))},
        "source_manifest": source_manifest,
        "quality": manifest["quality"],
        "missing_modalities": manifest["missing_modalities"],
    }
    (output_root / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repo-id", default="local/confined_reach_lerobot_v3")
    args = parser.parse_args()
    print(json.dumps(validate(args.source, args.output, args.repo_id), ensure_ascii=False, indent=2))
