import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ManiSkillGen.planner import SceneSpecPlanner
from ManiSkillGen.portal_trace_backend import PORTAL_ROBOTS, PORTAL_TASKS, default_portal_trace_spec, portal_trace_python
from ManiSkillGen.validator import validate_scene_spec


class PortalTraceRegistryTests(unittest.TestCase):
    def test_core_catalog_contains_six_tasks_and_six_robot_families(self):
        self.assertEqual(len(PORTAL_TASKS), 6)
        self.assertEqual(len(PORTAL_ROBOTS), 6)
        for task_id in PORTAL_TASKS:
            report = validate_scene_spec(default_portal_trace_spec(task_id, "Panda", seed=3))
            self.assertTrue(report["ok"], report["errors"])

    def test_portal_prompt_selects_portal_backend(self):
        spec = SceneSpecPlanner(api_key="").plan("PORTAL-Trace 的 Wipe 任务使用 Sawyer 机械臂", seed=4)
        self.assertEqual(spec["backend"], "portal_trace")
        self.assertEqual(spec["task"]["template"], "Wipe")
        self.assertEqual(spec["robot"]["type"], "Sawyer")

    def test_generic_stack_stays_on_maniskill_backend(self):
        spec = SceneSpecPlanner(api_key="").plan("用 Panda 完成方块堆叠", seed=4)
        self.assertEqual(spec["backend"], "maniskill3")
        self.assertEqual(spec["task"]["template"], "stack")

    def test_stale_configured_python_falls_back_to_project_runtime(self):
        project_runtime = Path(__file__).parents[1].parent / ".portal_uv_env" / "Scripts" / "python.exe"
        if not project_runtime.exists():
            self.skipTest("project Portal-Trace runtime is not installed")
        with patch.dict(os.environ, {"PORTAL_TRACE_PYTHON": r"C:\missing\python.exe"}):
            self.assertEqual(portal_trace_python(), project_runtime.resolve())


if __name__ == "__main__":
    unittest.main()
