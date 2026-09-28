# 核心知识资料

- `lol_catalog.json`：新增的英雄联盟端游独立资料，173 位英雄、865 条主技能、251 件召唤师峡谷装备；版本 `16.19.1`。查看与更新见 [维护说明](../../docs/maintenance/lol-knowledge.md)。

- `hero_relations.json`：英雄关系、阵容与战术基础资料。
- `knowledge_base.json`：通用教练决策规则。
- `skill_catalog.json`：技能说明、基础冷却和资料来源。

本轮只移动文件，三个 JSON 的内容保持不变。读取路径与 `scripts/update_skill_catalog.py` 的写入路径已同步调整。

这些文件属于项目资源；运行生成的数据库、战报、采集资料继续放在 `data/`。

## 2026-09-23 全英雄补充

`skill_catalog.json`新增132英雄的带来源打法与出装参考；`equipment_catalog.json`保存官网装备目录；`hero_voice_aliases.json`独立保存469条别名/错字映射；`voice_assistant_knowledge.json`为生成的语音计时资料。新增攻略均待版本核验，缺失装备不猜填。维护与访问说明见 `docs/maintenance/full-knowledge-2026-09-23.md`。
