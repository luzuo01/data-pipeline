> 本文保留模块开发说明和历史验收记录。2026-11-01 整理版的支持边界、安装步骤与实测结果以仓库根目录 README 和 docs 为准；文中旧样本与绝对路径不随发布提供。

# UE5 Panda 桌面机械臂 Demo

2026-10-27，本机 UE5.6.1 / Windows 已完成编译、生成、关节控制、夹爪控制和截图验证。当前实现是运动学姿态预览，可作为 UE SceneGenerator 的机器人资产层；没有接入 MuJoCo 动力学。

## 资产来源与选择

- 官方仓库：[frankarobotics/franka_ros](https://github.com/frankarobotics/franka_ros)。
- 固定 revision：`ddd2fffd9de44b02ad15b4bbb2bfa2cec4d60d98`。
- 模型：`franka_description/meshes/visual` 的 link0–link7、hand、finger。
- 关节、夹爪和 TCP 定义来自 common 下的 franka_arm.xacro、franka_hand.xacro、franka_robot.xacro；限位来自 panda/joint_limits.yaml。
- Apache-2.0 许可证已保存在 `EnvGen/Content/Robots/FrankaPanda/LICENSE`。
- DAE 经 Blender 4.3.2 合并为每个 link 一个 FBX，再由 UE5.6 导入 StaticMesh。稳定路径为 `/Game/Robots/FrankaPanda/Source/link0.link0` 等。

另一个候选是 [UnrealRoboticsLab](https://github.com/URLab-Sim/UnrealRoboticsLab)，它把 MuJoCo 接触动力学嵌入 UE，支持 MJCF、控制器和传感器。调研时 README 声明仅测试/支持 UE5.7，不能认为已经兼容你的 UE5.6.1；本次没有安装该插件。

## 直接运行

推荐从原 UE5 网页操作：[http://127.0.0.1:7860/](http://127.0.0.1:7860/)。右侧提供生成机械臂、姿态切换、夹爪开合按钮，操作后自动刷新左侧 UE 截图。8765 是另一个 ManiSkill 页面。

如果服务未启动，在 EnvGen 目录执行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn Scripts.server:app --host 127.0.0.1 --port 7860
```

机械臂示例和控制无需 API key。原自然语言/图片生成入口仍在，使用前需在启动服务的终端配置 OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL。机械臂按钮操作独立示例；聊天仍编辑 home_layout.json 的布局。

打开 `F:\PKU\DATA_PIPELINE\EnvDreamer\EnvGen\environment.uproject`，等编辑器加载完 ThirdPersonMap。不需要按 Play。在 PowerShell 执行：

```powershell
cd F:\PKU\DATA_PIPELINE\EnvDreamer\EnvGen
& D:\APP\Anaconda\python.exe -m Scripts.panda_demo --verify
```

脚本只用 Python 标准库，无需 API key、训练数据集或 LLM 请求。通过现有 127.0.0.1:30010 Remote Control 生成 Panda、120×80 cm 桌面、四条桌腿、4 cm 方块、10 cm 目标标记和底板，并设置相机。

对象使用 panda_demo_ 前缀，位置偏移到原房间旁边。重复运行替换同前缀测试对象，不覆盖 home_layout.json，也不主动保存关卡。重启 UE 后重新执行脚本即可。

改变姿态和夹爪开口：

```powershell
& D:\APP\Anaconda\python.exe -m Scripts.panda_demo --joints 0.2 -0.3 0.1 -1.8 0.2 1.4 0.3 --opening 0.03
```

输出：

- 截图：`EnvGen/Saved/AIShots/panda_tabletop.png`。
- 验证报告：`EnvGen/Saved/panda_demo_verification.json`（仅 --verify 写入）。
- 普通运行状态：`EnvGen/Saved/panda_demo_state.json`。

如果 UE 弹出自动重导入、恢复或保存窗口，需要先处理对话框，否则 Remote Control 会超时。网页布局提示和资产列表已加入 Panda，但本次端到端验证使用确定性示例，没有测试 LLM 自然语言生成质量。

## 控制接口与坐标

| 接口 | 参数/结果 |
|---|---|
| RC_AddAsset | AssetPath="franka_panda"，支持 panda/franka 及 registry alias；scale=[1,1,1] 为原始尺寸 |
| RC_SetRobotJoints | Id、J1…J7，弧度；拒绝非有限值和超出官方关节限位的值 |
| RC_SetRobotGripper | Id、OpeningMeters，米；有限输入限制在 0–0.08 m |
| RC_GetRobotState | joint_positions、gripper_opening_m、tcp_world_cm |
| RC_Snapshot | Name、ResX、ResY，保存编辑器视口 PNG |

脚本机器人 Id 是 `panda_demo_panda_robot`。布局 position 单位为厘米，rotation 为 UE 的 Pitch/Yaw/Roll 度数，scale 为无量纲倍数。模型已转换成厘米，不要再放大 100 倍。

ROS → UE 位置转换为 (100x, -100y, 100z)，旋转同步转换。TCP 是官方 hand 局部 Z 轴上 0.1034 m 的点，不是 hand 模型原点。

示例配置：`EnvGen/Config/panda_tabletop_layout.json`。脚本额外添加桌腿和地板。JSON 可供现有 layout sync 使用，尚未实现通用 ManiSkill SceneSpec → UE 转换器。

## 验证结果

- environmentEditor Win64 Development 在本机 UE5.6.1 构建成功。
- 实际编辑器生成机器人并取得新截图，模型尺寸与 link 连接已目视检查。
- 三组姿态与独立右手系齐次矩阵 FK 比较，TCP 最大数值差约 6.5e-6 cm，低于测试阈值 0.01 cm。这验证坐标和关节变换实现一致性，不表示真实机器人标定精度。
- 夹爪 0 和 0.08 m 开口状态回读通过。
- 越界关节命令被拒绝，原状态保持不变。

需要重新编译时先正常退出 UE：

```powershell
& E:\Games\UE_5.6\Engine\Build\BatchFiles\Build.bat environmentEditor Win64 Development F:\PKU\DATA_PIPELINE\EnvDreamer\EnvGen\environment.uproject -WaitMutex
```

## 当前边界

UE 机械臂本身具备场景搭建、七关节姿态控制、双指开合、TCP 查询和 RGB 截图。它仍通过直接设置变换运动，没有在 UE Chaos 内实现力矩驱动机械臂。物理抓取和任务成功验证现在由下述 SAPIEN 链路完成。

StaticMesh 组件虽然设置了碰撞 profile，但这不等同于验证过的机器人碰撞模型或动力学系统。关节限位检查不能判断方块是否可达，也不能证明任务能完成。

## 已完成：物理抓取、轨迹真值和 UE 白模视频

7860 网页新增自然语言任务执行。勾选“机械臂任务模式”（默认开启），在聊天框输入任务后点击“发送”或“按描述执行并导出”。“填入红绿堆叠示例”会填入可执行的示例描述。关闭任务模式可继续使用原房间布局生成入口。

当前支持 Panda 的方块抓取放置和堆叠，最多 8 个箱体对象。任务解析调用本地 .env 配置的 OpenAI 兼容接口（OPENAI_API_KEY、OPENAI_BASE_URL、MANISKILL_MODEL / OPENAI_MODEL）。模型输出仅作为 SceneSpec JSON 使用，不执行模型代码；不启用失败后的固定任务回退。机器人、形状、实体支撑物、任务对象/目标、成功判据与可达性通过校验后才执行。用户明确的位置不会被自动修复改变。

解析结果保存到 scene_spec.json，原始描述保存到 request.json。支持区分抓取对象与堆叠支撑物、物体颜色、尺寸、位置和稳定时长（0.5 至 5 秒）。堆叠支撑物使用真实可运动碰撞实体，成功要求两物体接触、足够支撑面积、位置/高度容差、松爪以及两个物体稳定。

`EnvGen/Scripts/grasp_pipeline.py` 复用 ManiSkillGen 的真实物理轨迹生成与 GTExporter；`ue_trajectory_render.py` 将每帧实测机器人和物体状态同步到 UE，替换机器人全部 59 个材质槽和桌面物体材质，导出 MP4 及逐帧对应表。UE 的 `/remote/batch` 在本工程触发过崩溃，因此渲染器使用逐条 Remote Control 调用。

`grasp_jobs.py` 提供后台任务、持久进度与受限下载。网页刷新会恢复状态，失败任务不发布产物，录制期间禁止其他网页操作修改场景。UE 编辑器视口须保持打开，不要最小化或手动调整场景。生成后先显示按描述配色的初始场景；视频仍为灰白模型；完成后 UE 场景恢复任务物体颜色并保持成功姿态。scene_initial.png 与 scene_final.png 保存两种彩色状态。

截图采用每次运行、每帧唯一文件名，避免 Windows 下网页读取文件与 UE 覆盖截图的竞争。之前固定文件名在第 212 帧出现过 ImageWriteQueue 无法删除旧文件的问题，已据此修复。

本次输出：`EnvGen/Saved/GraspRuns/grasp_20261027_01/`。303 个动作、304 帧观测，20 Hz；真实夹持检测 148 帧，抬高约 18.47 cm，末尾连续 10 帧满足松爪稳定放置，XY 落点误差约 0.871 mm。视频为 960×540、20 fps、15.2 秒；网页播放与分段下载已验证。HDF5 保存动作、关节状态、末端/物体位姿、引擎状态，以及 SAPIEN 相机 RGB、深度、法线、分割、内外参。

视频帧与 HDF5 观测索引一一对应，但 UE 视频相机与 SAPIEN GT 相机不同。不能直接将 HDF5 相机内参套到 UE 视频。当前验证不包含完整自碰撞检查或通用无碰撞路径规划。具体数据结构及限制见该输出目录的 README.md。

默认物理 Python 为 `D:/APP/Anaconda/python.exe`，可用 `MANISKILL_PYTHON` 覆盖。命令行也可执行：

```powershell
cd F:\PKU\DATA_PIPELINE\EnvDreamer\EnvGen
& D:\APP\Anaconda\python.exe -m Scripts.grasp_pipeline --output Saved/GraspRuns/grasp_my_run
```

输出目录需为新目录。已经完成物理导出但 UE 渲染失败时，可对同一目录加 `--resume`，重用成功物理轨迹重新渲染。

自然语言任务命令行参数：`--request path/to/request.json`，内容为 `{"prompt":"Panda 夹红色方块，放到绿色方块上，松爪并稳定至少 0.5 秒"}`。不传 request 时保留旧的固定搬运示例用于诊断。
