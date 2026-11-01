"""Natural language to constrained SceneSpec JSON.

The planner never executes model-produced Python.  A model, when configured,
is asked for JSON only; failures fall back to a local template so generation is
still useful offline.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Mapping

from .scene_spec import default_scene_spec
from .validator import _schema_errors
from .local_intent import primitive_roles, reject_unsupported_request, COLORS


def _load_local_env() -> None:
    """Load the project .env without overriding an explicitly set variable.

    The web server is commonly launched from a desktop shortcut, so shell
    environment variables are not reliable.  Keeping this tiny loader local
    avoids adding a runtime dependency just for three planner settings.
    """
    package_root = Path(__file__).resolve().parents[1]
    for path in (package_root / ".env", Path(__file__).with_name(".env")):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name and value and name not in os.environ:
                os.environ[name] = value


_load_local_env()


class SceneSpecPlanner:
    def __init__(self, *, model: str | None = None, api_key: str | None = None, base_url: str | None = None, allow_fallback: bool = True):
        self.model = model or os.getenv("MANISKILL_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
        # ``api_key=''`` is useful for deterministic offline callers/tests;
        # only ``None`` means "read the project environment".
        self.api_key = os.getenv("OPENAI_API_KEY") if api_key is None else api_key
        self.base_url = os.getenv("OPENAI_BASE_URL") if base_url is None else base_url
        self.allow_fallback = allow_fallback

    @staticmethod
    def system_prompt() -> str:
        from .portal_trace_backend import default_portal_trace_spec
        schema = json.loads((Path(__file__).with_name("schemas") / "scene_spec_v1.json").read_text(encoding="utf-8"))
        return (
            "You generate EnvDreamer SceneSpec JSON. Return one JSON object only. "
            "Use schema_version scene_spec_v1 and choose backend maniskill3 for Panda tabletop tasks "
            "or backend ue5 for Sawyer and UR5e tabletop Stack tasks. The ue5 backend runs in "
            "Unreal Engine 5.6.1 using imported articulated link assets and Chaos scene objects. "
            "The legacy portal_trace backend is not used by the application. PORTAL-Trace supports the exact task "
            "names NutAssemblySquareTight-v0, NutAssemblyRoundTight-v0, ToolHang, Stack, Door, "
            "Wipe and robot families Panda, Sawyer, IIWA, Kinova3, XArm7, UR5e. "
            "ManiSkill3 supports primitive tabletop pick_and_place, push and stack with Panda. "
            "Never return Python or executable code. Keep every object inside the tabletop workspace, "
            "and include success predicates and at least one camera. "
            "For explicit fixed positions use repair.locked_object_ids (object ID strings). "
            "Repairs may only move unlocked objects; never alter task, dimensions or success thresholds. "
            "PORTAL uses registered layouts only: objects must be empty and do not customize env_kwargs. "
            "Primitive sphere size is equal XYZ diameter, cylinder size is [diameter, diameter, height]. "
            "Objects start on the table, not floating; stack tasks start with separate objects on the table. "
            "The exact JSON contract below is mandatory, not a suggested format. "
            "scene.workspace and scene.objects are nested inside scene, never top-level. "
            "task is an OBJECT with template, object_id and target_id, never a string. "
            "success is an ARRAY of objects with predicate, object_id, target_id, position_tolerance, "
            "never an object containing predicates. For ManiSkill, use object_in_target for pick_and_place, "
            "object_displaced for push, object_on_object for stack. Never use object_in_region or region_id. "
            "cameras is an OBJECT keyed by camera name, never an array. Each value has position, look_at, "
            "resolution [width,height]; look_at must point at the table, not the world origin below it. "
            "Use only primitive_cube, primitive_target, primitive_sphere, primitive_cylinder asset IDs. "
            "A goal region uses primitive_target with shape box and dynamic false. "
            "Use metres, XYZ position arrays, RGBA color arrays; preserve the requested object and target colors. "
            "Unless the user specifies other dimensions, use the example's 4 cm box, 8 cm target, "
            "table height 0.75, Panda base [-0.4,0,0], and camera. "
            "Use the task-appropriate example's structure, adapting only the requested scene content.\n"
            "JSON schema:\n" + json.dumps(schema, ensure_ascii=False) +
            "\nComplete ManiSkill example:\n" + json.dumps(default_scene_spec(), ensure_ascii=False) +
            "\nComplete PORTAL example:\n" + json.dumps(default_portal_trace_spec(task_id="Stack", robot="Panda", seed=0), ensure_ascii=False)
        )

    @staticmethod
    def _subtasks(spec: Mapping[str, Any], *, source: str) -> list[dict[str, Any]]:
        """Build a deterministic EnvDreamer-style execution plan.

        The language model (when enabled) only supplies constrained SceneSpec
        JSON.  This list is derived locally from that JSON and is therefore
        safe to display and execute: it is a plan of typed operations, not
        model-generated Python.
        """
        backend = str(spec.get("backend", "maniskill3"))
        robot = spec.get("robot", {}).get("type") if isinstance(spec.get("robot"), Mapping) else None
        task = spec.get("task", {}).get("template") if isinstance(spec.get("task"), Mapping) else None
        asset = f"portal_{str(robot).lower()}" if backend == "portal_trace" else "franka_panda"
        if backend == "ue5":
            asset = ("ue5_sawyer" if robot == "Sawyer" else
                     "ue5_ur5e" if robot == "UR5e" else "franka_panda")
        if robot == "Kinova3":
            asset = "portal_kinova3"
        elif robot == "UR5e":
            asset = "portal_ur5e"
        ue_asset = None
        if backend == "ue5":
            ue_asset = ("franka_panda" if robot in {"franka", "panda"} else
                        f"/Game/Robots/PORTALTrace/{robot}/{robot}")
        elif backend == "portal_trace":
            ue_asset = f"/Game/Robots/PORTALTrace/{robot}/{robot}"
        return [
            {"id": "parse_intent", "type": "intent_analysis", "status": "completed", "source": source},
            {"id": "resolve_robot", "type": "asset_resolution", "status": "completed", "robot": robot,
             "asset": asset, "ue_asset": ue_asset},
            {"id": "resolve_task", "type": "task_resolution", "status": "completed", "backend": backend,
             "task": task, "environment": (spec.get("task", {}).get("env_name") if isinstance(spec.get("task"), Mapping) else None)},
            {"id": "validate_scene", "type": "scene_validation", "status": "pending"},
            {"id": "generate_scene", "type": "scene_generation", "status": "pending"},
            {"id": "verify_task", "type": "task_verification", "status": "pending", "success_predicates": spec.get("success", [])},
        ]

    @staticmethod
    def _prompt_template(prompt: str, seed: int) -> Dict[str, Any]:
        from .portal_trace_backend import default_portal_trace_spec, portal_prompt_match

        portal_task, portal_robot = portal_prompt_match(prompt)
        text = (prompt or "").lower()
        explicit_portal = "portal-trace" in text or "portal_trace" in text
        portal_signal = explicit_portal or portal_task in {
            "Wipe", "Door", "ToolHang", "NutAssemblySquareTight-v0", "NutAssemblyRoundTight-v0"
        }
        if portal_signal:
            task_id = portal_task or "Stack"
            robot = portal_robot or "Panda"
            if robot in {"Sawyer", "UR5e"} and not explicit_portal:
                spec = default_scene_spec(seed)
                spec["backend"] = "ue5"
                spec["robot"] = {"type": robot, "base_pose": [-0.4, 0.0, 0.0], "control_mode": "ue5_joint_pos"}
                spec["scene"]["template"] = "tabletop_stack"
                spec["scene"]["objects"] = [
                    {"id": "red_cube", "asset": "primitive_cube", "shape": "box", "size": [0.04, 0.04, 0.04], "pose": [-0.12, 0.0, 0.77], "color": [0.85, 0.1, 0.1, 1.0], "dynamic": True},
                    {"id": "green_cube", "asset": "primitive_cube", "shape": "box", "size": [0.06, 0.06, 0.06], "pose": [0.12, 0.0, 0.78], "color": [0.1, 0.75, 0.2, 1.0], "dynamic": True},
                ]
                spec["task"] = {"template": "stack", "object_id": "red_cube", "target_id": "green_cube"}
                spec["success"] = [{"predicate": "object_on_object", "object_id": "red_cube", "target_id": "green_cube", "position_tolerance": 0.025}]
                spec["planner"] = {"source": "local_fallback", "parsed": {"backend": "ue5", "task": "Stack", "robot": robot}}
                spec["planner"]["subtasks"] = SceneSpecPlanner._subtasks(spec, source="local_fallback")
                spec["description"] = prompt
                return spec
            spec = default_portal_trace_spec(task_id=task_id, robot=robot, seed=seed)
            spec["description"] = prompt or f"PORTAL-Trace {task_id} with {robot}"
            spec["planner"] = {"source": "local_fallback", "parsed": {"backend": "portal_trace", "task": task_id, "robot": robot}}
            spec["planner"]["subtasks"] = SceneSpecPlanner._subtasks(spec, source="local_fallback")
            return spec

        spec = default_scene_spec(seed)
        # The local planner is intentionally a constrained parser.  It can
        # compose several primitives and task parameters, while preserving a
        # machine-readable account of what was understood.
        robot_aliases = {
            "panda": "panda", "franka": "franka", "franka panda": "panda",
            "潘达": "panda", "熊猫": "panda", "sawyer": "Sawyer", "ur5e": "UR5e", "ur5": "UR5e",
        }
        robot = next((value for alias, value in robot_aliases.items() if alias in text), "franka")
        spec["robot"]["type"] = robot
        # Franka and Panda share the verified physical backend. Other native
        # robots remain explicit experimental UE-only choices.
        if robot in {"Sawyer", "UR5e"}:
            spec["backend"] = "ue5"
            spec["robot"]["control_mode"] = "ue5_joint_pos"

        color_map = {
            "red": [0.85, 0.1, 0.1, 1.0], "红": [0.85, 0.1, 0.1, 1.0],
            "blue": [0.1, 0.2, 0.9, 1.0], "蓝": [0.1, 0.2, 0.9, 1.0],
            "green": [0.1, 0.75, 0.2, 1.0], "绿": [0.1, 0.75, 0.2, 1.0],
            "yellow": [0.95, 0.75, 0.05, 1.0], "黄": [0.95, 0.75, 0.05, 1.0],
            "white": [0.9, 0.9, 0.9, 1.0], "白": [0.9, 0.9, 0.9, 1.0],
        }
        shape_aliases = {
            "sphere": ("sphere", "primitive_sphere", [0.05, 0.05, 0.05]),
            "球": ("sphere", "primitive_sphere", [0.05, 0.05, 0.05]),
            "cylinder": ("cylinder", "primitive_cylinder", [0.05, 0.05, 0.06]),
            "圆柱": ("cylinder", "primitive_cylinder", [0.05, 0.05, 0.06]),
            "cube": ("box", "primitive_cube", [0.04, 0.04, 0.04]),
            "方块": ("box", "primitive_cube", [0.04, 0.04, 0.04]),
            "box": ("box", "primitive_cube", [0.04, 0.04, 0.04]),
        }
        shape_key = next((key for key in shape_aliases if key in text), "cube")
        shape, asset, dimensions = shape_aliases[shape_key]

        number_words = {"one": 1, "一个": 1, "two": 2, "两个": 2, "两": 2, "three": 3, "三个": 3, "a": 1}
        count = next((value for word, value in number_words.items()
                      if (re.search(r"\b" + re.escape(word) + r"\b", text)
                          if word.isascii() else word in text)), 1)
        is_stack = any(word in text for word in ("stack", "堆", "叠"))
        is_push = any(word in text for word in ("push", "推", "推动"))
        is_pick = any(word in text for word in ("pick", "place", "抓", "拿", "放", "搬"))
        if is_stack:
            count = max(2, count)

        # Follow mention order, not dictionary order (blue used to win over green).
        color_mentions = sorted((text.index(word), color) for word, color in color_map.items() if word in text)
        mentioned_colors = []
        for _, color in color_mentions:
            if list(color) not in mentioned_colors:
                mentioned_colors.append(list(color))
        object_color = mentioned_colors[0] if mentioned_colors else list(spec["scene"]["objects"][0]["color"])
        target_color = mentioned_colors[1] if len(mentioned_colors) > 1 else list(spec["scene"]["objects"][1]["color"])
        descriptions, explicit_target, moving_color = primitive_roles(text)
        if descriptions:
            count = max(count, len(descriptions))
        if explicit_target:
            target_color = COLORS[explicit_target]
        workspace = spec["scene"]["workspace"]
        table_height = float(workspace["table_height"])
        objects: list[dict[str, Any]] = []
        for index in range(count):
            object_id = "cube" if index == 0 else f"cube_{index + 1}"
            z = table_height + dimensions[2] / 2
            x = -0.10 + index * 0.10 if count > 1 else 0.0
            if count == 2 and any(word in text for word in ("left", "right", "左", "右")):
                x = -0.12 if index == 0 else 0.12
            if any(word in text for word in ("left", "左")) and not any(word in text for word in ("right", "右")):
                x = -abs(x or 0.12)
            if any(word in text for word in ("right", "右")) and not any(word in text for word in ("left", "左")):
                x = abs(x or 0.12)
            objects.append({"id": object_id, "asset": asset, "shape": shape, "size": list(dimensions), "pose": [x, 0.0, z], "color": list(object_color), "dynamic": True})
        if descriptions:
            for index, obj in enumerate(objects):
                color, size_word = descriptions[min(index, len(descriptions)-1)]
                obj["color"] = list(COLORS[color])
                if size_word in {"large", "大"}:
                    obj["size"] = [.06, .06, .06]
                elif size_word in {"small", "小"}:
                    obj["size"] = [.04, .04, .04]
                obj["pose"][2] = table_height + obj["size"][2]/2
        target = dict(spec["scene"]["objects"][1])
        target["color"] = target_color
        target["pose"] = [0.22, 0.12, table_height + target["size"][2] / 2]
        objects.append(target)
        spec["scene"]["objects"] = objects

        if is_stack:
            spec["task"] = {"template": "stack", "object_id": objects[1]["id"], "target_id": objects[0]["id"]}
            spec["success"] = [{"predicate": "object_on_object", "object_id": objects[1]["id"], "target_id": objects[0]["id"], "position_tolerance": 0.03}]
        elif is_push:
            spec["task"] = {"template": "push", "object_id": objects[0]["id"], "target_id": "target"}
            spec["success"] = [{"predicate": "object_displaced", "object_id": objects[0]["id"], "target_id": "target", "position_tolerance": 0.03}]
        else:
            spec["task"] = {"template": "pick_and_place", "object_id": objects[0]["id"], "target_id": "target"}
            spec["success"] = [{"predicate": "object_in_target", "object_id": objects[0]["id"], "target_id": "target", "position_tolerance": 0.03}]

        if moving_color:
            selected = next((o for o in objects if o["dynamic"] and o["color"] == COLORS[moving_color]), None)
            if selected:
                spec["task"]["object_id"] = selected["id"]
                if is_stack:
                    support = next(o for o in objects if o["dynamic"] and o is not selected)
                    spec["task"]["target_id"] = support["id"]
                spec["success"][0].update(object_id=spec["task"]["object_id"], target_id=spec["task"]["target_id"])
        if any(word in text for word in ("left", "right", "左", "右")):
            spec["repair"] = {"locked_object_ids": [o["id"] for o in objects if o["dynamic"]]}
        recognized = ["robot", "shape", "count", "task", "colors"]
        unsupported = []
        if not (is_stack or is_push or is_pick):
            unsupported.append("未明确识别抓取/放置、推动或堆叠动作，已使用 pick_and_place")
        spec["description"] = prompt or "Franka tabletop scene"
        spec["planner"] = {"source": "local_fallback", "parsed": {"backend": spec["backend"], "robot": robot, "shape": shape, "count": count, "task": spec["task"]["template"], "recognized": recognized}, "warnings": unsupported}
        spec["planner"]["subtasks"] = SceneSpecPlanner._subtasks(spec, source="local_fallback")
        return spec

    @staticmethod
    def _decode(content: str) -> Dict[str, Any]:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I | re.S).strip()
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("planner response must be a JSON object")
        return value

    def _call_model(self, prompt: str, seed: int) -> Dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            response = client.chat.completions.create(
                model=self.model,
                temperature=0.1,
                max_tokens=3000,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": self.system_prompt()}, {"role": "user", "content": f"seed={seed}\n{prompt}"}],
                timeout=60,
            )
            content = response.choices[0].message.content or ""
            return self._decode(content)
        except Exception as exc:
            raise RuntimeError(f"SceneSpec planner request failed: {exc}") from exc

    def plan(self, prompt: str, *, seed: int = 0) -> Dict[str, Any]:
        reject_unsupported_request(prompt)
        errors: list[str] = []
        format_repair = None
        try:
            candidate = self._call_model(prompt, int(seed))
        except Exception as exc:
            errors.append(str(exc))
            candidate = None
        if candidate is None:
            if not self.allow_fallback:
                raise RuntimeError(errors[-1] if errors else "planner failed")
            candidate = self._prompt_template(prompt, int(seed))
        else:
            format_errors = _schema_errors(candidate)
            if format_errors:
                # One bounded format retry. Geometry remains the Validator's job;
                # never silently replace invalid model output with a default task.
                format_repair = {"original_candidate": candidate, "errors": format_errors, "attempts": 1}
                try:
                    corrected = self._call_model(
                        "Original user request:\n" + prompt +
                        "\nYour previous JSON failed the required schema. Correct its structure using "
                        "the system examples. Preserve the user's task, object identities, colors, "
                        "dimensions, explicit positions and success thresholds. Return complete JSON.\n"
                        "Previous JSON:\n" + json.dumps(candidate, ensure_ascii=False) +
                        "\nSchema errors:\n" + json.dumps(format_errors, ensure_ascii=False), int(seed))
                    remaining = _schema_errors(corrected)
                    format_repair.update(status="failed" if remaining else "corrected", remaining_errors=remaining)
                    candidate = corrected
                except Exception as exc:
                    format_repair.update(status="failed", error=str(exc))
        # Preserve invalid output for structured validation and bounded repair.
        # Replacing a failed candidate with a new template would change user intent.
        normalized = dict(candidate)
        normalized["seed"] = int(seed)
        # Resolve equivalent Panda names consistently, including model output.
        # Explicit UE requests are kept separate from the default physics path.
        robot_value = str(normalized.get("robot", {}).get("type", "")).lower() if isinstance(normalized.get("robot"), dict) else ""
        prompt_lower = str(prompt or "").lower()
        explicit_portal = "portal-trace" in prompt_lower or "portal_trace" in prompt_lower
        explicit_ue = "ue5" in prompt_lower or "unreal" in prompt_lower
        native_request = ((robot_value in {"franka", "panda"} and explicit_ue) or
                          robot_value in {"sawyer", "ur5e", "ur5"})
        if native_request and not explicit_portal and "maniskill3" not in prompt_lower:
            normalized["backend"] = "ue5"
            normalized.setdefault("robot", {})["control_mode"] = "ue5_joint_pos"
            if robot_value == "ur5": normalized["robot"]["type"] = "UR5e"
        elif robot_value in {"franka", "panda"} and not explicit_portal and normalized.get("backend") != "portal_trace":
            normalized["backend"] = "maniskill3"
            if normalized["robot"].get("control_mode") == "ue5_joint_pos":
                normalized["robot"]["control_mode"] = "pd_joint_pos"
        if errors:
            normalized.setdefault("planner", {}).setdefault("warnings", []).extend(errors)
            normalized["planner"]["source"] = "local_fallback"
        else:
            normalized.setdefault("planner", {})["source"] = "llm"
        normalized["planner"]["user_prompt"] = prompt
        normalized["planner"]["subtasks"] = self._subtasks(normalized, source=normalized["planner"].get("source", "unknown"))
        if format_repair:
            normalized["planner"]["format_repair"] = format_repair
        return normalized


def plan_scene_spec(prompt: str, *, seed: int = 0, planner: SceneSpecPlanner | None = None) -> Dict[str, Any]:
    return (planner or SceneSpecPlanner()).plan(prompt, seed=seed)
