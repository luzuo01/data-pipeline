from ..scene_spec import default_scene_spec


def build_spec(seed: int = 0):
    spec = default_scene_spec(seed)
    top = dict(spec["scene"]["objects"][0])
    top["id"] = "cube_top"
    top["pose"] = [0.0, 0.0, 0.81]
    top["color"] = [0.1, 0.75, 0.2, 1.0]
    spec["scene"]["objects"].insert(1, top)
    spec["scene"]["template"] = "tabletop_stack"
    spec["task"] = {"template": "stack", "object_id": "cube_top", "target_id": "cube"}
    spec["success"] = [{"predicate": "object_on_object", "object_id": "cube_top", "target_id": "cube", "position_tolerance": 0.03}]
    return spec
