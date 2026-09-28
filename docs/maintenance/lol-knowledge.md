# 英雄联盟端游知识库

2026-09-28 新增，独立于王者荣耀英雄目录、识别和技能计时。

## 使用

启动现有项目后访问 `/knowledge/lol`。独立服务默认链接为 http://127.0.0.1:8767/knowledge/lol ，工作台服务也提供相同路径。统一工作台页头、技能面板使用说明、原知识库和主工作台知识库页均提供入口。

支持英雄名、称号、英文名、腾讯官方别名/拼音关键词、技能名与描述、基础冷却、参考装备名、打法文本搜索；多个词同时匹配。可按英雄职业筛选。列表查询只返回摘要，选择英雄后加载详情；切换与搜索会丢弃旧响应。

## 本次快照

- Riot Data Dragon `16.19.1`，中文 `zh_CN`，173 位英雄。
- 每个英雄包含被动及 Q/W/E/R，共 865 条主技能资料；不声称完整覆盖变身、组合或武器切换子技能。
- 251 件可购买的召唤师峡谷装备，排除其他地图专属、非商店及英雄专属装备。页面展示各英雄参考池中的装备，未提供独立全装备浏览页。
- 官方使用/对抗技巧有 26 位英雄至少缺一类。页面如实显示缺失，同时提供明确标为项目整理的类型打法。
- 出装是按英雄机制与类型整理的可选装备池，包含说明和装备详情，不是六件套顺序、分路胜率榜或官方推荐。可选项不要求同时购买。
- 基础冷却保留原始等级数组；0 字段显示待核对，不解释成无冷却。攻速缩减、充能、变身等机制另有提示。未实现实战计时。
- 官方技巧并非全部随补丁更新，均标为版本待复核；已剔除剑魔大招复活的明显过期技巧。资料日期不等于国服版本上线确认。

## 文件与接口

- `resources/knowledge/lol_catalog.json`：本地数据快照，日常浏览无需外部请求。
- `scripts/update_lol_knowledge.py`：官方数据下载、清洗、装备池规则、完整性校验和原子替换。
- `gameplan/knowledge/lol_knowledge.py`：按文件修改时间更新缓存，查询和英雄详情。
- `gameplan/web/lol_routes.py`：由工作台与独立监控共同挂载。
- `static/lol-knowledge.html`、`.css`、`.js`：独立查询页面。
- `GET /api/knowledge/lol/heroes?q=安妮&role=法师`：摘要查询。
- `GET /api/knowledge/lol/heroes/Annie`：详情及来源。

官方来源：

- 版本： https://ddragon.leagueoflegends.com/api/versions.json
- 英雄目录： https://ddragon.leagueoflegends.com/cdn/16.19.1/data/zh_CN/champion.json
- 英雄详情示例： https://ddragon.leagueoflegends.com/cdn/16.19.1/data/zh_CN/champion/Annie.json
- 装备： https://ddragon.leagueoflegends.com/cdn/16.19.1/data/zh_CN/item.json
- 别名： https://game.gtimg.cn/images/lol/act/img/js/heroList/hero_list.js

## 更新

在项目根目录执行：

```powershell
.venv/Scripts/python.exe scripts/update_lol_knowledge.py
```

默认采用 Riot 最新公布版本；可用 `--version 16.19.1` 固定版本。需要系统 curl 和联网，保持 TLS 证书校验。不可变版本资料缓存于 `work/lol-sources/<version>/`，别名每次重新获取且记录独立版本。全部英雄下载和校验成功后才替换快照；失败不覆盖现有知识库。没有配置定时更新。

升级后检查新增英雄分类、装备参考池、特殊冷却和过期技巧；运行下述测试。当前快照测试固定核对 173 位，英雄新增后应同步这一版本验收数字。装备停售会从参考池剔除，并在 `omitted_item_ids` 中记录；维护时需检查该字段并补充合适替代项。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_lol_knowledge.py tests/test_full_knowledge.py tests/test_skill_knowledge.py -q
node --test tests/lol_knowledge_ui.cjs tests/knowledge_ui.cjs
node --check static/lol-knowledge.js
```

已验证两个服务入口、名称/别名/技能/装备查询、职业筛选、未知英雄、游戏目录隔离、数据更新缓存、加载失败重试、旧请求不能覆盖新选择、文本安全渲染；浏览器实测中文搜索和窄屏页面。未逐英雄人工验收数值与当前对局最优出装。
