# Web 问题修复与回归：2026-11-01

本次处理历史审计 B01–B10，并将错误路径测试纳入常规 pytest/CI。自然语言默认的 Franka 与 Panda 都使用 ManiSkill3 物理主链路；UE 白模回放入口保留。

## 修复对应关系

| 问题 | 修复 | 验证 |
| --- | --- | --- |
| B01 UE 失败仍成功 | 每个观测读取 UE 成功检查；拒绝缺失/异常/非布尔结果，检查初始未成功及末尾至少 10 个连续成功观测；导出前再检查成功证明 | 最终失败、检查缺失、超时、尾段中断、初始成功均拒绝；连续 10 帧成功正例通过；失败不产生成功 HDF5 或进入转换 |
| B02 无效 UE 场景被接受 | 验证失败返回 422，保留原场景；管线入口也先验证 UE SceneSpec | 负尺寸输入返回 invalid，原有效状态保持 |
| B03 Franka/Panda 后端不一致 | 别名默认统一为 maniskill3；显式 UE 请求才切换实验分支 | 三种别名的默认与显式后端测试；默认页面真实预览 |
| B04 默认场景假完成 | 默认入口实际构建 SAPIEN 场景；显式 UE-only 的 render=true 返回 unsupported；render=false 返回 validated 与静态检查说明，不覆盖生成状态 | 默认页面产生真实截图；实验入口不再报告已生成 |
| B05 畸形 JSON 500 | 四个接口统一 JSON 对象解析，错误返回结构化 400 | 原始畸形 JSON 的接口/实时浏览器请求 |
| B06 失败任务重启丢失 | 接受任务时写原子 job 记录，最终结果也落盘；启动后恢复失败/完成/中断状态 | 实际进程重启后失败仍为 200/failed，完成数据仍可下载 |
| B07 失败显示 0/0 | 顶层 failed/interrupted 优先显示明确状态；未知任务清理失效 localStorage 引用 | 浏览器失败状态与失效任务恢复检查 |
| B08 损坏 ZIP 500 | 捕获损坏格式，返回 400；检验后登记，失败删除本次上传文件 | 无效 ZIP 被拒绝，导入记录和上传目录均无残留 |
| B09 根目录 ZIP 无法识别 | 用路径组件匹配签名，支持根目录/包装目录；修正 Dexmal 嵌套 episode 路径与反斜杠 ZIP 文件大小读取 | pipeline / Table30 / Dexmal 的根与嵌套来源、Windows ZIP 提取 |
| B10 seed 静默截断 | 场景生成、修复和数据管线统一严格整数校验 | 小数、布尔、字符串和 null 均返回 400 |

另修复实际日志中发现的 Windows GBK/UTF-8 子进程解码问题：转换器子进程固定输出 UTF-8，父进程显式按 UTF-8 读取，非法诊断字节可替换而不终止读取。隔离子进程测试覆盖中文 stdout/stderr。

## 验证方式

常规执行 `python -m pytest -q` 即覆盖全部新增回归，不再需要 RUN_BUG_AUDIT 开关。设置 MANISKILL_INTEGRATION=1 并提供可用的 LEROBOT_PYTHON，可执行三种任务的真实仿真和转换。

浏览器测试使用 Python Playwright、无头 Edge（Chromium 引擎），先读取动态 DOM，再操作控件。入口为 scripts/webapp_audit.py；服务实际重启后运行 scripts/webapp_restart_probe.py。浏览器依赖在 requirements-webtest.txt，默认输出 `_validation/webapp-fixes-20261101/`。

本次浏览器 20 项检查全部通过；实际重启后完成/失败任务都恢复成功，未知任务引用会被清除。网页新生成 Panda 数据 run d072a86dc5ae，291 动作、292 观测、20 Hz、160×120 front，相应 LeRobot v3 ZIP、GIF、说明 JSON 可下载；独立验证器及官方 reader 通过。

最终自动测试统计和无本机路径的证据摘要见 [验证摘要](webapp-fixes-2026-11-01.json)。本地完整截图、trace、服务日志、JUnit、下载数据保存在忽略目录，不上传生成数据或运行环境。

## 能力边界

默认场景入口现在真正执行已验证的 Panda / ManiSkill3 链路。UE-only 场景构造仍未实现，因此显式请求不再返回假成功；用户可做静态验证。Panda 的 UE 白模状态回放能力不受该限制。

UE-only 轨迹检查证明的是 Actor 几何成功序列，字段明确标记为 UE_actor_geometry_only，不宣称接触动力学认证。其占位密集字段、实验机器人支持等仍遵循架构文档边界。本次没有重新构建 UE C++ 或完整执行白模渲染；UE 负例与正例通过隔离 Remote Control 返回值验证，真实物理验收来自 Panda / SAPIEN。
