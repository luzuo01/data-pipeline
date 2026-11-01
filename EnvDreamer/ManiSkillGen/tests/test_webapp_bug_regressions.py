"""Regression coverage for web validation, routing and persisted jobs."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from ManiSkillGen import app as web
from ManiSkillGen.planner import SceneSpecPlanner
from ManiSkillGen.scene_spec import default_scene_spec



@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(web, '_generation_lock', asyncio.Lock())
    monkeypatch.setattr(web, '_pipeline_jobs', {})
    monkeypatch.setattr(web, '_state', {'instance': None, 'last_prompt': None, 'updated_at': None})
    monkeypatch.setattr(web, 'planner', SceneSpecPlanner(api_key=''))
    with TestClient(web.create_app(), raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize('endpoint', ['scene/validate', 'scene/repair', 'scene/generate', 'pipeline/start'])
def test_malformed_json_returns_client_error(client, endpoint):
    r = client.post('/api/maniskill/' + endpoint, content='{broken', headers={'content-type': 'application/json'})
    assert r.status_code in {400, 422}, (endpoint, r.status_code, r.text)


def test_invalid_ue_scene_is_rejected_and_not_published(client):
    spec = default_scene_spec()
    spec['backend'] = 'ue5'
    spec['robot']['control_mode'] = 'ue5_joint_pos'
    spec['scene']['objects'][0]['size'][0] = -1
    r = client.post('/api/maniskill/scene/generate', json={'spec': spec, 'render': False})
    result = r.json()
    assert r.status_code == 422, {'http': r.status_code, 'status': result.get('status'), 'validation_ok': result.get('validation', {}).get('ok')}
    assert client.get('/api/maniskill/scene/state').json()['instance'] is None


def test_panda_and_franka_route_to_same_verified_backend():
    planner = SceneSpecPlanner(api_key='')
    panda = planner.plan('Panda 把红色方块放到蓝色目标区域')
    franka = planner.plan('Franka 把红色方块放到蓝色目标区域')
    assert panda['backend'] == franka['backend'] == 'maniskill3', {'Panda': panda['backend'], 'Franka': franka['backend']}


def test_failed_early_job_survives_restart(client, monkeypatch):
    started = client.post('/api/maniskill/pipeline/start', json={'spec': {'backend': 'portal_trace'}})
    assert started.status_code == 202
    run_id = started.json()['run_id']
    for _ in range(100):
        result = client.get('/api/maniskill/pipeline/' + run_id).json()
        if result['status'] != 'running':
            break
        time.sleep(.02)
    assert result['status'] == 'failed'
    monkeypatch.setattr(web, '_pipeline_jobs', {})  # same state loss as a fresh process
    restored = client.get('/api/maniskill/pipeline/' + run_id)
    assert restored.status_code == 200, {'before_restart': result, 'after_restart': restored.status_code}
    assert restored.json()['status'] == 'failed'


def test_fractional_seed_rejected_before_generation(client):
    spec = default_scene_spec()
    spec['backend'] = 'ue5'
    spec['robot']['control_mode'] = 'ue5_joint_pos'
    r = client.post('/api/maniskill/scene/generate', json={'spec': spec, 'seed': 1.5, 'render': False})
    assert r.status_code in {400, 422}, {'http': r.status_code, 'seed_input': 1.5}


@pytest.mark.parametrize('mode', ['final_false', 'missing', 'unavailable', 'interrupted_tail', 'initially_successful', 'valid'])
def test_ue_success_requires_actual_consecutive_evidence(tmp_path, monkeypatch, mode):
    """Controlled negative UE response; no live editor calls or physics claims."""
    import h5py
    import numpy as np
    from PIL import Image
    from EnvGen.Scripts import ue5_trajectory as ue
    calls = []
    checks = []
    def fake_call(function, params, base, builder):
        calls.append(function)
        if function == 'RC_GetRobotState':
            return {'ReturnValue': json.dumps({'tcp_world_cm': [1530, 0, 100]})}
        if function == 'RC_GetActorInfo':
            return {'ReturnValue': json.dumps({'location': {'x': 0, 'y': 0, 'z': 0}})}
        if function == 'RC_CheckStackSuccess':
            index = len(checks)
            if mode == 'unavailable':
                raise TimeoutError('UE check timed out')
            if mode == 'missing':
                return {'ReturnValue': True, 'OutJson': '{}'}
            success = index >= 176
            if mode == 'final_false' and index == 185:
                success = False
            if mode == 'interrupted_tail' and index == 180:
                success = False
            if mode == 'initially_successful':
                success = True
            checks.append(success)
            return {'ReturnValue': True, 'OutJson': json.dumps({'success': success})}
        return {'ReturnValue': True}
    def capture(rc, name, path, timeout=20):
        Image.fromarray(np.zeros((180, 320, 3), dtype=np.uint8)).save(path)
    monkeypatch.setattr(ue, '_call', fake_call)
    monkeypatch.setattr(ue, '_capture', capture)
    spec = default_scene_spec()
    spec['backend'] = 'ue5'
    spec['robot']['control_mode'] = 'ue5_joint_pos'
    if mode == 'valid':
        result = ue.generate_ue5_stack_episode(spec, tmp_path)
        assert result['validation']['task_rollout'] is True
        assert result['validation']['final_success_hold_frames'] == 10
        with h5py.File(result['source_h5']) as source:
            assert source.attrs['complete']
            np.testing.assert_array_equal(source['observations/success'][:], checks)
        return
    with pytest.raises((ValueError, RuntimeError), match='success (validation|check)'):
        ue.generate_ue5_stack_episode(spec, tmp_path)
    assert 'RC_CheckStackSuccess' in calls
    assert not (tmp_path / 'trajectory.h5').exists()
    if mode not in {'missing', 'unavailable'}:
        report = json.loads((tmp_path / 'trajectory_validation.json').read_text())
        assert report['task_rollout'] is False
        if mode == 'final_false':
            assert report['final_success_hold_frames'] == 0
        if mode == 'interrupted_tail':
            assert report['final_success_hold_frames'] == 5


@pytest.mark.parametrize('value', [1.5, True, '3', None])
@pytest.mark.parametrize('endpoint', ['scene/generate', 'scene/repair', 'pipeline/start'])
def test_strict_seed_validation(client, endpoint, value):
    response = client.post('/api/maniskill/' + endpoint, json={'spec': default_scene_spec(), 'seed': value})
    assert response.status_code == 400


def test_ue_static_validation_is_explicit_and_does_not_replace_state(client):
    spec = default_scene_spec()
    spec['backend'] = 'ue5'
    spec['robot']['control_mode'] = 'ue5_joint_pos'
    previous = {'scene_spec': default_scene_spec(), 'manifest': {'instance_id': 'accepted'}}
    web._state['instance'] = previous
    response = client.post('/api/maniskill/scene/generate', json={'spec': spec, 'render': False})
    assert response.status_code == 200
    assert response.json()['status'] == 'validated'
    assert response.json()['preview_url'] is None
    assert web._state['instance'] is previous
    response = client.post('/api/maniskill/scene/generate', json={'spec': spec, 'render': True})
    assert response.status_code == 422
    assert response.json()['status'] == 'unsupported'
    assert web._state['instance'] is previous


def test_accepted_job_exists_on_disk_before_planning_finishes(client, monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()
    def plan(*args, **kwargs):
        started.set()
        release.wait(3)
        raise ValueError('planner unavailable')
    monkeypatch.setattr(web.planner, 'plan', plan)
    try:
        response = client.post('/api/maniskill/pipeline/start', json={'prompt': 'Panda pick a cube'})
        assert response.status_code == 202 and started.wait(1)
        record = web.OUTPUT_DIR / 'datasets' / (response.json()['run_id'] + '.job.json')
        assert json.loads(record.read_text())['status'] == 'running'
    finally:
        release.set()
    for _ in range(100):
        if client.get('/api/maniskill/pipeline/' + response.json()['run_id']).json()['status'] == 'failed':
            break
        time.sleep(.01)
    assert json.loads(record.read_text())['status'] == 'failed'


def test_interrupted_planning_record_can_be_recovered(client):
    run_id = '123456abcdef'
    web._save_job({'run_id': run_id, 'status': 'running', 'episodes': []})
    result = client.get('/api/maniskill/pipeline/' + run_id)
    assert result.status_code == 200
    assert result.json()['status'] == 'interrupted'


def test_pipeline_rejects_failed_ue_proof_before_conversion(tmp_path, monkeypatch):
    from ManiSkillGen.pipeline import DataPipeline
    from EnvGen.Scripts import ue5_trajectory, lerobot_export
    monkeypatch.setattr(ue5_trajectory, 'generate_ue5_stack_episode', lambda *args, **kw: {
        'validation': {'task_rollout': False, 'final_success': False, 'final_success_hold_frames': 9}})
    converted = []
    monkeypatch.setattr(lerobot_export, 'convert_hdf5', lambda *args, **kw: converted.append(args))
    spec = default_scene_spec()
    spec['backend'] = 'ue5'
    spec['robot']['control_mode'] = 'ue5_joint_pos'
    result = DataPipeline(tmp_path).run(spec)
    assert result['status'] == 'failed' and not result['episodes']
    assert not converted


@pytest.mark.parametrize('robot', ['Panda', 'Franka', 'Franka Panda'])
def test_backend_aliases_and_explicit_ue_request(robot):
    planner = SceneSpecPlanner(api_key='')
    assert planner.plan(robot + ' pick a cube')['backend'] == 'maniskill3'
    assert planner.plan('UE5 ' + robot + ' pick a cube')['backend'] == 'ue5'
