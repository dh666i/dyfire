# 服务器更新

标准目录为 `/opt/dyfire`，Compose 项目名为 `dyfire`。当前线上旧项目不会被本工具识别或改动。

## 启用条件

1. 首次部署经过验证，镜像为 `ghcr.io/dh666i/dyfire@sha256:...` 的完整摘要。
2. `installed.json` 记录实际部署的 `version`、`schema`（目前为 1）、`image`。不得填写尚未部署的镜像。
3. 私有 GitHub 仓库只读令牌置于 `secrets/github-token`，root 所有、0600 权限。令牌只需读取仓库内容与发行信息。另用有 read:packages 权限的凭据在主机完成 `docker login ghcr.io`，不写入网页或仓库。
4. 每个可更新发行版使用 `vX.Y.Z` 标签，发行说明包含发布流水线生成的 `dyfire-manifest` JSON 注释，记录与镜像实际一致的 version、schema、image 摘要。草稿不参与自动更新。
5. 复制 `dyfire-updater.service` 到 systemd 服务目录并启用。没有完成上述配置，网页保持“更新服务暂未连接”。

更新过程：从固定私有仓库读取版本 → 校验版本与数据结构 → 检查扫码/发送及空间 → 下载固定镜像 → 停止写入服务 → 备份 data → 启动新版本 → 校验健康与实际版本。失败恢复旧镜像。不会自动恢复旧数据库，避免回滚执行记录后重复发送。

管理员也可以选择 Web-only 更新：只拉取并重建 `spark-web`，保留 `spark-auth`、`spark-worker` 和 `spark-monitor` 的进程与镜像不变。此模式只适用于前端/API 展示改动；涉及调度、发送、登录流程或数据结构的版本必须使用完整更新。Web-only 版本记录在 `web-installed.json`，不覆盖完整服务的 `installed.json`。

仅相同 schema 的版本可以自动更新。包含不兼容迁移的版本必须另行制定迁移方案；此机制不承诺任意版本之间回滚。

更新请求目录由应用写入，其余更新状态及部署目录由主机拥有。网页仅允许管理员提交 check/install，检查 CSRF，已有请求不会重复排队。

首次 Linux 容器和失败回滚演练尚未完成，暂不在旧生产服务器启用更新器。
