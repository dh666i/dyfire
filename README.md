# Dyfire

抖音续火任务与多用户管理服务。代码版本从 **0.1.0** 开始。

## 当前交付状态

- FastAPI / SQLite 业务服务保留账号验证、任务防重复、订阅兑换、通知及管理员权限。
- 已提供 PostgreSQL 驱动和 PostgreSQL 行级任务领取基础；生产切换前仍需完成一次性数据迁移、回放校验和故障演练。
- `/studio` 使用新设计，真实读取账号、好友与今日任务状态，支持批量创建及暂停/恢复。今日概览、账号列表、分页执行记录和版本更新页也使用同一套新设计。
- 账号绑定、编辑任务、通知、会员、账户中心、待处理事项和帮助页均通过新版界面提供真实流程；管理员页面保留现有权限与运维能力。
- 新版不使用示例账号、不把业务数据或密码存入 localStorage。
- 管理员系统设置已有检查更新/安装更新入口。尚未安装更新服务或没有兼容发行版时，不能安装。
- 生产 Web、auth、worker、monitor 使用固定摘要镜像；更新器由宿主机 systemd 管理，完整更新会在切换前保留部署快照并执行健康检查。
- 周期性宿主机备份不在当前部署范围；需要备份时应另行设计保留与异地复制策略。

## 构建与运行

```sh
cd frontend
npm ci
npm run build
cd ..
sudo python3 deploy/initialize.py
docker compose --env-file .env.console -f compose.console.yml build
docker compose --env-file .env.console -f compose.console.yml up -d
docker compose --env-file .env.console -f compose.console.yml exec spark-web python -m spark_console.cli create-admin admin@example.com
```

### 数据保留维护

`retention` 命令用于定期清理已过期的历史执行、登录会话、邮件投递和审计记录。默认是 **dry-run**，只输出 JSON 统计，不会删除数据；确认窗口和结果后才加 `--apply`。它会分批执行，并且始终保留执行中的任务、等待重试和“已提交待核实”记录、活跃扫码会话、排队/发送中/结果不确定的邮件，以及未过期的网页会话。

```sh
# 先预览（默认：执行记录 365 天、会话 30 天、邮件 30 天、审计 365 天）
docker compose --env-file .env.console -f compose.console.yml exec spark-web \
  python -m spark_console.cli retention

# 明确确认后执行；每批最多处理 500 条
docker compose --env-file .env.console -f compose.console.yml exec spark-web \
  python -m spark_console.cli retention --apply
```

生产环境建议由宿主机定时任务调用，先保存 dry-run 输出，再在低峰期执行 `--apply`。可使用 `--runs-days`、`--sessions-days`、`--mail-days`、`--audit-days` 和 `--batch-size` 调整窗口；所有参数都有上限，`--as-of` 仅用于可重复的演练且必须带时区。该命令不会删除用户、任务、账号、订阅或兑换码。

### SQLite 到 PostgreSQL 迁移

迁移命令默认只做完整性、空目标、活动任务/扫码/邮件检查，不写入目标；停掉所有 Web、auth、worker、monitor 写入进程后，经过备份和回放验证，再显式添加 `--apply`：

```sh
python -m spark_console.cli migrate-sqlite-postgres /data/spark.db \
  --target 'postgresql+psycopg://dyfire:密码@postgres:5432/dyfire'
python -m spark_console.cli migrate-sqlite-postgres /data/spark.db \
  --target 'postgresql+psycopg://dyfire:密码@postgres:5432/dyfire' --apply
```

该工具要求目标数据库为空，按表分批复制并校验行数；它不是在线双写或自动切换工具。

使用反向代理提供 HTTPS；默认仅在 127.0.0.1:8899 监听。绑定账号和发送任务需要 auth、worker 服务在线。新部署先创建管理员，再登录；不会预置默认密码。

### 可选 Redis 预备层

当前 Web、登录和发送任务仍以数据库为唯一事实来源，默认不会连接 Redis。保持 `SPARK_REDIS_ENABLED=false` 即可继续使用现有 SQLite 部署；此时登录、注册、扫码和密码找回继续使用进程内限流，且不会发起 Redis 连接。显式启用并配置 Redis 后，上述认证和扫码限流会改用跨进程原子固定窗口计数器，Redis 故障时请求会拒绝而不会悄悄退回本地计数器。发送任务仍未切换到 Redis 队列。需要在隔离环境验证时，可在 `.env.console` 设置：

```sh
SPARK_REDIS_ENABLED=true
SPARK_REDIS_URL=redis://redis:6379/0
```

然后显式启动 profile：

```sh
docker compose --env-file .env.console -f compose.console.yml --profile redis up -d redis
```

PostgreSQL 也提供默认关闭的隔离 profile，用于迁移回放和 schema/claim 预检。它不会自动改变现有 SQLite 服务：

```sh
docker compose --env-file .env.console -f compose.console.yml --profile postgres up -d postgres
SPARK_DATABASE_URL=postgresql+psycopg://dyfire:change-me-in-isolation@postgres:5432/dyfire \
  docker compose --env-file .env.console -f compose.console.yml --profile postgres run --rm --no-deps \
  spark-web python -m spark_console.cli migrate-sqlite-postgres /data/spark.db \
  --target 'postgresql+psycopg://dyfire:change-me-in-isolation@postgres:5432/dyfire'
```

迁移命令默认只读预检；只有完成备份、回放、Cookie 解密和任务计划一致性核验后才允许显式 `--apply`。不要把这个 profile 直接接到线上发送服务。

切入持久队列前，可以运行只读的队列预检。它会检查数据库任务领取所需的表、字段和 worker 分片配置；Redis 关闭时只报告 `disabled`，不会建立网络连接，也不会改变现有 worker 行为：

```sh
docker compose --env-file .env.console -f compose.console.yml \
  exec spark-web python -m spark_console.cli queue-preflight
```

在隔离 Redis 上验证 ACL、consumer group 和确认权限时，再显式加入 `--probe-stream`。该探针使用随机临时 Stream 发布一条标记消息，读取并确认后删除，不会触碰真实任务队列：

```sh
docker compose --env-file .env.console -f compose.console.yml \
  exec spark-web python -m spark_console.cli queue-preflight --probe-stream
```

探针通过后仍需完成 PostgreSQL 回放、发送幂等键、重复投递、故障降级和 worker 恢复演练，才可以逐步切换真实任务队列。

正式启用前仍需单独完成 Redis 持久化、访问控制、故障降级和队列重复投递演练；不要仅因为 profile 能启动就把它切入生产发送链路。

迁移已有站点时，必须同时保留数据库、cookie.key、session.key。不要对旧站直接执行新站初始化，也不要将新空目录替换已有数据卷。

## 更新机制

见 [部署与更新](deploy/README.md)。更新器运行在宿主机，由 systemd 管理；网页没有 Docker socket 或任意命令接口。私有仓库认证只在服务器配置。

第三方许可见 LICENSE、THIRD_PARTY_NOTICES.md 及随代码保留的许可文件。
