#!/usr/bin/env python3
"""
Layout synchronization helpers

Provides incremental diffs between layouts so that only the rooms that
changed (and their affected neighbours) are rebuilt inside Unreal.
Falls back to a full rebuild when no previous layout exists or when the
incremental strategy encounters an unexpected error.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, TypedDict

from Scripts.api.rc_client import rc_call_with_validation
from Scripts.build_empty_house import build_empty_house
from Scripts.core.home_agent import HomeAgent  # noqa: F401  (kept for compatibility/import side-effects)
from Scripts.utils.asset_registry import resolve_asset_path

# --------------------------------------------------------------------------------------
# Types & constants
# --------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

GridCoord = int
Vec3 = Tuple[float, float, float]
Color4 = Tuple[float, float, float, float]

class ItemNormalized(TypedDict):
    asset: str
    position: Tuple[int, int, int]
    rotation: Tuple[int, int, int]
    scale: Tuple[float, float, float]
    color: Tuple[float, float, float, float]

class ItemInfo(TypedDict, total=False):
    room: str
    id: str
    actor_id: str
    item: Dict[str, Any]
    normalized: ItemNormalized
    action: str

Layout = Dict[str, Any]
RoomDict = Dict[str, Any]

# Step size used to create per-wall-segment IDs along each side
WALL_SEGMENT_STEP = 100

COLOR_KEYS = {"wall_color", "floor_color"}
TEXTURE_KEYS = {"wall_texture", "floor_texture"}
VISUAL_KEYS = COLOR_KEYS | TEXTURE_KEYS
SKIP_DIFF_KEYS = VISUAL_KEYS | {"items"}


# --------------------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------------------

def _round_int(value: Any) -> int:
    """Round to nearest int; accepts str/float/int."""
    try:
        return int(round(float(value)))
    except Exception:
        # Fall back to direct int conversion (kept for backward compatibility)
        return int(value)


def _sanitize_fragment(value: Optional[str]) -> str:
    """Make a safe fragment for actor IDs."""
    value = value or ""
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value)
    value = value.strip("_")
    return value or "item"


def _item_actor_id(room_name: str, item_id: str) -> str:
    return f"item_{_sanitize_fragment(room_name)}_{_sanitize_fragment(item_id)}"


def _vec3(value: Optional[Iterable[Any]], fallback: Iterable[float]) -> Tuple[float, float, float]:
    try:
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return float(value[0]), float(value[1]), float(value[2])
    except Exception:
        pass
    fb = tuple(fallback)
    if len(fb) != 3:
        raise ValueError("fallback for _vec3 must be length 3")
    return float(fb[0]), float(fb[1]), float(fb[2])


def _color4(value: Optional[Iterable[Any]]) -> Color4:
    """RGBA with default alpha=1.0."""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        r, g, b = float(value[0]), float(value[1]), float(value[2])
        a = float(value[3]) if len(value) >= 4 else 1.0
        return (r, g, b, a)
    return (1.0, 1.0, 1.0, 1.0)


def _normalize_item(item: Dict[str, Any]) -> ItemNormalized:
    pos = _vec3(item.get("position"), (0.0, 0.0, 0.0))
    rot = _vec3(item.get("rotation"), (0.0, 0.0, 0.0))
    scale = _vec3(item.get("scale"), (1.0, 1.0, 1.0))
    col = _color4(item.get("color"))
    return {
        "asset": resolve_asset_path(str(item.get("asset", ""))).strip(),
        "position": (int(round(pos[0])), int(round(pos[1])), int(round(pos[2]))),
        "rotation": (int(round(rot[0])), int(round(rot[1])), int(round(rot[2]))),
        "scale": (float(scale[0]), float(scale[1]), float(scale[2])),
        "color": (float(col[0]), float(col[1]), float(col[2]), float(col[3])),
    }


# --------------------------------------------------------------------------------------
# Map builders
# --------------------------------------------------------------------------------------

def _collect_items(layout: Optional[Layout]) -> Dict[str, Dict[str, ItemInfo]]:
    """
    Returns a mapping: room_name -> (item_id -> ItemInfo)
    """
    result: Dict[str, Dict[str, ItemInfo]] = {}
    if not layout:
        return result

    rooms = layout.get("rooms", {}) or {}
    for room_name, room_data in rooms.items():
        items = room_data.get("items") or []
        if not isinstance(items, list):
            continue

        room_items: Dict[str, ItemInfo] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            if not item_id:
                continue
            actor_id = _item_actor_id(room_name, item_id)
            room_items[item_id] = {
                "room": room_name,
                "id": item_id,
                "actor_id": actor_id,
                "item": item,
                "normalized": _normalize_item(item),
            }
        if room_items:
            result[room_name] = room_items
    return result


# --------------------------------------------------------------------------------------
# Room neighborhood & diff
# --------------------------------------------------------------------------------------

def _room_bounds(room: RoomDict) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    try:
        b = room["bounds"]
        mn, mx = b["min"], b["max"]
        return (float(mn[0]), float(mn[1])), (float(mx[0]), float(mx[1]))
    except Exception:
        return None


def _room_neighbors(layout: Layout, room_name: str) -> Set[str]:
    """
    Two rooms are neighbors if they touch along a shared edge (axis-aligned),
    determined by overlapping on one axis and exact contact on the other.
    """
    rooms = layout.get("rooms", {}) or {}
    room = rooms.get(room_name)
    if not room:
        return set()

    neighbors: Set[str] = set()
    rb = _room_bounds(room)
    if rb is None:
        return neighbors
    (rb_min_x, rb_min_y), (rb_max_x, rb_max_y) = rb

    for other_name, other in rooms.items():
        if other_name == room_name:
            continue
        ob = _room_bounds(other)
        if ob is None:
            continue
        (o_min_x, o_min_y), (o_max_x, o_max_y) = ob

        overlap_x = min(rb_max_x, o_max_x) > max(rb_min_x, o_min_x)
        overlap_y = min(rb_max_y, o_max_y) > max(rb_min_y, o_min_y)

        # Contact along Y edges (north/south)
        if overlap_x and (
            _round_int(rb_max_y) == _round_int(o_min_y) or _round_int(rb_min_y) == _round_int(o_max_y)
        ):
            neighbors.add(other_name)

        # Contact along X edges (east/west)
        if overlap_y and (
            _round_int(rb_max_x) == _round_int(o_min_x) or _round_int(rb_min_x) == _round_int(o_max_x)
        ):
            neighbors.add(other_name)

    return neighbors


def _room_change_flags(old_room: Optional[RoomDict], new_room: Optional[RoomDict]) -> Dict[str, bool]:
    """
    Returns flags: color/texture/structural/any
    - visual (color/texture) changes are tracked separately from structural
    - items are ignored for structural comparison (they’re handled by diff_items)
    """
    if old_room is None or new_room is None:
        return {"color": False, "texture": False, "structural": True, "any": True}

    color_changed = {k: old_room.get(k) for k in COLOR_KEYS} != {k: new_room.get(k) for k in COLOR_KEYS}
    texture_changed = {k: old_room.get(k) for k in TEXTURE_KEYS} != {k: new_room.get(k) for k in TEXTURE_KEYS}

    def _strip_visual(room: RoomDict) -> RoomDict:
        return {k: v for k, v in room.items() if k not in SKIP_DIFF_KEYS}

    structural_changed = _strip_visual(old_room) != _strip_visual(new_room)

    return {
        "color": color_changed,
        "texture": texture_changed,
        "structural": structural_changed,
        "any": color_changed or texture_changed or structural_changed,
    }


def diff_layouts(old: Optional[Layout], new: Optional[Layout]) -> Dict[str, Set[str]]:
    old_rooms = (old or {}).get("rooms", {}) or {}
    new_rooms = (new or {}).get("rooms", {}) or {}

    old_names = set(old_rooms.keys())
    new_names = set(new_rooms.keys())

    removed = old_names - new_names
    added = new_names - old_names
    common = old_names & new_names

    changed: Set[str] = set()
    visual_changed: Set[str] = set()
    structural_changed: Set[str] = set()

    for name in common:
        flags = _room_change_flags(old_rooms.get(name), new_rooms.get(name))
        if flags["any"]:
            changed.add(name)
        if flags["color"] or flags["texture"]:
            visual_changed.add(name)
        if flags["structural"]:
            structural_changed.add(name)

    visual_only = visual_changed - structural_changed

    neighbor_rebuild: Set[str] = set()
    # Removed rooms affect old neighbors
    for name in removed:
        neighbor_rebuild |= _room_neighbors(old or {"rooms": {}}, name)
    # Structural changes affect neighbors in both old and new layout
    for name in structural_changed:
        neighbor_rebuild |= _room_neighbors(old or {"rooms": {}}, name)
        neighbor_rebuild |= _room_neighbors(new or {"rooms": {}}, name)
    # Newly added rooms affect new neighbors
    for name in added:
        neighbor_rebuild |= _room_neighbors(new or {"rooms": {}}, name)

    rooms_to_remove = removed | structural_changed | (neighbor_rebuild & old_names)
    rooms_to_build = added | structural_changed | (neighbor_rebuild & new_names)

    return {
        "removed": removed,
        "added": added,
        "changed": changed,
        "visual_changed": visual_changed,
        "structural_changed": structural_changed,
        "visual_only": visual_only,
        "neighbor_rebuild": neighbor_rebuild,
        "remove_set": rooms_to_remove,
        "build_set": rooms_to_build,
    }


def diff_items(old: Optional[Layout], new: Optional[Layout]) -> Dict[str, List[ItemInfo]]:
    old_map = _collect_items(old)
    new_map = _collect_items(new)

    removals: List[ItemInfo] = []
    additions: List[ItemInfo] = []
    updated_keys: Set[Tuple[str, str]] = set()

    # Detect removals & updates
    for room, items in old_map.items():
        new_items = new_map.get(room, {})
        for item_id, info in items.items():
            key = (room, item_id)
            if item_id not in new_items:
                removals.append({**info, "action": "removed"})
            else:
                new_info = new_items[item_id]
                if info["normalized"] != new_info["normalized"]:
                    # updated -> remove old & add new
                    removals.append({**info, "action": "updated"})
                    additions.append({**new_info, "action": "updated"})
                    updated_keys.add(key)

    # Detect additions
    for room, items in new_map.items():
        old_items = old_map.get(room, {})
        for item_id, info in items.items():
            key = (room, item_id)
            if item_id not in old_items:
                additions.append({**info, "action": "added"})
            elif key in updated_keys:
                continue

    return {"remove": removals, "add": additions}


# --------------------------------------------------------------------------------------
# Actor IDs per room geometry (walls/floor)
# --------------------------------------------------------------------------------------

def _get_room_actor_ids(layout: Layout, room_name: str) -> List[str]:
    """
    Get all actor IDs associated with a room (floor and wall segments).
    """
    rooms = layout.get("rooms", {}) or {}
    room = rooms.get(room_name)
    if not room:
        return []

    bounds = _room_bounds(room)
    if bounds is None:
        return []
    (min_x, min_y), (max_x, max_y) = bounds

    cx = _round_int((min_x + max_x) / 2.0)
    cy = _round_int((min_y + max_y) / 2.0)

    asset_ids = [f"floor_{room_name}_{cx}_{cy}"]

    # Wall segments at a fixed step to ensure deterministic IDs
    step = WALL_SEGMENT_STEP

    # North/South walls iterate along X; East/West walls iterate along Y
    for side in ("north", "south"):
        start = _round_int(min_x)
        end = _round_int(max_x)
        for coord in range(start, end + step, step):
            asset_ids.append(f"wall_{room_name}_{side[0]}_{coord}")

    for side in ("east", "west"):
        start = _round_int(min_y)
        end = _round_int(max_y)
        for coord in range(start, end + step, step):
            asset_ids.append(f"wall_{room_name}_{side[0]}_{coord}")

    return asset_ids


def remove_room_geometry(layout: Layout, room_name: str) -> None:
    """Remove all geometry for a room."""
    for asset_id in _get_room_actor_ids(layout, room_name):
        rc_call_with_validation("RC_Remove", {"Id": asset_id}, retries=0)


# --------------------------------------------------------------------------------------
# Item spawn/remove ops
# --------------------------------------------------------------------------------------

def _remove_item_actor(actor_id: str) -> bool:
    return bool(rc_call_with_validation("RC_Remove", {"Id": actor_id}, retries=0))


def _spawn_item_actor(info: ItemInfo) -> Dict[str, Any]:
    item = info.get("item", {}) or {}
    actor_id = info.get("actor_id", "")
    base_asset_path = resolve_asset_path(str(item.get("asset", "")))

    position = _vec3(item.get("position"), (0.0, 0.0, 0.0))
    rotation = _vec3(item.get("rotation"), (0.0, 0.0, 0.0))
    scale = _vec3(item.get("scale"), (1.0, 1.0, 1.0))
    r, p, y = float(rotation[2]), float(rotation[0]), float(rotation[1])  # keep original pitch/yaw/roll order below
    
    # Only send Color if texture/material is NOT present (to avoid overriding textures)
    has_texture = bool(item.get("texture"))
    color_payload = None
    if not has_texture and "color" in item:
        color = _color4(item.get("color"))
        color_payload = {"R": color[0], "G": color[1], "B": color[2], "A": color[3]}

    attempts: List[str] = []
    logger.info("[layout_sync] spawn item %s using asset '%s' -> resolved '%s'",
                info.get("id"), item.get("asset"), base_asset_path)

    def _spawn_with_path(path: str) -> bool:
        attempts.append(path)
        payload = {
            "Id": actor_id,
            "AssetPath": path,
            "X": float(position[0]),
            "Y": float(position[1]),
            "Z": float(position[2]),
            "ScaleX": float(scale[0]),
            "ScaleY": float(scale[1]),
            "ScaleZ": float(scale[2]),
            "Pitch": float(rotation[0]),
            "Yaw": float(rotation[1]),
            "Roll": float(rotation[2]),
        }
        # Only add Color if no texture is present
        if color_payload is not None:
            payload["Color"] = color_payload
        return bool(rc_call_with_validation("RC_AddAsset", payload, retries=0))

    def _try_variant(path: str, status: bool) -> bool:
        if status or not path or path in attempts:
            return status
        return _spawn_with_path(path)

    ok = _spawn_with_path(base_asset_path)

    pkg = ""
    obj = ""
    if "." in base_asset_path:
        pkg, obj = base_asset_path.rsplit(".", 1)
    base_no_class = obj[:-2] if obj.endswith("_C") else obj

    # 1) Ensure _C suffix attempt
    if pkg:
        if not obj.endswith("_C"):
            ok = _try_variant(f"{pkg}.{obj}_C", ok)
    else:
        ok = _try_variant(base_asset_path + ("_C" if not base_asset_path.endswith("_C") else ""), ok)

    # 2) Try without _C (pure asset reference)
    if pkg and base_no_class:
        ok = _try_variant(f"{pkg}.{base_no_class}", ok)

    # 3) Try Blueprint literal references (UE sometimes expects Blueprint'path')
    if pkg and base_no_class:
        ok = _try_variant(f"Blueprint'{pkg}.{base_no_class}'", ok)
        ok = _try_variant(f"/Script/Engine.Blueprint'{pkg}.{base_no_class}'", ok)
    if pkg and obj:
        ok = _try_variant(f"Blueprint'{pkg}.{obj}'", ok)
        ok = _try_variant(f"/Script/Engine.Blueprint'{pkg}.{obj}'", ok)

    return {"ok": ok, "attempts": attempts}


# --------------------------------------------------------------------------------------
# Main entry: apply layout update
# --------------------------------------------------------------------------------------

def apply_layout_update(
    previous: Optional[Layout],
    new_layout: Optional[Layout],
    layout_path: str,
) -> Dict[str, Any]:
    """
    Apply incremental or full layout update using HomeAgent for all room building.
    
    Keeps diffing/history tracking but delegates all room building (including textures/colors)
    to HomeAgent to avoid redundant/error-prone logic.
    """

    # Case 1: Clear everything
    if not new_layout or not new_layout.get("rooms"):
        removed_items: List[Dict[str, Any]] = []
        if previous and previous.get("rooms"):
            for room in sorted(previous["rooms"].keys()):
                remove_room_geometry(previous, room)
            for room_items in _collect_items(previous).values():
                for info in room_items.values():
                    ok = _remove_item_actor(info["actor_id"])
                    removed_items.append({
                        "room": info["room"],
                        "id": info["id"],
                        "asset": info["item"].get("asset"),
                        "actor": info["actor_id"],
                        "action": "removed",
                        "ok": bool(ok),
                    })
        return {
            "mode": "cleared",
            "rebuilt_rooms": [],
            "removed": sorted(previous.get("rooms", {}).keys()) if previous and previous.get("rooms") else [],
            "added": [],
            "changed": [],
            "visual_updates": [],
            "item_updates": removed_items,
        }

    # Case 2: Full rebuild (no previous layout)
    if not previous or not previous.get("rooms"):
        agent = HomeAgent(layout_path=layout_path, spawn_delay=0.08)
        agent.clear_home()
        agent.setup_entire_home()
        
        item_summaries: List[Dict[str, Any]] = []
        for room_items in _collect_items(new_layout).values():
            for info in room_items.values():
                spawn_result = _spawn_item_actor(info)
                item_summaries.append({
                    "room": info["room"],
                    "id": info["id"],
                    "asset": info["item"].get("asset"),
                    "actor": info["actor_id"],
                    "action": "added",
                    "ok": bool(spawn_result.get("ok")),
                    "attempts": spawn_result.get("attempts", []),
                })
        return {
            "mode": "full",
            "rebuilt_rooms": sorted(new_layout.get("rooms", {}).keys()),
            "removed": [],
            "added": sorted(new_layout.get("rooms", {}).keys()),
            "changed": [],
            "visual_updates": [],
            "item_updates": item_summaries,
        }

    # Case 3: Incremental update - use diffing to track changes, but use HomeAgent for building
    summary = diff_layouts(previous, new_layout)
    remove_targets: Set[str] = set(summary["remove_set"])
    build_targets: Set[str] = set(summary["build_set"])
    visual_only: Set[str] = set(summary.get("visual_only", set()))

    item_changes = diff_items(previous, new_layout)
    item_removals = item_changes.get("remove", [])
    item_additions = item_changes.get("add", [])
    item_ops_pending = bool(item_removals or item_additions)

    # Visual-only room changes should not trigger shell rebuild
    remove_targets -= visual_only
    build_targets -= visual_only

    # Nothing to do
    if not remove_targets and not build_targets and not visual_only and not item_ops_pending:
        return {
            "mode": "noop",
            "rebuilt_rooms": [],
            "removed": [],
            "added": [],
            "changed": [],
            "visual_updates": [],
            "item_updates": [],
        }

    item_summaries: List[Dict[str, Any]] = []

    try:
        # First: remove old/updated items
        for info in item_removals:
            ok = _remove_item_actor(info["actor_id"])
            item_summaries.append({
                "room": info["room"],
                "id": info["id"],
                "asset": info["item"].get("asset"),
                "actor": info["actor_id"],
                "action": "removed" if info.get("action") == "removed" else "updated",
                "ok": bool(ok),
            })

        # If any rooms need changes (structural OR visual), use HomeAgent to rebuild
        # HomeAgent handles textures/colors correctly, so we just delegate to it
        if remove_targets or build_targets or visual_only:
            logger.info("[layout_sync] Room changes detected - using HomeAgent to rebuild")
            agent = HomeAgent(layout_path=layout_path, spawn_delay=0.08)
            agent.clear_home()
            agent.setup_entire_home()

            # Respawn all items after rebuild
            for room_items in _collect_items(new_layout).values():
                for info in room_items.values():
                    spawn_result = _spawn_item_actor(info)
                    item_summaries.append({
                        "room": info["room"],
                        "id": info["id"],
                        "asset": info["item"].get("asset"),
                        "actor": info["actor_id"],
                        "action": "added",
                        "ok": bool(spawn_result.get("ok")),
                        "attempts": spawn_result.get("attempts", []),
                    })
            mode = "incremental_full" if (remove_targets or build_targets) else "visual-only"
            rebuilt_rooms = sorted(new_layout.get("rooms", {}).keys())
        else:
            # No room changes: just handle items
            for info in item_additions:
                spawn_result = _spawn_item_actor(info)
                item_summaries.append({
                    "room": info["room"],
                    "id": info["id"],
                    "asset": info["item"].get("asset"),
                    "actor": info["actor_id"],
                    "action": info.get("action", "added"),
                    "ok": bool(spawn_result.get("ok")),
                    "attempts": spawn_result.get("attempts", []),
                })
            mode = "items-only" if item_summaries else "noop"
            rebuilt_rooms = []

        return {
            "mode": mode,
            "rebuilt_rooms": rebuilt_rooms,
            "removed": sorted(summary["removed"]),
            "added": sorted(summary["added"]),
            "changed": sorted(summary["changed"]),
            "visual_updates": sorted(visual_only),
            "item_updates": item_summaries,
        }

    except Exception as exc:
        # Fallback: full rebuild using HomeAgent
        logger.exception("[layout_sync] Error during incremental update: %s", exc)
        logger.info("[layout_sync] Falling back to full rebuild")
        agent = HomeAgent(layout_path=layout_path, spawn_delay=0.08)
        agent.clear_home()
        agent.setup_entire_home()
        
        item_summaries = []
        for room_items in _collect_items(new_layout).values():
            for info in room_items.values():
                spawn_result = _spawn_item_actor(info)
                item_summaries.append({
                    "room": info["room"],
                    "id": info["id"],
                    "asset": info["item"].get("asset"),
                    "actor": info["actor_id"],
                    "action": "added",
                    "ok": bool(spawn_result.get("ok")),
                    "attempts": spawn_result.get("attempts", []),
                })
        return {
            "mode": "fallback_full",
            "error": str(exc),
            "rebuilt_rooms": sorted(new_layout.get("rooms", {}).keys()),
            "removed": sorted(summary.get("removed", set())),
            "added": sorted(summary.get("added", set())),
            "changed": sorted(summary.get("changed", set())),
            "visual_updates": [],
            "item_updates": item_summaries,
        }
