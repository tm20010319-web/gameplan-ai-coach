# 四位常用英雄资料

用户指定的练习分路：伽罗、艾琳发育路，小乔中路，吕布对抗路。偏好不作为当前对局分路的视觉证据。

官方技能页面已抓取快照并写入技能目录和 SQLite：

| 英雄 | 来源 | 本次官网大招基础冷却（秒） |
|---|---|---|
| 伽罗 | https://pvp.qq.com/web201605/herodetail/508.shtml | 40/35/30 |
| 艾琳 | https://pvp.qq.com/web201605/herodetail/155.shtml | 20/17.5/15 |
| 小乔 | https://pvp.qq.com/web201605/herodetail/106.shtml | 42/35/28 |
| 吕布 | https://pvp.qq.com/web201605/herodetail/123.shtml | 50 |

官网未标明当前版本；以上不是实战剩余冷却，也不是可用性确认。每个英雄的被动、三个主动技能、描述、来源和抓取时间都保留。网页快照位于 data/knowledge_sources/official-ID/。

data/hero_coaching_profiles.json 保存分路介绍、条件性建议、连招候选、机制依据技能编号、误区和缺失资料。SQLite heroes.data.coaching_profile 和英雄查询接口返回这些字段。建议模型通过 mechanisms 的 K 编号接收资料，模型缓存也包含这些依据。

四位已补齐用户确认的默认出装、30枚五级铭文和闪现。打法已替换为有来源的官网提示及用户提供的第三方攻略，逐条注明出处和条件，标记 mixed_source_conditional。原机制推导草案留档，不再作为当前攻略。当前版本及实测效果仍待核验，详细覆盖情况见 knowledge-audit.md。

保留现有三块页面布局。此项不新增语音、不更换识别模型、不自动定期抓取，后续收到新资料再更新。
