# 核心知识资料

- `hero_relations.json`：英雄关系、阵容与战术基础资料。
- `knowledge_base.json`：通用教练决策规则。
- `skill_catalog.json`：技能说明、基础冷却和资料来源。

本轮只移动文件，三个 JSON 的内容保持不变。读取路径与 `scripts/update_skill_catalog.py` 的写入路径已同步调整。

这些文件属于项目资源；运行生成的数据库、战报、采集资料继续放在 `data/`。
