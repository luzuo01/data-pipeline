> 本文保留模块开发说明和历史验收记录。2026-11-01 整理版的支持边界、安装步骤与实测结果以仓库根目录 README 和 docs 为准；文中旧样本与绝对路径不随发布提供。

# ManiSkill3 场景、轨迹与真值数据管线

这是 EnvDreamer 的独立 ManiSkill3 前端。它把自然语言任务转换成受约束的
`scene_spec_v1` JSON，再进行静态验证、有限次数位置修复、真实仿真稳定性检查和预览。UE `EnvGen` 的布局文件、
Remote Control 和截图状态不会被这个模块使用。

现已接通 **SceneGenerator → TrajectoryGen → GTExporter**：Panda 方块抓取放置、堆叠、推动，
成功执行后导出逐帧多模态 HDF5，并在网页播放轨迹、下载数据。支持按种子生成多个场景变体。
接口、格式、验收和支持边界见 [三模块实现说明](PIPELINE_IMPLEMENTATION.md)。不需要原论文 20k 数据集。

## 本地运行

```powershell
cd F:\PKU\DATA_PIPELINE\EnvDreamer
python -m pip install -r ManiSkillGen/requirements.txt
# In the separate ManiSkill3 environment, also install:
python -m pip install -r ManiSkillGen/requirements-maniskill.txt
python -m ManiSkillGen --port 8765
```

打开 `http://127.0.0.1:8765/`，输入自然语言任务即可生成预览。没有安装
ManiSkill3/SAPIEN 时，验证器和库级 generator 仍可用，但网页接口会拒绝生成并明确
报告运行时缺失，避免把示意图误认为 ManiSkill3 结果。安装 ManiSkill3 的独立 Conda
环境后，生成器会注册 `EnvDreamerTabletop-v1`，把 SceneSpec 中的桌面、对象、目标、
Franka 和相机真正编译到 SAPIEN；对应的官方 `PickCube-v1`、`PushCube-v1` 和
`StackCube-v1` 仅作为 API 兼容参考。

如果使用 OpenAI 兼容服务，把 `.env.example` 复制为项目根目录的 `.env`，填写
`OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `OPENAI_MODEL`。服务启动时会读取该文件；修改
后需要重启 `python -m ManiSkillGen`，浏览器刷新不会重载 Python 进程。

Windows 当前使用 `torch==2.7.1`。`torch 2.14.1+cpu` 在这台机器上会触发
`c10.dll` 的 WinError 1114，因此不要用未固定版本覆盖它。Windows 没有 Pinocchio
官方 wheel，`pd_ee_delta_pose` 在场景初始化时会记录并映射到 `pd_joint_pos`；需要
原始末端位姿控制时使用 WSL2/Linux 环境并安装 Pinocchio。

## Python API

```python
from ManiSkillGen import ManiSkillSceneGenerator, SceneSpecPlanner

