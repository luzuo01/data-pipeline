# 第三方来源与许可说明

本仓库整理已有本地工作，保留原模块边界；不把上游代码或资产声明为本项目原创。未对整个仓库新增统一许可证。

| 来源 | 本次关系 | 固定版本 / 说明 |
| --- | --- | --- |
| https://github.com/kswain98/EnvDreamer | UE 项目、场景桥接与本地扩展的基础 | 上游基线 `028027e2ce24e59dce8a59f3a77aa9848409a6e6`；该检出根目录未发现 LICENSE，保留来源说明，不推定额外授权 |
| https://github.com/huggingface/lerobot | 官方数据集 writer / reader，作为依赖安装 | `dd5313e305ff7aaa5b6c7b4769d1b26650ce250b`；不复制整个源码仓库 |
| ManiSkill / SAPIEN | 物理环境、机器人、传感器 | 依赖安装，见 requirements-maniskill.txt |
| https://github.com/frankarobotics/franka_ros | 随仓库发布的 Panda 转换网格 | `ddd2fffd9de44b02ad15b4bbb2bfa2cec4d60d98`；Apache-2.0 原文保留在 EnvDreamer/EnvGen/Content/Robots/FrankaPanda/LICENSE |
| Unreal Engine | UE5.6 编辑器与渲染运行时 | 单独安装；不分发引擎与预编译二进制 |
| PORTAL-Trace / robosuite | 可选预览与实验适配 | 外部安装；完整实验机器人资产不随主链路发布 |

`scripts/prepare_ue_assets.py` 从固定上游 commit 恢复地图、基础 Content 与 Plugins，约 607 MiB 未压缩。脚本不改变来源许可，不覆盖已有文件。原目录中的环境、下载数据集、研究仓库、日志、凭据和生成数据不随本次上传。
