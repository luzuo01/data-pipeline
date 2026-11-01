> 本文保留模块开发说明和历史验收记录。2026-11-01 整理版的支持边界、安装步骤与实测结果以仓库根目录 README 和 docs 为准；文中旧样本与绝对路径不随发布提供。

# 数据引擎：轨迹导入与 LeRobot 转换框架

这是一个用于轨迹导入和 LeRobot v3 转换的工作台。启动数据引擎后浏览器入口是 `http://127.0.0.1:8000/`，`/docs` 仅用于开发接口调试。`8765` 如果被 ManiSkillGen 占用，不是本数据引擎端口。

## 处理流程

```text
前端上传 / Hugging Face 数据集引用
        │
        ▼
导入登记（文件、来源、哈希、数据集名称）
        │
        ▼
格式探测 + 适配器校验
        │
        ▼
统一中间模型（episode / observation / action / timestamp / task）
        │
        ▼
字段映射、dtype/shape/时间轴校验
        │
        ▼
LeRobot v3（Parquet + meta）+ 原始文件 sidecar + semantic_manifest
        │
        ▼
作业状态、错误、产物地址返回前端
```

## 已用真实数据验证的适配器

| 适配器 | 实测输入 | 实测输出 | 验证 |
|---|---|---|---|
| `dexmal_jsonl_video_v1` | `Dexmal/so101_pick_cube` episode 0，234 行，共享 MP4 第 21–254 帧 | 234 行，6 维 state/action，两路 480×640 视频 | 全部数值逐行相等；官方 reader 首/中/末帧；视频帧数与抽样 PSNR |
| `robochallenge_table30_v2_arx5_v1` | `arrange_flowers/episode_000188`，2839 states，三路 720p 视频 | 2838 行，7 维 state、8 维下一姿态 action、三路视频 | 2838 行逐行相等；三路各 2838 帧；官方 reader 与抽样 PSNR |
| `pipeline_confined_reach_v1` | 本地 300 步失败策略轨迹 | 300 行，8 维 state、7 维原策略 action，无视频 | 全部数值/碰撞字段逐行相等；官方 reader 首/中/末行 |

实际报告在 `data_engine/_samples/validation_report.json`。转换实现位于 `source_adapters/`，可执行入口位于 `scripts/`。`Dexmal` 主页是组织页而不是单个数据集，本次选择其公开、体积适合验证且包含真实视频的 `Dexmal/so101_pick_cube`。

Table30 原始数据没有控制命令。该适配器按行为克隆常见处理使用 `state/image[t] -> measured ee_pose+gripper[t+1]`，并在 manifest 中明确标为派生 action，不能称为机器人原始 commanded action。仓库自带示例脚本在 `frame_interval > 1` 时没有同步跳过视频帧，本实现不沿用该读取方式。

## 当前代码边界

- `adapters.py` 定义通用表格适配器协议，并提供 JSONL/CSV 参考实现。
- `source_adapters/` 是按真实来源 schema 验证的适配器，使用官方 `LeRobotDataset` 写出包含视频的数据。
- `writer.py` 只用于通用数值轨迹原型；生产转换应优先走已验证 source adapter。
- `service.py` 提供导入登记、异步转换作业和本地存储实现。
- `api.py` 提供 FastAPI 接口。生产环境应把本地存储替换成对象存储和数据库队列。

## 前端接口

### 浏览器工作台

打开 `http://127.0.0.1:8000/`，按页面上的四步操作：

1. 上传一个 `.zip`，ZIP 内可以放 Dexmal、Table30v2 和质量闭环数据包中的一个或多个来源。
2. 点击“检查 ZIP 内容”，确认平台识别出的来源类型和 episode。
3. 填写输出数据集名称，点击“开始转换”。
4. 等待作业完成，页面会显示输出根目录和每个 LeRobot v3 子目录。

HDF5 页面和接口仍保留用于 `embodied_gt_v1` 样例；ZIP 页面是当前四类来源的统一入口。

内置样例输出为 281 条训练行、20 Hz、9 维 `observation.state`、8 维 `action` 和 front 视频。原始 HDF5 会保存到输出目录的 `extras/source_episode.h5`。当前 HDF5 适配器也支持 `stack` 任务模板，并从源 metadata 读取实际视频分辨率，不再假设固定的 480×640。

判断一个输出是否是 LeRobot，至少检查以下内容：

- `meta/info.json`、`meta/stats.json`、`meta/tasks.parquet`、`meta/episodes/chunk-000/file-000.parquet` 存在；
- `data/` 下有 Parquet，`videos/` 下的 MP4 帧数与数据行对齐；
- 用项目环境执行 `LeRobotDataset(repo_id, root=output, video_backend="pyav")` 能读取首帧、末帧和特征；
- `validation_report.json` 的 `status` 是 `passed`，并且包含源文件 SHA-256、映射字段和帧数检查。

