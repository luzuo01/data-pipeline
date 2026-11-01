> 本文保留模块开发说明和历史验收记录。2026-11-01 整理版的支持边界、安装步骤与实测结果以仓库根目录 README 和 docs 为准；文中旧样本与绝对路径不随发布提供。

# SceneGenerator → TrajectoryGen → LeRobot v3 + UE5 White Model

三个模块现已接通本机 ManiSkill3/SAPIEN，网页最终发布 LeRobot v3 数据集和 UE5 白模视频。内部 HDF5 只作为转换中间文件和 LeRobot `extras/source_episode.h5` 无损附件保留。不依赖 EnvDreamer-20k，不训练 Validator。这里的真值来自仿真引擎和渲染器；它表示仿真世界的状态，不等于真实世界无误差。

## 当前支持范围

| 模块 | 实现 | 边界 |
| --- | --- | --- |
| SceneGenerator | 自然语言或 SceneSpec → 场景/资产/机器人/任务/成功判据；静态验证、有限位置修复、真实初始化与短时稳定性检查 | 受约束任务与内置几何资产；不是任意任务生成器 |
| TrajectoryGen | Panda 抓取放置、堆叠、推动；URDF 数值 IK、笛卡尔路点、关节插值、真实控制器执行和成功验证 | 被操作物体为 XY 尺寸不超过 6.5 cm 的 box；堆叠支撑为 box；不保证所有布局可解 |
| GTExporter + LeRobotExporter | 同步多相机 RGB、深度、法线、分割、相机矩阵、物体/机器人/TCP 状态、动作、时间戳和引擎状态；写入 LeRobot v3 Parquet/MP4 | 仅发布成功轨迹；基本材质渲染，未做照片级真实化 |
| UE5 WhiteModelRenderer | 用同一条已验证状态轨迹驱动 UE5 StaticMesh，编码 `ue_white.mp4` | UE 是回放渲染器，不负责物理成功判定 |
| 数据倍增 | seed+i 随机场景位置/颜色，每条重新执行并验证 | 不是复制 HDF5，也不是 MimicGen 的任意示范迁移；未设置随机化时不会自动产生几何差异 |
| PORTAL-Trace | 保留注册任务的场景生成、初始化验证与预览 | 完整三阶段管线暂不支持；请求会明确失败 |

不包含任意 HDF5 导入、通用无碰撞运动规划、机器人自碰撞认证、Photorealizer 或 FidelityValidator。当前 DatasetPacker 固定输出 LeRobot v3；多个任务/随机种子验收不能证明任意场景的成功率。

## 网页使用

```powershell
cd F:\PKU\DATA_PIPELINE\EnvDreamer
& D:\APP\Anaconda\python.exe -m ManiSkillGen --port 8765
```

打开 http://127.0.0.1:8765/ 。输入“Panda 把红色方块放到蓝色目标区域”，选择数据条数，点击 **生成成功轨迹与真值数据**。先生成场景再生成数据也可以：描述未修改时第一条复用已接受的物体位置，后续样本继续按种子随机化。直接输入 SceneSpec JSON 优先于任务描述。

右侧“轨迹与数据”显示执行/导出进度、成功/失败数量、UE 白模视频和下载链接。下载项是 `lerobot_v3.zip`，其中 `data/`、`videos/`、`meta/` 是标准 LeRobot v3，`extras/source_episode.h5` 是无损真值附件。运行中可刷新页面恢复查询；关闭服务会中断未完成的工作。失败项保存在 run.json，不作为成功示范发布。场景生成和数据生成共享单个仿真工作线程，避免并发访问 SAPIEN。

自然语言可使用已配置的 OpenAI 兼容服务；JSON 示例不需要 API。解析器回退到本地模板时会显式记录来源，不应把回退理解成支持任意任务。

## 命令行与 Python

```powershell
# 示例采用 160×120 便于快速验收；可将 cameras.front.resolution 改为 [640,480]
& D:\APP\Anaconda\python.exe -m ManiSkillGen.pipeline --spec ManiSkillGen/examples/pipeline_pick_place.json --episodes 3 --seed 11
& D:\APP\Anaconda\python.exe -m ManiSkillGen.pipeline --spec ManiSkillGen/examples/pipeline_stack.json
& D:\APP\Anaconda\python.exe -m ManiSkillGen.pipeline --spec ManiSkillGen/examples/pipeline_push.json
```

