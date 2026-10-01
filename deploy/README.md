# 服务器更新

标准目录为 `/opt/dyfire`，Compose 项目名为 `douyin-spark-console`（沿用线上项目名）。更新器只操作这个标准部署目录。

## 启用条件

1. 首次部署经过验证，镜像为 `ghcr.io/dh666i/dyfire@sha256:...` 的完整摘要。
2. `installed.json` 记录实际部署的 `version`、`schema`（目前为 1）、`image`。不得填写尚未部署的镜像。
3. 私有 GitHub 仓库只读令牌置于 `secrets/github-token`，root 所有、0600 权限。令牌只需读取仓库内容与发行信息。另用有 read:packages 权限的凭据在主机完成 `docker login ghcr.io`，不写入网页或仓库。
4. 每个可更新发行版使用 `vX.Y.Z` 标签。GitHub Actions 会在容器健康检查通过后发布正式 Release，发行说明包含 `dyfire-manifest` JSON 注释，记录与镜像实际一致的 version、schema、image 摘要。草稿和预发行版永不参与自动更新。
5. 复制 `dyfire-updater.service` 到 systemd 服务目录并启用。没有完成上述配置，网页保持“更新服务暂未连接”。

更新过程：从固定私有仓库读取版本 → 校验版本与数据结构 → 检查扫码/发送及空间 → 从运行中的 Web 容器解析 `/data` 主机绑定目录 → 下载固定镜像 → 停止写入服务 → 生成部署前快照 → 启动新版本 → 校验健康与实际版本。镜像拉取最多等待 1 小时，停止/启动服务等本机操作最多等待 10 分钟。失败恢复旧镜像。不会自动恢复旧数据库，避免回滚执行记录后重复发送。完整更新和回滚都会在下载前、停止服务前检查活跃发送与扫码会话；发现任一操作进行中就拒绝切换，不中断现有抖音登录态或消息任务。

管理员也可以选择 Web-only 更新：只拉取并重建 `spark-web`，保留 `spark-auth`、`spark-worker` 和 `spark-monitor` 的进程与镜像不变。此模式只适用于前端/API 展示改动；涉及调度、发送、登录流程或数据结构的版本必须使用完整更新。Web-only 版本记录在 `web-installed.json`，不覆盖完整服务的 `installed.json`。

仅相同 schema 的版本可以自动更新。包含不兼容迁移的版本必须另行制定迁移方案；此机制不承诺任意版本之间回滚。

更新请求目录由应用写入，其余更新状态及部署目录由主机拥有。网页仅允许管理员提交 check/install，检查 CSRF，已有请求不会重复排队。默认不会自动安装或启用周期性宿主机备份；正式更新仍会在切换前保存可回退快照。

如需在确认磁盘空间和恢复目标后启用本机周期备份，可人工安装模板（不会影响
`www.chichiapi.cn`）：

```bash
sudo install -m 0644 deploy/dyfire-backup.service deploy/dyfire-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dyfire-backup.timer
systemctl list-timers dyfire-backup.timer
```

模板每天 03:17（最多延迟 15 分钟）调用一次性 `spark-web` Compose 容器，保留最近
14 个备份。启用前应确认 `.env.console` 指向正式镜像且 `data/backups` 有足够空间。
关闭 timer：

```bash
sudo systemctl disable --now dyfire-backup.timer
```

## 备份清单和校验

`backup-db` 只有在 SQLite 在线备份成功、完整性和外键检查通过后，才会把数据库
和同名 `.db.manifest.json` 清单原子提交到 `data/backups`。清单包含格式版本、文件
大小、SHA-256、schema 版本和 SQLite 检查结果，不包含 Cookie 或用户数据。清单缺失、
被改动、孤立或对应数据库损坏，都不能算作可恢复备份。可单独核验一个备份或整个
目录：

```bash
cd /opt/dyfire
python3 -m spark_console.cli verify-backup data/backups/spark-20261001T120000000000Z.db
python3 -m spark_console.cli verify-backups data/backups
```

`verify-backups` 返回非零表示至少一个备份、清单或孤立清单不可信；该命令只读，
不会删除文件、写入数据库或停止服务。历史旧文件缺少清单时，可用
`verify-backup --allow-legacy` 做 SQLite 检查和迁移盘点，但结果会明确标记
`manifest.verified=false`，不可作为正式恢复点。恢复演练仍应使用下文的
`deploy/recovery_drill.py`，并在演练前先通过清单校验。

## 反向代理安全配置

在 `.env.console` 中设置 `SPARK_TRUSTED_PROXY_CIDRS` 为应用容器看到的直接反向代理 IP/CIDR，多个值用逗号分隔；不要填公网客户端网段，也不要使用 `0.0.0.0/0`。留空时应用完全忽略 `X-Forwarded-For` 和 `X-Forwarded-Proto`。反向代理必须先清理客户端携带的同名请求头，再追加它自己计算的值，并将 HTTPS 终止状态写入 `X-Forwarded-Proto`。

应用只会对直连 HTTPS 或已信任代理报告的 HTTPS 响应发送 HSTS。确认所有子域名都支持 HTTPS 后，才设置 `SPARK_HSTS_INCLUDE_SUBDOMAINS=true`；默认关闭该选项以免误锁定尚未迁移的子域名。

安装 `dyfire-updater.service` 后，让 `/opt/dyfire/updates` 同时作为 Web 容器的只读状态目录和请求目录的宿主路径。完整更新前会检查正在运行的容器确实把 `/opt/dyfire` 下的数据目录绑定到 `/data`，避免误用旧卷路径。

## 恢复演练

备份文件不能只看文件存在或 SQLite 的单次完整性检查。可以在不接触线上数据库的情况下运行隔离恢复演练：

```bash
cd /opt/dyfire
python3 deploy/recovery_drill.py \
  data/backups/spark-20261001T120000000000Z.db \
  --cookie-key secrets/cookie.key
```

命令以只读方式打开源备份，在临时目录创建全新副本，然后验证表集合、逐表内容指纹、SQLite 完整性、外键和 schema migration。提供 `--cookie-key` 后还会逐个验证现有账号 Cookie 可以解密；报告只包含数量和哈希，不输出 Cookie。默认副本在命令结束时删除；需要人工抽查时显式指定一个全新的 `--output` 路径：

```bash
python3 deploy/recovery_drill.py \
  data/backups/spark-20261001T120000000000Z.db \
  --cookie-key secrets/cookie.key \
  --output /var/tmp/dyfire-recovery-check/restored.db
```

演练不会停止服务、写入源备份、覆盖 `/opt/dyfire/data/spark.db` 或自动回放任务。生产更新失败时更新器只恢复镜像并保留新的执行记录，这是为了避免恢复旧数据库造成重复发送；数据库恢复必须先在隔离目录完成本演练，再按人工审批和 RPO/RTO 流程执行。
