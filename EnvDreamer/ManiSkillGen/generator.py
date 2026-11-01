"""SceneSpec compiler for ManiSkill3 with a deterministic software fallback."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import random
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Mapping

from .repair import repair_scene, propose_runtime_repair, pose_locked
from .runtime_validation import probe_maniskill
from .preview import save_preview
from .portal_trace_backend import PortalTraceBackend
from .scene_spec import normalize_scene_spec
from .validator import SceneValidationError, load_asset_registry, validate_scene_spec


class ManiSkillSceneGenerator:
    """Compile constrained JSON scene descriptions into ManiSkill3 scenes.

    ``env_factory`` is useful for tests and for applications that keep a
    configured ManiSkill environment pool.  If ManiSkill3 is unavailable, the
    compiler still returns a complete instance and produces a software preview
    so the rest of the pipeline remains inspectable.
    """

    def __init__(self, output_dir: str | Path | None = None, *, asset_registry: str | Path | None = None, env_factory: Callable[..., Any] | None = None, prefer_maniskill: bool = True, allow_software_fallback: bool = True):
        self.output_dir = Path(output_dir or Path(__file__).with_name("outputs"))
        self.preview_dir = self.output_dir / "previews"
        self.manifest_dir = self.output_dir / "manifests"
        self.registry = load_asset_registry(asset_registry)
        self.env_factory = env_factory
        self.prefer_maniskill = bool(prefer_maniskill)
        self.allow_software_fallback = bool(allow_software_fallback)
        self.last_instance: Dict[str, Any] | None = None

    def validate(self, spec: Mapping[str, Any] | None) -> Dict[str, Any]:
        return validate_scene_spec(spec, asset_registry=self.registry, check_runtime=False)

    def _generate_portal_trace(self, spec: Mapping[str, Any], *, seed: int, render: bool, num_envs: int) -> Dict[str, Any]:
        started = time.perf_counter()
        report = self.validate(spec)
        if not report["ok"]:
            raise SceneValidationError(report)
        normalized = normalize_scene_spec(report["normalized_spec"], seed=seed)
        backend = PortalTraceBackend(self.output_dir)
        try:
            result = backend.generate(normalized, seed=int(seed), render=render)
        except Exception as exc:
            report.update(ok=False, errors=[str(exc)], issues=[{"code": "runtime_reset", "message": str(exc),
                          "severity": "error", "repairable": False, "objects": [], "path": "runtime"}])
            report["checks"]["environment_reset"] = False
            raise SceneValidationError(report) from exc
        runtime = result["runtime"]
        probe = runtime.get("validation_probe", {"ok": False, "issues": []})
        report["runtime"] = probe
        report["repair"] = {"status": "not_supported", "attempts": 0, "history": [],
                            "stop_reason": "PORTAL registered layout; automatic pose repair is not implemented"}
        report["checks"].update(runtime.get("checks", {}))
        if not probe.get("ok"):
            report.update(ok=False, issues=probe["issues"], errors=[i["message"] for i in probe["issues"]] or ["PORTAL runtime probe unavailable"])
            raise SceneValidationError(report)
        warnings = list(report.get("warnings", []))
        instance_id = result["instance_id"]
        preview_path = result.get("preview_path")
        if render and preview_path is None:
            report.update(ok=False, errors=["PORTAL real preview was requested but no image was produced"])
            raise SceneValidationError(report)
        checks = dict(report.get("checks", {}))
        checks.update(runtime.get("checks", {}))
        feasibility = dict(report.get("task_feasibility", {}))
        feasibility["runtime_probe"] = {
            "environment_reset": bool(runtime.get("checks", {}).get("environment_reset")),
            "success_at_reset": bool(runtime.get("success_at_reset", False)),
            "interpretation": "success_at_reset=false is expected when the task starts unsolved; it is not a failure",
        }
        manifest = {
            "instance_id": instance_id,
            "schema_version": normalized.get("schema_version"),
            "backend": "portal_trace",
            "runtime_backend": runtime.get("runtime_backend", "portal_trace_robosuite"),
            "robosuite_version": runtime.get("robosuite_version"),
            "seed": int(seed),
            "num_envs": int(num_envs),
            "task_template": normalized.get("task", {}).get("template"),
            "task_env_name": runtime.get("env_name"),
            "robot": normalized.get("robot", {}).get("type"),
            "portal_trace_source": normalized.get("portal_trace", {}).get("source", "PORTAL-Trace"),
            "task_feasibility": feasibility,
            "validation": {**report, "checks": checks, "task_feasibility": feasibility},
            "generated_at_unix": time.time(),
        }
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.manifest_dir / f"{instance_id}.json"
        manifest["manifest_path"] = str(manifest_path)
        manifest["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump({"scene_spec": normalized, "manifest": manifest}, handle, indent=2, ensure_ascii=False)
        instance: Dict[str, Any] = {
            "env": None,
            "reset": runtime.get("checks", {}).get("environment_reset"),
            "scene_spec": normalized,
            "objects": {},
            "robot": normalized.get("robot"),
            "task": normalized.get("task"),
            "success": normalized.get("success", []),
            "cameras": normalized.get("cameras", {}),
            "manifest": manifest,
            "preview_path": preview_path,
            "validation": {**report, "normalized_spec": normalized, "checks": checks, "task_feasibility": feasibility},
        }
        if preview_path:
            shutil.copyfile(preview_path, self.preview_dir / "latest.png")
        previous = self.last_instance
        self.last_instance = instance
        if previous and previous.get("env") is not None:
            previous["env"].close()
        return instance

    @staticmethod
    def _randomize(spec: Dict[str, Any], seed: int) -> Dict[str, Any]:
        result = copy.deepcopy(spec)
        rng = random.Random(seed)
        randomization = result.get("randomization", {}) or {}
        jitter = max(0.0, float(randomization.get("object_pose_jitter", 0.0)))
        color_jitter = max(0.0, float(randomization.get("color_jitter", 0.0)))
        for obj in result.get("scene", {}).get("objects", []):
            if not isinstance(obj, dict):
                continue
            if obj.get("dynamic", True) and jitter and not pose_locked(result, obj["id"]):
                obj["pose"][0] += rng.uniform(-jitter, jitter)
                obj["pose"][1] += rng.uniform(-jitter, jitter)
            if color_jitter:
                obj["color"] = [max(0.0, min(1.0, float(channel) + rng.uniform(-color_jitter, color_jitter))) for channel in obj["color"][:3]] + [obj["color"][3]]
        return result

    def _create_maniskill_env(self, spec: Mapping[str, Any], *, seed: int, render: bool, num_envs: int) -> tuple[Any, Any, str | None, Dict[str, Any]]:
        requested_control_mode = str(spec.get("robot", {}).get("control_mode", "pd_ee_delta_pose"))
        details: Dict[str, Any] = {"requested_control_mode": requested_control_mode, "runtime_control_mode": requested_control_mode, "warnings": []}
        if self.env_factory is not None:
            env = None
            try:
                env = self.env_factory(spec=spec, seed=seed, render=render, num_envs=num_envs)
                reset_result = env.reset(seed=seed) if hasattr(env, "reset") else None
                return env, reset_result, None, details
            except Exception as exc:
                if env is not None:
                    env.close()
                return None, None, f"custom ManiSkill env factory failed: {exc}", details
        if not self.prefer_maniskill:
            return None, None, "ManiSkill runtime disabled by configuration", details
        try:
            # SAPIEN and the Windows CPU Torch wheel can ship separate Intel
            # OpenMP runtimes. Allow the renderer process to load both; this is
            # needed for the supported Windows CPU setup and is scoped to this
            # process only.
            os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
            # Keep the CPU runtime deterministic and avoid duplicate Intel
            # OpenMP initialization on Windows.  These must be set before
            # importing torch/SAPIEN.
            os.environ.setdefault("OMP_NUM_THREADS", "1")
            os.environ.setdefault("MKL_NUM_THREADS", "1")
            torch_spec = importlib.util.find_spec("torch")
            if torch_spec and torch_spec.origin:
                torch_lib = str(Path(torch_spec.origin).parent / "lib")
                if os.path.isdir(torch_lib):
                    os.environ["PATH"] = torch_lib + os.pathsep + os.environ.get("PATH", "")
                    try:
                        os.add_dll_directory(torch_lib)
                    except (AttributeError, OSError):
                        pass
            import gymnasium as gym
            import mani_skill.envs  # noqa: F401, registers ManiSkill tasks
            from .maniskill_env import register_spec_env
        except ImportError as exc:
            return None, None, f"ManiSkill3 runtime unavailable ({exc})", details
        except OSError as exc:
            return None, None, (
                "ManiSkill3 native runtime failed to load (Windows DLL initialization): "
                f"{exc}. Restart the 8090 service after changing Python/Torch; "
                "use the supported CPU torch wheel and keep KMP_DUPLICATE_LIB_OK=TRUE."
            ), details
        except Exception as exc:
            return None, None, f"ManiSkill3 runtime initialization failed: {type(exc).__name__}: {exc}", details

        # The custom task compiles the exact SceneSpec objects and camera into
        # SAPIEN. Official tasks remain a fallback for installations where a
        # custom registration is unavailable.
        try:
            task_name = register_spec_env()
        except Exception as exc:
            task_name = None
            custom_registration_warning = f"custom ManiSkill scene registration failed: {exc}"
        else:
            custom_registration_warning = None
        actual_control_mode = requested_control_mode
        if requested_control_mode.startswith("pd_ee") and importlib.util.find_spec("pinocchio") is None:
            # ManiSkill's CPU end-effector controller constructs a Pinocchio
            # model. Pinocchio has no official Windows wheel, so use the joint
            # controller for scene creation while preserving the requested DSL
            # value in the manifest. Trajectory generation can require WSL2 for
            # the original end-effector controller later.
            actual_control_mode = "pd_joint_pos"
            details["runtime_control_mode"] = actual_control_mode
            details["warnings"].append("pinocchio is unavailable on Windows; runtime control_mode was mapped to pd_joint_pos for scene initialization")
        kwargs: Dict[str, Any] = {"num_envs": num_envs, "reward_mode": "none", "obs_mode": "state", "control_mode": actual_control_mode, "scene_spec": spec}
        # SAPIEN may still create a renderer for state-only environments. Set
        # the Windows backend explicitly in both render modes so a headless
        # request does not silently fall back to CUDA on multi-GPU machines.
        kwargs["render_backend"] = os.getenv("MANISKILL_RENDER_BACKEND", "cpu" if os.name == "nt" else "gpu")
        if render:
            kwargs["render_mode"] = "rgb_array"
            # SAPIEN's CUDA/Vulkan renderer can terminate the process on
            # Windows when the driver stack is incomplete. CPU rendering is
            # slower but gives a recoverable diagnostic and is the default for
            # this local web preview. Linux users can override it explicitly.
        env = None
        try:
            if task_name is None:
                raise RuntimeError(custom_registration_warning or "custom environment unavailable")
            env = gym.make(task_name, **kwargs)
            reset_result = env.reset(seed=seed)
            return env, reset_result, None, details
        except Exception as exc:
            if env is not None:
                env.close()
            return None, None, f"unable to create ManiSkill scene {task_name or 'EnvDreamerTabletop-v1'}: {exc}", details

    @staticmethod
    def _extract_frame(env: Any) -> Any:
        if env is None or not hasattr(env, "render"):
            return None
        try:
            frame = env.render()
            if isinstance(frame, Mapping):
                frame = frame.get("rgb", frame.get("Color", next(iter(frame.values()), None)))
            if hasattr(frame, "cpu"):
                frame = frame.cpu().numpy()
            elif hasattr(frame, "numpy"):
                frame = frame.numpy()
            return frame
        except Exception:
            return None

    @staticmethod
    def _runtime_task_probe(env: Any) -> Dict[str, Any]:
        """Inspect reset-time task state without pretending to run a policy."""
        probe: Dict[str, Any] = {
            "environment_reset": env is not None,
            "success_at_reset": None,
            "available": False,
            "interpretation": "reset state only; trajectory completion is not tested",
        }
        if env is None:
            return probe
        target = getattr(env, "unwrapped", env)
        evaluate = getattr(target, "evaluate", None)
        if not callable(evaluate):
            return probe
        try:
            value = evaluate()
            success = value.get("success") if isinstance(value, Mapping) else None
            if hasattr(success, "detach"):
                success = success.detach().cpu().reshape(-1).tolist()
            elif hasattr(success, "tolist"):
                success = success.tolist()
            if isinstance(success, (list, tuple)):
                probe["success_at_reset"] = bool(all(bool(item) for item in success)) if success else False
            elif success is not None:
                probe["success_at_reset"] = bool(success)
            probe["available"] = True
        except Exception as exc:
            probe["error"] = f"{type(exc).__name__}: {exc}"
        return probe

    def _audit_failure(self, report):
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        path = self.manifest_dir / f"failed_{uuid.uuid4().hex[:12]}.json"
        report["audit_path"] = str(path)
        path.write_text(json.dumps({"status": "rejected", "validation": report}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    def generate(self, spec: Mapping[str, Any] | None, seed: int = 0, num_envs: int = 1, render: bool = True,
                 auto_repair: bool = True, max_repair_attempts: int = 3) -> Dict[str, Any]:
        if num_envs != 1:
            raise ValueError("validated scene generation currently supports num_envs=1 only")
        if type(auto_repair) is not bool or type(max_repair_attempts) is not int or not 0 <= max_repair_attempts <= 5:
            raise ValueError("auto_repair must be boolean; max_repair_attempts must be an integer in [0, 5]")
        try:
            if isinstance(spec, Mapping) and spec.get("backend") == "portal_trace":
                return self._generate_portal_trace(spec, seed=seed, render=render, num_envs=num_envs)
            return self._generate_maniskill(spec, seed=int(seed), render=render, num_envs=num_envs,
                                           auto_repair=auto_repair, max_repair_attempts=max_repair_attempts)
        except SceneValidationError as exc:
            self._audit_failure(exc.report)
            raise

    def _generate_maniskill(self, spec, *, seed, render, num_envs, auto_repair, max_repair_attempts):
        started = time.perf_counter()
        initial = self.validate(spec)
        if any(not issue["repairable"] for issue in initial["issues"]):
            report = repair_scene(spec, seed=seed, enabled=auto_repair, max_attempts=max_repair_attempts, asset_registry=self.registry)
            raise SceneValidationError(report)
        normalized = normalize_scene_spec(initial["normalized_spec"], seed=seed)
        # Randomize once, then validate/repair the actual scene that will be compiled.
        randomized = self._randomize(normalized, seed)
        report = repair_scene(randomized, seed=seed, enabled=auto_repair, max_attempts=max_repair_attempts, asset_registry=self.registry)
        report["repair"]["requested_spec"] = normalized
        if not report["ok"]:
            raise SceneValidationError(report)
        runtime_spec = report["normalized_spec"]
        repair = report["repair"]
        history = repair["history"]
        env = None
        try:
            while True:
                env, reset_result, runtime_warning, details = self._create_maniskill_env(runtime_spec, seed=seed, render=render, num_envs=num_envs)
                if runtime_warning and not self.allow_software_fallback:
                    report.update(ok=False, errors=[runtime_warning])
                    report["issues"].append({"code": "runtime_reset", "message": runtime_warning, "severity": "error", "repairable": False, "objects": [], "path": "runtime"})
                    report["checks"]["environment_reset"] = False
                    repair.update(status="failed", stop_reason="runtime_unavailable")
                    raise SceneValidationError(report)
                report["checks"]["environment_reset"] = env is not None
                runtime = probe_maniskill(env, runtime_spec)
                history.append({"phase": "runtime", "attempt": repair["attempts"], "ok": runtime["ok"], "probe": runtime, "patches": []})
                if runtime["ok"] is not False:
                    break
                if env is not None:
                    env.close()
                    env = None
                proposal, patches = (None, [])
                if auto_repair and repair["attempts"] < max_repair_attempts:
                    proposal, patches = propose_runtime_repair(runtime_spec, runtime, seed=seed+repair["attempts"]+1, asset_registry=self.registry)
                if proposal is None:
                    report.update(ok=False, runtime=runtime, errors=[i["message"] for i in runtime["issues"]], issues=runtime["issues"])
                    report["checks"].update(runtime["checks"])
                    repair.update(status="failed", stop_reason="runtime_failed_or_no_safe_repair")
                    raise SceneValidationError(report)
                repair["attempts"] += 1
                history.append({"phase": "runtime_repair", "attempt": repair["attempts"], "patches": patches})
                runtime_spec = proposal
                report = self.validate(runtime_spec)
                report["repair"] = repair
            backend = "maniskill3" if env is not None else "software_fallback"
            instance_id = uuid.uuid4().hex[:12]
            frame = self._extract_frame(env) if render and env is not None else None
            if render and env is not None and frame is None:
                report.update(ok=False, errors=["simulator reset succeeded but real rendering failed"], runtime=runtime)
                report["checks"]["preview_render"] = False
                raise SceneValidationError(report)
            preview_path = save_preview(runtime_spec, self.preview_dir / f"{instance_id}.png", frame=frame) if render else None
            report["warnings"].extend(details.get("warnings", []))
            if runtime_warning:
                report["warnings"].append(runtime_warning)
            report["runtime"] = runtime
            report["checks"].update(runtime["checks"])
            report["checks"].update(environment_reset=env is not None, preview_render=(frame is not None) if render else None)
            report["task_feasibility"]["runtime_probe"] = self._runtime_task_probe(env)
            task_probe = report["task_feasibility"]["runtime_probe"]
            task_probe["success_after_settling"] = task_probe.pop("success_at_reset")
            report["task_feasibility"]["runtime_probe"]["interpretation"] = "state after settling only; no task policy was executed"
            report["normalized_spec"] = runtime_spec
            repair["status"] = "repaired" if repair["attempts"] else "not_needed"
            repair["stop_reason"] = "valid" if env is not None else "static_only"
            manifest = {
                "instance_id": instance_id, "schema_version": runtime_spec["schema_version"], "backend": "maniskill3",
                "runtime_backend": backend, "seed": seed, "num_envs": num_envs,
                "task_template": runtime_spec["task"]["template"], "robot": runtime_spec["robot"]["type"],
                "requested_control_mode": details.get("requested_control_mode"), "runtime_control_mode": details.get("runtime_control_mode"),
                "validation": report, "task_feasibility": report["task_feasibility"], "generated_at_unix": time.time(),
                "duration_ms": round((time.perf_counter()-started)*1000, 2),
            }
            self.manifest_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = self.manifest_dir / f"{instance_id}.json"
            manifest["manifest_path"] = str(manifest_path)
            manifest_path.write_text(json.dumps({"scene_spec": runtime_spec, "manifest": manifest}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            instance = {"env": env, "reset": reset_result, "scene_spec": runtime_spec,
                        "objects": {o["id"]: o for o in runtime_spec["scene"]["objects"]},
                        "robot": runtime_spec["robot"], "task": runtime_spec["task"], "success": runtime_spec["success"],
                        "cameras": runtime_spec["cameras"], "manifest": manifest, "preview_path": preview_path, "validation": report}
            if preview_path:
                shutil.copyfile(preview_path, self.preview_dir / "latest.png")
            previous = self.last_instance
            self.last_instance = instance
            if previous and previous.get("env") is not None:
                previous["env"].close()
            env = None  # Ownership transferred to last_instance.
            return instance
        finally:
            if env is not None:
                env.close()

    def preview(self, instance: Mapping[str, Any], camera: str = "front") -> str:
        spec = instance.get("scene_spec") or instance.get("spec")
        if not isinstance(spec, Mapping):
            raise ValueError("instance does not contain a scene_spec")
        backend = instance.get("manifest", {}).get("runtime_backend")
        if backend == "portal_trace_robosuite":
            if instance.get("preview_path"):
                return str(instance["preview_path"])
            raise RuntimeError("PORTAL instance was generated without rendering")
        frame = self._extract_frame(instance.get("env"))
        if backend == "maniskill3" and frame is None:
            raise RuntimeError("real simulator preview unavailable")
        instance_id = instance.get("manifest", {}).get("instance_id", "preview") if isinstance(instance.get("manifest"), Mapping) else "preview"
        return save_preview(spec, self.preview_dir / f"{instance_id}_{camera}.png", camera=camera, frame=frame)
