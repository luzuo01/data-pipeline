import json
from pathlib import Path
import numpy as np
import pytest
from ManiSkillGen.planner import SceneSpecPlanner
from ManiSkillGen.local_intent import COLORS
PROMPTS=json.loads((Path(__file__).parents[3]/'scripts/five_case_prompts.json').read_text(encoding='utf-8-sig'))

def spec_for(n):return SceneSpecPlanner(api_key='').plan(PROMPTS[n-1])

def test_cylinder_push_preserves_shape_color_and_action():
 s=spec_for(2);by={o['id']:o for o in s['scene']['objects']}
 assert s['task']['template']=='push'
 assert by[s['task']['object_id']]['shape']=='cylinder'
 assert by[s['task']['object_id']]['color']==COLORS['yellow']
 assert by[s['task']['target_id']]['color']==COLORS['green']

def test_stack_resolves_moving_and_support_roles():
 s=spec_for(3);by={o['id']:o for o in s['scene']['objects']};m=by[s['task']['object_id']];t=by[s['task']['target_id']]
 assert m['color']==COLORS['red'] and t['color']==COLORS['blue']
 assert m['size'][0]<t['size'][0]
 assert s['success'][0]['object_id']==m['id']

def test_selected_green_object_and_three_distinct_colors():
 s=spec_for(4);by={o['id']:o for o in s['scene']['objects']}
 assert len([o for o in by.values() if o['dynamic']])==3
 assert by[s['task']['object_id']]['color']==COLORS['green']
 assert by[s['task']['target_id']]['color']==COLORS['blue']
 assert {tuple(o['color']) for o in by.values() if o['dynamic']}=={tuple(COLORS[c]) for c in ['red','green','yellow']}

def test_unsupported_liquid_and_cloth_never_replaced_by_default():
 with pytest.raises(ValueError,match='不支持'):spec_for(5)

def test_ue_cylinder_axis_correction():
 from EnvGen.Scripts.ue_trajectory_render import object_transform
 t=object_transform([0,0,.78,2**-.5,0,2**-.5,0],{'shape':'cylinder','size':[.05,.05,.06]})['NewTransform']
 np.testing.assert_allclose([t['Rotation'][k] for k in ['X','Y','Z','W']],[0,0,0,1],atol=1e-7)
 assert t['Scale3D']=={'X':.05,'Y':.05,'Z':.06}
