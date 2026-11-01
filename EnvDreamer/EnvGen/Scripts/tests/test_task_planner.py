import copy
import unittest
from unittest.mock import patch

from Scripts.grasp_pipeline import grasp_spec
from Scripts.task_planner import plan_task,UETaskPlanner


def stack_spec():
    spec=grasp_spec()
    spec['task']={'template':'stack','object_id':'red','target_id':'green'}
    a,b=spec['scene']['objects']
    a.update(id='red',color=[.85,.1,.1,1])
    b.update(id='green',asset='primitive_cube',size=[.06]*3,pose=[.1,-.1,.78],color=[.1,.75,.2,1],dynamic=True)
    spec['success']=[{'predicate':'object_on_object','object_id':'red','target_id':'green','position_tolerance':.03}]
    spec['stability_seconds']=1.0
    return spec


class TaskPlannerTests(unittest.TestCase):
    def test_preserves_roles_colors_dimensions_and_stability(self):
        candidate=stack_spec()
        with patch.object(UETaskPlanner,'plan',return_value=copy.deepcopy(candidate)):
            result=plan_task('把红色方块放在绿色方块上，稳定1秒')
        self.assertEqual(result['task'],candidate['task'])
        self.assertEqual(result['scene']['objects'],candidate['scene']['objects'])
        self.assertEqual(result['stability_seconds'],1)

    def test_stack_cannot_use_noncolliding_target_region(self):
        candidate=stack_spec()
        candidate['scene']['objects'][1]['asset']='primitive_target'
        with patch.object(UETaskPlanner,'plan',return_value=candidate),self.assertRaisesRegex(ValueError,'碰撞'):
            plan_task('stack')

    def test_unreachable_explicit_position_is_not_repaired_into_success(self):
        candidate=stack_spec()
        candidate['scene']['objects'][0]['pose'][0]=5
        with patch.object(UETaskPlanner,'plan',return_value=candidate),self.assertRaises(ValueError):
            plan_task('红色方块固定在5米外')

    def test_reversed_success_roles_rejected(self):
        candidate=stack_spec()
        candidate['success'][0].update(object_id='green',target_id='red')
        with patch.object(UETaskPlanner,'plan',return_value=candidate),self.assertRaisesRegex(ValueError,'不一致'):
            plan_task('red on green')


if __name__=='__main__': unittest.main()