spec = SceneSpecPlanner().plan("把红色方块放到蓝色目标区域", seed=42)
generator = ManiSkillSceneGenerator()
report = generator.validate(spec)
instance = generator.generate(spec, seed=42, render=True)
print(instance["manifest"], instance["preview_path"])
```

所有模型输出都必须是 JSON；请求失败或没有 API key 时允许显式标记的本地模板回退。模型返回的无效场景会保留并交给 Validator，不会为了通过检查替换成另一个任务。`outputs/manifests/` 保存
规范、seed、任务模板、资产、运行时后端和验证结果，`outputs/previews/latest.png`
是网页使用的最新预览。

模型提示包含实际 JSON Schema 和完整双后端示例。结构错误（例如 task 为字符串、success 为对象、cameras 为数组）最多请求模型纠正一次，原始输出、错误和结果记录在 `planner.format_repair`。仍不合法时继续明确拒绝，不用默认场景冒充成功。几何错误仍交给有限位置修复处理。

本地无 API key 时也支持组合式自然语言解析：可以指定 Panda/Franka、抓取放置、
push、stack，物体数量、cube/sphere/cylinder、红/蓝/绿/黄颜色以及左右位置。
网页右侧“自然语言解析”会显示实际识别到的字段和 fallback 警告。没有被识别的动作
不会伪装成任意通用任务，而会明确回退到 `pick_and_place`；需要 wipe、开门、挂工具
或拧螺母时会自动切换到 PORTAL-Trace 后端。配置或代码变更后必须重启 8765 服务，
否则浏览器仍连接旧的 Python 进程。

Validator 还会输出 `task_feasibility`：检查任务对象是否可移动、目标区域是否能容纳
物体、目标是否在桌面上、stack 支撑面是否足够以及 Panda 的近似工作空间是否覆盖
关键点。`status=feasible` 只证明必要的静态条件；manifest 中的 `runtime_probe` 会
记录 reset 时的状态，但不会把“reset 时尚未成功”误判为任务失败。真实轨迹可完成性
由单独的 TrajectoryGen 执行并验证；只点击场景生成按钮不会执行任务。

## PORTAL-Trace 双后端

SceneSpec 的 `backend` 现在可以是 `maniskill3` 或 `portal_trace`。包含
`PORTAL-Trace`、`Wipe`、`Door`、`ToolHang`、两种 `NutAssembly...Tight-v0`，或
Sawyer/IIWA/Kinova3/XArm7/UR5e 的描述会选择 `portal_trace`；普通 Panda 的
pick/place、push、stack 仍然选择 ManiSkill3。

PORTAL-Trace 任务通过独立的 MuJoCo/robosuite worker 运行，避免和 SAPIEN/Torch
共享进程。准备依赖和仓库路径：

```powershell
uv venv --python 3.11.14 .portal_uv_env
uv pip install --python .portal_uv_env\Scripts\python.exe -r ManiSkillGen\requirements-portal-trace.txt
$env:PORTAL_TRACE_ROOT = "F:\\PKU\\DATA_PIPELINE\\_external_PORTAL_Trace_20261025"
```

也可以设置 `PORTAL_TRACE_PYTHON` 指向已有的 Python 3.11 环境。PORTAL-Trace
后端不会把不可运行的 robosuite 环境降级成 ManiSkill3 或软件示意图；worker
初始化失败时 API 会返回错误和运行时诊断。


## Validator 与修复闭环（2026-10-26）

详见 [实现与验收说明](VALIDATOR_IMPLEMENTATION.md)。不需要 20k 数据集或训练模型。

- ManiSkill：结构/资产/任务引用、保守几何碰撞、越界、支撑、悬空检查；默认最多 3 次确定性位置修复，然后重新编译并进行 20 个控制步的静置检查。运行时失败可在剩余次数内重新布置问题物体并重测。
- PORTAL：支持六个注册任务的真实初始化、状态有限性、短时速度与接触穿透检查；不支持自定义布局修复。未实现的布局、相机外参或物理属性修改会返回错误。
- UI：验证结果、修复历史、原始/最终规范、真实预览与刷新截图。桌面使用固定视口与详情内部滚动。
- `checks` 中 `true` 为通过，`false` 为失败，`null` 为未测试。场景预检不验证完整任务执行；数据管线另行记录数值 IK 和真实任务执行结果。通用无碰撞规划、机器人自碰撞认证仍未实现。
- 失败审计也写入 `outputs/manifests/failed_*.json`；未通过的场景不会替换已接受的预览。

当前 Windows 的 TCP 保留范围包含 8090；可用 `--port 8765` 避开，或者指定其他空闲端口。端口变化不需要修改防火墙或系统保留设置。

```powershell
cd F:\PKU\DATA_PIPELINE\EnvDreamer
& D:\APP\Anaconda\python.exe -m ManiSkillGen --port 8765
```

生成接口增加 `auto_repair`（默认 true）和 `max_repair_attempts`（0–5，默认 3）。
`POST /api/maniskill/scene/repair` 可仅进行静态修复，不启动仿真；`validate` 接口只检查，不修改场景。

```json
{"repair": {"locked_object_ids": ["cube"], "allow_pose_changes": true}}
```

上述字段放在 SceneSpec 顶层；锁定对象不进行位置随机扰动或修复。任务、物体尺寸、颜色、数量与成功阈值不作为修复变量。需要锁定全部位置时设置 `allow_pose_changes=false`。尚未支持的关系约束会被拒绝，不会忽略后继续修复。

网页展开“直接使用 SceneSpec JSON”，粘贴 `examples/repairable_scene.json`：应检测越界和悬空、调整位置并通过真实仿真。换成 `examples/locked_invalid_scene.json`：应拒绝修改锁定对象并返回失败。

```powershell
& D:\APP\Anaconda\python.exe -m pytest ManiSkillGen/tests -q
```
