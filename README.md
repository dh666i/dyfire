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

迁移已有站点时，必须同时保留数据库、cookie.key、session.key。不要对旧站直接执行新站初始化，也不要将新空目录替换已有数据卷。

## 更新机制

见 [部署与更新](deploy/README.md)。更新器运行在宿主机，由 systemd 管理；网页没有 Docker socket 或任意命令接口。私有仓库认证只在服务器配置。

第三方许可见 LICENSE、THIRD_PARTY_NOTICES.md 及随代码保留的许可文件。
