import pytest
from fastapi.testclient import TestClient
from ManiSkillGen.app import app
from ManiSkillGen.planner import SceneSpecPlanner
from ManiSkillGen.scene_spec import default_scene_spec
from ManiSkillGen.validator import validate_scene_spec
from ManiSkillGen.pipeline import DataPipeline

@pytest.mark.parametrize('prompt,count', [('Panda place three cubes',3),('Generate a scene with two cubes for Panda to push',2),('Panda push two cubes',2),('Panda place a cube',1),('Panda 推动三个方块',3)])
def test_fallback_quantity_is_not_a_substring_of_robot_or_action(prompt,count):
    spec=SceneSpecPlanner(api_key='').plan(prompt)
    assert sum(o['dynamic'] for o in spec['scene']['objects'])==count

def test_unknown_action_warning_survives_model_failure():
    spec=SceneSpecPlanner(api_key='').plan('Panda dance')
    assert any('pick_and_place' in w for w in spec['planner']['warnings'])
    assert any('OPENAI_API_KEY' in w for w in spec['planner']['warnings'])

def test_portal_underscore_alias_preserves_explicit_backend():
    assert SceneSpecPlanner(api_key='').plan('portal_trace Sawyer stack two cubes')['backend']=='portal_trace'

@pytest.mark.parametrize('seed',[-1,2**32,1.5,True,'3',None])
def test_invalid_nested_seed_is_rejected_without_coercion(seed):
    spec=default_scene_spec();spec['seed']=seed
    assert not validate_scene_spec(spec)['ok']

@pytest.mark.parametrize('endpoint',['scene/generate','scene/repair','pipeline/start'])
@pytest.mark.parametrize('seed',[-1,2**32])
def test_api_rejects_out_of_runtime_seed_range(endpoint,seed):
    with TestClient(app) as client:
        r=client.post('/api/maniskill/'+endpoint,json={'seed':seed,'spec':default_scene_spec()})
    assert r.status_code==400
    assert 'seed' in r.json()['error']

def test_episode_seed_overflow_is_rejected_before_start(tmp_path):
    with TestClient(app) as client:
        assert client.post('/api/maniskill/pipeline/start',json={'seed':2**32-1,'num_episodes':2}).status_code==400
    with pytest.raises(ValueError,match='seed'):
        DataPipeline(tmp_path).run(default_scene_spec(),seed=2**32-1,num_episodes=2)
    assert not list(tmp_path.iterdir())

def test_missing_optional_renderer_keeps_valid_dataset(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    from ManiSkillGen import pipeline
    from EnvGen.Scripts import lerobot_export
    spec=default_scene_spec()
    class Generator:
        def __init__(self,*args,**kwargs): self.last_instance=None
        def generate(self,*args,**kwargs): return {'env':SimpleNamespace(close=lambda:None),'validation':{'ok':True}}
    class Trajectory:
        def generate(self,*args,**kwargs): return {'states':[0,1],'validation':{}}
    class Exporter:
        def export(self,instance,trajectory,path,**kwargs):
            return dict(num_actions=1,num_observations=2,preview_path='preview.gif',gt_validation={},trajectory_validation={},scene_spec=spec,source_validation={'ok':True})
    def archive(root,path): path.write_bytes(b'fixture');return path
    monkeypatch.setattr(pipeline,'ManiSkillSceneGenerator',Generator)
    monkeypatch.setattr(pipeline,'TrajectoryGenerator',Trajectory)
    monkeypatch.setattr(pipeline,'GTExporter',Exporter)
    monkeypatch.setattr(lerobot_export,'convert_hdf5',lambda *a,**k:dict(repo_id='test/test',info={},manifest={}))
    monkeypatch.setattr(lerobot_export,'zip_dataset',archive)
    monkeypatch.setitem(sys.modules,'EnvGen.Scripts.ue_trajectory_render',None)
    result=DataPipeline(tmp_path).run(spec,render_ue=True)
    assert result['status']=='complete'
    assert result['episodes'][0]['white_model_video_path'] is None
    assert 'ModuleNotFoundError' in result['episodes'][0]['ue_render_error']

@pytest.mark.parametrize('requested,video,error,expected',[(True,None,'timeout','partial'),(True,None,None,'partial'),(True,'video.mp4',None,'complete'),(False,None,None,'complete'),(False,None,'legacy timeout','partial')])
def test_delivery_status_requires_requested_video(requested,video,error,expected):
    from ManiSkillGen.app import _pipeline_public
    result=_pipeline_public({'run_id':'0123456789ab','status':'complete','render_ue_requested':requested,'episodes':[{'index':0,'white_model_video_path':video,'ue_render_error':error}]})
    assert result['delivery_status']==expected
    assert result['status']=='complete'  # data success remains separately available
