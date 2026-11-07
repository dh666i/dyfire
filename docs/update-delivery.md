# 更新体积与发布约定

0.3.42 的 OCI 压缩层总计约 384.8 MiB，主要浏览器运行层约 334 MiB。
Docker 显示的约 1.62 GB 是展开后的镜像体积；四个容器使用同一摘要，共用本机镜像层。
它不等于下载四次，也不等于每次更新都会传输 1.62 GB。

普通应用更新复用 Ubuntu/Python/Chromium 层，字体单独放在应用代码之前的层。
后台已有的约 18 MiB 静态资源也独立于 Python 源码层，保留原 URL 和兼容资源；
修改 Python 不再重复拉取这整份资源。
运行依赖及字体没有变化时，客户端只拉缺少的应用层。基础系统每个 ISO 周刷新一次，
依赖文件变化也会触发重建。该维护规则保留补丁更新，不以永久冻结浏览器运行层换速度。
第一次采用新运行层、跨周系统刷新、浏览器/依赖升级或本机缓存被清理后，仍可能有大下载。

GitHub Actions 使用统一缓存 scope，并读取旧缓存作为过渡；正式发行把构建缓存保存到
现有 GHCR 的 buildcache 标签，避免 GitHub 临时缓存被回收后重新安装整个运行层。
PR 仅有镜像读取权限，只有发行工作流更新持久缓存，不增加服务器或用户凭据。
CI 另构建不发布的代码修改探针，
断言运行层、字体和未改变的应用层均复用，保留 `image-layer-reuse.json` 作为证据。
服务器仍只拉固定摘要镜像，不运行 npm、pip、apt 或编译源码。

管理员检查更新时显示压缩镜像体积、可复用层数和本次预计下载上限。
估算用目标 OCI 层与当前运行镜像比较；本机还缓存其他版本时实际可更小。
估算不可用时不显示假数字，也不阻塞正常更新。

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
