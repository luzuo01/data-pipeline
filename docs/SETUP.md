# 安装与运行

## 1. 获取代码

```powershell
git clone https://github.com/luzuo01/data-pipeline.git
cd data-pipeline
```

安装 Git、Python 3.11（仿真）、Python 3.12（LeRobot）。本次真实仿真在现有 Windows 环境验证；新机器仍需可用的 SAPIEN 渲染驱动。UE 回放另需 UE5.6 和 C++ 编译工具链。

## 2. 两个 Python 环境

```powershell
py -3.11 -m venv .venv-sim
.venv-sim\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv-sim\Scripts\python.exe -m pip install -r EnvDreamer/ManiSkillGen/requirements-maniskill.txt
py -3.12 -m venv lerobot_conversion/.venv
lerobot_conversion\.venv\Scripts\python.exe -m pip install -r lerobot_conversion/requirements.txt
```

LeRobot 固定源码 commit。`requirements.lock.txt` 是已有 Windows 转换环境的完整快照；其中 editable 本机绝对路径已改为固定 Git 引用。跨平台安装优先使用精简 `requirements.txt`，完整快照用于复现审计。仿真环境固定 `mani_skill==3.0.1`、`torch==2.7.1`；以实际显卡/平台兼容性为准。

Linux / WSL 使用 `python3.11 -m venv .venv-sim` 和 `python3.12 -m venv lerobot_conversion/.venv`，将 `Scripts/python.exe` 替换为 `bin/python`。主链路已支持两种解释器位置；UE 编辑器截图回放主要在 Windows 工作流验证。

已有环境可通过 **绝对路径** `LEROBOT_PYTHON` 指定转换解释器。未设置时依次检查项目 Windows/POSIX 环境，再使用当前解释器。若显式配置路径不存在，会明确报错。

## 3. 模型配置（可选）

复制 `EnvDreamer/.env.example` 到 `EnvDreamer/.env`，按自己的服务填写 key、base URL、model。留空 key 使用显式本地解析，不依赖远端模型。修改后重启 Python 服务。`.env` 不提交。

## 4. 网页和命令行

```powershell
cd EnvDreamer
..\.venv-sim\Scripts\python.exe -m ManiSkillGen --port 8765
```

打开 `http://127.0.0.1:8765/`。输入 Panda 抓取放置、堆叠或推动任务，或粘贴 SceneSpec JSON。网页生成数据默认请求 UE 视频；没有 UE 时，物理数据仍可成功，但视频会记录错误。

```powershell
# 同在 EnvDreamer 目录；这些 JSON 不需要模型 API
..\.venv-sim\Scripts\python.exe -m ManiSkillGen.pipeline --spec ManiSkillGen/examples/pipeline_pick_place.json --episodes 1 --seed 11
..\.venv-sim\Scripts\python.exe -m ManiSkillGen.pipeline --spec ManiSkillGen/examples/pipeline_stack.json
..\.venv-sim\Scripts\python.exe -m ManiSkillGen.pipeline --spec ManiSkillGen/examples/pipeline_push.json
# UE 就绪后在任一命令末尾添加 --render-ue
```

接口：`POST /api/maniskill/pipeline/start`，请求包含 `prompt` 或 `spec`、`seed`、`num_episodes`（1–10）、`auto_repair`、`max_repair_attempts`（0–5）和 `render_ue`。返回 202 与 `run_id`；轮询 `GET /api/maniskill/pipeline/{run_id}`。输出路径、下载和视频 URL 由实际返回结果确定。

## 5. UE 白模回放

仓库包含 Panda 资产、C++ 和配置；上游基础地图与 Plugins 通过固定版本恢复，避免重复发布约 607 MiB 基础资产。在根目录：

```powershell
python scripts/prepare_ue_assets.py
# 若已有原 EnvDreamer Git 检出，可复用其提交对象：
python scripts/prepare_ue_assets.py --source <existing-EnvDreamer-checkout>
```

脚本只从指定 commit 的 `EnvGen/Content`、`EnvGen/Plugins` 复制缺失文件，不覆盖当前代码、配置或 Panda。依次完成：

1. 为 `EnvDreamer/EnvGen/environment.uproject` 生成工程文件，编译 `environmentEditor`，用 UE5.6 打开。
2. 打开 `/Game/EnvGen/Maps/ThirdPersonMap`，确认 JsonSceneBuilder Actor 存在，Remote Control 启用。
3. HTTP 端口为 `30010`。执行 `Test-NetConnection 127.0.0.1 -Port 30010`；端口可达只表示监听存在。
4. 如果 Actor 路径与 `Scripts/panda_demo.py` 默认值不同，把实际 Object Path 写入 `RC_OBJECT_PATH`，也可配置 `RC_BASE_URL`。
5. 重新编译包含后台截图修复的 `environmentEditor`。编辑器需要加载地图和 level viewport，但窗口可以隐藏，不需要置于前台；使用 `-unattended -NoSplash` 避免启动弹窗阻塞。示例（替换为本机路径）：

```powershell
Start-Process '<UE5.6>/Engine/Binaries/Win64/UnrealEditor.exe' -ArgumentList '<绝对路径>/environment.uproject','-unattended','-NoSplash' -WindowStyle Hidden
```

`RC_Snapshot` 现在主动驱动视口渲染并确认文件写入。旧 DLL 仍会依赖可见视口，因此仅更新 Python 不够。后台模式仍需要图形运行环境与 GPU，并不等于服务器无桌面模式。

截图从当前项目的 `Saved/AIShots` 读取，因此运行的 UE 项目目录应与 Python 桥接目录一致。白模帧复用和失败处理见 [数据契约](DATA_CONTRACT.md)。

诊断（根目录，使用仿真解释器）：

```powershell
.venv-sim\Scripts\python.exe scripts/check_environment.py
.venv-sim\Scripts\python.exe scripts/check_environment.py --ue
```

诊断只做包发现、LeRobot reader 导入、端口与地图检查，不代表 GPU 仿真或完整渲染通过。

## 6. 测试与独立数据验证

```powershell
# 根目录
.venv-sim\Scripts\python.exe -m pytest -q
$env:MANISKILL_INTEGRATION='1'
.venv-sim\Scripts\python.exe -m pytest -q
lerobot_conversion\.venv\Scripts\python.exe lerobot_conversion/validate_conversion.py <lerobot-dataset-root>
```

`data_engine/` 是额外工作台，可在具备其依赖的环境从根目录运行 `python -m uvicorn data_engine.api:app --port 8000`。其历史样本不随仓库发布，需要自行提供对应源数据。UE-only / PORTAL 实验配置见模块源码和架构边界，不属于主链路的快速开始。
