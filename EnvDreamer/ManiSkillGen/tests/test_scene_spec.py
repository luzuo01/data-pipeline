import unittest

from ManiSkillGen.scene_spec import default_scene_spec
from ManiSkillGen.validator import validate_scene_spec


class SceneSpecTests(unittest.TestCase):
    def test_default_spec_is_valid(self):
        report = validate_scene_spec(default_scene_spec(7))
        self.assertTrue(report["ok"], report["errors"])

    def test_unknown_object_is_rejected(self):
        spec = default_scene_spec()
        spec["scene"]["objects"][0]["asset"] = "does_not_exist"
        report = validate_scene_spec(spec)
        self.assertFalse(report["ok"])
        self.assertTrue(any("not registered" in error for error in report["errors"]))

    def test_out_of_bounds_object_is_rejected(self):
        spec = default_scene_spec()
        spec["scene"]["objects"][0]["pose"] = [1.0, 0.0, 0.77]
        report = validate_scene_spec(spec)
        self.assertFalse(report["ok"])
        self.assertTrue(any("outside workspace" in error for error in report["errors"]))

    def test_task_feasibility_rejects_goal_smaller_than_object(self):
        spec = default_scene_spec()
        spec["scene"]["objects"][1]["size"] = [0.03, 0.03, 0.01]
        report = validate_scene_spec(spec)
        self.assertFalse(report["ok"])
        self.assertEqual(report["task_feasibility"]["status"], "infeasible")
        self.assertTrue(any(check["name"] == "goal_fit" and check["status"] == "fail" for check in report["task_feasibility"]["checks"]))

    def test_task_feasibility_is_explicitly_necessary_conditions_only(self):
        report = validate_scene_spec(default_scene_spec())
        self.assertEqual(report["task_feasibility"]["status"], "feasible")
        self.assertEqual(report["task_feasibility"]["proof_level"], "necessary_conditions_only")


if __name__ == "__main__":
    unittest.main()
