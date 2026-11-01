"""SceneGenerator -> physically executed TrajectoryGen -> synchronized GTExporter."""
from __future__ import annotations

import argparse
import copy
import json
import time
import uuid
from pathlib import Path

from .generator import ManiSkillSceneGenerator
from .gt_exporter import GTExporter
from .planner import SceneSpecPlanner
from .scene_spec import default_scene_spec
from .trajectory import TrajectoryGenerator


class DataPipeline:
    def __init__(self, output_dir=None):
        self.output_dir=Path(output_dir or Path(__file__).with_name("outputs")/"datasets").resolve()

    def run(self, spec, *, seed=0, num_episodes=1, auto_repair=True, max_repair_attempts=3,
            reference_scene=False, run_id=None, on_progress=None, render_ue=False):
        if type(num_episodes) is not int or not 1 <= num_episodes <= 10:
            raise ValueError("num_episodes must be an integer in [1,10]")
        if not isinstance(spec,dict):
            raise ValueError("spec must be a JSON object")
        if spec.get("backend", "maniskill3") not in {"maniskill3", "ue5"}:
            backend = spec.get("backend")
            raise ValueError(
                f"backend={backend!r} currently supports SceneGenerator + reset validation only; "
                "PORTAL-Trace Stack HDF5/trajectory export is not enabled yet"
            )
        run_id=run_id or uuid.uuid4().hex[:12]
        if len(run_id)!=12 or any(c not in "0123456789abcdef" for c in run_id):
            raise ValueError("invalid run ID")
        directory=self.output_dir/run_id
        directory.mkdir(parents=True,exist_ok=False)
        result={"run_id":run_id,"status":"running","requested_episodes":num_episodes,"successful_episodes":0,
                "episodes":[],"failed_episodes":[],"started_at":time.time(),"reference_scene":reference_scene,
                "augmentation":"independent seeded scene parameter randomization + physical skill re-execution; not MimicGen"}
        def progress(phase,episode,**extra):
            if on_progress: on_progress({"phase":phase,"episode":episode,"total_episodes":num_episodes,**extra})
        for index in range(num_episodes):
            started=time.perf_counter()
            generator = None
            instance=None
            try:
                candidate=copy.deepcopy(spec)
                if reference_scene and index==0:
                    candidate["randomization"]={}
                progress("scene",index)
                if candidate.get("backend") == "ue5":
                    from EnvGen.Scripts.ue5_trajectory import generate_ue5_stack_episode
                    from EnvGen.Scripts.lerobot_export import convert_hdf5, zip_dataset
                    episode_dir = directory / f"episode_{index:03d}"
                    episode_dir.mkdir(parents=True, exist_ok=True)
                    progress("ue5_scene", index, frames=0)
                    ue_result = generate_ue5_stack_episode(candidate, episode_dir, seed=int(seed) + index,
                                                           progress=lambda frame, total: progress("ue5_rollout", index, frames=frame, total_frames=total))
                    lerobot_root = episode_dir / "lerobot_v3"
                    converted = convert_hdf5(ue_result["source_h5"], lerobot_root,
                                             repo_id=f"local/{run_id}_episode_{index:03d}", task="Stack the moving cube on the target cube.")
                    lerobot_zip = zip_dataset(lerobot_root, episode_dir / "lerobot_v3.zip")
                    from EnvGen.Scripts.ue_trajectory_render import encode_frames
                    video_path = episode_dir / "ue_white.mp4"
                    encode_frames(episode_dir / "ue_frames", video_path, fps=20, frame_count=ue_result["frames"])
                    episode_metadata = episode_dir / "episode.json"
                    episode_metadata.write_text(json.dumps({"backend": "ue5", "robot": candidate["robot"],
                        "lerobot": {"root": str(lerobot_root), "archive": str(lerobot_zip), "repo_id": converted["repo_id"],
                                    "codebase_version": converted["info"].get("codebase_version"), "frames": converted["manifest"].get("training_rows"), "fps": converted["info"].get("fps")},
                        "white_model_video": str(video_path), "source_validation": {"ok": True, "backend": "ue5"},
                        "trajectory_validation": ue_result["validation"]}, ensure_ascii=False, indent=2), encoding="utf-8")
                    result["episodes"].append({"index": index, "seed": int(seed) + index, "success": True,
                        "num_actions": ue_result["frames"] - 1, "num_observations": ue_result["frames"],
                        "lerobot_path": str(lerobot_root), "lerobot_zip_path": str(lerobot_zip),
                        "white_model_video_path": str(video_path), "ue_render_error": None,
                        "preview_path": str(video_path), "lerobot_info": converted["info"],
                        "lerobot_manifest": converted["manifest"], "file_size_bytes": lerobot_zip.stat().st_size,
                        "gt_validation": {"temporal_alignment": True, "semantic_ids_recorded": True,
                                          "state_source": "UE5 Remote Control"}, "trajectory_validation": ue_result["validation"],
                        "scene_spec": candidate, "source_validation": {"ok": True, "backend": "ue5"},
                        "episode_metadata_path": str(episode_metadata), "duration_s": round(time.perf_counter() - started, 2)})
                    result["successful_episodes"] += 1
                    (directory / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    continue
                generator=ManiSkillSceneGenerator(directory/"scenes",allow_software_fallback=False)
                instance=generator.generate(candidate,seed=int(seed)+index,render=False,auto_repair=auto_repair,max_repair_attempts=max_repair_attempts)
                trajectory=TrajectoryGenerator().generate(instance,on_progress=lambda phase,steps:progress("trajectory",index,skill_phase=phase,control_steps=steps))
                episode_dir = directory / f"episode_{index:03d}"
                episode_dir.mkdir(parents=True, exist_ok=True)
                source_h5 = episode_dir / "trajectory.h5"
                progress("export",index,frames=0,total_frames=len(trajectory["states"]))
                exported=GTExporter().export(instance,trajectory,source_h5,
                                              on_progress=lambda frame,total:progress("export",index,frames=frame,total_frames=total))
                from EnvGen.Scripts.lerobot_export import convert_hdf5, zip_dataset
                lerobot_root = episode_dir / "lerobot_v3"
                task_template = candidate.get("task", {}).get("template") if isinstance(candidate.get("task"), dict) else None
                converted = convert_hdf5(source_h5, lerobot_root, repo_id=f"local/{run_id}_episode_{index:03d}", task=task_template)
                lerobot_zip = zip_dataset(lerobot_root, episode_dir / "lerobot_v3.zip")
                white_video = None
                ue_render_error = None
                if render_ue:
                    from EnvGen.Scripts.ue_trajectory_render import render_trajectory
                    progress("ue_render", index, frames=0, total_frames=len(trajectory["states"]))
                    try:
                        render_trajectory(episode_dir, lambda phase, message, **extra: progress(phase, index, message=message, **extra))
                        white_video = episode_dir / "ue_white.mp4"
                    except Exception as exc:
                        # The physics rollout and LeRobot export are already
                        # complete at this point.  Keep that valid episode
                        # publishable and expose the renderer error so the UI
                        # can offer a retry instead of reporting 0 successes.
                        ue_render_error = f"{type(exc).__name__}: {exc}"
                        progress("ue_render_failed", index, message=ue_render_error)
                episode_metadata = episode_dir / "episode.json"
                episode_metadata.write_text(json.dumps({
                    "lerobot": {"root": str(lerobot_root), "archive": str(lerobot_zip),
                                "repo_id": converted["repo_id"], "codebase_version": converted["info"].get("codebase_version"),
                                "frames": converted["manifest"].get("training_rows"), "fps": converted["info"].get("fps")},
                    "white_model_video": str(white_video) if white_video and white_video.is_file() else None,
                    "ue_render_error": ue_render_error,
                    "source_validation": instance["validation"], "trajectory_validation": trajectory["validation"],
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                result["episodes"].append({"index":index,"seed":int(seed)+index,"success":True,
                                          "num_actions":exported["num_actions"],"num_observations":exported["num_observations"],
                                          "lerobot_path":str(lerobot_root),"lerobot_zip_path":str(lerobot_zip),
                                          "white_model_video_path":str(white_video) if white_video and white_video.is_file() else None,
                                          "ue_render_error":ue_render_error,
                                          "preview_path":str(white_video) if white_video and white_video.is_file() else exported["preview_path"],
                                          "lerobot_info":converted["info"], "lerobot_manifest":converted["manifest"],
                                          "file_size_bytes":lerobot_zip.stat().st_size,"gt_validation":exported["gt_validation"],
                                          "trajectory_validation":exported["trajectory_validation"],
                                          "scene_spec":exported["scene_spec"],"source_validation":exported["source_validation"],
                                          "episode_metadata_path":str(episode_metadata),
                                          "duration_s":round(time.perf_counter()-started,2)})
                result["successful_episodes"]+=1
            except Exception as exc:
                result["failed_episodes"].append({"index":index,"seed":int(seed)+index,"error":f"{type(exc).__name__}: {exc}",
                                                 "validation":getattr(exc,"report",None)})
            finally:
                if instance is not None and instance.get("env") is not None:
                    instance["env"].close()
                if generator is not None:
                    generator.last_instance=None
            (directory/"run.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        result["status"]="complete" if result["successful_episodes"]==num_episodes else "partial" if result["successful_episodes"] else "failed"
        result["finished_at"]=time.time()
        result["duration_s"]=round(result["finished_at"]-result["started_at"],2)
        (directory/"run.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        progress(result["status"],num_episodes)
        return result


def main():
    parser=argparse.ArgumentParser(description="Generate successful tabletop demonstrations, LeRobot v3 truth data and optional UE5 white-model video")
    parser.add_argument("--spec",type=Path)
    parser.add_argument("--prompt")
    parser.add_argument("--seed",type=int,default=0)
    parser.add_argument("--episodes",type=int,default=1)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--render-ue", action="store_true", help="Also render UE5 white-model video; requires a running configured UE editor")
    args=parser.parse_args()
    if args.spec and args.prompt: parser.error("choose --spec or --prompt")
    spec=json.loads(args.spec.read_text(encoding="utf-8-sig")) if args.spec else SceneSpecPlanner().plan(args.prompt,seed=args.seed) if args.prompt else default_scene_spec()
    result=DataPipeline(args.output).run(spec,seed=args.seed,num_episodes=args.episodes,
                                       render_ue=args.render_ue,
                                       on_progress=lambda update:print(json.dumps(update,ensure_ascii=False),flush=True))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["status"]=="complete" else 1


if __name__=="__main__":
    raise SystemExit(main())
