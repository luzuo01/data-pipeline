# 数据契约与时间对齐

本文描述已验证的 Panda / ManiSkill3 主链路。UE-only 实验代码不满足完整真值契约。

## 时间与动作语义

T 个动作对应 T+1 个观测：`obs[t] → action[t] → obs[t+1]`。LeRobot 第 t 行保存 `observation[t] + action[t]`，`next.*` 保存下一观测。当前控制频率 20 Hz。

- `observation.state`：9 维 float32，7 个手臂关节角（rad）+ 两个实测手指位置（m）。
- `action`：8 维 float32，7 个绝对关节目标（rad）+ 归一化夹爪目标，-1 关闭、+1 打开。夹爪指令不是实测手指位移。
- `source.*` / `next.*` 保留低维源数据与下一时刻数据；原始精度、完整 T+1 观测保留在源归档。
- 使用实际执行 metadata 的控制模式，不将请求控制模式视为已执行语义。
- 不虚构 reward、terminated、truncated；success 不自动等于 episode termination。

## 标准目录

```text
lerobot_v3/
  data/chunk-000/file-000.parquet
  videos/observation.images.<camera>/chunk-000/file-000.mp4
  meta/info.json                 # codebase_version: v3.0
  meta/stats.json
  meta/tasks.parquet
  meta/episodes/chunk-000/file-000.parquet
  meta/semantic_manifest.json    # 本项目扩展：来源、单位、shape、对齐
  meta/source_metadata.json     # 本项目扩展：源 metadata
  extras/source_episode.h5      # 本项目扩展：逐字节源归档
```

转换不等于独立验证。运行 `validate_conversion.py` 后才生成独立验证报告。

## 多模态真值

| 数据 | 语义 |
| --- | --- |
| RGB | 标准视频字段；MP4 为有损编码，精确 RGB 在 HDF5 |
| depth | 光轴 Z 深度，float32，米；不转为 8 位视频 |
| normal | OpenCV 相机坐标系表面法线 |
| instance / semantic | 原生数值 ID，映射随 episode 记录 |
| valid | 有效几何像素掩码；无效深度/法线置零 |
| intrinsic / extrinsic | 每相机内参、world-to-camera 与 camera-to-world |
| qpos / qvel / TCP / base | 仿真器机器人状态与位姿 |
| object state | xyz + wxyz + 线速度 xyz + 角速度 xyz |
| engine state | 恢复与审计所需引擎状态 |

世界系 Z 向上，长度米，四元数 wxyz；相机系 X 右、Y 下、Z 前；像素中心 `(u+0.5,v+0.5)`。分割 ID 必须读取 episode 自带清单。

## 白模视频与训练 RGB

LeRobot RGB 来自 SAPIEN，与状态恢复逐观测对齐。独立的 `ue_white.mp4` 用于白模查看；当前每 5 个观测重新截取 UE 画面，中间复用，截图失败也可能复用前帧。应查看回放报告的帧映射、`reused`、采集步长与复用计数。播放 FPS 不等于独立渲染帧率，白模 MP4 不能直接宣称为逐观测真值视觉流。

## 验证

```powershell
lerobot_conversion\.venv\Scripts\python.exe lerobot_conversion/validate_conversion.py <dataset-root>
```

验证器检查 SHA-256、数值映射、shape/dtype、时间戳、MP4 帧数/时间、状态 cast 误差、RGB 编码误差，并用官方 LeRobot reader 读取样本与动作窗口。几何/轨迹真值由主链路报告验证，格式验证不替代物理验证。
