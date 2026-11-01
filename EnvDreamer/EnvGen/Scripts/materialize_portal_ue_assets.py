"""Convert the six PORTAL-Trace robosuite visual models to UE-importable FBX.

PORTAL-Trace itself contains MuJoCo/robosuite assets, not Unreal ``.uasset``
files.  This utility reads the native robosuite robot.xml files, imports the
referenced visual OBJ meshes with Blender, and writes one static FBX per robot.
The result is a visual asset for UE scene previews; it is intentionally not an
articulated UE physics actor.  Keep the original XML runtime for simulation.

Usage (from EnvGen):
    D:\\APP\\Blender\\blender.exe --background --python Scripts/materialize_portal_ue_assets.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT.parent / "ManiSkillGen" / "assets" / "portal_trace_robots.json"
OUT = ROOT / "Content" / "Robots" / "PORTALTrace"
PORTAL_ROOT = Path(os.getenv("PORTAL_TRACE_ROOT", ROOT.parent.parent / "_research_tmp" / "PORTAL-Trace"))
DEFAULT_SITE = ROOT.parent / ".portal_env" / "Lib" / "site-packages"


def _robot_root(robot: str) -> Path:
    slug = {"Panda": "panda", "Sawyer": "sawyer", "IIWA": "iiwa", "Kinova3": "kinova3", "XArm7": "xarm7", "UR5e": "ur5e"}[robot]
    configured = os.getenv("ROBOSUITE_ASSET_ROOT")
    base = Path(configured) if configured else DEFAULT_SITE / "robosuite" / "models" / "assets" / "robots"
    return base / slug


def _visual_meshes(root: Path) -> list[Path]:
    xml_path = root / "robot.xml"
    tree = ET.parse(xml_path)
    refs = [e.attrib.get("file", "") for e in tree.iter("mesh")]
    # The native models use either visual OBJ files, visual STL files, or a
    # single STL mesh for each link. Prefer the visual declarations and never
    # combine collision geometry when a visual variant exists.
    visual = [ref for ref in refs if ref.lower().endswith(".obj") and "_vis" in ref.lower()]
    if not visual:
        visual = [ref for ref in refs if ref.lower().endswith(".obj")]
    if not visual:
        visual = [ref for ref in refs if ref.lower().endswith(".stl") and "_vis" in ref.lower()]
    if not visual:
        visual = [ref for ref in refs if ref.lower().endswith(".stl") and "collision" not in ref.lower()]
    result: list[Path] = []
    for ref in visual:
        path = (root / ref).resolve()
        if path.is_file() and path not in result:
            result.append(path)
    return result


def _run_blender_import() -> None:
    """Executed inside Blender, where bpy is available."""
    import bpy  # type: ignore

    robots = json.loads(MANIFEST.read_text(encoding="utf-8"))["robots"]
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"source": str(PORTAL_ROOT), "robots": {}}
    for robot in robots:
        root = _robot_root(robot)
        meshes = _visual_meshes(root)
        if not meshes:
            raise RuntimeError(f"No visual meshes found for {robot}: {root}")
        bpy.ops.wm.read_factory_settings(use_empty=True)
        imported = []
        for mesh in meshes:
            before = set(bpy.data.objects)
            if mesh.suffix.lower() == ".obj":
                bpy.ops.wm.obj_import(filepath=str(mesh), use_split_objects=False, use_split_groups=False)
            else:
                bpy.ops.wm.stl_import(filepath=str(mesh))
            imported.extend(obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH")
        if not imported:
            raise RuntimeError(f"Blender imported no mesh objects for {robot}")
        # robosuite uses metres; UE's FBX importer treats the exported model
        # as centimetres when the object scale is 100.
        for obj in imported:
            obj.scale = (100.0, 100.0, 100.0)
        bpy.context.view_layer.objects.active = imported[0]
        for obj in imported:
            obj.select_set(True)
        out = OUT / robot / f"{robot}.fbx"
        out.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.export_scene.fbx(filepath=str(out), use_selection=True, object_types={"MESH"}, apply_scale_options="FBX_SCALE_ALL")
        report["robots"][robot] = {"fbx": str(out), "source_mesh_count": len(meshes), "imported_object_count": len(imported)}
    (OUT / "materialization_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    # Blender provides bpy; the fallback keeps accidental normal-Python
    # execution useful by printing the source paths that would be converted.
    try:
        import bpy  # type: ignore
    except ImportError:
        for name in json.loads(MANIFEST.read_text(encoding="utf-8"))["robots"]:
            print(name, _robot_root(name), len(_visual_meshes(_robot_root(name))))
    else:
        _run_blender_import()
