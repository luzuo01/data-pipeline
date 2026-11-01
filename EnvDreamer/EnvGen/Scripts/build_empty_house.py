#!/usr/bin/env python3
"""
Build Empty House Script
Creates empty room structures (floors, walls) without furniture.
"""

import sys
import time
import json
from pathlib import Path
from typing import Union

# Add Scripts directory to path
script_dir = Path(__file__).parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from core.home_agent import HomeAgent


def _resolve_layout(project_root: Path, layout_choice: Union[str, Path]):
    """Resolve the layout JSON path and metadata.

    Args:
        project_root: Root project directory
        layout_choice: "default", "small", or a custom JSON file path
    Returns:
        (layout_data, layout_path, layout_label)
    """
    # Normalize to string
    if isinstance(layout_choice, Path):
        layout_choice = str(layout_choice)

    # Allow user to directly specify JSON path
    if layout_choice.endswith(".json"):
        layout_path = Path(layout_choice).expanduser().resolve()
        layout_label = layout_path.name
    elif layout_choice == "small":
        layout_path = project_root / "Config" / "home_layout_small.json"
        layout_label = "Studio Apartment"
    else:
        layout_path = project_root / "Config" / "home_layout.json"
        layout_label = "Modern Apartment"

    if not layout_path.exists():
        raise FileNotFoundError(f"Layout file not found: {layout_path}")

    with open(layout_path, "r", encoding="utf-8") as f:
        layout_data = json.load(f)

    return layout_data, layout_path, layout_label


def build_empty_house(layout: str = "default"):
    """Build an empty house based on a given JSON layout."""
    project_root = Path(__file__).parent.parent
    layout_data, layout_path, layout_label = _resolve_layout(project_root, layout)

    print(f"Building empty house: {layout_label} ({layout_path})")
    agent = HomeAgent(layout_path=str(layout_path), spawn_delay=0.08)
    print("Clearing existing content...")
    agent.clear_home()
    time.sleep(1)
    print("Building room structures...")
    agent.setup_entire_home()
    print("Done: empty house structure complete.")


def build_single_room(room_name: str, layout: str = "default"):
    """Build a single room from the layout file."""
    project_root = Path(__file__).parent.parent
    layout_data, layout_path, layout_label = _resolve_layout(project_root, layout)

    rooms = layout_data.get("rooms", {})
    if room_name not in rooms:
        print(f"Room '{room_name}' not found. Available: {list(rooms.keys())}")
        return

    room_data = rooms[room_name]
    bounds = room_data.get("bounds", {})
    print(
        f"Building room: {room_data.get('name', room_name)} (bounds {bounds.get('min')} to {bounds.get('max')})"
    )

    agent = HomeAgent(layout_path=str(layout_path), spawn_delay=0.08)
    print("Clearing existing content...")
    agent.clear_home()
    time.sleep(1)
    print(f"Building structure for room: {room_name}")
    agent.setup_room(room_name)
    print(f"Done: empty {room_name} structure complete.")


def list_available_rooms(layout: str = "default"):
    """List available rooms in the layout file."""
    project_root = Path(__file__).parent.parent
    layout_data, layout_path, layout_label = _resolve_layout(project_root, layout)

    print(f"Layout: {layout_data.get('name', layout_label)}")
    print(f"Description: {layout_data.get('description', '<no description>')}")
    print(f"Units: {layout_data.get('units', '<unspecified>')}")
    print(f"Available rooms ({len(layout_data.get('rooms', {}))}):")

    for rname, rdata in layout_data["rooms"].items():
        bounds = rdata.get("bounds", {})
        print(f"  - {rname}: {rdata.get('purpose', '<unspecified>')}, bounds={bounds}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build empty house structures")
    parser.add_argument(
        "--layout",
        type=str,
        default="default",
        help=(
            "Choose layout: 'default', 'small', "
            "or a path to a custom JSON file (e.g., './Config/home_layout.json')"
        ),
    )
    parser.add_argument("--room", type=str, help="Build specific room only")
    parser.add_argument("--list", action="store_true", help="List available rooms")

    args = parser.parse_args()

    if args.list:
        list_available_rooms(args.layout)
    elif args.room:
        build_single_room(args.room, args.layout)
    else:
        build_empty_house(args.layout)
