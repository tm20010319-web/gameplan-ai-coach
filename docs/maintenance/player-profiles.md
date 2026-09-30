# PostgreSQL 玩家长期档案

新数据统一保存在 PostgreSQL：平台用户、完整聊天、对局、复盘、画像和更新历史。旧 SQLite 保留原样，不迁移旧用户或旧对局。图片保存在独立文件目录，数据库保存归属和索引。

## 当前本地环境

- PostgreSQL 17：`127.0.0.1:55432`，数据库 `gameplan`，应用角色 `gameplan_app`，仅监听本机。
- `.env` 的 `GAMEPLAN_DATABASE_URL` 是私密连接串；不要提交或贴到日志。
- 本地开发身份由 `GAMEPLAN_DEV_USER_ID` 指向“本地开发玩家”，页面明确标注。现在还不是登录系统。
- 启动：`powershell -ExecutionPolicy Bypass -File .\start-monitor.ps1 -NoWindow -NoAutoStart`。
- 档案页：`http://127.0.0.1:8767/profile`；监控页里的“玩家档案”也可进入。
- 程序：`work/postgresql-runtime/pgsql`；数据：`data/postgres-cluster`；日志：`work/postgresql.log`。
- 图片：`data/postgres-assets/<user_id>/`，可用 `GAMEPLAN_ASSET_DIR` 改到持久化磁盘。
- 本机管理员凭据：`data/postgres-private/`，只供本机维护，不能分发给客户端。

`start-monitor.ps1` 会在 `GAMEPLAN_LOCAL_POSTGRES=1` 时启动数据库，但不会重启已经运行的应用。应用代码或环境变量改变后需重启对应 uvicorn 进程。

## 数据关系与处理流程

| 表组 | 内容 |
| --- | --- |
| users / user_roles / coach_assignments | 平台账号、角色、教练与玩家授权关系 |
| review_streams / matches | 每个用户独立的识别流和对局 |
| match_events / match_assets / match_reviews | 游戏观察、证据图片、简短复盘 |
| chat_sessions / chat_messages | 会话及逐条完整消息，含失败回答前已提交的玩家文字 |
| player_profiles / profile_facts | 当前长期档案，按通用、王者荣耀、英雄联盟分开 |
| profile_update_jobs | 可恢复的异步画像更新任务 |
| profile_versions / profile_evidence / profile_consumed_messages | 每次画像版本、来源及已处理消息 |
| access_audit | 教练和管理员查看其他玩家档案的审计 |
| timer_sessions / timer_audit | 新模式下的文字技能计时数据 |

连续确认游戏结束 → 保存个人复盘 → 同一事务创建画像任务 → DeepSeek 总结新增复盘和未处理的玩家消息 → 原子保存画像、版本、证据及消息处理标记。聊天中的助手回复完整保存，但不能被当作玩家自述依据。

画像输入冻结于任务首次领取，聊天截止时间是该局复盘保存时间；之后收到的消息留给下一局。每批最多 30 条来源，每批完成后存检查点。同一玩家按顺序处理，不同玩家可并发。租约 120 秒，模型调用超时 50 秒；服务重启后可继续。失败自动重试，累计三次失败后显示重试按钮，并暂缓该玩家后续任务，旧画像和原始记录保留。

每条结论保留来源：明确自述、待验证、多局观察。行为结论至少有三局不同对局支持才可显示“多局观察”。新明确自述可纠正旧偏好，推测不能覆盖明确自述。画像用于后续聊天和复盘建议的个性化，不充当本局事实。

聊天初始接口返回最近 100 轮，数据库不截断；更早消息通过 `GET /api/chat/sessions/{sid}/history?before=<message_id>&limit=50` 分页。历史复盘仍使用 `/api/reviews`。档案页可查看当前事实、来源、处理状态和历史版本。

## 用户隔离与后续登录

所有私有读写从服务端身份上下文获取 user_id；不会相信客户端传来的 `X-User-Id`。复盘、图片、聊天查询均限定所有者，复合外键限制跨用户关联。访问其他人的画像须有 admin 角色或 coach 角色且存在 coach_assignments，访问有审计。当前用应用层授权和数据库外键，不是 PostgreSQL RLS。

接入登录时，验证真实会话或令牌后设置 `app.state.resolve_user(request)`，返回已映射到 users.id 的 UUID，未登录返回 None；支持异步函数。必须由服务端验证会话和账号归属，不要把任意请求参数直接返回。账号用 `(auth_provider, auth_subject)` 唯一绑定登录身份。

服务器须删除 `GAMEPLAN_DEV_USER_ID`、设置 `GAMEPLAN_LOCAL_POSTGRES=0`，通过 HTTPS 反向代理运行；可信代理、会话 cookie 和登录 CSRF 防护需随登录功能一起配置。聊天/复盘/档案允许已验证的远程身份访问，跨站 Origin 被拒绝。屏幕采集接口仍限本机；服务器不会采集玩家电脑，需要本地客户端负责上传。

旧版手动工作台的战报、BP记录接口在新数据库模式返回 503，避免写旧库；当前交付入口为独立监控、聊天及档案。未配置 PostgreSQL 的旧模式保持原有行为。配置了新库后，数据库故障不会自动回退到旧 SQLite。

## 迁到服务器

1. 在服务器安装 PostgreSQL 17 或更新版本，创建独立应用角色及其拥有的空数据库。保存连接串到服务端秘密配置。
2. 若保留本地开发数据：停止应用写入后，用 PostgreSQL 自带 `pg_dump -Fc` 备份 gameplan，在服务器用 `pg_restore --no-owner --no-acl` 恢复到新数据库；使用密码提示或受保护的 pgpass 文件，勿把密码写进命令参数。若服务器从零开始，直接执行下一步迁移即可。
3. 配置 `GAMEPLAN_DATABASE_URL` 后运行 `.venv\Scripts\python.exe scripts/migrate-database.py`。迁移按版本执行，重复运行安全。应用启动会检查缺失迁移。
4. 同步整个 postgres-assets 目录并配置服务器 `GAMEPLAN_ASSET_DIR`。数据库备份不包含图片；不要只复制数据库而遗漏图片。
5. 关闭本地开发身份，接入已验证的登录解析器，创建新账号与角色，验证两个玩家互不可见，再开放服务。

数据库和图片须定期备份并验证恢复。当前不自动删除历史记录和图片，需按真实使用量补充保留期、容量监控。便携运行环境和数据均被 Git 忽略；只提交代码无法自动复制数据库。首次本机安装脚本 `setup-local-database.py` 需要已初始化的本地 PostgreSQL 集群，重复运行会拒绝覆盖现有数据库。

## 验证

```powershell
$env:COACH_DATA_DIR = 'D:\tm_work\work\legacy-test-data'
$env:GAMEPLAN_TEST_POSTGRES = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_player_profiles_pg.py tests/test_match_reviews.py tests/test_knowledge_chat.py -q
node --test tests/profile_ui.cjs tests/chat_ui.cjs tests/reviews_ui.cjs
```

PostgreSQL 测试每次创建随机 `gp_test_*` schema，测试后只删除该 schema；验证隔离、完整聊天、幂等、租约、失败恢复、授权、证据和版本，同时校验旧 SQLite 文件哈希未变。真实模型测试另需 `GAMEPLAN_TEST_LIVE_MODEL=1`，只发送合成测试消息并写入临时 schema。
