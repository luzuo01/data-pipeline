from ..scene_spec import default_scene_spec


def build_spec(seed: int = 0):
    spec = default_scene_spec(seed)
    spec["scene"]["template"] = "tabletop_push"
    spec["task"] = {"template": "push", "object_id": "cube", "target_id": "target"}
    spec["success"] = [{"predicate": "object_displaced", "object_id": "cube", "target_id": "target", "position_tolerance": 0.03}]
    return spec
