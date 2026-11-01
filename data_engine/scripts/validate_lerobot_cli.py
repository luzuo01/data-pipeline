from __future__ import annotations

import json
import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    required = [
        "meta/info.json",
        "meta/stats.json",
        "meta/tasks.parquet",
        "meta/episodes/chunk-000/file-000.parquet",
    ]
    missing = [item for item in required if not (root / item).exists()]
    data_files = list((root / "data").glob("**/*.parquet")) if (root / "data").exists() else []
    report = {"path": str(root), "format": "LeRobot v3", "required_files": required, "missing_files": missing, "data_files": len(data_files), "valid": not missing and bool(data_files)}
    if report["valid"]:
        from lerobot.datasets import LeRobotDataset
        info = json.loads((root / "meta/info.json").read_text(encoding="utf-8"))
        dataset = LeRobotDataset(info.get("repo_id", "local/validation"), root=root, video_backend="pyav")
        sample = dataset[0]
        report.update({"total_frames": len(dataset), "fps": info.get("fps"), "features": sorted(sample.keys()), "official_reader": "passed"})
    print(json.dumps(report, ensure_ascii=False))
