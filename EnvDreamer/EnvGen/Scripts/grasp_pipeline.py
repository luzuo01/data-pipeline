"""Physical pick/place in SAPIEN, state-aligned UE white rendering, and export.

Run with the Python environment containing ManiSkill, h5py and imageio-ffmpeg.
The UE editor is a renderer; success is decided by the physical rollout.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def grasp_spec():
    from ManiSkillGen.scene_spec import default_scene_spec
    spec = default_scene_spec(0)
    spec['description'] = 'Physically grasp the 4 cm cube, lift, transfer to target, release and settle.'
    spec['randomization'] = {}
    spec['repair'] = {'allow_pose_changes': False}
    spec['scene']['workspace']['size'] = [1.2, .8, .5]
    spec['robot']['base_pose'] = [-.3, 0., .75]
    spec['robot']['control_mode'] = 'pd_joint_pos'
    spec['scene']['objects'][0].update(pose=[.1, .1, .77], color=[.1, .8, .1, 1])
    spec['scene']['objects'][1].update(pose=[.1, -.2, .751], size=[.1, .1, .002])
    spec['cameras']['front'].update(resolution=[320, 240])
    return spec


def simulate(directory, progress, spec=None):
    import h5py
    import numpy as np
    from ManiSkillGen.generator import ManiSkillSceneGenerator
    from ManiSkillGen.trajectory import TrajectoryGenerator
    from ManiSkillGen.gt_exporter import GTExporter

    spec = spec or grasp_spec()
    write_json(directory / 'scene_spec.json', spec)
    progress('scene', '正在构建真实物理抓取场景')
    generator = ManiSkillSceneGenerator(directory / 'scene_audit', allow_software_fallback=False)
    instance = generator.generate(spec, seed=0, render=False, auto_repair=False)
    try:
        spec = instance['scene_spec']
        write_json(directory / 'scene_spec.json', spec)
        from Scripts.ue_trajectory_render import preview_scene
        progress('ue_scene', '正在 UE 生成任务对应的彩色场景')
        preview_scene(spec, directory)
        trajectory = TrajectoryGenerator().generate(instance, on_progress=lambda stage, steps:
            progress('physics', f'物理执行：{stage}', control_steps=steps))
        states = trajectory['states']
        object_id, target_id = spec['task']['object_id'], spec['task']['target_id']
        cube = np.stack([s['objects'][object_id] for s in states])
        target = np.stack([s['objects'][target_id] for s in states])
        hold_frames = trajectory['success_hold_frames']
        grasped = np.array([s['grasped'] for s in states])
        assert grasped.any(), 'No physical finger contact grasp was recorded'
        assert all(s['success'] and not s['grasped'] for s in states[-hold_frames:]), 'Final settled release failed'
        assert float(cube[:, 2].max()) > spec['scene']['workspace']['table_height'] + .075
        progress('export', '抓取放置成功，正在导出逐帧真值', total_frames=len(states))
        metadata = GTExporter().export(instance, trajectory, directory / 'trajectory.h5',
            on_progress=lambda n, total: progress('export', '导出仿真真值', frames=n, total_frames=total))
        proof = {'physics_engine': 'ManiSkill3 / SAPIEN PhysX', 'success': True,
                 'task':spec['task'], 'description':spec.get('description',''),
                 'initial_success': states[0]['success'], 'final_success_hold_frames': hold_frames,
                 'grasped_observation_count': int(grasped.sum()),
                 'max_cube_center_height_m': float(cube[:, 2].max()),
                 'initial_cube_pose_wxyz': cube[0, :7].tolist(),
                 'final_cube_pose_wxyz': cube[-1, :7].tolist(),
                 'target_center_m': target[-1,:3].tolist(),
                 'final_xy_error_m': float(np.linalg.norm(cube[-1, :2] - target[-1,:2])),
                 'validation': trajectory['validation'], 'gt_validation': metadata['gt_validation'],
                 'num_actions': len(trajectory['actions']), 'num_observations': len(states),
                 'control_dt_s': trajectory['control_dt'], 'no_attachment_or_object_teleport_in_physics': True}
        write_json(directory / 'success.json', proof)
        with (directory / 'trajectory.csv').open('w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['observation_index','time_s','stage','success','grasped'] +
                            [f'qpos_{i}' for i in range(9)] +
                            ['tcp_x_m','tcp_y_m','tcp_z_m','cube_x_m','cube_y_m','cube_z_m'])
            for index, state in enumerate(states):
                writer.writerow([index, index * trajectory['control_dt'],
                    trajectory['stages'][index] if index < len(trajectory['stages']) else 'terminal',
                    state['success'], state['grasped'], *state['qpos'].tolist(),
                    *state['tcp_pose'][:3].tolist(), *cube[index, :3].tolist()])
        return proof
    finally:
        instance['env'].close()


def run(directory, *, resume=False, physics_only=False, request=None):
    directory.mkdir(parents=True, exist_ok=resume)
    start = time.time()
    def progress(phase, message, **extra):
        write_json(directory / 'progress.json', {'status':'running','phase':phase,'message':message,
            'pid':os.getpid(), 'elapsed_s':round(time.time()-start,1), **extra})
    try:
        progress('planning','正在解析任务描述')
        if resume:
            proof = json.loads((directory / 'success.json').read_text(encoding='utf-8'))
            if not proof['success']:
                raise ValueError('Cannot render unsuccessful trajectory')
        else:
            spec = None
            if request:
                from Scripts.task_planner import plan_task
                data = json.loads(request.read_text(encoding='utf-8'))
                write_json(directory/'request.json',data)
                spec = plan_task(data['prompt'])
                progress('planned','任务已解析：'+spec['task']['template'],task=spec['task'])
            proof = simulate(directory, progress, spec)
        if physics_only:
            progress('physics_complete', '物理轨迹已通过，等待 UE 渲染')
            return
        from Scripts.ue_trajectory_render import render_trajectory
        render = render_trajectory(directory, progress)
        progress('lerobot', '正在生成 LeRobot v3 真值数据集')
        from Scripts.lerobot_export import convert_hdf5, zip_dataset
        dataset_root = directory / 'lerobot_v3'
        task = proof.get('task', {}).get('template') if isinstance(proof.get('task'), dict) else None
        if dataset_root.is_dir() and (dataset_root / 'meta' / 'info.json').is_file():
            converted = {'repo_id': f'local/{directory.name}',
                         'info': json.loads((dataset_root / 'meta' / 'info.json').read_text(encoding='utf-8')),
                         'manifest': json.loads((dataset_root / 'meta' / 'semantic_manifest.json').read_text(encoding='utf-8'))}
        else:
            converted = convert_hdf5(directory / 'trajectory.h5', dataset_root,
                                     repo_id=f'local/{directory.name}', task=task)
        archive = directory / 'lerobot_v3.zip'
        if not archive.is_file():
            archive = zip_dataset(dataset_root, archive)
        result = {'status':'complete','message':'真实物理轨迹、LeRobot v3 真值数据和 UE 白模视频已导出',
                  'success':proof, 'render':render, 'lerobot':{
                      'repo_id': converted['repo_id'],
                      'root': str(dataset_root),
                      'archive': str(archive),
                      'codebase_version': converted['info'].get('codebase_version'),
                      'frames': converted['manifest'].get('training_rows'),
                      'fps': converted['info'].get('fps'),
                  }, 'elapsed_s':round(time.time()-start,1)}
        result['files'] = {name: {'bytes':(directory/name).stat().st_size,
            'sha256':hashlib.sha256((directory/name).read_bytes()).hexdigest()}
            for name in ('lerobot_v3.zip','trajectory.csv','ue_white.mp4','success.json','scene_spec.json')}
        # Keep trajectory.h5 as an internal retry/render source, but do not
        # expose it as the task's final data format. The LeRobot archive also
        # contains the lossless source episode under extras/.
        write_json(directory / 'result.json', result)
        write_json(directory / 'progress.json', result)
    except Exception as exc:
        write_json(directory / 'progress.json', {'status':'failed', 'message':f'{type(exc).__name__}: {exc}',
            'elapsed_s':round(time.time()-start,1)})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--physics-only', action='store_true')
    parser.add_argument('--request', type=Path)
    args = parser.parse_args()
    run(args.output.resolve(), resume=args.resume, physics_only=args.physics_only,request=args.request)
