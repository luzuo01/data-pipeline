import unittest
from unittest.mock import patch

from ManiSkillGen.planner import SceneSpecPlanner
from ManiSkillGen.scene_spec import default_scene_spec
from ManiSkillGen.validator import _schema_errors


class PlannerTests(unittest.TestCase):
    def test_green_object_and_blue_target_keep_mention_order(self):
        spec = SceneSpecPlanner(api_key="").plan("把绿色方块放到蓝色目标区域")
        self.assertEqual(spec["scene"]["objects"][0]["color"], [0.1, 0.75, 0.2, 1.0])
        self.assertEqual(spec["scene"]["objects"][1]["color"], [0.1, 0.2, 0.9, 1.0])

    def test_prompt_contains_actual_schema_and_nested_examples(self):
        text = SceneSpecPlanner.system_prompt()
        self.assertIn('"$defs"', text)
        self.assertIn('"object_in_target"', text)
        self.assertIn('"front": {', text)
        self.assertIn('"object_id": "cube"', text)

    def test_model_schema_error_gets_one_format_retry_with_original_intent(self):
        bad = default_scene_spec()
        bad["task"] = "pick_and_place"
        bad["success"] = {"predicates": [{"type": "object_in_region"}]}
        bad["cameras"] = [{"id": "front"}]
        good = default_scene_spec()
        planner = SceneSpecPlanner(api_key="", allow_fallback=False)
        with patch.object(planner, "_call_model", side_effect=[bad, good]) as call:
            result = planner.plan("把红色方块放到蓝色区域", seed=1)
        self.assertEqual(call.call_count, 2)
        self.assertIn("把红色方块放到蓝色区域", call.call_args.args[0])
        self.assertFalse(_schema_errors(result))
        self.assertEqual(result["planner"]["source"], "llm")
        self.assertEqual(result["planner"]["format_repair"]["status"], "corrected")
        self.assertEqual(result["planner"]["format_repair"]["original_candidate"]["task"], "pick_and_place")

    def test_repeated_bad_model_output_is_preserved_not_replaced_by_template(self):
        bad = default_scene_spec()
        bad["task"] = "unsupported task"
        planner = SceneSpecPlanner(api_key="")
        with patch.object(planner, "_call_model", return_value=bad) as call:
            result = planner.plan("explicit task")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["task"], "unsupported task")
        self.assertEqual(result["planner"]["format_repair"]["status"], "failed")

    def test_panda_prompt_is_preserved_in_scene_spec(self):
        spec = SceneSpecPlanner(api_key="").plan("生成一个 panda 机械臂在桌面上抓取方块的场景。", seed=1)
        self.assertEqual(spec["robot"]["type"], "panda")

    def test_local_parser_composes_shape_count_colors_and_task(self):
        spec = SceneSpecPlanner(api_key="").plan("Panda stack two green spheres", seed=1)
        self.assertEqual(spec["backend"], "maniskill3")
        self.assertEqual(spec["task"]["template"], "stack")
        self.assertEqual(len(spec["scene"]["objects"]), 3)  # two spheres + target
        self.assertEqual(spec["scene"]["objects"][0]["shape"], "sphere")
        self.assertEqual(spec["scene"]["objects"][0]["color"], [0.1, 0.75, 0.2, 1.0])
        self.assertIn("parsed", spec["planner"])

    def test_planner_emits_deterministic_subtask_plan_for_portal_stack(self):
        spec = SceneSpecPlanner(api_key="").plan("PORTAL-Trace Sawyer stack two cubes", seed=2)
        subtasks = spec["planner"]["subtasks"]
        self.assertEqual([item["id"] for item in subtasks], [
            "parse_intent", "resolve_robot", "resolve_task", "validate_scene", "generate_scene", "verify_task"
        ])
        self.assertEqual(subtasks[1]["ue_asset"], "/Game/Robots/PORTALTrace/Sawyer/Sawyer")
        self.assertEqual(subtasks[2]["task"], "Stack")


if __name__ == "__main__":
    unittest.main()
