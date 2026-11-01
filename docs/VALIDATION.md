# 2026-11-01 整理与验证记录

本次从原工作目录提取自然语言合成主链路，在独立 Git 克隆中整理、执行测试，然后提交目标仓库。原 EnvDreamer 检出、运行环境、历史数据和未提交改动没有被替换。

## 本次实际执行

| 项目 | 结果 | 说明 |
| --- | --- | --- |
| GitHub 读取 | 通过 | `git ls-remote`、clone；起始 commit `74c4b07e18050b306de509696057825e83046271` |
| GitHub 推送预检 | 通过 | `git push --dry-run`，包含新分支 ref 的权限预检；未创建预检分支 |
| 整合测试 | **67 passed, 1 skipped** | `MANISKILL_INTEGRATION=1`，65.75 秒；覆盖主模块、UE Python 桥接、数据工作台 |
| 真实仿真 | 通过 | Panda 抓取放置、堆叠、推动；抓取任务有双相机 |
| LeRobot 独立验证 | 三份全部 passed | 对本次新生成的三份数据运行 validate_conversion.py；含官方 reader、动作窗口与 padding |
| Python 编译检查 | 通过 | 主模块、桥接、转换器、数据工作台和脚本 |
| UE 资产恢复 | 通过 | 固定上游 commit；首次复制 2432 个文件，第二次跳过全部已有文件 |
| 环境诊断 | 通过 | 包发现、独立 reader 导入、UE 端口与地图存在 |
| UE 新白模视频 | **本次未重跑** | 端口可达不能证明完整渲染；不将历史渲染记录当成本次新验收 |
| 远端 LLM 请求 | **本次未调用** | 真实任务测试使用显式本地解析，避免依赖个人 API 配置 |
| GitHub Actions | 已配置 | 本地验证不代表托管 runner 已通过，以仓库 Actions 页面为准 |

唯一跳过项：项目未携带可选 PORTAL-Trace Python 环境，故跳过其陈旧路径回退测试。真实仿真中的 Pinocchio 缺失警告已记录；Panda 轨迹使用项目数值 IK，三种任务均通过。

## 新生成数据

| 任务 | 动作 T | 观测 T+1 | 相机 | 结果 |
| --- | ---: | ---: | --- | --- |
| pick_and_place | 291 | 292 | front + side | 物理、真值、LeRobot 独立验证均通过 |
| stack | 274 | 275 | front | 物理、真值、LeRobot 独立验证均通过 |
| push | 218 | 219 | front | 物理、真值、LeRobot 独立验证均通过 |

测试使用 seed=3、160×120 相机；检查初始非成功、末尾连续成功、T/T+1 时间对齐、8 维动作、相机矩阵互逆、几何误差和多相机差异。该结果是三条样本验证，不是任意场景成功率。

机器可读独立验证结果见 [validation-2026-11-01.json](validation-2026-11-01.json)，包含全部数值检查次数、源 SHA 校验、视频帧数、误差与 reader 采样位置。生成数据保留在本地忽略目录 `_validation/`，未随 Git 上传。

## 复现命令

仓库根目录，先准备两个 Python 运行时：

```powershell
$env:MANISKILL_INTEGRATION='1'
# 如使用已有环境，指定其真实绝对路径；项目自带环境路径会自动发现。
$env:LEROBOT_PYTHON='<absolute-path-to-LeRobot-python.exe>'
python -m pytest -q
python -m compileall -q EnvDreamer/ManiSkillGen EnvDreamer/EnvGen/Scripts lerobot_conversion data_engine scripts
```

若使用 `--basetemp=_validation/pytest`，需要先创建 `_validation` 父目录；pytest 会清理指定的临时测试目录，不应指向需要保留的数据。

## 整理变更

- 新增根 README、架构、安装、数据契约、第三方来源与本次验收说明。
- 保留主模块、转换器、UE Python/C++、Panda 资产、数据导入工作台及原测试。
- 新增统一 pytest 配置、GitHub Actions、环境诊断和固定版本 UE 资产恢复脚本。
- CLI 新增 `--render-ue`；LeRobot 解释器发现兼容 Windows/POSIX，显式无效路径不再悄悄回退。
- 依赖快照中的个人绝对 editable 路径替换为固定上游 commit。
- 示例环境配置无凭据；清除复制配置中的 Android 文件服务器 token；忽略密钥、环境、缓存和生成数据。
- 对旧模块说明标记历史范围，明确白模帧复用和 UE-only 占位字段。

上传前对候选文本进行了私钥/API token/GitHub token/凭据赋值模式检查，未发现剩余匹配；检查不等同于正式安全审计。本次没有重新构建 UE C++、完整重装两个环境或执行实验机器人链路。
