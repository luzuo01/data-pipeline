from __future__ import annotations

import argparse
from pathlib import Path

from data_engine.source_adapters.dexmal import convert_episode


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("top_video", type=Path)
    parser.add_argument("wrist_video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repo-id", default="local/dexmal_so101_sample")
    args = parser.parse_args()
    convert_episode(args.jsonl, args.top_video, args.wrist_video, args.output, args.repo_id)
