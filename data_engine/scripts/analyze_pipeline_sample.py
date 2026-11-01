from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def analyze(root: Path, output: Path | None = None) -> dict[str, Any]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "analysis_report.json":
            files.append({"path": str(path.relative_to(root)).replace("\\", "/"), "bytes": path.stat().st_size, "extension": path.suffix.lower(), "sha256": sha256(path)})
    records = jsonl(root / "data/control_records.jsonl")
    decisions = jsonl(root / "data/decisions.jsonl")
    outcomes = jsonl(root / "data/control_outcomes.jsonl")
    physics = jsonl(root / "data/physics.jsonl")
    contacts = jsonl(root / "data/contact_observations.jsonl")
    preview_rows = list(csv.DictReader((root / "data/trajectory_preview.csv").open(encoding="utf-8-sig", newline="")))
    task = json.loads((root / "scene/task.json").read_text(encoding="utf-8"))
    quality = json.loads((root / "quality/episode_quality.json").read_text(encoding="utf-8"))
    first, last = records[0], records[-1]
    timestamps = [float(row["decision"]["simulation_time_s"]) for row in records]
    physics_times = [float(row["t_start_s"]) for row in physics]
    collision_intervals = sum(bool(row["collision"]) for row in physics)
    return {
        "schema": "pipeline-confined-reach-analysis-v1",
        "source_package": root.name,
        "file_inventory": {"count": len(files), "by_extension": dict(Counter(item["extension"] or "<none>" for item in files)), "files": files},
        "episode": {
            "episode_id": first["episode_id"],
            "control_rows": len(records),
            "decision_rows": len(decisions),
            "outcome_rows": len(outcomes),
            "preview_rows": len(preview_rows),
            "control_rate_hz": 20,
            "control_dt_s": 0.05,
            "time_start_s": timestamps[0],
            "time_end_s": timestamps[-1],
            "state_shape": [len(first["decision"]["q_before"])],
            "policy_action_shape": [len(first["decision"]["action_task"])],
            "executed_joint_target_shape": [len(first["decision"]["executed_joint_target"])],
            "tcp_pose_shape": [3, 3],
            "action_semantics": "world_xyz_m + world_rotvec_rad + raw gripper channel; original policy output",
            "state_semantics": "decision-before q_before; 8 mixed-unit coordinates, source does not provide complete joint type table",
        },
        "physics": {
            "rows": len(physics),
            "rate_hz": 500,
            "dt_s": 0.002,
            "control_to_physics_ratio": 25,
            "time_start_s": physics_times[0],
            "time_end_s": float(physics[-1]["t_end_s"]),
            "collision_flagged_intervals": collision_intervals,
            "contact_observation_rows": len(contacts),
            "torque_vector_shape": [len(physics[0]["actuator_joint_torque_nm"])],
            "sidecar_policy": "retain physics.jsonl and contact_observations.jsonl; do not mix into 20 Hz training frames",
        },
        "quality": {
            "kind": quality["kind"],
            "success": quality["original_score"]["success"],
            "min_position_error_m": quality["min_control_boundary_position_error_m"],
            "final_position_error_m": quality["final_position_error_m"],
            "collision_free_false_control_intervals": quality["collision_free_false_control_intervals"],
            "qualification": quality["qualification"],
            "grasp_loss": quality["grasp_loss"],
        },
        "modalities": {"rgb": False, "depth": False, "segmentation": False, "camera_calibration": False, "proprioception": True, "policy_action": True, "physics": True, "contacts": True, "scene_metadata": True},
        "lerobot_mapping": {
            "observation.state": "decision.q_before -> float32[8]",
            "action": "decision.action_task -> float32[7]",
            "timestamp": "decision.simulation_time_s -> 20 Hz",
            "source.executed_joint_target": "decision.executed_joint_target -> float64[8]",
            "source.ik_joint_target": "decision.ik_joint_target -> float64[8]",
            "source.tcp_position_target_m": "decision.tcp_position_target_m -> float64[3]",
            "source.tcp_position_end_m": "physics[state_end].tcp_position_m -> float64[3]",
            "source.position_error_m": "outcome.position_error_m -> float64[1]",
            "source.collision_free": "outcome.collision_free -> bool[1]",
            "source.physics_slice": "outcome.physics_start_index/end_index -> int64[2]",
            "sidecar": "complete source package under extras/source_package",
        },
        "training_qualification": "diagnostic_failed_policy; preserve source success=0 and do not add to successful expert pool",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(args.root.resolve(), args.output)
    target = args.output or (args.root / "analysis_report.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
