"""Resolve the six PORTAL-Trace robot assets.

PORTAL-Trace does not vendor mesh files in its repository.  Its registry
identifies the native robosuite MJCF family and checksum; the actual XML and
meshes are installed with robosuite in the isolated PORTAL runtime.  This
module turns that registry entry into a concrete asset directory without
mixing the MuJoCo process into the ManiSkill process.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "assets" / "portal_trace_robots.json"


def portal_robot_assets() -> dict[str, dict[str, Any]]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    robots = data.get("robots", {})
    if not isinstance(robots, dict):
        raise ValueError("portal_trace_robots.json has no robots object")
    return robots


def portal_robot_asset(robot: str) -> dict[str, Any]:
    assets = portal_robot_assets()
    try:
        return dict(assets[robot])
    except KeyError:
        raise KeyError(f"unknown PORTAL-Trace robot asset {robot!r}; expected {tuple(assets)}") from None


def _python_site_packages(python: str | os.PathLike[str] | None) -> Path | None:
    if python:
        executable = Path(python)
        if os.name == "nt":
            candidate = executable.parent.parent / "Lib" / "site-packages"
        else:
            candidate = executable.parent.parent / "lib"
            versions = sorted(candidate.glob("python*/site-packages"))
            candidate = versions[-1] if versions else candidate
        if candidate.is_dir():
            return candidate
    candidates = [
        ROOT.parent / ".portal_env" / "Lib" / "site-packages",
        ROOT.parent / ".portal_uv_env" / "Lib" / "site-packages",
    ]
    return next((path for path in candidates if path.is_dir()), None)


def resolve_portal_robot_asset(robot: str, *, python: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Return the manifest plus the installed robosuite XML and mesh root."""
    entry = portal_robot_asset(robot)
    site_packages = _python_site_packages(python or os.getenv("PORTAL_TRACE_PYTHON"))
    if site_packages is None:
        return {**entry, "available": False, "mjcf_path": None, "asset_directory": None}
    asset_root = site_packages / "robosuite" / "models" / "assets" / "robots" / Path(entry["asset_directory_relative_path"]).name
    mjcf = asset_root / "robot.xml"
    return {**entry, "available": mjcf.is_file(), "mjcf_path": str(mjcf) if mjcf.is_file() else None, "asset_directory": str(asset_root) if asset_root.is_dir() else None}


def resolve_all_portal_robot_assets(*, python: str | os.PathLike[str] | None = None) -> dict[str, dict[str, Any]]:
    return {name: resolve_portal_robot_asset(name, python=python) for name in portal_robot_assets()}
