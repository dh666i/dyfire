# 测试边界

`quality.yml` 是 PR 与 main 候选共用的同提交门禁，Release 只接受已通过它的 main 候选。完整收集 `tests/console`，
以及部署路径和 PostgreSQL profile 测试；新测试不必再手工加入文件清单。
CI 提供隔离 PostgreSQL、Redis、Chromium，使用测试数据库和假 SMTP。四个测试分片各自拥有
服务与工作目录，汇总时核对完整测试清单，确保每个收集的用例恰好执行一次。

根目录下的 `test_web_chat.py`、`test_tasks.py`、`test_stickers.py`、
`test_avatars.py` 是依赖抖音账号、真实平台或历史 CLI 的探针，不自动运行。
它们不应使用生产账号纳入常规 CI。历史 WZ 定时发送工作流及其结构测试已移除。
只导入函数的 `test_core_tasks_import.py` 仍保留。

前端 `*.test.js` 默认全部收集。启用浏览器验收时只连接隔离本地预览，
不得把真实发送或真实邮件作为烟雾测试。生产只做只读健康和版本检查。

main push 同时运行质量门禁和 Web/browser 候选构建。候选必须通过容器运行检查、漏洞
扫描与锁定基础层复用检查；全部门禁成功后才生成 exact-SHA 的候选清单。版本标签工作流
校验其仓库、分支、push 事件、工作流、提交、版本和摘要，复制同一 OCI index 到版本标签并
创建 Release，不重新构建另一份镜像。缺失候选或任一门禁失败都不发布正式 Release。
运行结果与源码提交由 GitHub Actions 保留，镜像带有构建来源和 SBOM；服务器不承担编译工作。
