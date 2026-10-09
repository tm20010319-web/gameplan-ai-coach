# Sale Root 王者助手接口

Gameplan 仍为独立服务，Sale Root 新增 `KING` 适配器，经 `POST /api/king-agent/query` 调用现有聊天逻辑。不会复制游戏模块，也不会在 Root 服务中运行屏幕采集。

本仓库仅包含 Gameplan 服务侧代码；Sale Root 适配器和启动器需要从独立 Sale 项目取得。文中的 `D:/tm_work`、`D:/sale-agent-main` 是原联调电脑路径，其他电脑需替换成自己的项目路径。密钥、测试账号和已运行的服务不会随 Git 拉取自动复制。

接入时为两个项目的 `.env` 配置相同的随机 `KING_AGENT_SERVICE_TOKEN`（至少 32 个字符），Sale 设置 `KING_AGENT_ENABLED=true` 与 `MULTI_AGENT_ORCHESTRATOR_ENABLED=true`，`KING_AGENT_BASE_URL` 指向 Gameplan 服务，默认本机地址为 `http://127.0.0.1:8767`。不要将该密钥交给浏览器。原联调电脑已完成此配置；新电脑需自行配置。

启动或重启 Gameplan：

```powershell
cd D:/tm_work
./start-monitor.ps1 -NoWindow
```

Sale 也需要重启查询服务并保留其有效模型、存储和登录配置。已登录 Sale 聊天通过原 `/api/v1/query` 使用：

仅在本机测试 Root 与王者助手时，可运行 `D:/sale-agent-main/scripts/start-root-king.ps1`。它启动 `http://127.0.0.1:8001/html` 并保留真实密码/JWT 登录；测试账号见 Sale 的 `tmp/root-king-local/test-account.json`。Root 使用独立 SQLite 历史和进程内测试会话，不需要 MongoDB/Redis；Gameplan 仍调用真实服务和原有数据库。该测试入口不启动销售检索或 Java 门店，重启 Root 后需要重新登录、新建对话。

1. `切换到王者助手` 或 `切换到AI电竞助手`。
2. 提问，如 `妲己大招冷却多久`、`小明大招用了`。
3. `切回销售助手` 或 `切换到门店系统`。

用户身份由 Sale 登录与会话所有权校验后传递。Gameplan 在已迁移的 PostgreSQL/Redis 中建立稳定玩家 UUID 与聊天映射，读取该玩家档案、保存聊天和文字计时。相同用户/Root 会话再次进入时复用数据；不同用户或租户相互隔离；仅旧 SQLite 模式、缺失密钥、未认证身份、停用玩家和数据库故障均不能继续调用。

监控、图片/视频识别、复盘和档案继续使用原工作台。本次接入没有添加远程屏幕采集或 Gameplan 页面单点登录；浏览器页面的 `app.state.resolve_user` 与开发账号仍保持原有身份方式，不能把 Root 绑定误当成页面已经登录。

完整 Root 配置、容器地址、合同和测试命令见 Sale 项目 `D:/sale-agent-main/docs/KING_AGENT_INTEGRATION.md`。两边重配密钥或部署路径时，可运行：

```powershell
./.venv/Scripts/python.exe D:/sale-agent-main/scripts/configure_king_agent.py --sale-env D:/sale-agent-main/.env --gameplan-env D:/tm_work/.env --base-url http://127.0.0.1:8767
```

检查记录：内部接口经过真实 PostgreSQL 隔离 schema 与 FakeRedis 测试；跨项目通过真实 loopback HTTP 测试了 Root 同步/SSE、经过 Java JWT 验证的公共入口、用户绑定、切换与恢复。销售/门店和 Root 历史使用隔离测试替身，没有调用真实外部模型或发布到线上。
