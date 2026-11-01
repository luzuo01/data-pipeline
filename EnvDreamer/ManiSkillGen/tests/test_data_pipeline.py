import asyncio
import copy
import json
import os
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ManiSkillGen.gt_exporter import decode_camera
from ManiSkillGen.kinematics import URDFKinematics
from ManiSkillGen.scene_spec import default_scene_spec


def test_urdf_ik_matches_known_fk_and_rejects_unreachable(tmp_path):
    path=tmp_path/"arm.urdf"
    path.write_text('''<robot name="test"><joint name="j" type="revolute"><parent link="root"/><child link="arm"/><axis xyz="0 0 1"/><limit lower="-3" upper="3"/></joint><joint name="tip" type="fixed"><parent link="arm"/><child link="tcp"/><origin xyz="1 0 0"/></joint></robot>''')
    kin=URDFKinematics(path,["j"],"tcp")
    target=kin.forward([.7])
    np.testing.assert_allclose(target[:3,3],[np.cos(.7),np.sin(.7),0])
    solution=kin.solve(target,[0])
    np.testing.assert_allclose(solution,[.7],atol=1e-4)
    target[2,3]=5
    with pytest.raises(ValueError,match="unreachable"):
        kin.solve(target,[0])


def camera_fixture():
    y,x=np.indices((8,10));k=np.array([[20.,0,5],[0,20,4],[0,0,1]])
    position=np.stack([(x+.5-5)*2/20,-(y+.5-4)*2/20,np.full_like(x,-2.),np.ones_like(x)],-1)[None]
    normal=np.zeros_like(position);normal[...,2]=1
    segmentation=np.zeros_like(position,dtype=np.uint32);segmentation[...,1]=16
    raw={"Color":np.ones_like(position)*.5,"Position":position,"Normal":normal,"Segmentation":segmentation}
    params={"intrinsic_cv":k[None],"extrinsic_cv":np.eye(4)[None,:3,:]}
    return raw,params


def test_raw_depth_normals_pixel_centers_and_camera_alignment():
    raw,params=camera_fixture()
    data,_,error=decode_camera(raw,params)
    assert data["depth_m"].dtype==np.float32
    np.testing.assert_allclose(data["depth_m"],2)
    np.testing.assert_allclose(data["normal_cv"][0,0],[0,0,-1])
    assert error<1e-8
    raw["Segmentation"][0,0,0,1]=0
    data,_,_=decode_camera(raw,params)
    assert not data["valid"][0,0] and data["depth_m"][0,0]==0


def test_wrong_intrinsics_and_missing_modalities_are_rejected():
    raw,params=camera_fixture()
    params["intrinsic_cv"][0,0,2]+=2
    with pytest.raises(ValueError,match="projection"):
        decode_camera(raw,params)
    del raw["Normal"]
    with pytest.raises(ValueError,match="requires"):
        decode_camera(raw,params)


def test_pipeline_does_not_publish_failed_trajectories(tmp_path,monkeypatch):
    from ManiSkillGen import pipeline
    calls=[]
    class Generator:
        def __init__(self,*args,**kwargs): self.last_instance=None
        def generate(self,spec,**kwargs):
            class Env:
                def close(self): calls.append("closed")
            return {"env":Env(),"scene_spec":spec}
    class Trajectory:
        def generate(self,*args,**kwargs): raise RuntimeError("grasp failed")
    monkeypatch.setattr(pipeline,"ManiSkillSceneGenerator",Generator)
    monkeypatch.setattr(pipeline,"TrajectoryGenerator",Trajectory)
    r=pipeline.DataPipeline(tmp_path).run(default_scene_spec(),num_episodes=2)
    assert r["status"]=="failed" and r["successful_episodes"]==0
    assert len(r["failed_episodes"])==2 and len(calls)==2
    assert not list(tmp_path.rglob("*.h5"))
    assert json.loads((tmp_path/r["run_id"]/"run.json").read_text())["status"]=="failed"


def test_pipeline_jobs_keep_api_responsive_and_serialize_generation(tmp_path,monkeypatch):
    from ManiSkillGen import app as web
    from ManiSkillGen import pipeline
    started,release=threading.Event(),threading.Event()
    class Pipeline:
        def __init__(self,*args): pass
        def run(self,spec,**kwargs):
            started.set();release.wait(5)
            return {"run_id":kwargs["run_id"],"status":"complete","episodes":[],"successful_episodes":0}
    monkeypatch.setattr(pipeline,"DataPipeline",Pipeline)
    monkeypatch.setattr(web,"_generation_lock",asyncio.Lock())
    monkeypatch.setattr(web,"OUTPUT_DIR",tmp_path)
    with TestClient(web.app) as client:
        response=client.post("/api/maniskill/pipeline/start",json={"spec":default_scene_spec()})
        assert response.status_code==202
        run_id=response.json()["run_id"]
        assert started.wait(2)
        assert client.get(f"/api/maniskill/pipeline/{run_id}").json()["status"]=="running"
        assert client.post("/api/maniskill/scene/generate",json={}).status_code==409
        assert client.get(f"/api/maniskill/pipeline/{run_id}/files/episode_000/lerobot_v3.zip").status_code==404
        release.set()
        for _ in range(50):
            result=client.get(f"/api/maniskill/pipeline/{run_id}").json()
            if result["status"]!="running": break
            time.sleep(.01)
        assert result["status"]=="complete"


