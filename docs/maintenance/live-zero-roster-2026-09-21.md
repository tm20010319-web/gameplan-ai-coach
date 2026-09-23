# 2026-09-21 实测敌方名单一直为 0/5

本次明确定位并修复的是名单建立失败。不是五个不同英雄被认成同一英雄，而是战绩表同一行的英雄标签与同名昵称被当成两个英雄。名单恢复不代表敌方闪现、大招实战识别已通过。

## 真实证据

- 已保存 `work/live-zero-audit-20260921/`，包含当时尚未轮换删除的 32 批档案，覆盖本机 09:54:55–09:55:34。30 批识别为对局，敌方名单为空，技能检测状态为 `waiting_identity`；一批战绩请求报 502，一批阶段未知。这些档案不能代表整场测试的全部画面。
- 报错档案为 `cast-1789955697052115800-bb8b169b.zip`，09:54:57，游戏时间 00:45。真实 OCR 得到我方 7 项、敌方 8 项。敌方为元歌×2、露娜×2、貂蝉×2、朵莉亚×2；上官婉儿标签与昵称合并为“上官婉儿上官婉儿”而被漏掉。
- `ScreenObservation` 在去重之前执行最多 5 项的名单校验，抛出 `ValidationError`，接口返回 502“模型返回的画面状态无法校验，本帧已丢弃。”实际失败来自本地 OCR 名单，不是这次调用的技能模型。整帧未写入追踪器，随后持续等待身份。
- 用户后补截图游戏时间 04:47，敌方顺序为貂蝉、上官婉儿、露娜、朵莉亚、元歌；灰色英雄名称和红色昵称仍各显示一次。旧代码对这张图同样输出敌方 8 项。操控英雄伽罗属于我方。

## 修改

`gameplan/skills/combat_evidence.py`：

1. 在已经验证的战绩表内，按阵营、行高度分组，每行仅选择靠左的英雄标签；旁边即使是另一个有效英雄名，也不能额外成为名单成员。
2. 对一个 OCR 框内严格重复两遍的已知英雄名，仅在标签半边灰度增强复读得到同一个高置信度完整英雄名时恢复该标签。任意昵称中的英雄名子串不用于补名单。
3. 同一阵营输出唯一英雄名，避免重复标签把有效战绩帧撑出数据长度限制。

没有放宽敌我归属，没有把战绩头像、技能图标或数字作为技能施放事件，没有修改技能模型和计时策略。

## 复现与验证

修复前：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scoreboard_same_name.py -q --tb=short
```

最初 3 项全部失败，其中真实接口回归返回 502，与现场相同。修复后该组通过，后补截图及昵称子串边界检查加入后共 5 项通过。

两张保存的真实图片重新执行 OCR，没有注入 OCR 或模型回答进行识别结果对照：

| 画面 | 旧代码敌方结果 | 修复后敌方结果 |
| --- | --- | --- |
| 00:45 现场档案 | 8 项，缺上官婉儿 | 上官婉儿、元歌、露娜、貂蝉、朵莉亚 |
| 04:47 用户截图 | 8 项，缺上官婉儿 | 貂蝉、上官婉儿、露娜、朵莉亚、元歌 |

00:45 图中我方廉颇标签仍未读全，修复后我方为 4 人；04:47 图中我方 5 人均可读。两张图中的伽罗都只在我方名单。原图、原始 OCR、新 OCR、来源与哈希位于 `tests/fixtures/combat/scoreboard-same-name/`，对照输出位于 `work/live-zero-audit-20260921/fresh-ocr-comparison.json`。

相关组合回归（包含当时的 3 项专项检查）217 项通过：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_scoreboard_same_name.py tests\test_live_video_hud.py tests\test_skill_diagnostics.py tests\test_scoreboard_skill_history.py tests\test_unbound_enemy_casts.py tests\test_effect_actors.py tests\test_keyframe_casts.py tests\test_realtime_evidence.py tests\test_enemy_casts.py tests\test_monitor_app.py tests\test_health_nameplates.py tests\test_health_levels.py tests\test_loading_summoners.py -q --tb=short
```

后续专项 5 项也通过，计数与上述组合有重叠。本次没有修改前端。

重启 8767 服务后，运行下列脚本，直接把原图交给部署后的 HTTP 接口，不替换 OCR、模型或识别结果：

```powershell
.\.venv\Scripts\python.exe work\live-zero-audit-20260921\verify_live.py
```

两张战绩图均返回 HTTP 200 和完整的 5 人敌方名单；随后同一独立验证会话提交真实战斗 HUD 图，名单仍保留。战绩和后续单帧均未产生技能计时。结果保存于 `work/live-zero-audit-20260921/deployed-http-replay.json`；验证使用独立会话并在结束后清空，没有向用户当前会话灌入旧画面的名单或技能事件。

## 当前限制

部署前最新实际采集图（10:04:05）显示 AirDroid“设备断开连接”，保存为 `work/live-zero-audit-20260921/pre-deploy-latest.png`。因此本次验证了真实图片的名单识别、HTTP 接口和名单保留，未完成投屏恢复后的敌方闪现或大招实机验收。此前真实录像中目标定位和技能模型漏报的未解决项仍见 `live-video-enemy-cast-audit-2026-09-20.md`。
