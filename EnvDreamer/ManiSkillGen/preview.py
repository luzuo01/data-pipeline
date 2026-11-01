"""Preview rendering for generated scenes.

When ManiSkill3 returns an RGB frame it is written directly.  The fallback
renderer is intentionally simple and deterministic, but still exposes the
robot, table, objects and target in a useful inspection image on headless
machines.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping


def _rgba(color: Any) -> tuple[int, int, int, int]:
    values = list(color) if isinstance(color, (list, tuple)) else [0.7, 0.7, 0.7, 1.0]
    values = (values + [1.0] * 4)[:4]
    return tuple(max(0, min(255, int(float(value) * 255))) for value in values)  # type: ignore[return-value]


def _software_frame(spec: Mapping[str, Any], camera: str = "front"):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - dependency is listed as optional
        raise RuntimeError("Pillow is required for software previews") from exc
    cameras = spec.get("cameras", {})
    config = cameras.get(camera, next(iter(cameras.values()), {}))
    resolution = config.get("resolution", [640, 480]) if isinstance(config, Mapping) else [640, 480]
    width, height = int(resolution[0]), int(resolution[1])
    image = Image.new("RGBA", (width, height), (232, 239, 247, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    workspace = spec.get("scene", {}).get("workspace", {})
    size = workspace.get("size", [0.8, 0.8, 0.5])
    table_height = float(workspace.get("table_height", 0.75))
    scale = min(width / (float(size[0]) * 1.25), height / (float(size[1]) * 1.35))
    origin = (width * 0.50, height * 0.70)

    def project(point):
        x, y, z = [float(v) for v in point]
        # A small isometric projection keeps the scene legible without a 3-D dependency.
        return (origin[0] + (x - y) * scale, origin[1] - (x + y) * scale * 0.36 - (z - table_height) * scale * 0.9)

    # tabletop as a parallelogram
    hx, hy = float(size[0]) / 2, float(size[1]) / 2
    corners = [project((-hx, -hy, table_height)), project((hx, -hy, table_height)), project((hx, hy, table_height)), project((-hx, hy, table_height))]
    draw.polygon(corners, fill=(180, 148, 105, 255), outline=(95, 70, 45, 255))
    for step in range(1, 6):
        t = step / 6
        draw.line([project((-hx + 2 * hx * t, -hy, table_height)), project((-hx + 2 * hx * t, hy, table_height))], fill=(145, 112, 78, 70), width=1)

    # robot base and arm (schematic but clearly marked)
    rx, ry, rz = spec.get("robot", {}).get("base_pose", [0, 0, 0])
    base = project((rx, ry, table_height))
    elbow = project((rx + 0.10, ry, table_height + 0.30))
    wrist = project((rx + 0.03, ry, table_height + 0.42))
    draw.ellipse((base[0] - 18, base[1] - 8, base[0] + 18, base[1] + 8), fill=(70, 76, 87, 255), outline=(30, 35, 45, 255))
    draw.line([base, elbow, wrist], fill=(68, 74, 88, 255), width=12, joint="curve")
    draw.ellipse((wrist[0] - 7, wrist[1] - 7, wrist[0] + 7, wrist[1] + 7), fill=(210, 215, 225, 255), outline=(40, 45, 55, 255))
    draw.text((base[0] - 34, base[1] + 13), "Franka", fill=(30, 35, 45, 255))

    for obj in spec.get("scene", {}).get("objects", []):
        if not isinstance(obj, Mapping):
            continue
        pos = project(obj.get("pose", [0, 0, table_height]))
        dims = obj.get("size", [0.04, 0.04, 0.04])
        radius = max(4, int(max(float(dims[0]), float(dims[1])) * scale * 0.55))
        fill = _rgba(obj.get("color", [0.7, 0.7, 0.7, 1]))
        shape = obj.get("shape", "box")
        if obj.get("asset") == "primitive_target" or shape == "target":
            draw.ellipse((pos[0] - radius * 1.5, pos[1] - radius * 0.6, pos[0] + radius * 1.5, pos[1] + radius * 0.6), fill=fill, outline=(20, 40, 140, 255), width=2)
            draw.ellipse((pos[0] - radius * 0.6, pos[1] - radius * 0.25, pos[0] + radius * 0.6, pos[1] + radius * 0.25), outline=(255, 255, 255, 230), width=2)
        elif shape == "sphere":
            draw.ellipse((pos[0] - radius, pos[1] - radius, pos[0] + radius, pos[1] + radius), fill=fill, outline=(40, 40, 40, 255))
        else:
            draw.rounded_rectangle((pos[0] - radius, pos[1] - radius, pos[0] + radius, pos[1] + radius), radius=3, fill=fill, outline=(40, 40, 40, 255))
        draw.text((pos[0] + radius + 3, pos[1] - 7), str(obj.get("id", "object")), fill=(25, 30, 40, 255))

    draw.rectangle((12, 12, min(width - 12, 340), 48), fill=(255, 255, 255, 220), outline=(100, 110, 125, 255))
    draw.text((22, 24), f"ManiSkill3 preview  |  {spec.get('task', {}).get('template', 'task')}", fill=(25, 30, 40, 255))
    return image


def save_preview(spec: Mapping[str, Any], output_path: str | Path, *, camera: str = "front", frame: Any = None) -> str:
    """Save a ManiSkill RGB frame or a software fallback as PNG."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if frame is None:
        image = _software_frame(spec, camera)
    else:
        try:
            from PIL import Image
            if hasattr(frame, "ndim") and int(frame.ndim) == 4:
                frame = frame[0]
            image = Image.fromarray(frame) if not hasattr(frame, "save") else frame
            if image.mode != "RGBA":
                image = image.convert("RGBA")
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Pillow is required to save previews") from exc
    image.save(path, format="PNG")
    return str(path)
