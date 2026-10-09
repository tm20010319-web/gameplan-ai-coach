# GAMEPLAN · 电竞室 AI 助教

无需摄像头，通过电脑屏幕、投屏窗口、截图或录屏提供游戏知识问答、选人和阵容建议、敌方技能提醒、文字计时、个人复盘与玩家长期档案。当前包含王者荣耀和英雄联盟端游资料；两款游戏的识别、战术和计时支持范围不同。项目不操作游戏。

本文于 **2026-10-08** 根据当前工作目录的代码、数据、启动脚本、自动化检查及已有验收记录核对更新。“已实现”表示功能链路存在；自动化检查和局部录像验证均不等于全部真实对局已验收。

## 功能与完成情况

| 功能 | 当前实现 | 已有验证与限制 |
| --- | --- | --- |
| 王者荣耀知识库 | 132 个英雄条目、537 条技能；另有 60 英雄教练机制库和 10 条通用决策原则 | 本地目录和检索有自动化检查；数据是项目保存的快照，特殊机制、来源冲突和版本变化仍需核对 |
| 英雄联盟知识库 | `16.19.1` 中文快照，173 位英雄，被动及 Q/W/E/R、参考装备池和打法搜索 | 检索、筛选和页面有检查；不是实时版本、胜率榜或逐英雄验收过的最优出装 |
| 小象助手聊天 | 游戏/英雄判断、同名确认、连续追问、本地资料引用、缺失内容的外部模型补充 | 已有接口、持久化和页面检查；复杂问题的语义相关性及模型补充仍需核对 |
| 聊天技能计时 | 王者荣耀已收录的主动技能及闪现；英雄联盟大招/R 和闪现；支持多人报告、歧义确认、取消与恢复 | 文字报告触发基础冷却估算，不是视觉识别到真实施放；不计装备、急速、刷新等实际修正 |
| 监控与截图分析 | Windows 屏幕/窗口采集、选区图片分析、视频分析、暂停/停止、来源切换 | 有接口、样本和页面检查；具体速度、遮挡与设备兼容性取决于采集方式和硬件 |
| 王者荣耀敌方自动技能提醒 | 自动检测大招和召唤师技能特效，区分确认记录与疑似候选，生成基础冷却值/范围或未知状态 | 已验证若干真实录像片段；全英雄、全皮肤准确率、真实闪现正例覆盖和持续投屏时延仍待验收 |
| 面板文字计时 | “妲己放大了”“小明没大”等文字指令，别名确认、多英雄登记、倒计时、移除和清空 | 有后端及页面检查；与聊天计时规则不同，采用 3 秒报时延迟补偿 |
| 选人、加载与局中建议 | 王者荣耀选人/阵容机制、分路和个人打法；按可见状态生成建议或本地备用结果 | 有规则、接口和样本检查；英雄身份与视觉草稿需要核对，不能外推为全场识别准确率 |
| 自动个人复盘 | 监控中区分两款游戏，记录个人事实，连续确认结束后生成最多两条“问题＋下局做法”；历史与关键画面可查看 | 生命周期、恢复、输出约束有检查；两款游戏完整真实对局的识别效果仍待实局验证 |
| 玩家长期档案 | 用户归属、聊天/复盘来源、画像历史、失败重试、玩家/教练/管理员访问控制 | 存储与授权逻辑已实现；当前本地开发身份不等于正式登录，真实账号体系尚待接入 |
| Redis 存储 | 知识、旧工作台、计时、聊天、复盘、档案和迁移工具已有 Redis 实现 | 隔离的 FakeRedis 检查已通过；本次核对的本地 `.env` 未启用 Redis，真实迁移和重启恢复仍需验收 |
| 旧手动工作台与战报 | 手动阵容、局面示例、动作记录、PNG/二维码/H5 战报及大屏同步 | SQLite 旧模式保留；PostgreSQL 模式下部分旧工作台记录接口返回 503，不能按完整旧演示流程使用 |
| 语音录入与播报 | 存在文字命令解析器及接受已转写文字的兼容接口 | 未接入麦克风、ASR 转写或 TTS 播报；模块名中的 `voice` 不表示语音端到端功能已经完成 |