页面也提供 `POST /v1/validate/lerobot`，它会返回结构文件检查、总帧数、特征和官方 reader 状态。

### ZIP 批量导入

平台支持把多个来源放进一个 ZIP 后自动识别。当前已验证的 ZIP 来源签名是：

- `dexmal_jsonl_video_v1`：`episode_*.jsonl` 与同目录的 `top.mp4`、`wrist.mp4`；
- `robochallenge_table30_v2_arx5`：episode 目录下的 `states/states.jsonl`、`meta/episode_meta.json` 和三路相机 MP4；
- `pipeline_confined_reach_v1`：包含 `data/control_records.jsonl` 和 `scene/task.json` 的质量闭环数据包。

上传接口仍然使用 `POST /v1/imports`，但文件名为 `.zip` 时会返回 `inspection.sources`。然后调用：

```http
POST /v1/zip/conversions
Content-Type: application/json
```

```json
{
  "import_id": "imp_xxx",
  "output_repo_id": "project/zip-v1",
  "task": "可选的 Table30 任务描述",
  "preserve_source": true
}
```

每个识别出的来源会生成一个独立的 LeRobot v3 子目录，接口轮询 `GET /v1/jobs/{job_id}`。作业成功后，`output_uri` 是输出根目录，`validation.sources[*].output_uri` 是各个 LeRobot 数据集目录；根目录还包含 `zip_conversion_report.json` 和 `extras/source_upload.zip`。

### `POST /v1/imports`

`multipart/form-data`：`file`、`dataset_name`、`source_type=upload`。

成功响应：

```json
{"import_id":"imp_xxx","filename":"episode.jsonl","bytes":1234,"format":"jsonl"}
```

### `POST /v1/conversions`

```json
{
  "import_id": "imp_xxx",
  "output_repo_id": "project/reach-v1",
  "mapping": {
    "observation": ["observation.qpos"],
    "action": ["action"],
    "timestamp": "timestamp",
    "task": "task",
    "episode": "episode_id"
  },
  "target_version": "lerobot_v3",
  "preserve_source": true
}
```

返回 `202` 和 `job_id`。前端轮询 `GET /v1/jobs/{job_id}`，状态为 `queued/running/succeeded/failed`。失败响应包含稳定的 `error.code` 和 `error.message`，前端不需要解析堆栈。

### `GET /v1/formats`

返回通用导入格式、已经真实数据验证的 profile 和规划中的格式。新增格式必须增加适配器、字段映射测试和样例数据后再从 `planned` 移入已验证列表。

## LeRobot 目标约定

第一版统一中间字段只规定：`observation.state`、`action`、`timestamp`、`episode_index`、`frame_index`、`task`。动作语义、单位、关节顺序、坐标系必须在映射配置和 `semantic_manifest.json` 中显式声明，不能由服务猜测。原始文件默认保留并计算 SHA-256，便于追溯与重转换。

## 运行

```powershell
pip install -r data_engine/requirements.txt
uvicorn data_engine.api:app --reload --port 8000
```

接口文档：`http://localhost:8000/docs`。

## 复现实测转换

以下命令使用仓库已有的 LeRobot Python 环境：

```powershell
# Dexmal 单个完整 episode
lerobot_conversion\.venv\Scripts\python.exe -m data_engine.scripts.convert_dexmal_sample `
  data_engine\_samples\dexmal_so101\episode_00000.jsonl `
  data_engine\_samples\dexmal_so101\top.mp4 `
  data_engine\_samples\dexmal_so101\wrist.mp4 `
  <new-output-directory>

# Table30 ARX5 单个完整 episode
lerobot_conversion\.venv\Scripts\python.exe -m data_engine.scripts.convert_table30_sample `
  data_engine\_samples\table30_arrange_flowers\episode_000188 `
  <new-output-directory>

# 三套真实数据统一验证
lerobot_conversion\.venv\Scripts\python.exe -m data_engine.scripts.validate_real_samples
```

## 后续适配器顺序

1. Parquet/Arrow：直接按 schema 读取，避免 CSV 的类型丢失。
2. HDF5/ROS bag/自定义压缩包：逐格式实现 probe、时间轴和视频对齐校验。
3. Hugging Face：通过 dataset/revision/config/split 固定来源，缓存下载文件并记录 commit hash。
4. LeRobot 输入：先验证版本和 manifest，再提供合并或重打包，不重复编码视频。
