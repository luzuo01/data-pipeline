"""Compile user requests into validated, executable Panda tabletop tasks."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ManiSkillGen.planner import SceneSpecPlanner
from ManiSkillGen.validator import validate_scene_spec

EXAMPLE = 'Panda 机械臂夹起一个边长 4 厘米的红色方块，放在边长 6 厘米的绿色方块上。两个方块初始相距 20 厘米，均在可达范围内。松开夹爪并撤离，堆叠后稳定至少 0.5 秒。'


class UETaskPlanner(SceneSpecPlanner):
    @staticmethod
    def system_prompt():
        from Scripts.grasp_pipeline import grasp_spec
        example = grasp_spec()
        example['scene']['objects'][0]['color'] = [.85,.1,.1,1.]
        example['scene']['objects'][1].update(asset='primitive_cube',size=[.06]*3,
            pose=[.1,-.1,.78],color=[.1,.75,.2,1.],dynamic=True)
        example['task']['template'] = 'stack'
        example['success'][0]['predicate'] = 'object_on_object'
        return SceneSpecPlanner.system_prompt() + (
            '\nThis request runs in the UE + SAPIEN Panda bridge. Only backend maniskill3, '
            'Panda/franka, box primitives, and pick_and_place or stack are supported. '
            'Do not use portal_trace. If request is unsupported return {"unsupported": "Chinese explanation"}. '
            'For stacking A onto B: task.object_id refers to A (moved), task.target_id refers to B (support). '
            'Both are primitive_cube, dynamic true, initially separate and resting on the table. '
            'For place-in-region use primitive_target, dynamic false for the region. '
            'Preserve specified colors, sizes, distances and explicit positions; never silently change an unreachable request. '
            'Default tabletop size [1.2,.8,.5], table_height .75, robot base [-.3,0,.75]. '
            'Default object XY [.1,.1], target XY [.1,-.1], separation .2 m. '
            'Default moved cube side .04 m, stack support side .06 m. '
            'Set randomization={} and repair={"allow_pose_changes":false}. '
            'The action skill verifies release and stability for >=0.5 seconds; do not invent additional success schema fields. '
            'Store requested stable duration as top-level stability_seconds (default .5, range .5 to 5). '
            'Use pd_joint_pos and front resolution [320,240]. '
            'Exact stack example: '+json.dumps(example,ensure_ascii=False))


def plan_task(prompt):
    if not prompt or not prompt.strip():
        raise ValueError('请输入机械臂任务描述。')
    spec = UETaskPlanner(allow_fallback=False).plan(prompt.strip())
    if spec.get('unsupported'):
        raise ValueError(spec['unsupported'])
    # LLMs often preserve the user-facing capitalisation ("Panda", "Franka").
    # SceneSpec validation and the ManiSkill loader use canonical lowercase IDs.
    robot_value = str(spec.get('robot', {}).get('type', '')).strip().lower()
    robot_alias = {'panda':'panda', 'franka':'franka', 'franka panda':'panda'}
    if robot_value in robot_alias:
        spec['robot']['type'] = robot_alias[robot_value]
    control_value = str(spec.get('robot', {}).get('control_mode', '')).strip().lower()
    if control_value:
        spec['robot']['control_mode'] = control_value
    if spec.get('backend') != 'maniskill3' or spec.get('task',{}).get('template') not in ('stack','pick_and_place'):
        raise ValueError('当前执行入口支持 Panda 方块抓取放置和堆叠任务。')
    objects = spec.get('scene',{}).get('objects',[])
    if not 2 <= len(objects) <= 8 or any(o.get('shape') != 'box' for o in objects):
        raise ValueError('当前 UE 任务桥接支持 2 至 8 个方块或方形目标区。')
    if spec.get('robot',{}).get('type','').lower() not in ('panda','franka'):
        raise ValueError('当前执行入口仅支持 Panda 机械臂。')
    task=spec['task']
    definitions={o['id']:o for o in objects}
    moved=definitions.get(task.get('object_id'),{})
    support=definitions.get(task.get('target_id'),{})
    if not moved.get('dynamic') or moved.get('asset') != 'primitive_cube':
        raise ValueError('抓取对象必须是可运动的实体方块。')
    if task['template']=='stack' and (support.get('asset')!='primitive_cube' or not support.get('dynamic')):
        raise ValueError('堆叠支撑物必须是有碰撞的实体方块，不能是目标区域。')
    expected='object_on_object' if task['template']=='stack' else 'object_in_target'
    if not spec.get('success') or any(p.get('predicate')!=expected or p.get('object_id')!=task['object_id']
        or p.get('target_id')!=task['target_id'] for p in spec['success']):
        raise ValueError('成功判据与任务的抓取对象、目标或堆叠关系不一致。')
    spec['randomization'] = {}
    spec['repair'] = {'allow_pose_changes':False,'locked_object_ids':[o['id'] for o in objects]}
    spec['robot']['control_mode'] = 'pd_joint_pos'
    spec['cameras']['front']['resolution'] = [320,240]
    duration = float(spec.get('stability_seconds',.5))
    if not .5 <= duration <= 5:
        raise ValueError('稳定验证时长须在 0.5 至 5 秒之间。')
    spec['stability_seconds'] = duration
    report = validate_scene_spec(spec, check_runtime=False)
    if not report['ok']:
        raise ValueError('场景规格验证失败：'+json.dumps(report['errors'],ensure_ascii=False))
    return spec
