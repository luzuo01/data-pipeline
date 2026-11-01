from __future__ import annotations

import argparse
from pathlib import Path

from data_engine.source_adapters.table30 import convert_arx5_episode


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("episode", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repo-id", default="local/table30_arrange_flowers_sample")
    parser.add_argument("--prompt", default="Put the 4 flowers into the vase.")
    parser.add_argument("--max-frames", type=int)
    args = parser.parse_args()
    convert_arx5_episode(args.episode, args.output, args.repo_id, args.prompt, args.max_frames)
