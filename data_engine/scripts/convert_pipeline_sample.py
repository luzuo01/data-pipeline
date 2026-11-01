from __future__ import annotations

import argparse
from pathlib import Path

from data_engine.source_adapters.pipeline_sample import convert_package


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repo-id", default="local/confined_reach_sample")
    args = parser.parse_args()
    convert_package(args.source, args.output, args.repo_id)
