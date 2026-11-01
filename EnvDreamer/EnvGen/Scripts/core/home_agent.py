#!/usr/bin/env python3
"""
Home Setup Agent - Clean texture implementation
"""

import argparse
import json
import os
import time
from pathlib import Path
from typing import Optional, Tuple, List, Dict

from api.rc_client import rc_call
from utils.asset_registry import resolve_asset_path


def _load_texture_registry() -> Dict[str, str]:
    """Load texture mappings from texture_registry.json"""
    # Get the project root (EnvGen directory)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    registry_path = os.path.join(project_root, "Config", "texture_registry.json")

    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("textures", {})
    except Exception as e:
        print(f"[HomeAgent] WARNING: Failed to load texture registry: {e}")
        return {}


# Load texture registry once at module level
_TEXTURE_REGISTRY = _load_texture_registry()


def _get_texture_path(texture_name: str) -> str:
    """Resolve texture shortcut to full path, or return as-is if not found"""
    return _TEXTURE_REGISTRY.get(texture_name.lower()) or texture_name


class HomeAgent:
    def __init__(self, layout_path: str = None, spawn_delay: float = 0.05, pivot_mode: str = "base"):
        if layout_path is None:
            layout_path = Path(__file__).parent.parent.parent / "Config" / "home_layout.json"

        with open(layout_path, "r") as f:
            self.config = json.load(f)

        self.spawn_delay = float(spawn_delay)
        self.pivot_mode = (self.config.get("mesh_pivot") or pivot_mode).lower()
        if self.pivot_mode not in ("base", "center"):
            self.pivot_mode = "base"

        print(f"[HomeAgent] Loaded: {self.config.get('name', 'Layout')}")
        print(f"[HomeAgent] Rooms: {', '.join(self.config['rooms'].keys())}")

        self.building = self.config.get("building_boundaries", {"min": [-400, -400, 0], "max": [800, 400, 300]})
        self.tile_size = float(self.config.get("tile_size", 100.0))
        self.floor_thickness = float(self.config.get("floor_thickness", 5.0))
        self.wall_thickness = float(self.config.get("wall_thickness", 10.0))
        self.door_width = float(self.config.get("door_width", 100.0))
        self.default_floor_color = tuple(self.config.get("floor_color", [0.8, 0.8, 0.8, 1.0]))
        self.default_wall_color = tuple(self.config.get("wall_color", [0.9, 0.9, 0.9, 1.0]))
        self.door_floor_patches = []

    def _cube_z(self, height_cm: float, bottom_z: float) -> float:
        return bottom_z if self.pivot_mode == "base" else bottom_z + height_cm / 2.0

    def spawn_cube(self, id: str, x: float, y: float, z: float, sx_cm: float, sy_cm: float, sz_cm: float, color: Optional[Tuple] = None):
        """
        Spawn a cube. If color is None, no color is sent (for texture-only objects).
        If color is provided, it's used (for color-only objects).
        """
        base = self.tile_size
        payload = {
            "Id": id,
            "AssetPath": resolve_asset_path("cube"),
            "X": float(x), "Y": float(y), "Z": float(z),
            "ScaleX": float(sx_cm / base),
            "ScaleY": float(sy_cm / base),
            "ScaleZ": float(sz_cm / base),
            "Pitch": 0.0, "Yaw": 0.0, "Roll": 0.0,
        }
        # Only add Color if provided (don't send color when texture will be applied)
        if color is not None:
            payload["Color"] = {"R": color[0], "G": color[1], "B": color[2], "A": color[3]}
        rc_call("RC_AddAsset", payload)
        time.sleep(self.spawn_delay)

    def apply_texture(self, actor_id: str, texture_name: str):
        """Apply texture/material to spawned actor"""
        time.sleep(0.1)  # Delay to ensure actor is fully created

        texture_path = _get_texture_path(texture_name)

        result = rc_call("RC_SetMaterialTexture", {"Id": actor_id, "TexturePath": texture_path})
        if result:
            print(f"[HomeAgent] ✓ Applied texture/material '{texture_name}' to {actor_id}")
        else:
            print(f"[HomeAgent] ✗ Failed to apply texture/material '{texture_name}' to {actor_id}")

    def clear_home(self):
        print("[HomeAgent] Clearing...")
        rc_call("ClearAllJsonGeneratedActors", {})
        time.sleep(0.1)

    def _adjacent_room_on_side(self, room_name: str, side: str) -> Optional[str]:
        rooms = self.config["rooms"]
        rb = rooms[room_name]["bounds"]
        rmin, rmax = rb["min"], rb["max"]

        for other_name, other in rooms.items():
            if other_name == room_name:
                continue
            ob = other["bounds"]
            omin, omax = ob["min"], ob["max"]

            if side == "north" and omin[1] == rmax[1] and min(rmax[0], omax[0]) > max(rmin[0], omin[0]):
                return other_name
            elif side == "south" and omax[1] == rmin[1] and min(rmax[0], omax[0]) > max(rmin[0], omin[0]):
                return other_name
            elif side == "east" and omin[0] == rmax[0] and min(rmax[1], omax[1]) > max(rmin[1], omin[1]):
                return other_name
            elif side == "west" and omax[0] == rmin[0] and min(rmax[1], omax[1]) > max(rmin[1], omin[1]):
                return other_name
        return None

    @staticmethod
    def _owns_shared_wall(a: str, b: str) -> bool:
        return a < b

    def _door_span(self, room_name: str, other_name: str, side: str) -> Optional[Tuple[float, float]]:
        rooms = self.config["rooms"]
        rb, ob = rooms[room_name]["bounds"], rooms[other_name]["bounds"]
        rmin, rmax = rb["min"], rb["max"]
        omin, omax = ob["min"], ob["max"]
        w = self.door_width

        if side in ("north", "south"):
            s, e = max(rmin[0], omin[0]), min(rmax[0], omax[0])
        elif side in ("east", "west"):
            s, e = max(rmin[1], omin[1]), min(rmax[1], omax[1])
        else:
            return None

        if e - s < w:
            return None
        c = (s + e) / 2.0
        return c - w / 2.0, c + w / 2.0

    def move_camera(self, position: list, rotation: list):
        rc_call("RC_SetCameraLocation", {"X": position[0], "Y": position[1], "Z": position[2]})
        rc_call("RC_SetCameraRotation", {"Pitch": rotation[0], "Yaw": rotation[1], "Roll": rotation[2]})

    def build_all_walls(self):
        print("[HomeAgent] Building walls...")
        rooms = self.config["rooms"]
        t = self.wall_thickness
        self.door_floor_patches = []

        for name, room in rooms.items():
            b = room["bounds"]
            min_x, min_y, min_z = b["min"]
            max_x, max_y, max_z = b["max"]
            wall_color = tuple(room.get("wall_color", self.default_wall_color))
            wall_texture = room.get("wall_texture")

            wall_height = max_z - min_z
            if wall_height <= 0:
                continue

            wall_bottom_z = self.floor_thickness
            wall_center_z = self._cube_z(wall_height, wall_bottom_z)

            def spawn_segments_for_side(side: str, fixed_coord: float, full_start: float, full_end: float, neighbor: Optional[str]):
                if full_end <= full_start:
                    return

                def spawn_piece(seg_start: float, seg_end: float, tag: str):
                    length = seg_end - seg_start
                    if length <= 0:
                        return

                    if side in ("north", "south"):
                        x, y, sx, sy = (seg_start + seg_end) / 2.0, fixed_coord, length, t
                        coord = int(round(x))
                    else:
                        x, y, sx, sy = fixed_coord, (seg_start + seg_end) / 2.0, t, length
                        coord = int(round(y))

                    wall_id = f"wall_{name}_{side[0]}_{coord}"
                    # If texture is present, don't send color (texture will be applied)
                    # Otherwise use the wall color
                    spawn_color = None if wall_texture else wall_color
                    self.spawn_cube(wall_id, x, y, wall_center_z, sx, sy, wall_height, spawn_color)

                    if wall_texture:
                        self.apply_texture(wall_id, wall_texture)

                def add_door_floor_patch(d0: float, d1: float, other: str):
                    length = d1 - d0
                    if length <= 0:
                        return

                    rooms_cfg = self.config["rooms"]
                    floor_color_a = tuple(rooms_cfg[name].get("floor_color", self.default_floor_color))
                    floor_color_b = tuple(rooms_cfg[other].get("floor_color", self.default_floor_color))
                    floor_texture_a = rooms_cfg[name].get("floor_texture")
                    floor_texture_b = rooms_cfg[other].get("floor_texture")
                    half_width = t / 2.0
                    door_center = (d0 + d1) / 2.0

                    if side in ("north", "south"):
                        cx1, cy1, sx1, sy1 = door_center, fixed_coord - half_width / 2.0, length, half_width
                        cx2, cy2, sx2, sy2 = door_center, fixed_coord + half_width / 2.0, length, half_width
                    else:
                        cx1, cy1, sx1, sy1 = fixed_coord - half_width / 2.0, door_center, half_width, length
                        cx2, cy2, sx2, sy2 = fixed_coord + half_width / 2.0, door_center, half_width, length

                    self.door_floor_patches.append((cx1, cy1, sx1, sy1, floor_color_a, floor_texture_a))
                    self.door_floor_patches.append((cx2, cy2, sx2, sy2, floor_color_b, floor_texture_b))

                def spawn_shared_with_door(seg_start: float, seg_end: float, other: str):
                    if seg_end <= seg_start or not self._owns_shared_wall(name, other):
                        return

                    door_span = self._door_span(name, other, side)
                    if not door_span or (seg_end - seg_start <= self.door_width):
                        spawn_piece(seg_start, seg_end, "shared_full")
                        return

                    d0, d1 = door_span
                    d0, d1 = max(d0, seg_start), min(d1, seg_end)
                    if d1 <= d0:
                        spawn_piece(seg_start, seg_end, "shared_full")
                        return

                    spawn_piece(seg_start, d0, "shared_a")
                    spawn_piece(d1, seg_end, "shared_b")
                    add_door_floor_patch(d0, d1, other)

                if not neighbor:
                    spawn_piece(full_start, full_end, "solo")
                    return

                ob = rooms[neighbor]["bounds"]
                o_start, o_end = (ob["min"][0], ob["max"][0]) if side in ("north", "south") else (ob["min"][1], ob["max"][1])
                overlap_start, overlap_end = max(full_start, o_start), min(full_end, o_end)

                if overlap_end <= overlap_start:
                    spawn_piece(full_start, full_end, "solo_no_olap")
                    return

                if overlap_start > full_start:
                    spawn_piece(full_start, overlap_start, "solo_before")
                spawn_shared_with_door(overlap_start, overlap_end, neighbor)
                if overlap_end < full_end:
                    spawn_piece(overlap_end, full_end, "solo_after")

            for side, coord, start, end in [
                ("north", max_y, min_x, max_x),
                ("south", min_y, min_x, max_x),
                ("east", max_x, min_y, max_y),
                ("west", min_x, min_y, max_y)
            ]:
                spawn_segments_for_side(side, coord, start, end, self._adjacent_room_on_side(name, side))

        print("[HomeAgent] Walls built.")

    def build_all_floors(self):
        print("[HomeAgent] Building floors...")
        rooms = self.config["rooms"]
        total_rooms = 0

        for name, room in rooms.items():
            b = room["bounds"]
            min_x, min_y = b["min"][0], b["min"][1]
            max_x, max_y = b["max"][0], b["max"][1]
            floor_color = tuple(room.get("floor_color", self.default_floor_color))
            floor_texture = room.get("floor_texture")

            inset = self.wall_thickness / 2.0
            inner_min_x, inner_max_x = min_x + inset, max_x - inset
            inner_min_y, inner_max_y = min_y + inset, max_y - inset

            if inner_max_x <= inner_min_x:
                inner_min_x, inner_max_x = min_x, max_x
            if inner_max_y <= inner_min_y:
                inner_min_y, inner_max_y = min_y, max_y

            width, depth = inner_max_x - inner_min_x, inner_max_y - inner_min_y
            if width <= 0 or depth <= 0:
                continue

            cx, cy = (inner_min_x + inner_max_x) / 2.0, (inner_min_y + inner_max_y) / 2.0
            cz = self._cube_z(self.floor_thickness, 0.0)
            fid = f"floor_{name}_{int(round(cx))}_{int(round(cy))}"

            # If texture is present, don't send color (texture will be applied)
            # Otherwise use the floor color
            spawn_color = None if floor_texture else floor_color
            self.spawn_cube(fid, cx, cy, cz, width, depth, self.floor_thickness, spawn_color)

            if floor_texture:
                self.apply_texture(fid, floor_texture)

            total_rooms += 1

        # Door patches
        for i, patch in enumerate(self.door_floor_patches):
            if len(patch) == 6:
                cx, cy, sx, sy, color, texture = patch
            else:
                cx, cy, sx, sy, color = patch
                texture = None
            cz = self._cube_z(self.floor_thickness, 0.0)
            spawn_color = None if texture else color
            door_id = f"floor_door_{i}"
            self.spawn_cube(door_id, cx, cy, cz, sx, sy, self.floor_thickness, spawn_color)
            if texture:
                self.apply_texture(door_id, texture)

        print(f"[HomeAgent] Floors: {total_rooms} slabs + {len(self.door_floor_patches)} door patches")

    def setup_entire_home(self):
        print(f"\n{'=' * 60}\nSetting up: {self.config.get('name', 'Layout')}\n{'=' * 60}\n")
        self.build_all_walls()
        self.build_all_floors()

    def setup_room(self, room_name: str):
        print(f"\n{'=' * 60}\nRoom: {room_name}\n{'=' * 60}")
        self.build_all_walls()
        self.build_all_floors()


def setup_home(layout: str = "default", room: Optional[str] = None, delay: float = 0.05, pivot: str = "base"):
    if layout == "default":
        layout_path = Path(__file__).parent.parent.parent / "Config" / "home_layout.json"
    elif layout == "small":
        layout_path = Path(__file__).parent.parent.parent / "Config" / "home_layout_small.json"
    else:
        layout_path = Path(layout)

    agent = HomeAgent(str(layout_path), spawn_delay=delay, pivot_mode=pivot)

    if room:
        agent.setup_room(room)
    else:
        agent.setup_entire_home()

    return agent


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a home from JSON layout")
    parser.add_argument("--layout", "-l", default="default", help="default | small | /path/to/layout.json")
    parser.add_argument("--room", "-r", default=None, help="Build specific room only")
    parser.add_argument("--delay", "-d", type=float, default=0.05, help="Spawn delay (seconds)")
    parser.add_argument("--pivot", "-p", default="base", choices=["base", "center"], help="Cube pivot mode")
    args = parser.parse_args()
    setup_home(layout=args.layout, room=args.room, delay=args.delay, pivot=args.pivot)
