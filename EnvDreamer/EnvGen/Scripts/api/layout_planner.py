#!/usr/bin/env python3
"""
Layout Planner
- Uses an LLM to generate a home layout JSON (rooms, bounds, colors, cameras)
- Validates schema and optionally writes to Config for live rebuilding

This mirrors the approach in api/llm_planner.py but targets layout files
instead of RC function plans.
"""

from __future__ import annotations

import json
import base64
import os
from typing import Any, Dict, List, Optional, Tuple

import openai

from Scripts.core.config import OPENAI_BASE_URL, OPENAI_MODEL, OPENAI_API_KEY
from Scripts.utils.asset_registry import list_assets


_DEFAULT_MODEL = os.getenv("LAYOUT_MODEL") or OPENAI_MODEL or "gpt-4o-mini"

def _get_client():
    # Asset demos and screenshots should work without an LLM credential.
    if not OPENAI_API_KEY:
        raise RuntimeError("自然语言生成需要在启动服务前配置 OPENAI_API_KEY；机械臂示例和截图无需 API key。")
    return openai.OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


def _schema_spec() -> str:
    return (
        "Return EXACTLY this JSON object with no extra commentary:"
        "\n{"
        "\n  \"name\": string,"
        "\n  \"description\": string,"
        "\n  \"units\": \"centimeters\","
        "\n  \"rooms\": {"
        "\n    <room_key>: {"
        "\n      \"name\": string,"
        "\n      \"type\": one of [\"living\", \"kitchen\", \"bathroom\", \"bedroom\", \"circulation\", \"storage\", \"dining\", \"office\"],"
        "\n      \"purpose\": string,"
        "\n      \"bounds\": { \"min\": [int,int,int], \"max\": [int,int,int] },"
        "\n      \"center\": [int,int,int],"
        "\n      \"camera\": { \"position\": [int,int,int], \"rotation\": [int,int,int], \"description\": string },"
        "\n      \"floor_color\": [float,float,float,float],"
        "\n      \"floor_texture\": string,  // Optional: texture for floor"
        "\n      \"wall_color\": [float,float,float,float],"
        "\n      \"wall_texture\": string,  // Optional: texture for walls"
        "\n      \"items\": ["
        "\n        {"
        "\n          \"id\": string,"
        "\n          \"asset\": string,"
        "\n          \"position\": [int,int,int],"
        "\n          \"rotation\": [int,int,int],"
        "\n          \"scale\": [float,float,float],"
        "\n          \"color\": [float,float,float,float],  // Optional: solid color (default white)"
        "\n          \"texture\": string  // Optional: texture path or shortcut (wood, metal, concrete, brick, stone)"
        "\n        }, ..."
        "\n      ]"
        "\n    }, ..."
        "\n  }"
        "\n}"
    )


def _control_asset_hint(max_entries: int = 40) -> str:
    """Generate a short list of control blueprint shortcuts for the LLM."""
    try:
        assets = list_assets()
    except Exception:
        return ""

    entries: List[tuple[str, str]] = []
    for name, path in assets.items():
        lname = name.lower()
        if (
            lname.startswith(("ctrl_", "bp_"))
            or "chair" in lname
            or "table" in lname
            or "room" in lname
            or "franka" in lname
            or "panda" in lname
            or "robot" in lname
        ):
            entries.append((name, path))
    if not entries:
        return ""

    entries.sort(key=lambda item: (item[1] != "franka_panda", item[0]))
    trimmed = entries[:max_entries]
    lines = [f"{name} -> {path}" for name, path in trimmed]
    return "Control asset shortcuts (name -> path):\n- " + "\n- ".join(lines)


