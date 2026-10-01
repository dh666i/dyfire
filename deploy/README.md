# 服务器更新

标准目录为 `/opt/dyfire`，Compose 项目名为 `douyin-spark-console`（沿用线上项目名）。更新器只操作这个标准部署目录。

## 启用条件

1. 首次部署经过验证，镜像为 `ghcr.io/dh666i/dyfire@sha256:...` 的完整摘要。
2. `installed.json` 记录实际部署的 `version`、`schema`（目前为 1）、`image`。不得填写尚未部署的镜像。
3. 私有 GitHub 仓库只读令牌置于 `secrets/github-token`，root 所有、0600 权限。令牌只需读取仓库内容与发行信息。另用有 read:packages 权限的凭据在主机完成 `docker login ghcr.io`，不写入网页或仓库。
4. 每个可更新发行版使用 `vX.Y.Z` 标签。GitHub Actions 会在容器健康检查通过后发布正式 Release，发行说明包含 `dyfire-manifest` JSON 注释，记录与镜像实际一致的 version、schema、image 摘要。草稿和预发行版永不参与自动更新。
5. 复制 `dyfire-updater.service` 到 systemd 服务目录并启用。没有完成上述配置，网页保持“更新服务暂未连接”。

更新过程：从固定私有仓库读取版本 → 校验版本与数据结构 → 检查扫码/发送及空间 → 从运行中的 Web 容器解析 `/data` 主机绑定目录 → 下载固定镜像 → 停止写入服务 → 生成部署前快照 → 启动新版本 → 校验健康与实际版本。镜像拉取最多等待 1 小时，停止/启动服务等本机操作最多等待 10 分钟。失败恢复旧镜像。不会自动恢复旧数据库，避免回滚执行记录后重复发送。

管理员也可以选择 Web-only 更新：只拉取并重建 `spark-web`，保留 `spark-auth`、`spark-worker` 和 `spark-monitor` 的进程与镜像不变。此模式只适用于前端/API 展示改动；涉及调度、发送、登录流程或数据结构的版本必须使用完整更新。Web-only 版本记录在 `web-installed.json`，不覆盖完整服务的 `installed.json`。

仅相同 schema 的版本可以自动更新。包含不兼容迁移的版本必须另行制定迁移方案；此机制不承诺任意版本之间回滚。

更新请求目录由应用写入，其余更新状态及部署目录由主机拥有。网页仅允许管理员提交 check/install，检查 CSRF，已有请求不会重复排队。当前不启用周期性宿主机备份；正式更新仍会在切换前保存可回退快照。

## 反向代理安全配置

在 `.env.console` 中设置 `SPARK_TRUSTED_PROXY_CIDRS` 为应用容器看到的直接反向代理 IP/CIDR，多个值用逗号分隔；不要填公网客户端网段，也不要使用 `0.0.0.0/0`。留空时应用完全忽略 `X-Forwarded-For` 和 `X-Forwarded-Proto`。反向代理必须先清理客户端携带的同名请求头，再追加它自己计算的值，并将 HTTPS 终止状态写入 `X-Forwarded-Proto`。

应用只会对直连 HTTPS 或已信任代理报告的 HTTPS 响应发送 HSTS。确认所有子域名都支持 HTTPS 后，才设置 `SPARK_HSTS_INCLUDE_SUBDOMAINS=true`；默认关闭该选项以免误锁定尚未迁移的子域名。

安装 `dyfire-updater.service` 后，让 `/opt/dyfire/updates` 同时作为 Web 容器的只读状态目录和请求目录的宿主路径。完整更新前会检查正在运行的容器确实把 `/opt/dyfire` 下的数据目录绑定到 `/data`，避免误用旧卷路径。