```python
from ManiSkillGen.pipeline import DataPipeline
from ManiSkillGen.scene_spec import default_scene_spec

result = DataPipeline().run(default_scene_spec(), seed=11, num_episodes=3)
print(result["status"], result["episodes"], result["failed_episodes"])
```

模块也可以单独调用。必须保留真实环境直到导出结束：

```python
from ManiSkillGen.generator import ManiSkillSceneGenerator
from ManiSkillGen.trajectory import TrajectoryGenerator
from ManiSkillGen.gt_exporter import GTExporter
from ManiSkillGen.scene_spec import default_scene_spec

generator = ManiSkillSceneGenerator(allow_software_fallback=False)
instance = generator.generate(default_scene_spec(), seed=0, render=False)
try:
    trajectory = TrajectoryGenerator().generate(instance)
    result = GTExporter().export(instance, trajectory, "sample.h5")
finally:
    instance["env"].close()
```

HTTP：`POST /api/maniskill/pipeline/start`，请求包含 `prompt` 或 `spec`，以及 `seed`、`num_episodes`（1–10）、`auto_repair`、`max_repair_attempts`（0–5）、`render_ue`。网页会将 `render_ue` 设为 true；需要 UE5 编辑器和 Remote Control 处于可用状态。返回 202 和 `run_id`；轮询 `GET /api/maniskill/pipeline/{run_id}`。成功项的 `download_url`、`white_model_video_url`、`preview_url`、`metadata_url` 可直接使用。

默认输出 `ManiSkillGen/outputs/datasets/<run_id>/episode_NNN/`：`lerobot_v3.zip`、`lerobot_v3/`、`ue_white.mp4`（启用 UE 回放时）、`episode.json` 和内部转换源。网页不会把内部 HDF5 作为最终下载格式发布。批次状态 `complete`、`partial`、`failed` 分别表示全成功、部分成功、无成功。

## 执行和成功验证

轨迹使用实际 Panda URDF 求 IK，并对照引擎 TCP 验证 FK。Windows 无需 Pinocchio；通过 `pd_joint_pos` 控制器执行 7 维绝对关节角目标和 1 维归一化夹爪目标。不会通过瞬移物体或创建附着关系制造成功。

抓取检查真实抓持和抬升；推动使用闭合夹爪侧面接触。末尾至少连续 10 个控制时刻满足环境成功判据，且要求释放、稳定和目标位置条件。堆叠还要求真实接触与至少 50% 支撑面积。初始已经成功的场景被拒绝，避免输出无意义示范。

动作执行最多 650 步，当前每步 0.05 秒仿真时间。检查物体状态有限性、跌落、非任务物体位移（≤1.5 cm）、涉及场景物体的接触穿透（≤1 cm）。这些检查不能代替机器人自碰撞检测或通用运动规划；未验证项保留为 null。失败明确记入批次。

## LeRobot v3 与无损真值

LeRobot 原生字段包含 `observation.state`、`action`、`observation.images.*`，以及每个相机的 `observation.depth.*`、`observation.normal.*`、`observation.instance_id.*`、`observation.semantic_id.*` 和 `observation.valid.*`。`meta/semantic_manifest.json` 给出每个字段的来源、shape、单位和时间偏移；`extras/source_episode.h5` 保留原始引擎状态、末端轨迹、相机矩阵、完整 T+1 观测和所有数据集属性。

## 内部源格式：embodied_gt_v1

每条文件是一段成功任务执行及其场景定义。**T 个动作对应 T+1 个观测**：`obs[t] → action[t] → obs[t+1]`。

