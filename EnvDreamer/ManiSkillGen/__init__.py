"""ManiSkill3 scene generation frontend.

The package keeps the scene description and validation independent from the
optional ManiSkill3/SAPIEN runtime.  This makes it possible to author and
validate scenes on machines that do not have a graphics stack installed, then
use the same specification with ManiSkill3 when it is available.
"""

from .generator import ManiSkillSceneGenerator
from .planner import SceneSpecPlanner
from .portal_trace_backend import PortalTraceBackend, default_portal_trace_spec
from .scene_spec import default_scene_spec, normalize_scene_spec
from .validator import validate_scene_spec

__all__ = [
    "ManiSkillSceneGenerator",
    "SceneSpecPlanner",
    "PortalTraceBackend",
    "default_portal_trace_spec",
    "default_scene_spec",
    "normalize_scene_spec",
    "validate_scene_spec",
]
