"""Small deterministic SceneSpec templates used by the planner and tests."""

from .tabletop_pick_place import build_spec as pick_and_place
from .tabletop_push import build_spec as push
from .tabletop_stack import build_spec as stack

__all__ = ["pick_and_place", "push", "stack"]
