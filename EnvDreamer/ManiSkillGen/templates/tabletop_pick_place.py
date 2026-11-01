from ..scene_spec import default_scene_spec


def build_spec(seed: int = 0):
    """Return the canonical cube-to-target tabletop task."""
    return default_scene_spec(seed)
