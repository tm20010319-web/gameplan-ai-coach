# Python 源码目录

| 子目录 | 作用 |
|---|---|
| `web` | 主服务、独立监控、图片分析接口 |
| `monitoring` | 监控调度、屏幕采集 |
| `ai` | 模型调用、教练建议 |
| `tactics` | 战术知识、选人、加载阵容、个人打法、分路 |
| `vision` | 英雄、头像、血条、等级、携带技能识别 |
| `skills` | 技能释放证据、连续追踪、范围特效、冷却计时 |
| `knowledge` | 技能资料查询、知识引用 |
| `core` | 数据模型、数据库存储、战报 |

项目根路径由 `paths.py` 提供。核心知识 JSON 在项目根目录下的 `resources/knowledge/`；业务数据库与识别素材仍在 `data/`。

日常启动仍使用项目根目录的 `start-monitor.ps1` 或 `start.ps1`。根目录两个 Python 文件只提供兼容入口，实际接口实现在 `web/`。

导入模块使用完整包路径，例如 `from gameplan.core.models import VisionRequest`。运行测试时在项目根目录执行 `.venv/Scripts/python.exe -m pytest tests`。
