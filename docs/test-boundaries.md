# 测试边界

`quality.yml` 是 PR 和 Release 共用的同提交门禁。完整收集 `tests/console`，
以及部署路径和 PostgreSQL profile 测试；新测试不必再手工加入文件清单。
CI 提供隔离 PostgreSQL、Redis、Chromium，使用测试数据库和假 SMTP。

根目录下的 `test_web_chat.py`、`test_tasks.py`、`test_stickers.py`、
`test_avatars.py` 是依赖抖音账号、真实平台或历史 CLI 的探针，不自动运行。
它们不应使用生产账号纳入常规 CI。历史 WZ 定时发送工作流及其结构测试已移除。
只导入函数的 `test_core_tasks_import.py` 仍保留。

前端 `*.test.js` 默认全部收集。启用浏览器验收时只连接隔离本地预览，
不得把真实发送或真实邮件作为烟雾测试。生产只做只读健康和版本检查。

发布工作流先调用同一个质量门禁，成功后才构建、推送摘要镜像并创建 Release。
镜像运行检查和漏洞扫描失败时不发布正式 Release。运行结果与源码提交由
GitHub Actions 保留，镜像带有构建来源和 SBOM；服务器不承担编译工作。
