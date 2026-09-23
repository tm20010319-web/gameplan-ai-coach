# GAMEPLAN 项目交接文档

更新时间：2026-09-12（Asia/Shanghai）

这是一份给新 Codex 会话使用的项目上下文。新会话开始时先阅读本文件，再阅读 `README.md`、`docs\requirements\电竞室AI助教_完整需求与实施方案.md`、`docs/hero-name-recognition.md` 和本文列出的关键代码。

## 项目目标

GAMEPLAN 是王者荣耀电竞室 AI 助教。当前重点是：

1. 用户把手机或游戏画面投屏到电脑。
2. 本地 Ollama/Qwen3-VL 识别选人或加载画面中的英雄。
3. 加载阶段确认双方阵容、本人英雄和分路。
4. 根据整套阵容生成个人打法建议，默认使用 DeepSeek 文本接口，失败时使用本地规则兜底。
5. 选人阶段识别敌方已选英雄和禁用英雄，按分路筛选候选英雄的机制参考。
6. 后续继续实现局中观察、技能释放记录、赛后战报等文档中的功能。

## 当前运行环境

- 工作目录：`D:\tm_work`
- Windows 本机，RTX 5070 Ti 16GB
- Ollama：`0.34.0`
- 本地视觉模型：`qwen3-vl:8b`
- 本地模型 GPU 已恢复，`ollama ps` 曾显示 `100% GPU`
- Ollama GPU 修复：将安装包 `cuda_v13` 下签名有效的 10 个 `*140*.dll` 复制到 Ollama 程序目录，未修改 Windows 系统 DLL。
- 监控服务常用端口：`8769`
- 当前工作台入口：`http://127.0.0.1:8769/monitor?view=bp`
- 加载阵容页面：`http://127.0.0.1:8769/monitor?view=loading`
- 局中观察页面：`http://127.0.0.1:8769/monitor?view=screen`
- 选人助手旧入口：`http://127.0.0.1:8769/bp`

启动服务：

```powershell
Set-Location D:\tm_work
.\start-monitor.ps1 -Port 8769 -NoWindow -NoAutoStart
```

如果端口已有当前服务，先访问健康接口，不要重复启动：

```powershell
Invoke-RestMethod http://127.0.0.1:8769/api/health
```

## 已完成能力

### Ollama GPU

此前 GPU 探测失败原因是 `llama-server.exe` 使用系统旧版 `MSVCP140.dll 14.36.32532.0` 崩溃，异常代码 `0xc0000005`。修复后模型识别从约 33 秒降到首次约 6.9 秒、热启动约 3.2 秒。日志和修复记录：

- `D:\tm_work\server.log`
- `D:\tm_work\work\ollama-local-runtime-fix.json`

### 加载阵容识别

关键文件：

- `gameplan/vision/hero_recognition.py`
- `gameplan/web/picture_analysis.py`
- `static/picture.js`
- `static/picture.html`
- `docs/hero-name-recognition.md`

识别流程使用 OCR 定位两排加载百分比和 VS，再裁剪十个名字槽位，交给 Qwen3-VL 读取官方英雄名。OCR 与模型冲突、重复或不可读时保留待确认，不靠阵容补猜。

已真实验证：清晰样本十个英雄及顺序正确；低清样本拒识；位置交换回归通过。一次样本不代表全英雄准确率。

连续监控逻辑已加入：选定固定区域后持续采样，连续两帧槽位阵容一致时锁定并暂停扫描，下一局重新开始。实时识别参数仍需要真实投屏验证。

### 外部阵容建议

关键文件：

- `gameplan/tactics/personal_plan.py`
- `static/picture.js`
- `tests/test_external_personal_plan.py`

个人建议流程：已确认的双方阵容 + 本人英雄 + 分路发送给外部文本模型。默认 DeepSeek，也支持 Qwen 和豆包的 OpenAI-compatible 接口配置：

```env
COACH_PROVIDER=deepseek
COACH_API_KEY=
COACH_BASE_URL=
COACH_MODEL=
DASHSCOPE_API_KEY=
ARK_API_KEY=
```

