> 本文保留模块开发说明和历史验收记录。2026-11-01 整理版的支持边界、安装步骤与实测结果以仓库根目录 README 和 docs 为准；文中旧样本与绝对路径不随发布提供。

# LeRobot v3 + UE5 白模输出链路

网页中的自然语言任务现在按以下顺序执行：

```text
自然语言
  → SceneSpec / 任务子任务计划
  → ManiSkill3/SAPIEN 物理场景
  → 物理成功轨迹与逐帧真值
  → LeRobot v3 数据集
  → UE5 StaticMesh 白模逐帧回放
  → ue_white.mp4
```

LeRobot 数据集是最终训练格式，压缩包内包含标准目录：

```text
lerobot_v3/
  data/                         # Parquet 训练行
  videos/                       # observation.images.* MP4
  meta/info.json                # codebase_version = v3.0
  meta/stats.json
  meta/tasks.parquet
  meta/episodes/
  meta/semantic_manifest.json   # 字段映射与对齐说明
  extras/source_episode.h5      # 可选的逐字节源归档，便于追溯原始布局
```

当前已经接通并可生成成功轨迹与白模视频的是 ManiSkill3 Panda/Franka 桌面任务（抓取放置、堆叠、推动）。PORTAL-Trace 六种 UE 静态资产仍可用于场景预览；其成功轨迹和 LeRobot 导出需要后续为各机器人实现对应控制器与动作语义映射。

LeRobot 的标准训练视图包含 `observation.state`、`action` 和 RGB 视频，并将深度、法线、实例/语义分割、valid mask 作为原生数值字段写入。相机内外参、物体/机器人/TCP 位姿和引擎状态通过 `source.*`/`next.*` 字段写入，`semantic_manifest.json` 标明来源和时间对齐关系。`extras/source_episode.h5` 是数据集内部的可选无损附件，不是网页发布的最终格式。

UE 白模视频由 `EnvGen/Scripts/ue_trajectory_render.py` 按同一条已验证的物理状态轨迹回放，使用 UE5.6.1 中的白模 StaticMesh，并写出 `ue_white.mp4`。UE 编辑器需要打开项目、保持视口可用并启用 Remote Control（本项目端口为 `30010`）。启动检查可以使用：

```powershell
Test-NetConnection 127.0.0.1 -Port 30010
```

确认 `TcpTestSucceeded : True` 后，再在 `EnvDreamer` 目录启动 `ManiSkillGen` 网页并点击“生成成功轨迹与真值数据”。如果只运行物理环境，LeRobot 数据仍可生成；请求白模视频时必须先让 UE Remote Control 在线。

旧的 `trajectory.h5` 仅作为转换和 UE 重渲染的内部中间文件保留，网页下载项改为 `lerobot_v3.zip`。
