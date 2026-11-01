import tempfile
import unittest
from pathlib import Path

from ManiSkillGen.generator import ManiSkillSceneGenerator
from ManiSkillGen.scene_spec import default_scene_spec


class GeneratorSmokeTests(unittest.TestCase):
    def test_generate_is_deterministic_for_same_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            generator = ManiSkillSceneGenerator(Path(directory), prefer_maniskill=False)
            first = generator.generate(default_scene_spec(), seed=13, render=True)
            second = generator.generate(default_scene_spec(), seed=13, render=True)
            self.assertEqual(first["scene_spec"], second["scene_spec"])
            self.assertTrue(Path(first["preview_path"]).exists())
            self.assertTrue(Path(first["manifest"]["manifest_path"]).exists())
            self.assertEqual(first["manifest"]["runtime_backend"], "software_fallback")


if __name__ == "__main__":
    unittest.main()
