# 现有站点的网页更新

主机服务：`dyfire-web-updater`，目录 `/opt/dyfire-web-updater`。
私有仓库使用单仓库只读部署密钥，GitHub SSH 使用官方 443 端口。
Web 只可写请求子目录，不能读取私钥或使用 Docker socket。

发布步骤：同步 VERSION，执行 frontend 的 npm run build，提交源码与 frontend/dist，验证后发布 vX.Y.Z 标签并推送。正式标签不得移动。
站内检查更新读取正式标签，不会把 main 的每次提交当成版本。

只允许前端、Web 路由、模板和静态文件更新；执行服务、数据库模型和依赖变更需要完整发布方案。
镜像在旧服务运行时准备，随后仅替换 spark-web。网页有短暂切换时间，不承诺零停机。
失败恢复原 Web 镜像和配置，不还原数据库，以免重放已完成的发送任务。
备份位于主机更新目录的 backups。更新器程序本身由主机维护，不自动执行发行包内的部署脚本。

诊断：systemctl status dyfire-web-updater；journalctl -u dyfire-web-updater。