| 路径 | 内容 |
| --- | --- |
| `actions` | `[T,8]` float32：7 个 Panda 手臂绝对关节角目标（rad）+ 夹爪归一化指令 [-1,1]；-1 关闭、+1 打开 |
| `timestamps`, `action_timestamps` | `[T+1]`、`[T]`，相对于首个记录状态的仿真秒数 |
| `stages` | `[T]`，approach/grasp/lift/transport/place/push 等控制阶段 |
| `observations/qpos`, `qvel` | 手臂与夹爪关节实测状态；顺序见 metadata 的 joint_names |
| `observations/tcp_pose`, `robot_base_pose` | `[T+1,7]`，世界系 xyz + wxyz 四元数；TCP 序列即末端轨迹 |
| `observations/success` | `[T+1]`，每个时刻的任务成功判据结果 |
| `objects/<index>/state` | `[T+1,13]`：xyz、wxyz、线速度 xyz、角速度 xyz；组属性 id 关联 SceneSpec |
| `engine/...` | 逐时刻引擎 state_dict；原始键路径保存在 key_path_json 属性 |
| `cameras/<name>/rgb` | `[T+1,H,W,3]` uint8，仿真原始材质 RGB |
| `cameras/<name>/depth_m` | `[T+1,H,W]` float32，光轴 Z 深度（米），不是欧氏距离 |
| `cameras/<name>/normal_cv` | `[T+1,H,W,3]` float32，渲染器表面法线，转换到相机 OpenCV 坐标系 |
| `cameras/<name>/instance_id`, `semantic_id` | 实例/机器人 link ID，以及语义类别；查 metadata.segmentation_catalog |
| `cameras/<name>/valid` | 有效几何像素掩码；无效深度和法线为零 |
| `cameras/<name>/intrinsic_cv` | 每帧 3×3 相机内参 |
| `cameras/<name>/world_to_camera_cv`, `camera_to_world_cv` | 每帧 4×4 变换矩阵 |
| `cameras/<name>/visible_pixels` | 每个实例的可见像素数；列 ID 顺序见 metadata.visible_pixel_columns |
| `metadata_json` | 场景规格、资产/任务/成功条件、场景和轨迹验证、GT 检查、坐标约定、版本、URDF 哈希 |

世界系 Z 向上，长度米；相机系 X 向右、Y 向下、Z 向前。像素中心 `(u+0.5,v+0.5)`。语义 ID：0 背景、1 机器人、2 桌子、3 操作物体、4 目标、5 干扰物。实例 ID 随场景变化，必须用每份数据自带映射，不能跨文件硬编码。

导出使用执行时记录的引擎状态逐帧恢复并渲染，使图像与位姿处于同一时刻；不是根据语言推测标签，也不是再次积分动作后假定状态一致。每帧验证恢复状态误差 ≤1e-5、深度重投影误差 <0.02 像素、法线单位长度、分割映射完整，以及可见物体点与引擎几何的相容性（容差 2 mm）。完整文件标记 `complete=true`。仿真状态恢复不承诺跨引擎版本的动作重放逐位一致。

GIF 仅为缩小到 320×240、最多约 100 帧的查看用动画。训练和真值读取使用 LeRobot v3 数据集；UE 白模视频是按物理状态逐帧回放的可视化产物。

## 本机验收记录

- 原有与新增普通测试：46 通过，3 个真实仿真测试默认跳过。
- 开启真实仿真后，新增模块测试共 8 项通过，覆盖抓取放置、堆叠、推动及双相机导出。
- 最终完整测试集开启真实仿真：49 项全部通过（18.25 秒）；Pinocchio 缺失警告不影响本实现的数值 IK。
- 独立轨迹验收：抓取放置 287 步、堆叠 274 步、推动 218 步，均满足末尾连续成功条件。
- seeds 11、12 的两条 160×120 抓取放置变体：2/2 成功，共约 12 秒，每份约 6.2 MB。重投影最大误差约 0.0027 px，引擎状态恢复误差为 0。
- 网页完整链路：seed 0、640×480、281 动作/282 观测，约 31.41 秒、63.3 MiB，网页显示成功与动画/下载链接。

耗时随分辨率、相机数、轨迹长度、硬件和模型请求延迟变化，以上仅是本机实测样本。

```powershell
$env:MANISKILL_INTEGRATION='1'
& D:\APP\Anaconda\python.exe -m pytest ManiSkillGen/tests -q
```
