# Web 更新切换

Web 更新时先启动一个临时候选实例，在 `127.0.0.1:18999` 验证 `/health/ready` 与 `/api/version`。
候选使用当前 Web 的数据、密钥和资源限制，仅连接现有应用网络，不继承 `spark-web` 的服务别名。
候选入口只检查数据库结构版本，不执行 `create_schema`，也不启动登录、发送或监控循环。
此路径仅适用于已经通过组件兼容性和数据库结构检查的 Web 更新。

确认候选健康后，成都源站的两份 Dyfire 专属 Nginx 配置切到候选实例；等待旧 Nginx workers
退出、已有请求结束后，再由更新器重建标准 `spark-web` 容器。新标准容器通过版本、健康和摘要检查后，
流量切回 `127.0.0.1:8899`，候选在连接排空后退出。最终 Compose 服务名称、端口和其他三个服务保持原结构。

宿主模块接口：

```python
from deploy import web_switch

if web_switch.enabled(ROOT):
    web_switch.switch(
        ROOT, target_image, target_version, operation_id,
        recreate=lambda image: compose(image, 'up', '-d', '--no-build', '--no-deps', 'spark-web'),
        ready=verify_canonical_web,
        publish=write_state,
        previous_image=old_image,
        previous_version=old_version,
        project=COMPOSE_PROJECT,
    )
```

`recreate` 必须只替换 Web；`ready` 必须核验标准端口的健康、版本及目标摘要。
安装记录只在 `switch` 成功返回后提交。任一阶段失败会恢复旧 Web 镜像及标准上游。
若进程中断，下一次调用先读取 `updates/web-handoff.json`，恢复未完成的切换，再继续新的操作。
长请求120秒内未结束时保留承载它的实例，不强杀。更新器空闲时调用
`web_switch.maintenance(ROOT, project=COMPOSE_PROJECT)`，只对 `complete/restored` 的切换记录
做一次排空检查；仍有旧连接则立即返回，不改变路由、不阻塞等待。排空后按操作标签清理候选，
并记录 `cleanup_complete`。这也覆盖切换已完成、尚未写下清理标志就崩溃的情况。

一次性安装由部署人员执行：

```sh
sudo python3 /opt/dyfire/deploy/install_web_switch.py --root /opt/dyfire
sudo python3 /opt/dyfire/deploy/install_web_switch.py --root /opt/dyfire --apply
```

安装器仅修改 `/etc/nginx/sites-enabled/dyfire.cn.conf` 和 `dyfire-origin-http.conf` 中明确的旧上游，
保留域名、来源IP限制、TLS和转发头；如果任一文件包含其他域名则拒绝执行。
它新增一个 `conf.d/dyfire-web-upstream.conf` 引用 `/opt/dyfire/deploy/web-upstream.conf`。
配置校验失败会恢复原文件；原配置文本保存在小型迁移记录中，不复制数据库或用户数据。

`dyfire-web-route.service` 是固定的 oneshot 系统服务：先 `nginx -t`，成功后 reload。
更新器仍只写 `/opt/dyfire`，无需扩大其 systemd 沙箱对 `/etc` 的写权限。
模块会等待新一代 Nginx workers 出现，再等待旧一代结束，避免 reload 命令返回过早造成连接中断。

本方案不修改香港桥接、不影响其他域名，也不重启 Docker、auth、worker 或 monitor。
临时候选沿用 Web 的256MiB限制；部署前应确认内存余量，并检查更新前后其他服务容器ID/启动时间。
