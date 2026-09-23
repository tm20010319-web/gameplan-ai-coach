# GAMEPLAN Bug 修复清单

更新时间：2026-09-12

## 当前测试概况

运行命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

结果：76 项测试中，9 项失败、3 项错误。

## 修复顺序

请严格按下面顺序逐项修复。每完成一项，重新运行对应测试。

### 1. 恢复 Advisor 外部模型调用（最高优先级）

**相关文件**

- `gameplan/ai/advisor.py`
- `tests/test_advisor.py`

**现象**

多个测试显示外部请求没有发出，结果直接变成 `local_rules` 或 `ok`：

- `test_external_gets_only_game_facts_then_same_context_is_cached`
- `test_missing_key_returns_honest_local_fallback_without_network`
- `test_manual_sample_override_is_explicit_and_wrong_match_is_rejected`
- `test_stale_live_frame_drops_dynamic_facts_and_becomes_lineup_advice`

**检查重点**

- `provider_config()` 是否正确读取 `DEEPSEEK_API_KEY`。
- 测试 patch 的 `post_json` 是否与实际调用路径一致。
- 没有 API key 时必须返回 `status=not_configured`，且不得联网。
- 有 API key 时必须真正调用外部接口。

**验收命令**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_advisor -v
```

### 2. 修复 Advisor 状态机

**相关测试**

- `test_live_response_is_discarded_if_it_arrives_after_freshness_window`
- `test_perspective_change_obeys_request_rate_limit`
- `test_untrusted_or_invented_external_result_fails_closed`

**预期行为**

- 响应超过 freshness window：返回 `stale`。
- 请求过于频繁：返回 `throttled`。
- 外部模型输出无法通过校验：返回 `unavailable`，不能发布结果。

### 3. 修复选人助手的中文乱码和分路枚举

**相关文件**

- `gameplan/tactics/bp_assistant.py`
- `static/bp.html`
- `static/bp.js`
- `static/bp.css`

**现象**

`lane` 的 `Literal` 值、错误提示和页面文案出现乱码，可能导致合法中文分路无法通过 Pydantic 校验。

**预期分路**

```text
对抗路、发育路、中路、打野、游走
```

修复后确认所有 Python 文件保存为 UTF-8，并检查 HTTP 响应头和 HTML 的 charset。

### 4. 修复选人候选过滤

**相关文件**

- `gameplan/tactics/bp_assistant.py`
- `tests/test_bp_assistant.py`

**失败测试**

```text
test_excludes_banned_selected_and_unavailable
```

候选英雄不得出现在以下任一集合中：

- 己方已选
- 敌方已选
- 禁用英雄
- 不可用英雄
- 不在英雄目录中的英雄

锁定英雄后必须返回 `status=locked`，停止换人推荐。

### 5. 过滤观察结果中的未知英雄

**相关文件**

- `gameplan/web/monitor_app.py`
- `gameplan/monitoring/monitor_runtime.py`

**失败测试**

```text
test_observation_uses_shared_pipeline_and_keeps_only_valid_game_fields
```

`ally_roster`、`enemy_roster` 中只允许保留英雄目录里的合法名称；未知名称应丢弃或标记为待确认，不能直接进入打法建议。

### 6. 收紧图片英雄识别结果校验

**相关文件**

- `gameplan/web/picture_analysis.py`
- `gameplan/vision/hero_recognition.py`

**失败测试**

```text
test_invalid_or_truncated_hero_output_is_not_presented
```

模型输出以下情况时必须拒绝：

- 截断名称
- 不在 `KNOWN_HEROES` 的名称
- 空字符串或无效字符串
- 重复英雄
- 与识别阶段不匹配的英雄

接口应返回明确的 `HTTPException` 或可重试状态，不能把无效结果展示给用户。

### 7. 修复识别请求 schema 约束

**相关文件**

- `gameplan/ai/advisor.py`
- `gameplan/vision/hero_recognition.py`

**现象**

`test_compact_identity_request_follows_arbitrary_catalog_heroes_without_dynamic_numbers` 失败，schema 中缺少预期的英雄 `enum` 约束。

**预期**

将当前英雄目录动态写入 schema 的 `enum`，避免视觉模型自由编造英雄名，同时继续支持目录中新增英雄。

### 8. 更新旧测试和交接文档

仅在代码行为确认正确后，更新：

- `tests/test_monitor_app.py` 中旧的 `local_fallback` 断言
- 交接文档中“当前失败测试”列表

不要先修改测试来掩盖实现问题。

## 全量验收

所有单项修复完成后运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check static/picture.js
node --check static/bp.js
node --check static/workspace.js
node work/test_workspace.cjs
```

目标：所有测试通过，且统一工作台、加载识别、选人识别和外部建议的边界行为与交接文档一致。
