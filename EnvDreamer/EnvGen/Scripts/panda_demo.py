"""UE5 Panda tabletop preview. Standard-library-only; no LLM/API key needed.

Run from EnvGen: python -m Scripts.panda_demo --verify
Objects are added beside the existing room using the panda_demo_ ID prefix.
Does not overwrite home_layout.json or save the current UE map.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OBJECT = '/Game/EnvGen/Maps/ThirdPersonMap.ThirdPersonMap:PersistentLevel.BP_JsonSceneBuilder_C_UAID_BCFCE7D92346448C02_1693193303'
READY = [0, -math.pi / 4, 0, -3 * math.pi / 4, 0, math.pi / 2, math.pi / 4]


def call(fn, params, base, object_path, *, generate_transaction=True):
    body = json.dumps({'objectPath': object_path, 'functionName': fn,
                       'parameters': params, 'generateTransaction': generate_transaction}).encode()
    req = urllib.request.Request(base + '/remote/object/call', data=body,
                                 method='PUT', headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.load(response)
    if result.get('ReturnValue') is False:
        raise RuntimeError(f'{fn} rejected: {params}')
    return result


def reference_tcp(q):
    """Independent right-handed ROS homogeneous-matrix FK, in metres."""
    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
    def pose(x, y, z, roll=0, yaw=0):
        c, s, C, S = math.cos(roll), math.sin(roll), math.cos(yaw), math.sin(yaw)
        return [[C, -S, 0, x], [c*S, c*C, -s, y], [s*S, s*C, c, z], [0, 0, 0, 1]]
    origins = [(0, 0, .333, 0), (0, 0, 0, -math.pi/2),
               (0, -.316, 0, math.pi/2), (.0825, 0, 0, math.pi/2),
               (-.0825, .384, 0, -math.pi/2), (0, 0, 0, math.pi/2),
               (.088, 0, 0, math.pi/2)]
    matrix = pose(0, 0, 0)
    for (x, y, z, roll), angle in zip(origins, q):
        matrix = mul(matrix, pose(x, y, z, roll, angle))
    matrix = mul(matrix, pose(0, 0, .107, yaw=-math.pi/4))
    matrix = mul(matrix, pose(0, 0, .1034))
    return [matrix[i][3] for i in range(3)]


def run_demo(base_url='http://127.0.0.1:30010', object_path=DEFAULT_OBJECT,
             verify=False, joints=None, opening=.06, snapshot=True):
    joints = READY if joints is None else joints
    rc = lambda fn, **p: call(fn, p, base_url, object_path)
    room = json.loads((ROOT / 'Config/panda_tabletop_layout.json').read_text())['rooms']['panda_workspace']
    offset = (1250, -200, 0)
    robot_id = 'panda_demo_panda_robot'
    for item in room['items']:
        x, y, z = [a + b for a, b in zip(item['position'], offset)]
        pitch, yaw, roll = item['rotation']
        sx, sy, sz = item['scale']
        rc('RC_AddAsset', Id='panda_demo_' + item['id'], AssetPath=item['asset'],
           X=x, Y=y, Z=z, Pitch=pitch, Yaw=yaw, Roll=roll,
           ScaleX=sx, ScaleY=sy, ScaleZ=sz, Color=dict(zip('RGBA', item['color'])))
    # Table legs are separate basic primitives, with an accurately known pivot.
    for i, (x, y) in enumerate([(1480, -30), (1580, -30), (1480, 30), (1580, 30)]):
        rc('RC_AddAsset', Id=f'panda_demo_leg_{i}', AssetPath='/Engine/BasicShapes/Cube.Cube',
           X=x, Y=y, Z=35, Pitch=0, Yaw=0, Roll=0,
           ScaleX=.04, ScaleY=.04, ScaleZ=.70, Color=dict(R=.12, G=.14, B=.16, A=1))
    rc('RC_AddAsset', Id='panda_demo_floor', AssetPath='/Engine/BasicShapes/Cube.Cube',
       X=1530, Y=0, Z=-2.5, Pitch=0, Yaw=0, Roll=0,
       ScaleX=4, ScaleY=4, ScaleZ=.05, Color=dict(R=.3, G=.32, B=.35, A=1))
    report = {'kind': 'kinematic_preview', 'checks': []}
    if verify:
        poses = [READY, [.2, -.3, .1, -1.8, .2, 1.4, .3],
                 [-.4, -.7, .5, -2.2, -.3, 1.9, -.6]]
        for q in poses:
            rc('RC_SetRobotJoints', Id=robot_id, **{f'J{i+1}': v for i, v in enumerate(q)})
            state = json.loads(rc('RC_GetRobotState', Id=robot_id)['ReturnValue'])
            ros = reference_tcp(q)
            expected = [1500 + 100 * ros[0], -100 * ros[1], 75 + 100 * ros[2]]
            error = math.dist(expected, state['tcp_world_cm'])
            assert error < .01, (expected, state, error)
            report['checks'].append({'joints_rad': q, 'tcp_world_cm': state['tcp_world_cm'], 'error_cm': error})
        # Exercise both gripper endpoints and verify state round-trips.
        for gap in [0., .08]:
            rc('RC_SetRobotGripper', Id=robot_id, OpeningMeters=gap)
            state = json.loads(rc('RC_GetRobotState', Id=robot_id)['ReturnValue'])
            assert abs(state['gripper_opening_m'] - gap) < 1e-6
        report['gripper_endpoints_passed'] = True
        before = json.loads(rc('RC_GetRobotState', Id=robot_id)['ReturnValue'])
        try:
            rc('RC_SetRobotJoints', Id=robot_id, **{f'J{i+1}': 100. for i in range(7)})
        except RuntimeError as error:
            assert 'RC_SetRobotJoints rejected' in str(error)
        else:
            raise AssertionError('Out-of-limit joints must be rejected')
        assert json.loads(rc('RC_GetRobotState', Id=robot_id)['ReturnValue']) == before
        report['joint_limits_passed'] = True
    rc('RC_SetRobotJoints', Id=robot_id, **{f'J{i+1}': v for i, v in enumerate(joints)})
    rc('RC_SetRobotGripper', Id=robot_id, OpeningMeters=opening)
    x, y, z = [a + b for a, b in zip(room['camera']['position'], offset)]
    rc('RC_SetCameraLocation', X=x, Y=y, Z=z)
    pitch, yaw, roll = room['camera']['rotation']
    rc('RC_SetCameraRotation', Pitch=pitch, Yaw=yaw, Roll=roll)
    if not snapshot:
        report['state'] = json.loads(rc('RC_GetRobotState', Id=robot_id)['ReturnValue'])
        return report
    time.sleep(2)
    shot = ROOT / 'Saved/AIShots/panda_tabletop.png'
    old_time = shot.stat().st_mtime_ns if shot.exists() else -1
    result = rc('RC_Snapshot', Name='panda_tabletop', ResX=1280, ResY=720)
    for _ in range(30):
        if shot.exists() and shot.stat().st_mtime_ns > old_time:
            break
        time.sleep(1)
    else:
        raise RuntimeError('Scene created, but screenshot timed out. Activate the UE viewport and retry.')
    report['snapshot'] = result.get('OutPath')
    report['state'] = json.loads(rc('RC_GetRobotState', Id=robot_id)['ReturnValue'])
    report_name = 'panda_demo_verification.json' if verify else 'panda_demo_state.json'
    (ROOT / 'Saved' / report_name).write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:30010')
    parser.add_argument('--object-path', default=DEFAULT_OBJECT)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--joints', type=float, nargs=7, default=READY)
    parser.add_argument('--opening', type=float, default=.06)
    print(json.dumps(run_demo(**vars(parser.parse_args())), indent=2))
