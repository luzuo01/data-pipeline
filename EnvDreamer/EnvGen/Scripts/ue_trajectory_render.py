"""Replay measured SAPIEN states into UE; UE is only the white-model renderer."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import h5py
import imageio.v2 as imageio
import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

from .ue_scene_runtime import capture_snapshot, clear_pipeline_actors

# The legacy EnvGen worker is normally launched from ``EnvGen`` and exposes
# ``Scripts`` as a top-level namespace.  ManiSkillGen imports this renderer
# from the repository root, where that namespace does not exist.  Support
# both launch layouts so the LeRobot pipeline can render UE without relying on
# the caller's current working directory.
try:
    from Scripts.panda_demo import ROOT, DEFAULT_OBJECT, call, run_demo
    from Scripts.grasp_pipeline import write_json
except ModuleNotFoundError as exc:
    if exc.name not in {"Scripts", "Scripts.panda_demo", "Scripts.grasp_pipeline"}:
        raise
    from EnvGen.Scripts.panda_demo import ROOT, DEFAULT_OBJECT, call, run_demo
    from EnvGen.Scripts.grasp_pipeline import write_json


def world_to_ue(position):
    return np.asarray(position) * [100., -100., 100.] + [1530., 0., 0.]


def object_transform(pose, definition):
    pose = np.asarray(pose).copy()
    if definition['shape'] == 'cylinder':
        # SAPIEN cylinder axis is X; UE primitive axis is Z.
        q = Rotation.from_quat(pose[[4,5,6,3]]) * Rotation.from_euler('y', -np.pi/2)
        x,y,z,w = q.as_quat()
        pose[3:7] = [w,x,y,z]
    return transform(pose, definition['size'])


def transform(pose, scale):
    x, y, z = world_to_ue(pose[:3]).tolist()
    w, qx, qy, qz = map(float, pose[3:7])
    return {'NewTransform': {'Translation': dict(X=x, Y=y, Z=z),
        'Rotation': dict(X=-qx, Y=qy, Z=-qz, W=w),
        'Scale3D': dict(zip(('X', 'Y', 'Z'), map(float, scale)))},
        'bSweep': False, 'bTeleport': True}


def actor_exists(base, builder, actor_id):
    try:
        payload = json.loads(call('RC_GetActorInfo', {'Id': actor_id}, base, builder,
                                  generate_transaction=False)['ReturnValue'])
        return 'error' not in payload and 'location' in payload
    except Exception:
        return False


def configure_preview_camera(base, builder):
    """Set the stable tabletop camera without re-adding the whole demo scene."""
    room = json.loads((ROOT / 'Config/panda_tabletop_layout.json').read_text())['rooms']['panda_workspace']
    offset = (1250, -200, 0)
    x, y, z = [a + b for a, b in zip(room['camera']['position'], offset)]
    call('RC_SetCameraLocation', {'X': x, 'Y': y, 'Z': z}, base, builder,
         generate_transaction=False)
    pitch, yaw, roll = room['camera']['rotation']
    call('RC_SetCameraRotation', {'Pitch': pitch, 'Yaw': yaw, 'Roll': roll}, base, builder,
         generate_transaction=False)


def apply_operations(base, operations):
    # UE 5.6's /remote/batch crashes on native material operations in this
    # project. Individual calls are reliable and preserve ordering.
    return [call(fn, params, base, path, generate_transaction=False) for path, fn, params in operations]


def task_actor_ids(spec):
    return {obj['id']:f'panda_demo_task_{i}' for i,obj in enumerate(spec['scene']['objects'])}


def capture(rc, name, destination):
    return capture_snapshot(rc, name + '_' + str(time.time_ns()), destination)



def encode_frames(frames_dir, output, *, fps=20, frame_count=None):
    """Encode a numbered UE frame directory as an H.264 MP4."""
    frames_dir = Path(frames_dir)
    output = Path(output)
    files = sorted(frames_dir.glob("*.png"))
    if frame_count is not None:
        files = files[:int(frame_count)]
    if not files:
        raise ValueError(f"no UE frames found in {frames_dir}")
    partial = output.with_suffix(".partial.mp4")
    with imageio.get_writer(str(partial), fps=float(fps), codec="libx264", quality=8,
                            pixelformat="yuv420p", macro_block_size=2,
                            ffmpeg_params=["-movflags", "+faststart"]) as writer:
        for path in files:
            writer.append_data(imageio.imread(path))
    partial.replace(output)
    return output


def setup_scene(spec, base, builder):
    rc=lambda fn,**p:call(fn,p,base,builder,generate_transaction=False)
    call('EditorSetGameView', {'bGameView':True,'ViewportConfigKey':'None'}, base,
         '/Script/LevelEditor.Default__LevelEditorSubsystem',generate_transaction=False)
    clear_pipeline_actors(base, builder)
    run_demo(base_url=base, object_path=builder, snapshot=False)
    reuse_existing = False
    for actor_id in ['panda_demo_green_cube', 'panda_demo_blue_target']:
        rc('RC_Remove', Id=actor_id)
    def add(actor_id,position,size,color,shape="box"):
        x,y,z=world_to_ue(position).tolist()
        mesh={'box':'Cube','target':'Cube','cylinder':'Cylinder','sphere':'Sphere'}[shape]
        rc('RC_AddAsset',Id=actor_id,AssetPath=f'/Engine/BasicShapes/{mesh}.{mesh}',X=x,Y=y,Z=z,
           Pitch=0,Yaw=0,Roll=0,ScaleX=size[0],ScaleY=size[1],ScaleZ=size[2],Color=dict(zip('RGBA',color)))
    workspace=spec['scene']['workspace']
    width,depth=workspace['size'][:2]
    height=workspace['table_height']
    def update_or_add(actor_id, position, size, color, shape="box"):
        if reuse_existing:
            x, y, z = world_to_ue(position).tolist()
            try:
                rc('RC_SetLocation', Id=actor_id, X=x, Y=y, Z=z)
                rc('RC_SetScale', Id=actor_id, SX=float(size[0]), SY=float(size[1]), SZ=float(size[2]))
                rc('RC_SetMaterialColor', Id=actor_id, Color=dict(zip('RGBA', color)))
                return
            except Exception:
                # A stale actor ID means the editor is only partially warm;
                # fall through to one explicit add operation.
                pass
        add(actor_id, position, size, color, shape)
    update_or_add('panda_demo_work_table',[0,0,height-.025],[width,depth,.05],[.48,.3,.15,1])
    for i,(x,y) in enumerate([(-width/2+.1,-depth/2+.1),(width/2-.1,-depth/2+.1),
                              (-width/2+.1,depth/2-.1),(width/2-.1,depth/2-.1)]):
        update_or_add(f'panda_demo_leg_{i}',[x,y,(height-.05)/2],[.04,.04,height-.05],[.15,.15,.15,1])
    mapping=task_actor_ids(spec)
    for obj in spec['scene']['objects']:
        update_or_add(mapping[obj['id']],obj['pose'],obj['size'],obj['color'],obj['shape'])
    base_position=list(spec['robot']['base_pose'])
    if abs(base_position[2])<1e-6:
        base_position[2]=height
    x,y,z=world_to_ue(base_position).tolist()
    rc('RC_SetLocation',Id='panda_demo_panda_robot',X=x,Y=y,Z=z)
    update_or_add('panda_demo_robot_mount',
                  [base_position[0]-.04, base_position[1], base_position[2]/2],
                  [.32, .30, base_position[2]], [.28,.28,.28,1])
    return rc


def preview_scene(spec,directory):
    base=os.environ.get('RC_BASE_URL','http://127.0.0.1:30010')
    builder=os.environ.get('RC_OBJECT_PATH',DEFAULT_OBJECT)
    rc=setup_scene(spec,base,builder)
    capture(rc,'initial_'+directory.name,directory/'scene_initial.png')


def render_trajectory(directory, progress):
    base = os.environ.get('RC_BASE_URL', 'http://127.0.0.1:30010')
    builder = os.environ.get('RC_OBJECT_PATH', DEFAULT_OBJECT)
    rc = lambda fn, **params: call(fn, params, base, builder, generate_transaction=False)
    with h5py.File(directory / 'trajectory.h5', 'r') as h5:
        if not h5.attrs['complete'] or not h5['observations/success'][-10:].all():
            raise ValueError('Only complete, successful physical trajectories may be rendered')
        qpos = h5['observations/qpos'][:]
        tcp = h5['observations/tcp_pose'][:]
        robot_base = h5['observations/robot_base_pose'][:]
        timestamps = h5['timestamps'][:]
        objects = {g.attrs['id']: g['state'][:] for g in h5['objects'].values()}
        metadata = json.loads(h5['metadata_json'][()])
    progress('ue_setup', '配置 UE 白模材质和逐帧轨迹回放', total_frames=len(qpos))
    spec=metadata['scene_spec']
    setup_scene(spec,base,builder)
    robot_id = 'panda_demo_panda_robot'
    def actor_path(actor_id):
        info = json.loads(rc('RC_GetActorInfo', Id=actor_id)['ReturnValue'])
        return builder.rsplit('.', 1)[0] + '.' + info['name']
    robot_path = actor_path(robot_id)
    object_ids = task_actor_ids(spec)
    object_paths = {key: actor_path(value) for key, value in object_ids.items()}
    definitions = {obj['id']: obj for obj in metadata['scene_spec']['scene']['objects']}
    components = json.loads(rc('RC_GetComponentInfo', Id=robot_id)['OutJson'])['components']
    material_ops = []
    for component in components:
        if component['class'] != 'StaticMeshComponent':
            continue
        path = robot_path + '.' + component['name']
        slots = call('GetNumMaterials', {}, base, path)['ReturnValue']
        for slot in range(slots):
            material_ops.append((path, 'SetMaterial', {'ElementIndex': slot,
                'Material': '/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial'}))
    apply_operations(base, material_ops)
    for name, gray in [('work_table', .45)] + [(f'leg_{i}', .3) for i in range(4)]:
        rc('RC_SetMaterialColor', Id='panda_demo_' + name, Color=dict(R=gray,G=gray,B=gray,A=1))
    for obj_id,actor_id in object_ids.items():
        gray=.9 if obj_id==spec['task']['object_id'] else .25
        rc('RC_SetMaterialColor',Id=actor_id,Color=dict(R=gray,G=gray,B=gray,A=1))
    frames_dir = directory / 'ue_frames'
    frames_dir.mkdir(exist_ok=True)
    shot_prefix = 'grasp_' + directory.name + '_' + str(time.time_ns())
    max_tcp_error, max_object_error, max_joint_error = 0., 0., 0.
    frame_map = []
    reused_frame_count = 0
    last_frame = None
    capture_stride = 1
    # Let material compilation / the viewport settle before the first capture.
    time.sleep(2)
    for index, joints in enumerate(qpos):
        operations = [(robot_path, 'K2_SetActorTransform', transform(robot_base[index], [1,1,1])),
            (builder, 'RC_SetRobotJoints', {'Id': robot_id, **{f'J{i+1}':float(v) for i,v in enumerate(joints[:7])}}),
            (builder, 'RC_SetRobotGripper', {'Id': robot_id, 'OpeningMeters':float(np.clip(joints[7:9].sum(),0,.08))})]
        for key, path in object_paths.items():
            operations.append((path, 'K2_SetActorTransform', object_transform(objects[key][index], definitions[key])))
        apply_operations(base, operations)
        actual = json.loads(rc('RC_GetRobotState', Id=robot_id)['ReturnValue'])
        error = float(np.linalg.norm(np.array(actual['tcp_world_cm']) - world_to_ue(tcp[index,:3])))
        max_tcp_error = max(max_tcp_error, error)
        max_joint_error = max(max_joint_error, float(np.max(np.abs(np.array(actual['joint_positions'])-joints[:7]))))
        if error > .05 or max_joint_error > 1e-5:
            raise ValueError(f'UE/physical robot pose mismatch at observation {index}: {error} cm')
        for key, actor_id in object_ids.items():
            info = json.loads(rc('RC_GetActorInfo', Id=actor_id)['ReturnValue'])
            actual_xyz = [info['location'][k] for k in ('x','y','z')]
            obj_error = float(np.linalg.norm(np.array(actual_xyz)-world_to_ue(objects[key][index,:3])))
            max_object_error = max(max_object_error, obj_error)
            if obj_error > .02:
                raise ValueError(f'UE object pose mismatch: {key}, observation {index}')
        shot_name = f'{shot_prefix}_{index:05d}'
        filename = f'{index:05d}.png'
        timeout = float(os.environ.get('UE_FIRST_CAPTURE_TIMEOUT_S' if index == 0 else 'UE_CAPTURE_TIMEOUT_S',
                                      '90' if index == 0 else '15'))
        capture_snapshot(rc, shot_name, frames_dir / filename, timeout=timeout)
        with Image.open(frames_dir / filename) as img:
            frame = img.convert('RGB')
        filename = f'{index:05d}.png'
        frame.save(frames_dir / filename)
        last_frame = frame.copy()
        frame_map.append({'frame':index,'observation_index':index,'time_s':float(timestamps[index]),
                          'file':'ue_frames/'+filename,'sha256':hashlib.sha256((frames_dir/filename).read_bytes()).hexdigest()})
        if index % 5 == 0 or index == len(qpos)-1:
            progress('ue_render', 'UE 正在渲染白模视频', frames=index+1, total_frames=len(qpos))
    progress('encode', '正在编码和验证白模 MP4', frames=len(qpos),total_frames=len(qpos))
    fps = 1. / metadata['control_dt']
    partial = directory / 'ue_white.partial.mp4'
    with imageio.get_writer(str(partial), fps=fps, codec='libx264', quality=8,
                            pixelformat='yuv420p', macro_block_size=2, ffmpeg_params=['-movflags','+faststart']) as writer:
        for entry in frame_map:
            writer.append_data(imageio.imread(directory / entry['file']))
    with imageio.get_reader(str(partial)) as reader:
        count = reader.count_frames()
        video_metadata = reader.get_meta_data()
        for i in (0, len(qpos)//2, len(qpos)-1):
            if reader.get_data(i).shape != (540,960,3):
                raise ValueError('Unexpected video resolution')
    if count != len(qpos) or abs(video_metadata['fps']-fps) > .001:
        raise ValueError('Video/trajectory frame alignment failed')
    if len({entry['sha256'] for entry in frame_map}) < 10:
        raise ValueError('Video appears frozen')
    partial.replace(directory / 'ue_white.mp4')
    manifest = {'renderer':'Unreal Engine 5.6.1', 'physics_engine':'ManiSkill3 / SAPIEN PhysX',
        'mode':'measured_state_replay_not_UE_physics', 'frames':len(qpos),'fps':fps,
        'duration_s':len(qpos)/fps,'resolution':[960,540],
        'world_mapping':'UE_cm = [1530,0,0] + [100,-100,100] * SAPIEN_world_m',
        'camera_ue_cm':[1420,-180,185], 'camera_pitch_yaw_roll_deg':[-21,59,0],
        'camera_note':'UE video camera differs from the SAPIEN GT camera in trajectory.h5',
        'max_tcp_error_cm':max_tcp_error,'max_object_position_error_cm':max_object_error,
        'max_joint_error_rad':max_joint_error,'robot_material_slots_overridden':len(material_ops),
        'reused_frame_count':reused_frame_count,'capture_stride':capture_stride,
        'robot_mount':'static support pedestal; robot base and trajectory are unchanged',
        'gripper_note':'UE symmetric fingers use summed measured finger positions clipped to [0,.08] m; raw individual positions are preserved in HDF5',
        'frame_map':frame_map}
    write_json(directory / 'ue_render.json', manifest)
    # Leave the UE scene in its successful final pose with requested colors.
    for obj in spec['scene']['objects']:
        rc('RC_SetMaterialColor',Id=object_ids[obj['id']],Color=dict(zip('RGBA',obj['color'])))
    capture(rc,'final_'+directory.name,directory/'scene_final.png')
    return {key:value for key,value in manifest.items() if key != 'frame_map'}
