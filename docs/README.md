# 项目分类导航

源码已按功能集中到 `gameplan/`，核心知识资料集中到 `resources/knowledge/`。根目录的 `app.py` 和 `monitor_app.py` 仅作为兼容启动入口；原启动脚本和浏览器地址继续有效。

## 目录总览

```text
gameplan/               Python 实现代码
  web/                  服务和页面接口
  monitoring/           监控流程与屏幕采集
  ai/                   模型接口与建议
  tactics/              战术、选人和个人打法
  vision/               英雄、血条、图标识别
  skills/               施放证据与冷却计时
  knowledge/            知识查询与引用
  core/                 数据结构、存储、复盘
resources/knowledge/    三份核心知识库 JSON
static/                 网页与界面素材
tests/                  自动化测试与样本
scripts/                维护工具
data/                   业务数据与识别资源
docs/                   文档
work/                   日志、诊断、验证与备份
```

## 打开项目

- 日常浏览入口：[独立监控工作台](http://127.0.0.1:8767/monitor?monitor=off)。此地址只打开界面，不自动开始监控。
- 如果页面打不开，在项目根目录运行 `./start-monitor.ps1 -NoAutoStart`。启动器会启动或复用后台并打开浏览器。
- [独立监控启动脚本](../start-monitor.ps1)；[主工作台启动脚本](../start.ps1)；[完整使用说明](../README.md)。

## 需求与方案

以下文档已集中到 `requirements/`：

- [完整需求与实施方案](requirements/电竞室AI助教_完整需求与实施方案.md)
- [无摄像头交付计划](requirements/电竞室_AI_助教项目_无摄像头交付计划.md)
- [屏幕助教方案评审](requirements/Qwen3-VL-8B_屏幕助教方案_评审修订稿.md)
- [敌方技能释放与冷却提醒需求](requirements/敌方技能释放记录与冷却提醒_需求规格.md)

## 源码按功能查找

| 功能 | 文件位置（相对于项目根目录） |
|---|---|
| 服务入口 | [app.py](../gameplan/web/app.py)、[monitor_app.py](../gameplan/web/monitor_app.py) |
| 监控与屏幕采集 | [monitor_runtime.py](../gameplan/monitoring/monitor_runtime.py)、[screen_capture.py](../gameplan/monitoring/screen_capture.py) |
| AI 接口与图片分析 | [integrations.py](../gameplan/ai/integrations.py)、[advisor.py](../gameplan/ai/advisor.py)、[picture_analysis.py](../gameplan/web/picture_analysis.py) |
| 战术、选人和个人方案 | [coach.py](../gameplan/tactics/coach.py)、[bp_assistant.py](../gameplan/tactics/bp_assistant.py)、[loading_plan.py](../gameplan/tactics/loading_plan.py)、[personal_plan.py](../gameplan/tactics/personal_plan.py)、[lane_guidance.py](../gameplan/tactics/lane_guidance.py) |
| 英雄与画面识别 | [hero_recognition.py](../gameplan/vision/hero_recognition.py)、[bp_portraits.py](../gameplan/vision/bp_portraits.py)、[loading_evidence.py](../gameplan/vision/loading_evidence.py)、[loading_spells.py](../gameplan/vision/loading_spells.py)、[summoner_icons.py](../gameplan/vision/summoner_icons.py)、[health_levels.py](../gameplan/vision/health_levels.py)、[health_nameplates.py](../gameplan/vision/health_nameplates.py) |
| 施放证据与冷却计时 | [auto_skill_monitor.py](../gameplan/skills/auto_skill_monitor.py)、[combat_evidence.py](../gameplan/skills/combat_evidence.py)、[combat_tracks.py](../gameplan/skills/combat_tracks.py)、[grounded_casts.py](../gameplan/skills/grounded_casts.py)、[temporal_casts.py](../gameplan/skills/temporal_casts.py)、[flash_motion.py](../gameplan/skills/flash_motion.py)、[field_effects.py](../gameplan/skills/field_effects.py)、[cooldowns.py](../gameplan/skills/cooldowns.py) |
| 知识库逻辑 | [skill_knowledge.py](../gameplan/knowledge/skill_knowledge.py)、[knowledge_advice.py](../gameplan/knowledge/knowledge_advice.py) |
| 数据结构、存储与复盘 | [models.py](../gameplan/core/models.py)、[storage.py](../gameplan/core/storage.py)、[reporting.py](../gameplan/core/reporting.py) |
| 网页界面与素材 | [static](../static/)；工作台、监控、图片、选人、战报页面均在此处 |
| 自动化测试与固定样本 | [tests](../tests/)；包括 Python 测试、`.cjs` 前端测试及 `fixtures/` |
| 数据维护工具 | [scripts](../scripts/)；资料导入、头像更新、技能更新、知识库审计 |

## 知识资料与实现说明

- [知识库说明](hero-knowledge.md)、[四英雄资料说明](four-hero-knowledge.md)
- [60 英雄教练知识库](王者荣耀_AI助教_高级教练知识库_v2_60英雄.md)
- [英雄技能与大招冷却索引](英雄技能与大招冷却索引.md)
- [知识库审计报告](knowledge-audit.md)、[审计数据](knowledge-audit.json)
- [英雄名称识别说明](hero-name-recognition.md)
- [敌方大招闪现字幕实现与验证](敌方大招闪现字幕_实现与验证.md)
- [李元芳资料来源审核](li-yuanfang-source-review.md)、[头条来源审核](li-yuanfang-toutiao-review.md)

索引和审计报告由维护脚本写入固定位置，因此保留原路径。

## 数据、依赖和开发记录

| 类别 | 位置与处理方式 |
|---|---|
| 核心知识数据 | [resources/knowledge](../resources/knowledge/) 中的三个 JSON；程序与更新脚本使用同一目录 |
| 业务数据库与资源 | [data](../data/) 中的数据库、教练资料、头像、技能图标与战报；保留原位置 |
| 资料来源存档 | [data/knowledge_sources](../data/knowledge_sources/)；保存采集、导入的来源证据 |
| 前端测试依赖 | [data/ui-check](../data/ui-check/)；测试脚本固定引用，保留原位置 |
| Python 环境 | 根目录 `.venv/`；启动脚本固定引用 |
| 配置 | 根目录 `.env`、`.env.example`、`requirements.txt`、`.gitignore` |
| 运行日志 | 当前独立监控日志在 [work/monitor-logs](../work/monitor-logs/)；旧根目录日志归档到 `work/logs/legacy/`，其他开发日志仍在 `work/` |
| 诊断与验证过程 | [work](../work/) 中的诊断脚本、专项验证目录、截图及结果；按下表辨认 |
| 编辑器与缓存 | `.idea/`、`__pycache__/`、`.pytest_cache/`；未清理 |

`work/` 中的内容不能整体视为可删除文件：

| 用途 | 典型内容 |
|---|---|
| 专项验证 | `realtime-v2/`、`summoner-loading-fix/`、`level-gate-fix/`、`gaojianli-fix/` |
| 诊断脚本 | `diagnose_*`、`probe_*`、`verify_*`、性能测试脚本 |
| 环境修复证据 | Ollama GPU 检查及运行库修复记录 |
| 历史源码备份 | [setup-backup-20260909](../work/setup-backup-20260909/) |
| 本轮整理备份 | [organization-backups/2026-09-17](../work/organization-backups/2026-09-17/) |

## 历史文档与整理记录

- [2026-09-12 原始交接](archive/2026-09-12/PROJECT_HANDOFF_2026-09-12.md)
- [2026-09-12 更新版交接](archive/2026-09-12/PROJECT_HANDOFF_2026-09-12_UPDATED.md)
- [2026-09-12 缺陷修复指南](archive/2026-09-12/GAMEPLAN_BUG_FIX_GUIDE_2026-09-12.md)
- [2026-09-17 第一批整理记录](maintenance/organization-2026-09-17.md)
- [2026-09-17 源码分类整理记录](maintenance/source-organization-2026-09-17.md)

历史交接反映当时状态，当前入口和实际源码以项目现状为准。
