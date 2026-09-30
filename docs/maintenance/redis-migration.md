# Redis 8 迁移准备与切换

## 当前状态（2026-09-30）

本次改造位于 `D:\tm_work` 最新主项目，保留原有未提交功能。
已实现 Redis 的英雄知识、来源文档、工作台、计时、聊天、复盘、玩家档案、权限和任务恢复接口。
未修改项目 `.env`，尚未安装 Redis，尚未将正式数据导入真实 Redis。
现有 SQLite / PostgreSQL 后端保留用于等待安装期间继续使用及回退。
设置 `GAMEPLAN_REDIS_URL` 后，运行接口选择 Redis，连接失败不会自动读取旧数据库。

Windows 的 WSL 自动安装被系统策略阻止，用户决定稍后手动安装。
真实 Redis 8、服务重启恢复、Windows 重启恢复尚未验证。

## 已备份和整理的数据

备份目录：`data/database-backups/20260930T054056045126Z/`。

- `snapshot.json`：SQLite 与 PostgreSQL 的全部数据表逻辑快照。
- `*.sqlite3`：使用 SQLite backup API 生成的一致备份。
- `checksums.json`：各个备份文件的 SHA-256。
- `redis-prepared.json`：待导入 Redis 的各集合。
- `work/redis-migration-before/`：改造前源码和 `.env` 的本地备份。

| 数据 | 数量 |
| --- | ---: |
| 旧工作台对局 | 90 |
| 建议 / 审计 / 战报 | 2 / 631 / 7 |
| 英雄 / 知识元数据 | 132 / 4 |
| 来源文档 / 章节 | 3 / 35 |
| 旧聊天会话 / 转换后的独立消息 | 16 / 108 |
| PostgreSQL 用户 / 角色 | 1 / 1 |
| 合并后计时会话 | 90 |

旧 SQLite 每行另存于 Redis 的 `legacy:<filename>:<table>` 集合，保留原始 JSON 字符串等原值以供追溯。
旧数据归属现有 `GAMEPLAN_DEV_USER_ID` 用户。计时会话同编号冲突时优先保留 PostgreSQL 的较新状态，旧状态仍保存在工作台集合及原始行档案。
旧聊天的历史时间只能沿用会话的 `updated` 时间，不伪造不存在的逐消息时间。
数据库外的英雄头像、原文归档、海报和模型文件仍保留原路径。

## 本机安装后的步骤

先在 Windows 手动安装官方 WSL 与 Ubuntu；按系统提示处理必要的重启。
Ubuntu 需启用 systemd；可用 `wsl -d Ubuntu -- systemctl is-system-running` 检查。
如果需启用，在 `/etc/wsl.conf` 添加 `[boot]` 和 `systemd=true`，然后关闭 WSL 再启动。
不要在应用或其他 WSL 任务运行时强制关闭 WSL。

在 `D:\tm_work` 的 PowerShell 中：

```powershell
.\.venv\Scripts\python.exe scripts\configure-local-redis.py --install
```

该脚本通过 Redis 官方 APT 仓库安装 8.x，创建 `gameplan-redis.service`。
密码和待启用连接串位于忽略提交的 `data/redis-private/`，不会打印到终端。
安装脚本拒绝接管已有的第三方 Redis 包安装；如已有 Redis，需先核对配置。
Ubuntu 安装脚本需要 root，Redis 服务本身以 `redis` 用户运行。

安装配置为：仅监听本机、密码认证、AOF、`appendfsync always`、RDB、`noeviction`，业务键不设置 TTL。
持久化数据放在 WSL Linux 文件系统 `/var/lib/gameplan-redis`。
参考 [Redis 持久化说明](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)
与 [淘汰策略说明](https://redis.io/docs/latest/develop/reference/eviction/)。

正式迁移前停止应用写入，并确保原 PostgreSQL 可连接。
准备阶段快照不包含之后新增的数据，因此正式导入应重新获取快照：

```powershell
.\.venv\Scripts\python.exe scripts\migrate-to-redis.py --apply
```

命令输出新的快照绝对路径。导入要求空 Redis 命名空间，逐行核对，不覆盖正在使用的 Redis 数据。
导入时验证 Redis 8.x、AOF/落盘/禁止淘汰配置、全部记录内容及没有 TTL，并执行同步 RDB 保存。
如果执行失败，`.env` 不会切换。

记录输出的快照路径，保持应用停止，测试 Redis 服务重启：

```powershell
wsl -d Ubuntu -u root -- systemctl restart gameplan-redis.service
.\.venv\Scripts\python.exe scripts\migrate-to-redis.py --snapshot '上一步输出的snapshot.json完整路径' --verify
```

随后测试 Windows 重启后的恢复；启动 Redis 后使用同一快照再次 `--verify`。
通过后在同一验证命令末尾添加 `--enable`，才会写入项目 `.env` 并停止自动启动 PostgreSQL。
原 PostgreSQL 连接配置保留，但 Redis 模式优先选择 Redis。
重启应用并检查聊天、复盘、知识检索、工作台和技能计时。
`start.ps1` 与 `start-monitor.ps1` 在本机 Redis 模式会先通过 WSL 启动 Redis 服务并检查版本。

重新导入不同快照时选择新 `GAMEPLAN_REDIS_PREFIX`；不要清空正在使用的数据。
回退前停止应用，将 `GAMEPLAN_REDIS_URL` 清空、`GAMEPLAN_LOCAL_REDIS` 设为 `0`，恢复原 PostgreSQL 启动配置。
切换后新增的 Redis 数据不会自动回写旧数据库，需另外处理。

## 测试与限制

开发依赖：`pip install -r requirements-dev.txt`。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_redis_persistence.py -q
```

默认使用 fakeredis 隔离测试，覆盖聊天去重/分页、失败请求保留、用户与教练权限、
并发事务重试/回滚、复盘与档案任务租约恢复、冻结输入、档案版本、来源导入、
工作台与计时同步、WebSocket、公开战报链接、真实备份的逐行模拟导入校验。
真实 Redis 测试通过环境变量 `GAMEPLAN_TEST_REDIS_URL` 指定连接串，仅删除随机创建的测试命名空间。
模拟测试通过不能代替真实服务及重启测试。

本次验证：9 项 Redis 隔离测试通过，113 项知识/聊天/复盘/计时原功能测试通过，
6 项真实 PostgreSQL 回归测试通过；1 项调用外部模型的可选测试跳过。
Python 编译、PowerShell 启动脚本语法、备份 SHA-256、原 `.env` 一致性检查通过。

Redis 按集合存为 Hash，跨集合写入使用共享修订号及 WATCH/MULTI/EXEC 重试。
列表查询读取相关集合；这是当前小型单机数据量的实现。未来多用户、高并发或大规模历史数据
应增加按用户拆分的集合、有序集合索引及更细的事务冲突范围，再评估容量与性能。
本次没有声称 Redis 比原数据库更快，也未承诺断电、硬件损坏时绝对不会丢数据；
服务器上线需独立备份，并将备份保存在另一台设备上。

## 以后迁到服务器

服务器安装 Redis 8，沿用持久化配置，将数据和数据库外资源一同迁移。
应用使用 `GAMEPLAN_REDIS_URL`（支持 `rediss://`）及同一前缀；
设置 `GAMEPLAN_LOCAL_REDIS=0`，避免启动 Windows WSL。
Redis 通过服务器内部网络连接；监听地址、ACL、TLS、备份和外部认证按实际环境配置。
本机 `GAMEPLAN_DEV_USER_ID` 只允许本机开发访问；服务器需配置现有的可信登录解析接口。
