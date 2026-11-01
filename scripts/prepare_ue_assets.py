"""Restore upstream UE maps/plugins at a fixed commit without overwriting local files."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/kswain98/EnvDreamer.git"
REVISION = "028027e2ce24e59dce8a59f3a77aa9848409a6e6"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Existing EnvDreamer Git checkout; committed objects only")
    args = parser.parse_args()
    source = (args.source or ROOT / "external" / "EnvDreamer").resolve()
    if not source.exists():
        if args.source:
            parser.error(f"Source does not exist: {source}")
        source.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", URL, str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "cat-file", "-e", REVISION + "^{commit}"], check=True)
    copied = skipped = 0
    with tempfile.TemporaryDirectory(prefix="data-pipeline-ue-") as temporary:
        archive = Path(temporary) / "assets.tar"
        subprocess.run(["git", "-C", str(source), "archive", "--format=tar",
                        "--output", str(archive), REVISION, "EnvGen/Content", "EnvGen/Plugins"], check=True)
        with tarfile.open(archive) as tar:
            target_root = (ROOT / "EnvDreamer").resolve()
            for member in tar:
                if not member.isfile():
                    continue
                relative = Path(member.name)
                if relative.parts[:2] not in {("EnvGen", "Content"), ("EnvGen", "Plugins")}:
                    raise ValueError(f"Unexpected archive path: {member.name}")
                target = (target_root / relative).resolve()
                if not target.is_relative_to(target_root):
                    raise ValueError(f"Unsafe archive path: {member.name}")
                if target.exists():
                    skipped += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                copied += 1
    print(f"UE assets restored at {REVISION}: {copied} copied, {skipped} already present")
    print("Build/open EnvDreamer/EnvGen/environment.uproject with UE 5.6 and set RC_OBJECT_PATH if needed.")


if __name__ == "__main__":
    main()
