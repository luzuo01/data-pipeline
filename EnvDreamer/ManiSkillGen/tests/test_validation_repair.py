import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from ManiSkillGen.generator import ManiSkillSceneGenerator
from ManiSkillGen.planner import SceneSpecPlanner
from ManiSkillGen.repair import repair_scene
from ManiSkillGen.runtime_validation import probe_maniskill
from ManiSkillGen.scene_spec import default_scene_spec
from ManiSkillGen.validator import SceneValidationError, validate_scene_spec
from ManiSkillGen.portal_trace_backend import default_portal_trace_spec


def scene():
    spec = default_scene_spec()
    spec["randomization"] = {}
    return spec


@pytest.mark.parametrize("mutate", [
    lambda s: s["scene"]["objects"][0]["pose"].__setitem__(0, float("nan")),
    lambda s: s["scene"].__setitem__("workspace", []),
    lambda s: s.__setitem__("success", None),
    lambda s: s["scene"]["workspace"].__setitem__("table_height", "nan"),
    lambda s: s["success"][0].__setitem__("object_id", []),
    lambda s: s.__setitem__("repair", {"locked_object_ids": "cube"}),
    lambda s: s["randomization"].__setitem__("object_pose_jitter", "bad"),
])
def test_malformed_reports_instead_of_throwing(mutate):
    spec = scene()
    mutate(spec)
    report = validate_scene_spec(spec)
    assert not report["ok"]
    json.dumps(report, allow_nan=False)


def test_static_runtime_flag_does_not_claim_reset():
    assert validate_scene_spec(scene(), check_runtime=True)["checks"]["environment_reset"] is None


def test_overlap_static_obstacle_and_floating_are_errors():
    spec = scene()
    obstacle = copy.deepcopy(spec["scene"]["objects"][0])
    obstacle.update(id="obstacle", dynamic=False)
    spec["scene"]["objects"].append(obstacle)
    assert "overlap" in {i["code"] for i in validate_scene_spec(spec)["issues"]}
    spec["scene"]["objects"][0]["pose"][2] += .2
    assert "unsupported" in {i["code"] for i in validate_scene_spec(spec)["issues"]}


def test_deterministic_repair_preserves_intent_and_input():
    spec = scene()
    spec["scene"]["objects"][0]["pose"] = [1, 0, 1.1]
    original = copy.deepcopy(spec)
    a = repair_scene(spec, seed=17)
    b = repair_scene(spec, seed=17)
    assert a["ok"], a["errors"]
    assert a == b
    assert spec == original
    assert a["repair"]["status"] == "repaired"
    candidate = copy.deepcopy(a["normalized_spec"])
    for dst, src in zip(candidate["scene"]["objects"], original["scene"]["objects"]):
        dst["pose"] = src["pose"]
    assert candidate == original


def test_marker_is_not_collision_or_support():
    spec = scene()
    spec["scene"]["objects"][0]["pose"][:2] = [.2, .1]
    assert validate_scene_spec(spec)["ok"]
    spec["scene"]["objects"][0]["pose"][2] += .01
    assert not validate_scene_spec(spec)["ok"]


@pytest.mark.parametrize("mode", ["locked", "disabled", "impossible", "semantic", "constraint"])
def test_repair_stops_without_changing_task(mode):
    spec = scene()
    spec["scene"]["objects"][0]["pose"][0] = 2
    if mode == "locked":
        spec["repair"] = {"locked_object_ids": ["cube"]}
    if mode == "impossible":
        spec["scene"]["objects"][0]["size"] = [2, 2, 2]
    if mode == "semantic":
        spec["success"][0]["predicate"] = "object_on_object"
    if mode == "constraint":
        spec["constraints"] = {"left_of": ["cube", "target"]}
    report = repair_scene(spec, enabled=mode != "disabled", max_attempts=2)
    assert not report["ok"]
    assert report["repair"]["attempts"] <= 2
    assert report["normalized_spec"]["task"] == spec["task"]
    assert report["normalized_spec"]["success"] == spec["success"]


def test_planner_keeps_invalid_model_candidate_for_repair(monkeypatch):
    planner = SceneSpecPlanner(api_key="", allow_fallback=False)
    spec = scene()
    spec["scene"]["objects"][0]["pose"][0] = 2
    monkeypatch.setattr(planner, "_call_model", lambda *args: spec)
    assert planner.plan("fixed scene")["scene"]["objects"][0]["pose"][0] == 2