项目结构见 [分类导航](docs/README.md)，集中需求见 [完整需求与实施方案](docs/requirements/电竞室AI助教_完整需求与实施方案.md)。历史需求和交接记录需结合当前代码及后续维护记录阅读。

## 启动与页面入口

### 首次从 GitHub 拉取

仓库地址：[gameplan-ai-coach](https://github.com/tm20010319-web/gameplan-ai-coach)。在 Windows 上执行：

```powershell
git clone https://github.com/tm20010319-web/gameplan-ai-coach.git
cd gameplan-ai-coach
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\start-monitor.ps1 -NoAutoStart
```

需要 Python 3.11+。只在首次拉取、没有 `.env` 时复制示例文件；更新已有工作目录使用 `git pull --ff-only`，保留自己的环境配置。

仓库包含后端、前端、知识数据、测试样本、数据库迁移和启动脚本。本机 `.env`、密钥、数据库、玩家记录、模型权重、虚拟环境及运行缓存不随代码分发。默认示例未启用 PostgreSQL/Redis，基础本地知识聊天和文字计时可使用 SQLite 模式；视觉分析需另行配置 Ollama/视觉模型，模型建议需填写自己的 API 配置。玩家长期档案与 Root 用户绑定需按下文准备 PostgreSQL 或 Redis。

本仓库是独立的 `tm_work` 项目，包含 Gameplan 侧的 Root 接口；Sale Root 的适配器及测试启动器位于独立 Sale 项目，不包含在此仓库中。仅拉取本仓库可启动 Gameplan，不能直接启动 Sale Root。接入合同见 [Root 接入说明](docs/maintenance/root-king-integration.md)。

### 已有本地项目安装

需要 Python 3.11+。在项目根目录首次安装：

```powershell
cd D:\tm_work
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

按需要从 `.env.example` 创建 `.env` 并填写配置，不覆盖已有配置。知识查询、手动规则和文字计时不需要外部 API Key；视觉识别需要可用的视觉模型，外部建议、模型补充和自动复盘分析需要对应模型配置。

### 独立监控服务（推荐的日常入口）

```powershell
# 打开界面，先不自动开始采集
.\start-monitor.ps1 -NoAutoStart

# 使用页面中保存的自动开始设置
.\start-monitor.ps1

# 只启动后台，不打开窗口
.\start-monitor.ps1 -NoWindow
```

默认后台为 `monitor_app:app`，仅监听 `127.0.0.1:8767`。启动器隐藏启动后台，优先使用 Edge/Chrome 应用窗口，重复启动会复用健康服务。端口冲突时可使用 `-Port 8768`；不会终止其他占用端口的程序。后台日志在 `work/monitor-logs/`，关闭页面后服务保持待用。

| 页面 | 默认地址 |
| --- | --- |
| 统一对局工作台 | [只打开监控界面](http://127.0.0.1:8767/monitor?monitor=off) |
| 小象助手聊天 | [聊天](http://127.0.0.1:8767/chat) |
| 玩家长期档案 | [档案](http://127.0.0.1:8767/profile) |
| 英雄联盟资料 | [联盟知识库](http://127.0.0.1:8767/knowledge/lol) |
| 独立技能面板 | [面板](http://127.0.0.1:8767/monitor/panel?panel=1&monitor=off) |
| 选区图片页面 | [图片分析](http://127.0.0.1:8767/monitor?mode=picture&embedded=true&monitor=off) |
| 接口文档 | [Swagger](http://127.0.0.1:8767/docs) |

`/monitor` 当前外层页面加载整屏/窗口监控工作台，不再将固定图片分析作为该外层页面的默认视图。图片分析可使用上表独立入口。独立服务也已挂载聊天、复盘和账号/档案接口，不应再描述为“不加载复盘”。

设置 `GAMEPLAN_LOCAL_POSTGRES=1` 或 `GAMEPLAN_LOCAL_REDIS=1` 时，监控启动脚本会调用相应的本地数据库启动脚本。复制项目代码不等于安装或迁移了数据库，准备方法见下文存储说明。

### 旧手动工作台

```powershell
.\start.ps1
# 改端口
.\start.ps1 -Port 8766
```

默认入口为 [手动工作台](http://127.0.0.1:8765/)，前台终端关闭或按 Ctrl+C 后停止。它保留 `app:app` 入口；默认仅监听本机。可信局域网演示可使用 `-Lan`，手机与电脑需同网并允许专用网络防火墙访问。二维码分享地址可通过 `PUBLIC_BASE_URL` 配置，换网或改端口后须重新核对。

未配置新数据库时，可演示“选阵容 → 结构化局面提醒 → 登记动作与结果 → 战报/二维码 → 同步大屏”。按钮示例不是实时对局识别。配置 PostgreSQL 后，旧工作台写入接口受到限制，日常使用应进入独立监控、聊天和档案页面。

直接启动服务时需加载已有 `.env`，例如：

```powershell
.\.venv\Scripts\python.exe -m uvicorn monitor_app:app --host 127.0.0.1 --port 8767 --env-file .env
```

## 监控、图片与视频

- 屏幕和窗口来源由本机采集接口提供。首次选择游戏/投屏窗口或屏幕，之后可沿用保存的来源；窗口最小化时等待恢复。独立窗口采集与整屏采集的遮挡行为不同，以界面的采集方式提示为准。
- 实时采集与模型处理分开，采集目标间隔为 100 毫秒，模型同一时间处理一批输入，不用旧截图堆积替代当前画面。这个目标不表示识别能达到每秒十次。
- 观察内容可选择“敌方大招与召唤师技能”“完整状态与自身技能”或“英雄优先”，当前页面默认选择技能观察。看不清的英雄、等级和自身技能读数保留未知。
- 视频默认“每隔 10 帧取 1 帧”，另有智能筛选、60 帧/秒完整识别、跟随播放位置及连续片段分析模式。目标采样率不等于实际解码/推理速度；跟随模式可能跳过片段，完整分析可能滞后。
- 固定图片支持框选区域、上传图片、阵容/英雄核对及重新分析。换图撤下旧建议，晚到结果不能覆盖新来源。只有选定区域或主动提交的图片进入相应分析链路。
- 固定图片链路可向外部视觉模型发送图片；部分结果使用最多 32 条、30 分钟的内存缓存，缓存的是分析结果。不要把所有实时输入都理解为必定命中该缓存。
- 原工作台上传/裁切、固定图片、自动监控与复盘采样的保存方式不同：旧上传会保存收到的文件和处理图，复盘会保存关键采样；不能统一声称“所有截图不落盘”。

原生桌面采集依赖 Windows 与可用的用户桌面。浏览器屏幕共享需要适用的 localhost/HTTPS 环境及用户选定来源；项目已有本机屏幕接口不能直接采集远程玩家电脑。手机投屏需要另行准备 scrcpy、AirDroid Cast 或其他投屏工具，未对全部设备和传输时延作验收。

## 敌方技能自动识别与冷却

当前已经实现自动链路，主要代码位于 `gameplan/monitoring/monitor_runtime.py` 和 `gameplan/skills/`，不再只是手动登记的预研接口。自动视觉链路针对**王者荣耀敌方大招与召唤师技能**；英雄联盟聊天计时不能视为联盟敌方施放已支持自动识别。

1. 从加载/战绩等画面获得阵容及身份信息，结合敌方血条、昵称、等级和战场特效产生候选。
2. 自动检测结果经过归属、技能、证据和重复事件校验；可归属的明确特效允许单帧触发，时序起手证据用于更明确的时间范围。
3. 确认记录与疑似候选分开保存和展示。有具体英雄及特效依据的疑似大招也可开始**疑似基础冷却范围**，不写成已确认施放。
4. 计时以源画面时间或可见起手区间为依据；首次看到持续特效的时间可能晚于真实释放。重复观察同一次疑似事件不反复重置计时。
5. 只识别到携带图标、达到四级或技能按钮变化不会直接当作施放。资料缺失、动态冷却或必要条件不足时显示未知；朵莉亚参与识别，但不能凭其大招基础值编造固定倒计时。

当前策略包含资料表的 132 个英雄，代码中的处理时延目标为 **20 秒**。这是覆盖范围和目标，不是 132 个英雄逐个通过实战验收或全部提示保证在 20 秒内出现。剩余冷却仅作基础值估算，不完整计算装备减冷却、刷新、充能次数和条件返还；倒计时到期不等于直接确认游戏内技能已可用。自动记录当前由进程内追踪器维护，也不能与可持久化的文字计时混为一谈。

已有真实素材验证及后续修复记录：

- [2026-09-20 真实录像漏报排查](docs/maintenance/live-video-enemy-cast-audit-2026-09-20.md)：记录墨子、吕布等漏报与当时未通过的验收项。
- [2026-09-21 证据校验改进](docs/maintenance/ultimate-accuracy-2026-09-21.md)：墨子、孙策片段及反例验证，仍有目标定位漏报。
- [2026-09-22 漏提示修复](docs/maintenance/ultimate-recall-fix-2026-09-22.md)：小乔疑似事件通过真实接口进入递减计时；记录的最早提交画面到返回约 15.67 秒，未包含完整手机投屏传输时延。

因此，这项功能应描述为“**自动识别与估算计时已实现，部分真实片段已验证，全面实战验收待完成**”。完整需求还包括更全面的时间校准、纠错和真实画面验收，见 [需求规格](docs/requirements/敌方技能释放记录与冷却提醒_需求规格.md)。

## 聊天与文字计时

[聊天助手说明](docs/maintenance/knowledge-chat.md)包含检索、模型补充和 API 合同。聊天优先使用本地知识，模糊英雄名先确认，支持连续追问和跨游戏切换。模型补充显示独立来源，未经联网核验，不自动回写知识库。

聊天中可报告“妲己放大了”“小明和诸葛亮都没大招”“寒冰 R 用了”“奶妈、牛头和阿狸没大没闪”。王者技能按已收录资料计时；联盟仅支持大招与闪现，不为 Q/W/E 启动计时。默认采用基础冷却，不推算实际技能等级、装备或急速。多人报告、取消、待确认及请求重试有各自处理规则。

独立面板的文字计时见 [文字计时闭环](docs/maintenance/text-cooldown-loop-2026-09-23.md)：大招和闪现、别名/多人报告、歧义选择、未知冷却、刷新恢复、最后 5 秒及到期文字提示。面板计时会扣除 3 秒报时延迟补偿；聊天计时从接收报告或确认时起算，不沿用这项补偿。

`/voice-command` 接受已转写的文字并要求唤醒词，它不负责录音或语音识别。目前没有麦克风采集、ASR 或 TTS，也没有页面关闭期间的系统报时通知。

## 自动复盘与玩家档案

自动复盘仅对实时监控中的个人对局建立记录，连续确认游戏、操控英雄及结束画面后进入分析。上传视频或单张图片不自动登记为玩家本人新的一局。事实不足时显示记录不足；结算不明确时保留“未确认结束”，不编造胜负。每条建议关联本局事实，外部分析失败可重试。详见 [自动个人复盘](docs/maintenance/automatic-match-review.md)。

配置新数据库后，玩家档案整合用户、完整聊天、个人复盘、画像来源和版本。局后任务按玩家顺序更新档案，保留明确自述、待验证和多局观察的区别；重试、租约与任务恢复已有实现。完整聊天存储不等于前端一次加载全部历史：新库初始返回最近 100 轮并支持历史分页，旧 SQLite 聊天模式保留最近 20 轮。详见 [玩家长期档案](docs/maintenance/player-profiles.md)。

页面正式身份接入点为服务端 `app.state.resolve_user(request)`，需验证登录后返回已映射的用户 UUID。客户端传入用户编号不能直接获得权限。本地页面仍使用开发账号；另已实现 Sale Root 的服务间身份绑定与 KING 路由，Root 登录用户通过内部接口映射为独立玩家。该绑定不等于 Gameplan 浏览器页面已完成单点登录。

## 存储模式与本地状态

运行时选择见 `gameplan/persistence/database.py`。本文核对的是本地文件配置，未检查运行中进程的实际环境或数据库健康情况。

| 配置 | 使用方式 |
| --- | --- |
| 未配置 `GAMEPLAN_DATABASE_URL` 和 `GAMEPLAN_REDIS_URL` | 旧 SQLite 模式：工作台、聊天、复盘等分别存储；不具备正式多用户登录体系 |
| 配置 `GAMEPLAN_DATABASE_URL`，未配置 Redis | PostgreSQL 用户、聊天、计时、复盘和档案；账号归属由服务端身份上下文确定；部分旧工作台记录接口禁用 |
| 配置 `GAMEPLAN_REDIS_URL` | 对应业务存储优先使用 Redis；连接失败不会自动回退旧库；资产文件仍需单独保存 |

**2026-10-08 本地 `.env` 核对结果：** 已配置 PostgreSQL、`GAMEPLAN_LOCAL_POSTGRES=1` 和开发用户；未配置 `GAMEPLAN_REDIS_URL`。因此不能写成“Redis 正式迁移已完成”，也不能将旧 SQLite 演示模式描述成当前所有功能的运行方式。具体连接串、身份编号和密钥不在 README 中公开。

Redis 改造与迁移准备见 [Redis 迁移说明](docs/maintenance/redis-migration.md)。已有快照和准备产物；正式导入前需重新获取数据，验证真实 Redis、持久化、服务与系统重启恢复，不能拿 FakeRedis 测试替代这些步骤。不要为了运行旧演示随意删除现有数据库配置或覆盖正式数据。

默认资产在项目 `data/` 下，旧模式数据库包括 `coach.sqlite3`、`chat.sqlite3`、`reviews.sqlite3`；旧上传图和战报分别位于 `frames/`、`reports/`。`COACH_DATA_DIR` 可调整旧模式数据目录；新库资产位置由 `GAMEPLAN_ASSET_DIR` 等配置决定。数据库备份不包含全部资产文件，迁移需同时核对图片和海报。历史数据和图片没有统一自动清理策略。

## 模型配置

按链路分别设置，模型名称和供应商可用性以部署配置及实际请求结果为准。README 不保证某台电脑的模型已安装、GPU 已启用或接口当前可用。

| 配置 | 用途 |
| --- | --- |
| `OLLAMA_URL`、`OLLAMA_VISION_MODEL` | 本机游戏画面识别；页面可选择视觉模型 |
| `OLLAMA_CHAT_MODEL` | 聊天本地语义检索与资料选择；代码缺省为 `qwen3-vl:4b-instruct`，本地地址受回环检查约束 |
| `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` | DeepSeek 建议、复盘和档案等相关文本链路 |
| `DEEPSEEK_VISION_MODEL` | 固定图片链路的外部视觉模型配置；请求会包含用户选定图片 |
| `COACH_PROVIDER`、`COACH_BASE_URL`、`COACH_MODEL`、`COACH_API_KEY` | 个人方案/聊天补充等链路的供应商覆盖配置，支持 DeepSeek、Qwen、Doubao |
| `DASHSCOPE_API_KEY`、`ARK_API_KEY` | 相应供应商密钥，未使用通用覆盖密钥时由对应链路读取 |

聊天的 Doubao 补充已有 `/responses` 适配；其他链路各有自己的调用协议，不应仅改供应商名称就假定全部功能已兼容。聊天语义模型故障不等于资料缺失；外部失败应保留本地依据或显示失败/备用状态。修改 `.env` 后，逐请求读取配置的功能可直接重试；依赖进程环境的设置需重启相应服务。

## 模块与知识维护

| 目录 | 职责 |
| --- | --- |
| `gameplan/web/` | FastAPI 入口，监控、图片、聊天、复盘、计时、知识及账号接口 |
| `gameplan/monitoring/` | 屏幕/窗口采集、观察编排、复盘运行时、原生面板和诊断 |
| `gameplan/vision/` | 英雄、昵称、血条、等级、加载/选人和召唤师图标识别 |
| `gameplan/skills/` | 自动施放证据、目标追踪、疑似候选、冷却估算及动作模型实验 |
| `gameplan/voice/` | 文字技能命令解析、别名确认与报时补偿，尚未连接真实语音输入输出 |
| `gameplan/tactics/` | 本地教练规则、选人、加载、分路与个人方案 |
| `gameplan/ai/` | 模型接口、知识聊天、对局建议、复盘和画像任务 |
| `gameplan/knowledge/` | 王者/联盟知识检索、技能资料与聊天计时规则 |
| `gameplan/core/` | 数据模型、旧存储、聊天/复盘门面及战报生成 |
| `gameplan/persistence/` | PostgreSQL/Redis、用户归属、任务恢复和迁移 |
| `static/` | 工作台、监控、聊天、知识库、档案、复盘及战报页面 |
| `tests/`、`scripts/`、`docs/` | 自动化检查与样本、维护工具、需求与验证记录 |

根目录 `app.py`、`monitor_app.py` 保留兼容启动入口，主要实现集中在 `gameplan/`。`work/` 存放日志、实验、诊断和验证产物，不是全部业务模块的源码目录。

知识快照主要位于 `resources/knowledge/`：

- `hero_relations.json`：60 位王者教练机制条目，非全部技能目录。
- `knowledge_base.json`：10 条通用决策原则。
- `skill_catalog.json`：132 个王者英雄条目、537 条技能；动态/冲突冷却保留限制，见 [技能资料说明](docs/hero-knowledge.md)。
- `hero_voice_aliases.json`：共享的英雄别名与错字映射，共享称呼需要确认。
- `lol_catalog.json`：英雄联盟 `16.19.1`、173 位英雄及参考装备池；`lol_summoner_spells.json` 保存相应召唤师技能资料，见 [联盟维护说明](docs/maintenance/lol-knowledge.md)。

这些数量是本地文件核对结果，不表示最新游戏版本目录。新增、重做或数值变更后应更新资料并重新检查，不能仅改 README 数量。项目整理的打法和装备池需与官方字段区分。

## API 摘要

两个服务的挂载范围不同，完整请求与响应以各自 `/docs` 和源码为准。Sale Root 接入使用新增的独立内部接口，详情见[Root 接入说明](docs/maintenance/root-king-integration.md)。

| 接口 | 用途 / 服务 |
| --- | --- |
| `GET /api/health`、`GET /api/monitor/status` | 健康与监控状态；两个服务 |
| `GET /api/screen/sources`、`POST /api/screen/frame` | 本机屏幕/窗口来源与采样；两个服务 |
| `POST /api/vision/observe` | 观察、敌方技能及自动复盘入口；两个服务，工作台通过 `/api/vision/{kind}` 的 `observe` 分支提供 |
| `POST /api/monitor/match/reset`、`POST /api/monitor/summoner/correct` | 自动监控清局与召唤师携带技能更正；独立监控服务 |
| `GET /api/knowledge/skills/status`、`GET /api/knowledge/skills/{hero}` | 王者技能目录及详情；两个服务 |
| `GET /api/knowledge/lol/heroes`、`GET /api/knowledge/lol/heroes/{hero_id}` | 联盟摘要检索与详情；两个服务 |
| `POST /api/chat/sessions`、`GET /api/chat/sessions/{sid}`、`POST /api/chat/sessions/{sid}/messages` | 聊天创建、恢复和发送；两个服务 |
| `POST /api/king-agent/query` | Sale Root 内部调用；需服务密钥和可信用户身份；两个服务 |
| `GET /api/chat/sessions/{sid}/history` | 新数据库模式下完整聊天分页；两个服务 |
| `GET /api/matches/{match_id}/skill-timers`、`POST /api/matches/{match_id}/skill-timers` | 手动计时快照与登记；两个服务 |
| `POST /api/matches/{match_id}/text-command`、`POST /api/matches/{match_id}/voice-command` | 普通文字/已转写唤醒词指令；两个服务 |
| `POST /api/matches/{match_id}/skill-timers/clear`、`POST /api/matches/{match_id}/skill-timers/{timer_id}/cancel` | 清空或撤销文字/手动计时；两个服务 |
| `GET /api/reviews`、`GET /api/reviews/{match_id}`、`GET /api/reviews/frames/{frame_id}` | 个人历史复盘与关键画面；两个服务 |
| `POST /api/reviews/{match_id}/retry` | 重试复盘分析；两个服务 |
| `GET /api/me`、`GET /api/profile`、`GET /api/profile/versions`、`GET /api/profile/evidence` | 新数据库身份、档案与来源；两个服务 |
| `POST /api/profile/jobs/{job_id}/retry` | 重试档案更新；两个服务 |
| `POST /api/picture/frame`、`/recognize`、`/analysis`、`/personal-plan` | 固定图片提交、识别、分析和个人方案；两个服务，共用 `/api/picture` 前缀 |
| `POST /api/bp/plan`、`POST /api/loading/plan`、`POST /api/coach/analysis` | 选人、加载与建议；具体挂载范围见 `/docs` |
| `POST /api/matches`、`POST /api/bp/advice`、`POST /api/in_game/advice` | 旧工作台对局、阵容与局面规则；工作台服务，受存储模式限制 |
| `POST /api/post_game/review`、`GET /api/reports/{id}`、`GET /r/{id}` | 旧手动复盘、海报/二维码/H5；工作台服务 |
| `WS /ws/{match_id}` | 旧工作台同局同步；工作台服务 |

## 访问边界与服务器接入状态

本地开发模式的私有接口与屏幕/外部图片分析接口限制本机同源访问。新数据库模式已经提供用户所有权和服务端身份接入点，正式登录验证仍需实现；旧 SQLite 战报/工作台演示不能直接当作有完整账号隔离的服务器产品。

迁到服务器需关闭开发用户、接入可信登录解析器、配置 HTTPS/代理，并验证不同用户的聊天、复盘、图片和档案隔离。服务器进程的 Windows 截图接口不能代替玩家电脑采集，远程监控需另行设计浏览器共享上传或本地客户端。服务端密钥、原始图片、模型补充和分享内容也需按实际链路确认。

**Sale Root 王者助手接入已实现并通过本地联调：** 两个项目独立运行，Root 新增 `KING`，通过带服务密钥的 `POST /api/king-agent/query` 调用现有聊天、知识检索、文字计时和玩家档案上下文；支持 `切换到王者助手`、`切换到AI电竞助手` 和切回原助手。已认证的 Sale 用户及其 Root 会话映射到用户所属的 PostgreSQL/Redis 聊天记录。两边本地 `.env` 已配置相同连接密钥，重启服务加载；正式 Sale 登录、业务依赖及部署地址仍需有效。监控/视频/复盘保持原页面，页面单点登录和远程玩家屏幕采集没有在本次实现。详见[接入与使用](docs/maintenance/root-king-integration.md)。

## 验证记录与待验收项

前一次 README 核对执行了以下现有检查；当时未启动服务、迁移数据库或调用真实外部模型：

- Python：`test_all_hero_suspected_timers`、`test_suspected_cooldowns`、`test_text_cooldown_routes`、`test_voice_aliases`、`test_voice_cooldown_assistant`、`test_chat_timers`、`test_lol_knowledge`、`test_full_knowledge`、`test_match_reviews`、`test_redis_persistence` 共 **1892 项通过**。数量包含参数化条目，不是 1892 场真实对局。
- 前端：监控、面板文字计时、聊天、王者/联盟知识库、复盘和档案的七个现有 Node 测试文件通过。它们使用模拟 DOM/API，不能替代真实浏览器、原生面板或游戏实测。
- Python 检查使用临时数据目录，未配置真实数据库测试开关；Redis 检查使用 FakeRedis，不证明真实 Redis 迁移成功。玩家档案的真实 PostgreSQL 测试及真实模型检查未在本次执行，方法见相关维护文档。

本次 Root 接入新增验证：Gameplan 内部接口及相关聊天、计时、Redis、玩家档案回归共 **136 项通过、1 项跳过**；包括已启动本地 PostgreSQL 后在专用测试 schema 中验证身份绑定、会话恢复、请求去重和用户/租户隔离。Sale Root 的同步、SSE、切换、登录与配置检查共 **91 项通过**，其中跨项目使用真实本机 HTTP、测试 Java JWT 和隔离的存储/销售/门店替身。没有调用真实外部模型或部署线上服务。

后续仍需验收：全英雄/皮肤自动施放准确率与误报漏报、真实闪现释放、持续投屏端到端时延、两款游戏完整个人复盘、游戏与模型同机性能、原生置顶面板交互、手机扫码、真实 Redis 迁移及重启恢复，以及正式页面登录和线上 Sale 多用户部署。

过去的 GPU 状态、模型耗时、服务端口存活和联调成功记录只说明当时的测试条件，不作为当前运行状态或全面完成的承诺。