def _build_system_prompt(current_layout: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """Builds a multimodal-like message list with current layout context."""
    sys_text = (
        "You are an expert interior layout generator for an Unreal-based tool. "
        "You edit an apartment/home layout as JSON. "
        "Follow these constraints strictly:"\
        "\n- Units are centimeters."
        "\n- Snap all X/Y bounds to 100cm grid; Z min is 0, Z max is wall height (e.g., 300)."
        "\n- Each room's bounds define an axis-aligned rectangular prism."
        "\n- Set center to midpoint of bounds (min/max)."
        "\n- If user asks to add rooms, create new keys; if remove, omit those rooms entirely."
        "\n- Keep existing rooms unchanged unless the prompt requests modifications."
        "\n- Preserve existing room items unless the user asks to move, remove, or replace them."
        "\n- Update floor_color and wall_color when the user asks for color changes."
        "\n- Rooms can have textures: Add \"floor_texture\" or \"wall_texture\" fields for realistic surfaces."
        "\n- When the user requests furniture/props, populate the room \"items\" list with objects describing each asset."
        "\n- If a room has no items, set \"items\": [] to make that explicit."
        "\n- Use asset registry short names (e.g., bp_table, bp_chair, store_table, store_chair) when possible."
        "\n- Control blueprints live under /Game/Control/... so names like ctrl_chair_chair_a, ctrl_table_table_a, bp_chair, BP_Table, BP_Sofa_A are valid."
        "\n- Keep item positions inside the room bounds. Floor furniture rests on the floor; tabletop props and robot bases rest on the table top. Respect each mesh pivot."
        "\n- Provide sensible rotation (Pitch,Yaw,Roll degrees) and per-axis scale ([sx,sy,sz]) values; honor user-specified transforms."
        "\n- When the user requests chair/prop placement, specify the exact asset plus any offsets/rotations/scale to match their description."
        "\n- When the user asks to resize or move a room, adjust its bounds.min/bounds.max on the 100cm grid and update the derived center."
        "\n- Colors are RGBA floats 0..1; default to [1.0,1.0,1.0,1.0] if not specified."
        "\n- Asset examples: bp_table, bp_chair, store_table, store_chair, franka_panda (see Config/asset_registry.json)."
        "\n- For a tabletop manipulation scene, add one item with asset franka_panda; it is a kinematic Panda actor with seven joint controls and a gripper."
        "\n- franka_panda is already life size: use scale [1,1,1]. Scale is dimensionless, not centimeters. Robot controls use radians; gripper opening uses meters."
        "\n- For a 4 cm manipulation cube use /Engine/BasicShapes/Cube.Cube with scale [0.04,0.04,0.04], centered 2 cm above the tabletop."
        "\n- Use descriptive cameras for each room."
        "\n- Ensure room adjacencies are consistent (shared edges align exactly)."
        "\n\nMATERIAL OPTIONS:"
        "\n- ITEMS: Choose color OR texture per item"
        "\n  * Solid color: Set \"color\" field as RGBA [r,g,b,a]"
        "\n  * Texture: Set \"texture\" field with path or shortcut"
        "\n- ROOMS: Add floor_texture and/or wall_texture for realistic surfaces"
        "\n  * Example: \"floor_texture\": \"wood\" for wooden floors"
        "\n  * Example: \"wall_texture\": \"brick\" for brick walls"
        "\n- TEXTURE SHORTCUTS: See Config/texture_registry.json for available shortcuts"
        "\n  Common: wood, wood_walnut, metal, concrete, brick, stone, cobblestone"
        "\n- FULL PATHS: Can also use full Unreal paths like \"/Game/StarterContent/Textures/T_Wood_Oak_D.T_Wood_Oak_D\""
        "\n- Textures override colors and provide realistic materials"
        "\n\nADVANCED FEATURES:"
        "\n- Object Query: Reference RC_GetActorInfo(object_id) if you need to query current state from Unreal"
        "\n  (rarely needed since current_layout already provides all details)"
    )

    messages: List[Dict[str, Any]] = [{"role": "system", "content": sys_text}]

    if current_layout:
        messages.append({
            "role": "system",
            "content": json.dumps({
                "current_layout": current_layout,
                "note": "If prompt is incremental, start from this and apply only requested changes."
            }),
        })

    messages.append({
        "role": "system",
        "content": _schema_spec(),
    })

    asset_hint = _control_asset_hint()
    if asset_hint:
        messages.append({
            "role": "system",
            "content": asset_hint,
        })

    return messages


def generate_layout_from_prompt(prompt: str, current_layout: Dict[str, Any] | None = None, validate_output: bool = True) -> Dict[str, Any]:
    """Call LLM to produce a full layout JSON based on the prompt and current layout."""
    messages = _build_system_prompt(current_layout)
    messages.append({"role": "user", "content": prompt})

    try:
        resp = _get_client().chat.completions.create(
            model=_DEFAULT_MODEL,
            temperature=0.15,
            max_tokens=4000,
            messages=messages,
            response_format={"type": "json_object"},
            timeout=60,
        )
    except Exception as e:
        raise RuntimeError(f"Layout planner connection failed: {e}")

    content = (resp.choices[0].message.content or "").strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Layout planner returned non-JSON: {e}")

    if validate_output:
        ok, errs = validate_layout_schema(data)
        if not ok:
            raise RuntimeError(f"Generated layout failed validation: {errs}")

    return data


def validate_layout_schema(layout: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Lightweight schema validation for layout JSON."""
    errs: List[str] = []

    def _len3(a) -> bool:
        return isinstance(a, list) and len(a) == 3

    def _len4(a) -> bool:
        return isinstance(a, list) and len(a) == 4

    if not isinstance(layout, dict):
        return False, ["Top-level layout must be an object"]

    for key in ("name", "description", "units", "rooms"):
        if key not in layout:
            errs.append(f"Missing top-level key: {key}")

    if layout.get("units") != "centimeters":
        errs.append("units must be 'centimeters'")

    rooms = layout.get("rooms")
    if not isinstance(rooms, dict) or not rooms:
        errs.append("rooms must be a non-empty object")
    else:
        for rkey, rdata in rooms.items():
            if not isinstance(rdata, dict):
                errs.append(f"room {rkey}: must be object")
                continue
            for rk in ("name", "type", "purpose", "bounds", "center", "camera", "floor_color", "wall_color"):
                if rk not in rdata:
                    errs.append(f"room {rkey}: missing {rk}")

            bounds = rdata.get("bounds", {})
            if not isinstance(bounds, dict) or "min" not in bounds or "max" not in bounds:
                errs.append(f"room {rkey}: bounds.min/max required")
            else:
                if not _len3(bounds.get("min", [])) or not _len3(bounds.get("max", [])):
                    errs.append(f"room {rkey}: bounds arrays must be length 3")

            if not _len3(rdata.get("center", [])):
                errs.append(f"room {rkey}: center must be length 3 array")

            cam = rdata.get("camera", {})
            if not isinstance(cam, dict) or not _len3(cam.get("position", [])) or not _len3(cam.get("rotation", [])):
                errs.append(f"room {rkey}: camera.position/rotation must be length 3 arrays")

            if not _len4(rdata.get("floor_color", [])) or not _len4(rdata.get("wall_color", [])):
                errs.append(f"room {rkey}: floor_color/wall_color must be length 4 arrays")

            items = rdata.get("items")
            if items is not None:
                if not isinstance(items, list):
                    errs.append(f"room {rkey}: items must be a list when present")
                else:
                    for idx, item in enumerate(items):
                        if not isinstance(item, dict):
                            errs.append(f"room {rkey} items[{idx}]: must be object")
                            continue
                        for key in ("id", "asset", "position"):
                            if key not in item:
                                errs.append(f"room {rkey} items[{idx}]: missing {key}")
                        pos = item.get("position")
                        if pos is not None and not _len3(pos):
                            errs.append(f"room {rkey} items[{idx}]: position must be length-3 array")
                        rot = item.get("rotation")
                        if rot is not None and not _len3(rot):
                            errs.append(f"room {rkey} items[{idx}]: rotation must be length-3 array")
                        scale = item.get("scale")
                        if scale is not None and (not isinstance(scale, list) or len(scale) != 3):
                            errs.append(f"room {rkey} items[{idx}]: scale must be length-3 array")
                        color = item.get("color")
                        if color is not None and not _len4(color):
                            errs.append(f"room {rkey} items[{idx}]: color must be length-4 array")

    return (len(errs) == 0), errs


def write_layout_file(target_path: str, layout: Dict[str, Any]) -> None:
    """Write layout JSON to disk (UTF-8)."""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=2)


def _guess_mime_from_bytes(data: bytes) -> str:
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "application/octet-stream"


def _data_url_from_bytes(data: bytes) -> str:
    mime = _guess_mime_from_bytes(data)
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def generate_layout_from_image(prompt: str, image_bytes: bytes, current_layout: Dict[str, Any] | None = None, validate_output: bool = True) -> Dict[str, Any]:
    """
    Vision-enabled layout generation. The model looks at a real room image
    and produces a best-effort layout JSON following the same schema. If a
    current layout is provided, it should preserve unspecified rooms and
    only add/modify what the prompt requests.
    """
    messages = _build_system_prompt(current_layout)

    # Add image and user instruction
    data_url = _data_url_from_bytes(image_bytes)
    user_content: List[Dict[str, Any]] = []
    if prompt:
        user_content.append({"type": "text", "text": prompt})
    user_content.append({"type": "text", "text": "Analyze this room image and output the layout JSON."})
    user_content.append({"type": "image_url", "image_url": {"url": data_url}})

    messages.append({"role": "user", "content": user_content})

    try:
        resp = _get_client().chat.completions.create(
            model=_DEFAULT_MODEL,
            temperature=0.15,
            max_tokens=4000,
            messages=messages,
            response_format={"type": "json_object"},
            timeout=60,
        )
    except Exception as e:
        raise RuntimeError(f"Layout planner (image) connection failed: {e}")

    content = (resp.choices[0].message.content or "").strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Layout planner (image) returned non-JSON: {e}")

    if validate_output:
        ok, errs = validate_layout_schema(data)
        if not ok:
            raise RuntimeError(f"Generated layout (image) failed validation: {errs}")

    return data
