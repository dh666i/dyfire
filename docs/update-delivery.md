# 更新体积与发布约定

历史 0.3.42 的 OCI 压缩层总计约 384.8 MiB，主要浏览器运行层约 334 MiB；
Docker 显示的约 1.62 GB 是展开体积。这些是旧版测量值，不代表当前两个组件的下载量。

从 0.4.6 起仍有四个业务服务，但改用两类镜像：`spark-web` 和 `spark-monitor`
使用不含 Playwright/Chromium 的 Web 镜像；`spark-auth` 和 `spark-worker` 共享浏览器镜像。
Docker 按内容摘要复用同机层，不会因为有四个容器就下载四份。Web-only 更新只拉轻量 Web
组件，登录、发送和监控进程继续使用原镜像；完整更新才同步两类组件。

`Dockerfile.runtime` 独立维护系统、Python 依赖和 Chromium。`runtime.yml` 每周或手动
构建运行环境候选，完成非 root 启动、浏览器本地页面验收及安全扫描，产出
`runtime-images-lock`。核对通过后更新 `deploy/runtime-images.json` 中的固定摘要。
应用构建验证锁文件与运行环境输入指纹一致，直接引用已验证运行层；日期变化不会让每次
应用发布重新执行 apt/pip/Chromium 安装。运行依赖变化必须先更新运行环境锁，不能悄悄
跳过补丁维护。首次切换两类镜像、接受新的系统/浏览器运行层，或本机缓存被清理后，
仍可能有较大下载。字体和品牌资源继续保持独立层。

主分支的质量门禁与 Web/browser 候选构建并行，测试分为四个隔离分片并核对完整收集清单。
候选镜像分别使用 GHCR 的 `buildcache-web` 与 `buildcache-browser` 缓存；只对可信 main
push 开放镜像写权限，PR 只读。每个候选完成容器验收、安全扫描和基础层复用验证，
直接比对候选与锁定基础镜像的层，保存 `image-layer-reuse.json`，不再额外重建探针镜像。
全部门禁通过才产出同一提交的 `release-candidate`。版本标签工作流核对其仓库、main
分支、push 事件、工作流、提交、版本和镜像摘要，然后提升已验证镜像并创建 Release；
发行阶段不重复测试与重建。服务器只拉固定摘要，不运行 npm、pip、apt 或编译源码。

发行标签继续保留完整 OCI index、SBOM 与 provenance；部署清单对每个组件记录 index 摘要，
并选取其中唯一的 Linux amd64 运行镜像摘要。服务器拉取这个已验证构建中的运行镜像，
避免 Docker/containerd 把大型安全证明附件也当作部署下载。不会删除证明或改变运行镜像内容。

新发布清单使用 `manifest_version: 2` 和 `schema: 2`，使旧版单镜像更新器拒绝自动安装。
新更新器分别读取 `compatibility.database/api/browser`；其中 database 是数据兼容协议，
不等于 SQLite 的逐次迁移版本号。迁移必须先同步宿主更新器及 Compose，再执行组件更新。
`installed.json` 与 `web-installed.json` 保存实际组件摘要，Web-only 不覆盖执行服务版本。

管理员检查更新时显示压缩镜像体积、可复用层数和本次预计新增下载量。
估算用目标 OCI 层与当前运行镜像比较；本机还缓存其他版本时实际可更小。
估算不可用时不显示假数字，也不阻塞正常更新。

GHCR 仍是当前唯一镜像来源。缓存和分层减少重复下载，但不能消除成都到 GHCR 的网络
延迟或首次下载；不承诺每次发布固定秒数。后续若实测瓶颈主要在跨境链路，可增加国内
镜像同步，但应同步同一已验证摘要并校验，不临时换不可信镜像站或重新构建另一份产物。

官方依据：

- [Docker 构建缓存失效规则](https://docs.docker.com/build/cache/invalidation/)
- [Docker 与 GitHub Actions 缓存](https://docs.docker.com/build/ci/github-actions/cache/)

# 公网桥接登录

浏览器使用 `https://dyfire.cn`，桥接可以使用内部源站名称。生产设置
`SPARK_PUBLIC_ORIGIN=https://dyfire.cn`，同源校验始终使用这个显式地址，
不盲信客户端的 Forwarded-Host。源站代理也应在 dyfire 专属 server 中设置
`proxy_set_header Host dyfire.cn;`，保留可信代理 scheme/IP 的现有处理。

只改 dyfire 的配置，不能修改同机其他站点。拒绝的浏览器表单返回站内错误页，
API/JSON 请求保持 403；有效登录、注册和找回请求均需覆盖公网主机名经过内部桥接的场景。

## 实时进度与恢复点

更新器读取本机 Docker Engine 的拉取进度流，显示层下载、复用和解压阶段。百分比按目标发行镜像层计算；元数据和网络协议开销不计入，未知大小时显示不定进度。下载或解压出现实际增量会刷新活跃计时，重复相同事件不会掩盖停滞。断线最多尝试三次，每次先核对本机摘要缓存，已完成层可复用。拉取结束后再次核对固定摘要，未取得确认时保持原服务运行。凭据只在内存传给 Docker，不进入状态文件。

宿主事务日志记录已接受请求及切换阶段。重复点击复用同一操作编号；更新器重启后继续核对
已接受操作，不能因为原请求超过五分钟就丢掉正在执行的更新。Web 切换启用前先安装
`web_switch.py`、`install_web_switch.py` 和 `dyfire-web-route.service`：临时候选通过健康、
版本和既有数据库 schema 检查后接流量，原固定端口容器替换并验收后切回；旧请求退出后
清理候选。候选不运行数据库迁移或消息循环。详见 [Web 切换](web-handoff.md)。

完整更新前的快照包含数据库/WAL 的 SHA256、大小、schema、SQLite 完整性与外键校验清单。`python deploy/snapshot_inventory.py --verify` 默认只读，不删除；历史无清单副本标为 legacy，仍可保留恢复。清理必须显式传 `--apply`，没有自动备份或自动删除定时任务。宿主更新器需要同步 `updater.py`、`update_transaction.py`、`image_pull.py`、`snapshot_inventory.py` 及 Web 切换模块。失败恢复原镜像和部署记录，不回写旧数据库，以免重复发送已经提交的任务。
