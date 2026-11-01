"""Read-only runtime diagnostics; does not contact a model or execute a robot task."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ue", action="store_true", help="Also check localhost:30010 and the UE startup map")
    args = parser.parse_args()
    modules = ["numpy", "scipy", "h5py", "fastapi", "jsonschema", "mani_skill", "sapien", "imageio", "imageio_ffmpeg"]
    report = {"python": sys.executable, "modules_discoverable": {name: importlib.util.find_spec(name) is not None for name in modules}}
    local = ROOT / "lerobot_conversion" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = os.environ.get("LEROBOT_PYTHON") or (str(local) if local.is_file() else sys.executable)
    try:
        result = subprocess.run([executable, "-c", "from lerobot.datasets import LeRobotDataset; print('reader import OK')"], capture_output=True, text=True, timeout=60)
        report["lerobot"] = {"python": executable, "ok": result.returncode == 0, "detail": (result.stdout if result.returncode == 0 else result.stderr)[-1000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        report["lerobot"] = {"python": executable, "ok": False, "detail": str(exc)}
    if args.ue:
        try:
            with socket.create_connection(("127.0.0.1", 30010), timeout=2):
                port_open = True
        except OSError:
            port_open = False
        report["ue"] = {"local_port_open": port_open, "startup_map_present": (ROOT / "EnvDreamer/EnvGen/Content/EnvGen/Maps/ThirdPersonMap.umap").is_file(), "note": "TCP reachability is not proof of a usable Remote Control actor or a render."}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(report["modules_discoverable"].values()) and report["lerobot"]["ok"] and (not args.ue or all(report["ue"][key] for key in ("local_port_open", "startup_map_present"))) else 1


if __name__ == "__main__":
    raise SystemExit(main())
