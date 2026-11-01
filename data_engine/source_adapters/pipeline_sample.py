from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np


def convert_package(source: Path, output: Path, repo_id: str) -> dict:
    """Convert the confined-space pipeline package without inventing images or success."""
    from lerobot.datasets import LeRobotDataset

    if output.exists():
        raise FileExistsError(output)
    records_path = source / "data" / "control_records.jsonl"
    task_path = source / "scene" / "task.json"
    quality_path = source / "quality" / "episode_quality.json"
    physics_path = source / "data" / "physics.jsonl"
    records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    physics = [json.loads(line) for line in physics_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    task = json.loads(task_path.read_text(encoding="utf-8"))
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    if len(records) != 300:
        raise ValueError(f"expected 300 control records, got {len(records)}")
    if len(physics) != 7500:
        raise ValueError(f"expected 7500 physics intervals, got {len(physics)}")
    timestamps = np.asarray([row["decision"]["simulation_time_s"] for row in records], dtype=np.float64)
    if not np.allclose(np.diff(timestamps), 0.05, atol=1e-8, rtol=0):
        raise ValueError("control records are not uniformly sampled at 20 Hz")
    if any(row["decision"]["decision_index"] != i for i, row in enumerate(records)):
        raise ValueError("decision indices are not contiguous")
    features = {
        "observation.state": {"dtype": "float32", "shape": (8,), "names": [f"source_joint_{i}" for i in range(8)]},
        "action": {"dtype": "float32", "shape": (7,), "names": ["dx_m", "dy_m", "dz_m", "rx_rad", "ry_rad", "rz_rad", "gripper_raw"]},
        "source.tcp_position_before_m": {"dtype": "float64", "shape": (3,), "names": ["x", "y", "z"]},
        "source.position_error_m": {"dtype": "float64", "shape": (1,), "names": ["position_error_m"]},
        "source.collision_free": {"dtype": "bool", "shape": (1,), "names": ["collision_free"]},
        "source.executed_joint_target": {"dtype": "float64", "shape": (8,), "names": [f"joint_{i}" for i in range(8)]},
        "source.ik_joint_target": {"dtype": "float64", "shape": (8,), "names": [f"joint_{i}" for i in range(8)]},
        "source.tcp_position_target_m": {"dtype": "float64", "shape": (3,), "names": ["x", "y", "z"]},
        "source.tcp_position_end_m": {"dtype": "float64", "shape": (3,), "names": ["x", "y", "z"]},
        "source.grip_channel": {"dtype": "float64", "shape": (1,), "names": ["grip"]},
        "source.grip_command": {"dtype": "int64", "shape": (1,), "names": ["command"]},
        "source.control_step": {"dtype": "int64", "shape": (1,), "names": ["control_step"]},
        "source.physics_slice": {"dtype": "int64", "shape": (2,), "names": ["start_inclusive", "end_exclusive"]},
    }
    dataset = LeRobotDataset.create(
        repo_id=repo_id,
        root=output,
        robot_type="widowx_source_identity",
        fps=20,
        features=features,
        use_videos=False,
    )
    instruction = task["language_instruction_zh"]
    for row in records:
        decision, outcome = row["decision"], row["outcome"]
        physics_end = physics[outcome["physics_end_index"] - 1]
        dataset.add_frame({
            "observation.state": np.asarray(decision["q_before"], dtype=np.float32),
            "action": np.asarray(decision["action_task"], dtype=np.float32),
            "source.tcp_position_before_m": np.asarray(decision["tcp_position_before_m"], dtype=np.float64),
            "source.position_error_m": np.asarray([outcome["position_error_m"]], dtype=np.float64),
            "source.collision_free": np.asarray([outcome["collision_free"]], dtype=np.bool_),
            "source.executed_joint_target": np.asarray(decision["executed_joint_target"], dtype=np.float64),
            "source.ik_joint_target": np.asarray(decision["ik_joint_target"], dtype=np.float64),
            "source.tcp_position_target_m": np.asarray(decision["tcp_position_target_m"], dtype=np.float64),
            "source.tcp_position_end_m": np.asarray(physics_end["state_end"]["tcp_position_m"], dtype=np.float64),
            "source.grip_channel": np.asarray([decision["grip_channel"]], dtype=np.float64),
            "source.grip_command": np.asarray([decision["grip_command"]], dtype=np.int64),
            "source.control_step": np.asarray([outcome["control_step"]], dtype=np.int64),
            "source.physics_slice": np.asarray([outcome["physics_start_index"], outcome["physics_end_index"]], dtype=np.int64),
            "task": instruction,
        })
    dataset.save_episode()
    dataset.finalize()
    manifest = {
        "adapter": "pipeline_confined_reach_v1",
        "source_package": source.name,
        "source_episode_id": records[0]["episode_id"],
        "training_rows": len(records),
        "fps": 20,
        "alignment": "Each row preserves the decision-before state and the policy action at the same control boundary.",
        "state_semantics": "Eight mixed-unit source joint coordinates; the source package does not provide a complete joint name/type table.",
        "action_semantics": "Original seven-dimensional policy output: world translation delta, world rotation vector, raw gripper channel.",
        "quality": {
            "kind": quality["kind"],
            "original_score": quality["original_score"],
            "training_warning": "Failed policy diagnostic; not relabeled as a successful expert demonstration.",
        },
        "missing_modalities": ["rgb", "depth", "camera calibration"],
        "missing_modalities_policy": "No placeholder frames or fabricated camera metadata were generated.",
        "physics_sidecar": {"path": "extras/source_package/data/physics.jsonl", "rows": len(physics), "native_rate_hz": 500, "control_to_physics_ratio": 25},
        "source_package": "extras/source_package",
    }
    path = output / "meta" / "semantic_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copytree(source, output / "extras" / "source_package")
    return manifest