DeepSeek 请求已关闭 thinking，真实测试曾约 18 秒返回完整个性化方案。模型输出要经过阵营范围、依据编号、结构和违规内容校验，失败时显示本地规则方案。

当前已有依据编号和“建议依据”展开区域，但英雄机制资料大多没有完整的 `approved`、版本、来源、核验日期，因而不能被视为已审核事实。模型仍可能生成不应有的技能细节，已有拦截规则但需要继续收紧。

### 选人助手

关键文件：

- `gameplan/tactics/bp_assistant.py`
- `static/bp.html`
- `static/bp.js`
- `static/bp.css`
- `tests/test_bp_assistant.py`

接口：

- `GET /api/bp-assistant/heroes`
- `POST /api/bp-assistant/observe`
- `POST /api/bp-assistant/recommend`

页面会读取屏幕，调用 Ollama 识别选人界面，识别结果需要用户确认己方位置和分路。候选会过滤已选英雄、敌方英雄、禁用英雄、不可用英雄和分路。锁定英雄后停止换人推荐。

重要边界：加载图和选人图必须区分；`gameplan/tactics/bp_assistant.py` 已增加加载布局拦截。仅凭屏幕不能可靠判断电脑前用户操控哪一个槽位，因此需要用户确认己方位置，锁定英雄后才停止建议。

当前关系来自 `gameplan/tactics/coach.py` 的 `COUNTERS`，属于机制草案，页面明确显示“待审核/不代表同路克制”。

### 统一工作台

关键文件：

- `gameplan/web/monitor_app.py`
- `static/workspace.html`
- `static/workspace.js`
- `static/workspace.css`

`/monitor`、`/bp` 默认返回统一工作台，内部用单个 iframe 切换：选人参考、加载阵容与打法、局中观察。切换时触发旧 iframe 的 `pagehide`，尝试停止旧采样，避免多个页面同时占用 Ollama。嵌入旧页面使用 `embedded=true`。

工作台导航测试脚本：`work/test_workspace.cjs`。

## 测试

已通过：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_hero_recognition.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*personal_plan.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_bp_assistant.py -v
node work/test_workspace.cjs
node --check static/picture.js
node --check static/bp.js
node --check static/workspace.js
```

`tests/test_monitor_app.py` 目前有两项旧断言失败：一个期待旧的 `local_fallback` 来源名称，另一个是旧观察测试期待过滤掉未知英雄。统一入口和本次选人功能没有依赖这两项旧断言；新会话应优先修正测试与当前实现的一致性，不要忽略失败。

## 当前最重要的未完成项

1. 用真实王者选人截图验证 `POST /api/bp-assistant/observe`，确认不同选人布局、禁用槽位、预选槽位和英雄池不会误识别。
2. 修复 `tests/test_monitor_app.py` 的两项旧断言，并补统一工作台的 HTTP/浏览器验收。
3. 将选人识别出的敌方英雄、禁用名单和用户确认的己方分路传递到加载页面，减少重复填写。
4. 补齐首批英雄对位资料的审核字段：版本、来源、核验日期、审核状态、适用分路、失效条件。
5. 不要让外部模型自由补技能编号、装备数值、红开蓝开、实际对线对象或实时事件；缺少证据时只给条件性行动建议。
6. 按完整实施方案继续实现局中视觉状态、事件触发、敌方技能记录与倒计时；这些均未完成验收。
7. 真实游戏投屏验收：记录采集频率、Ollama 推理时间、显存、游戏帧率影响、换局重置和窗口移动。

## 新会话开始时的建议指令

把以下内容直接发给新会话：

> 请先阅读 `D:\tm_work\docs\archive\2026-09-12\PROJECT_HANDOFF_2026-09-12.md`，再阅读其中列出的关键文件。继续实现当前项目，不要重做已经完成的 Ollama GPU、加载识别和统一工作台。先修复 `tests/test_monitor_app.py` 的两项旧断言，再用真实或可复现样本验收选人自动识别；保持用户确认己方阵营/分路和锁定后停止推荐的边界。

