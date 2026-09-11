# Dyfire

抖音续火任务与多用户管理服务。代码版本从 **0.1.0** 开始。

## 当前交付状态

- FastAPI / SQLite 业务服务保留账号验证、任务防重复、订阅兑换、通知及管理员权限。
- `/studio` 使用新设计，真实读取账号、好友与今日任务状态，支持批量创建及暂停/恢复。
- 账号绑定、编辑任务、通知、会员及管理员页面暂时沿用原有功能页面；全站新版迁移尚未完成。
- 新版不使用示例账号、不把业务数据或密码存入 localStorage。
- 管理员系统设置已有检查更新/安装更新入口。尚未安装更新服务或没有兼容发行版时，不能安装。
- 当前不是已验证的服务器发行包。上线前需完成容器构建验证、真实部署演练和新版全站迁移。

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

使用反向代理提供 HTTPS；默认仅在 127.0.0.1:8899 监听。绑定账号和发送任务需要 auth、worker 服务在线。新部署先创建管理员，再登录；不会预置默认密码。

迁移已有站点时，必须同时保留数据库、cookie.key、session.key。不要对旧站直接执行新站初始化，也不要将新空目录替换已有数据卷。

## 更新机制

见 [部署与更新](deploy/README.md)。更新器运行在宿主机，由 systemd 管理；网页没有 Docker socket 或任意命令接口。私有仓库认证只在服务器配置。

第三方许可见 LICENSE、THIRD_PARTY_NOTICES.md 及随代码保留的许可文件。
