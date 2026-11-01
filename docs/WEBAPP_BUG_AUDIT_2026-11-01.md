# 重启与 Web 应用测试：2026-11-01

> 本文为修复前的历史审计记录。后续修复及当前测试结果见 [修复记录](WEBAPP_FIXES_2026-11-01.md)；文中的失败数、默认跳过策略和未修改业务状态仅描述审计时点。

结论：**显式 ManiSkill3 / Panda 主链路能够完成物理任务、导出并下载 LeRobot v3；默认自然语言入口与 UE 实验分支存在正确性问题。** 原有测试通过不足以覆盖错误路径。

基线提交：4d4a0c60c3ad4b49bc989075bc3d4ee6bdca0bed。本次只新增测试、依赖说明与报告，未修改业务逻辑、未提交或推送 GitHub。

## 环境与范围

- 8765 服务切换并重启到发布检出 github-data-pipeline/EnvDreamer；末次重启后仍运行于 http://127.0.0.1:8765/。
- 按 webapp-testing 流程先读取动态 DOM，再选控件；Python Playwright + 无头 Edge（Chromium 引擎），桌面 1440×1000、移动端 390×844。
- 页面真实请求，Panda 实际 ManiSkill3/SAPIEN 仿真、转换和下载；未调用远端 LLM。
- 正例指定 render_ue=false。本次未重新编译 UE 或完整渲染视频；UE 成功判定用**隔离负例**控制 Remote Control 返回值验证，不代表真实 UE 实验。
- 导入工作台用 FastAPI TestClient 和临时目录测试，不修改已有导入数据。

## 结果

| 测试集 | 结果 |
| --- | --- |
| 原有测试，开启真实仿真 | **67 passed / 1 skipped**，46.20 秒 |
| 新增浏览器/实时接口场景 | **15 通过 / 4 未满足预期**，共 19 个检查 |
| 新增隔离边界案例 | **2 passed / 11 failed**，共 13 个参数化案例 |
| 实际重启后的恢复 | 已完成任务 200 且可下载；规划早期失败任务 404 丢失 |
| 新生成数据独立验证 | passed，含官方 reader、源 SHA、数值/视频、动作窗口、末尾 padding |
| 未捕获 JavaScript 异常 | 0；异常输入对应 HTTP 错误另行记录 |

本次网页数据：run df62453258c5，seed=3，Panda pick_and_place，160×120 front 相机，291 动作 / 292 观测，12.57 秒；ZIP 8,636,401 bytes。下载是真实 v3.0 数据与 GIF。

新增已知失败审计默认跳过，设置 RUN_BUG_AUDIT=1 才执行，跳过不表示已修复。11 次断言失败不等于 11 个独立 bug，四个畸形 JSON 接口共享一类原因。

## 问题清单

### B01 · P1：UE 最终失败仍标记成功轨迹

位置：EnvDreamer/EnvGen/Scripts/ue5_trajectory.py:257、306；下游 ManiSkillGen/pipeline.py:73。

隔离负例令 RC_CheckStackSuccess 返回 success=false。最终 observations/success[-1]=False，但源文件仍标记 complete=True，返回 task_rollout=True，最后 10 帧中 9 帧成功被标为 final_success_hold_frames=9。下游没有额外成功门槛即可加入成功列表。

影响：失败轨迹可能被作为成功训练数据；成功帧数量也不是连续尾段长度。建议以真实成功序列强制验收，最终失败或检查不可用时拒绝成功导出。案例：test_ue_failed_success_check_cannot_certify_rollout。

### B02 · P1：无效 UE 场景返回成功并替换当前场景

位置：EnvDreamer/ManiSkillGen/app.py:149、162。

使用正常示例，设 backend=ue5、control_mode=ue5_joint_pos、物体 size[0]=-1。验证给出 validation.ok=false，但生成接口仍为 HTTP 200、status=ok，并写入当前场景。页面左边“检查完成”，右边“结构失败”。

建议验证失败立即返回 422，并保留原有效状态。案例：test_invalid_ue_scene_is_rejected_and_not_published。截图：03-invalid-ue-accepted.png。

### B03 · P1：同一机器人别名改变主链路

位置：EnvDreamer/ManiSkillGen/planner.py:182、334；模板默认任务 index.html:14。

无 key 时，“Panda 把红色方块放到蓝色目标区域”选择 maniskill3，同样任务改为 Franka 则选择 ue5。页面默认使用 Franka，因此进入有占位深度/法线/矩阵的实验分支，而非 README 验证的物理主链路。

建议统一别名、明确选择后端，默认走已验证链路。案例：test_panda_and_franka_route_to_same_verified_backend。

### B04 · P2：默认场景按钮没有实际生成 UE 场景与截图