class FakeEnv:
    def __init__(self, spec, mode="stable"):
        self.unwrapped = self
        self.mode = mode
        self.closed = False
        self.steps = 0
        self._object_handles = {
            o["id"]: SimpleNamespace(pose=SimpleNamespace(p=np.array([o["pose"]]), q=np.array([[1, 0, 0, 0]])),
                                     linear_velocity=np.zeros((1, 3)), angular_velocity=np.zeros((1, 3)))
            for o in spec["scene"]["objects"]}
        self.agent = SimpleNamespace(robot=SimpleNamespace(qpos=np.zeros((1, 9)), qvel=np.zeros((1, 9))))
        self.scene = SimpleNamespace(get_contacts=lambda: [])

    def reset(self, **kwargs):
        return None

    def step(self, action):
        self.steps += 1
        if self.mode == "fall":
            self._object_handles["cube"].pose.p[0, 2] -= .1
        if self.mode == "moving":
            self._object_handles["cube"].linear_velocity[0, 0] = .1
        if self.mode == "nan":
            self.agent.robot.qpos[0, 0] = float("nan")

    def render(self):
        return np.zeros((48, 64, 3), dtype=np.uint8)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("mode,expected", [("stable", True), ("fall", False), ("moving", False), ("nan", False)])
def test_runtime_probe_detects_motion_falls_and_nan(mode, expected):
    report = probe_maniskill(FakeEnv(scene(), mode), scene())
    assert report["ok"] is expected
    assert report["checks"]["task_rollout"] is None


def test_runtime_rebuild_repairs_and_revalidates(tmp_path):
    instances = []
    def factory(**kwargs):
        env = FakeEnv(kwargs["spec"], "moving" if not instances else "stable")
        instances.append(env)
        return env
    generator = ManiSkillSceneGenerator(tmp_path, env_factory=factory)
    result = generator.generate(scene(), seed=9)
    assert instances[0].closed
    assert result["validation"]["runtime"]["ok"]
    assert result["validation"]["repair"]["attempts"] == 1
    assert result["scene_spec"]["success"] == scene()["success"]
    assert result["scene_spec"]["scene"]["objects"][0]["pose"] != scene()["scene"]["objects"][0]["pose"]


def test_failed_runtime_retries_bounded_and_last_preview_preserved(tmp_path):
    generator = ManiSkillSceneGenerator(tmp_path, env_factory=lambda **kw: FakeEnv(kw["spec"]))
    generator.generate(scene())
    preview = (tmp_path / "previews/latest.png").read_bytes()
    failed_envs = []
    def bad_factory(**kw):
        env = FakeEnv(kw["spec"], "moving")
        failed_envs.append(env)
        return env
    generator.env_factory = bad_factory
    with pytest.raises(SceneValidationError) as info:
        generator.generate(scene(), max_repair_attempts=2)
    assert len(failed_envs) == 3
    assert all(env.closed for env in failed_envs)
    assert info.value.report["repair"]["attempts"] == 2
    assert list((tmp_path / "manifests").glob("failed_*.json"))
    assert (tmp_path / "previews/latest.png").read_bytes() == preview


def test_render_failure_is_not_software_success(tmp_path):
    env = FakeEnv(scene())
    env.render = lambda: None
    generator = ManiSkillSceneGenerator(tmp_path, env_factory=lambda **kw: env)
    with pytest.raises(SceneValidationError):
        generator.generate(scene())
    assert env.closed
    assert not (tmp_path / "previews/latest.png").exists()


def test_locks_prevent_randomization_and_runtime_repair(tmp_path):
    spec = scene()
    spec["repair"] = {"locked_object_ids": ["cube"]}
    spec["randomization"] = {"object_pose_jitter": .1}
    generator = ManiSkillSceneGenerator(tmp_path, env_factory=lambda **kw: FakeEnv(kw["spec"]))
    result = generator.generate(spec)
    assert result["scene_spec"]["scene"]["objects"][0]["pose"] == spec["scene"]["objects"][0]["pose"]
    generator.env_factory = lambda **kw: FakeEnv(kw["spec"], "moving")
    with pytest.raises(SceneValidationError) as info:
        generator.generate(spec)
    assert info.value.report["repair"]["attempts"] == 0


def test_portal_does_not_pretend_custom_layout_is_applied():
    spec = default_portal_trace_spec()
    report = validate_scene_spec(spec)
    assert report["ok"]
    assert report["checks"]["geometry"] is None
    assert report["task_feasibility"]["status"] == "unknown"
    spec["scene"]["objects"] = scene()["scene"]["objects"]
    assert not validate_scene_spec(spec)["ok"]


def test_only_benchmark_disabled_nut_is_excluded_from_velocity():
    from ManiSkillGen.portal_trace_worker import inactive_nut_dofs
    env = SimpleNamespace(single_object_mode=2, obj_to_use="square", nuts=[
        SimpleNamespace(name="square", joints=["square_joint"]),
        SimpleNamespace(name="round", joints=["round_joint"])],
        sim=SimpleNamespace(model=SimpleNamespace(get_joint_qvel_addr=lambda joint: (15, 21))))
    assert inactive_nut_dofs(env, "NutAssemblySquareTight-v0") == (list(range(15, 21)), ["round"])
    assert inactive_nut_dofs(env, "Stack") == ([], [])
    env.single_object_mode = 0
    assert inactive_nut_dofs(env, "NutAssemblySquareTight-v0") == ([], [])