def test_pipeline_file_routes_publish_nested_lerobot_and_video(tmp_path, monkeypatch):
    """Public download URLs contain an episode subdirectory and must resolve."""
    from ManiSkillGen import app as web

    run_id = "0123456789ab"
    episode_dir = tmp_path / "datasets" / run_id / "episode_000"
    episode_dir.mkdir(parents=True)
    (episode_dir / "lerobot_v3.zip").write_bytes(b"zip-placeholder")
    (episode_dir / "ue_white.mp4").write_bytes(b"mp4-placeholder")
    (episode_dir / "episode.json").write_text("{}", encoding="utf-8")
    (tmp_path / "datasets" / run_id / "run.json").write_text(json.dumps({
        "run_id": run_id,
        "status": "complete",
        "episodes": [{"index": 0}],
    }), encoding="utf-8")
    monkeypatch.setattr(web, "OUTPUT_DIR", tmp_path)
    with TestClient(web.app) as client:
        zip_response = client.get(f"/api/maniskill/pipeline/{run_id}/files/episode_000/lerobot_v3.zip")
        video_response = client.get(f"/api/maniskill/pipeline/{run_id}/files/episode_000/ue_white.mp4")
        metadata_response = client.get(f"/api/maniskill/pipeline/{run_id}/files/episode_000/episode.json")
        assert zip_response.status_code == 200 and zip_response.content == b"zip-placeholder"
        assert video_response.status_code == 200 and video_response.content == b"mp4-placeholder"
        assert metadata_response.status_code == 200 and metadata_response.json() == {}
        assert client.get(f"/api/maniskill/pipeline/{run_id}/files/episode_000/trajectory.h5").status_code == 404


@pytest.mark.skipif(os.getenv("MANISKILL_INTEGRATION")!="1",reason="opt-in real SAPIEN physics and rendering")
@pytest.mark.parametrize("task",["pick_and_place","stack","push"])
def test_real_task_and_ground_truth_contract(tmp_path,task):
    import h5py
    from ManiSkillGen.pipeline import DataPipeline
    from ManiSkillGen.planner import SceneSpecPlanner
    spec=SceneSpecPlanner(api_key="").plan(f"Panda {task} two cubes" if task=="stack" else f"Panda {task} a cube")
    spec["randomization"]={}
    spec["cameras"]["front"]["resolution"]=[160,120]
    # A second camera verifies sensor identity and extrinsics rather than duplicating RGB.
    if task=="pick_and_place":
        spec["cameras"]["side"]=copy.deepcopy(spec["cameras"]["front"])
        spec["cameras"]["side"]["position"]=[.8,-.8,1.4]
    result=DataPipeline(tmp_path).run(spec,seed=3)
    assert result["status"]=="complete",result["failed_episodes"]
    episode=result["episodes"][0]
    with h5py.File(Path(episode["lerobot_path"]) / "extras" / "source_episode.h5","r") as h5:
        assert h5.attrs["complete"]
        steps=len(h5["actions"])
        assert steps>20 and len(h5["observations/qpos"])==steps+1
        assert not h5["observations/success"][0] and h5["observations/success"][-10:].all()
        assert h5["actions"].shape[1]==8
        np.testing.assert_allclose(h5["action_timestamps"][:],h5["timestamps"][:-1])
        for camera in spec["cameras"]:
            group=h5["cameras/"+camera]
            for name in ("rgb","depth_m","normal_cv","instance_id","semantic_id","valid","intrinsic_cv","world_to_camera_cv","camera_to_world_cv"):
                assert len(group[name])==steps+1
            np.testing.assert_allclose(group["world_to_camera_cv"][0]@group["camera_to_world_cv"][0],np.eye(4),atol=1e-6)
        meta=json.loads(h5["metadata_json"][()].decode())
        assert meta["gt_validation"]["max_object_geometry_violation_m"]<.002
        assert meta["trajectory_validation"]["task_rollout"]
        if task=="pick_and_place":
            assert not np.array_equal(h5["cameras/front/rgb"][0],h5["cameras/side/rgb"][0])