位置：EnvDreamer/ManiSkillGen/app.py:144。

页面默认任务点击“生成 · 验证 · 修复”后为 200、validation.ok=true，但 UE 分支只静态验证并构造字典，preview_path=None，没有调用场景构造。截图为空、页面提示完成。

建议实现生成/截图/运行验证，或准确标为仅静态检查。截图：02-default-franka.png。

### B05 · P2：畸形 JSON 导致 500

位置：EnvDreamer/ManiSkillGen/app.py:103、111、124、206。

向 scene/validate、scene/repair、scene/generate、pipeline/start 发送 application/json 原始字节 {broken，四处均 500。await req.json() 在异常处理范围外。最终实时测试使用 bytes，已排除“发送合法 JSON 字符串”的测试误差。

建议统一 JSON 解码处理，返回结构化 400/422。案例：test_malformed_json_returns_client_error。

### B06 · P2：早期失败任务重启后永久丢失

位置：EnvDreamer/ManiSkillGen/app.py:240；pipeline.py:28。

请求 portal_trace 导出，先收到 202，随后 failed。实际 run bab4eae7285a 重启后变为 404；对照成功 run df62453258c5 仍是 200、complete，下载可恢复。

异常只写内存，发生在 run.json 创建前。页面仍提示“刷新页面可重新查询”，实际无法恢复。建议接受任务时就落盘，所有结束状态统一持久化。截图：08-restart-lost-job.png。

### B07 · P2：失败任务显示“0 条成功，0 条失败”

位置：EnvDreamer/ManiSkillGen/templates/index.html:119。

同 B06，顶层 failed/error 已有错误，状态栏却显示 0/0。失败数只来自 failed_episodes.length，未处理 planning 级失败。

建议区分任务失败与 episode 失败，优先展示顶层失败状态。截图：07-failed-job-status.png。

### B08 · P2：损坏 ZIP 上传触发 500

位置：data_engine/service.py:47；api.py:62。

上传 invalid.zip、内容 not a zip，返回 500；接口只捕获 ValueError，漏掉 BadZipFile，上传登记也先于验证。

建议返回结构化 400/422，验证后登记或清理失败上传。案例：test_corrupt_zip_is_a_client_error。

### B09 · P2：ZIP 根目录数据来源无法识别

位置：data_engine/zip_pipeline.py:82。

根直接包含 data/control_records.jsonl 与 scene/task.json 时来源为空；相同文件外套 sample/ 就识别成功。签名匹配要求前导 /data/，没有处理空前缀。

建议按父目录组件匹配。案例：test_pipeline_zip_discovery_independent_of_wrapper_folder。只测试来源结构签名，不声称空 JSON 是完整可转换轨迹。

### B10 · P3：种子参数校验不一致

位置：EnvDreamer/ManiSkillGen/app.py:132。

scene/generate 接收 seed=1.5 并截断为整数；pipeline/start 则返回 400。建议统一严格整数校验，避免配置静默改变。案例：test_fractional_seed_rejected_before_generation。

## 已通过的关键路径

前端 JSON 错误后按钮恢复；Panda 真实场景预览；管线拒绝 0/11 条请求、小数 seed 和字符串布尔值；并发第二任务返回 409，状态接口约 19 ms；页面刷新恢复运行/完成任务；v3 ZIP、JSON、GIF 下载；内部 HDF5 不单独公开；移动端无横向溢出；成功数据经服务重启仍可恢复。

## 复现

根目录准备独立浏览器环境，安装 requirements-webtest.txt，执行 scripts/webapp_discovery.py、scripts/webapp_audit.py。默认使用已安装 Edge；可设置 PLAYWRIGHT_CHANNEL=chrome。实际重启服务后运行 scripts/webapp_restart_probe.py。

服务从 EnvDreamer 目录执行 python -m ManiSkillGen --port 8765，配置有效的 LEROBOT_PYTHON。浏览器测试会真实生成新数据。

隔离边界案例：设置环境变量 RUN_BUG_AUDIT=1，然后运行：

    python -m pytest EnvDreamer/ManiSkillGen/tests/test_webapp_bug_regressions.py data_engine/test_import_bug_regressions.py -q --tb=short

## 证据与后续

本地 _validation/webapp-20261101/ 包含 browser-results.json、browser-trace.zip、截图 01–08、physical-run.json、下载 ZIP、live-dataset-validation.json、restart-results.json、api-bugs.xml、existing-integration.xml 与服务日志。生成数据/trace/日志按原规则忽略，不进入 Git。

优先修复 B01/B02，再解决后端选择和默认场景入口 B03/B04，随后处理错误契约、持久化、导入边界。本次没有修复业务问题，也没有把 UE 实验结果当作完整真值验收。
