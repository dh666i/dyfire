# Dyfire

![Dyfire Spark Mark](spark_console/static/spark-mark.svg)

**Dyfire 是一个用于管理抖音好友续火任务的开源控制台。**它提供账号绑定、好友同步、定时任务、执行记录和管理员管理界面，并以独立服务处理网页、账号验证、任务执行和监控。

[![官网](https://img.shields.io/badge/官网-dyfire.cn-1f7a59)](https://dyfire.cn/)
[![Latest release](https://img.shields.io/github/v/release/dh666i/dyfire)](https://github.com/dh666i/dyfire/releases/latest)
[![Build and validation](https://github.com/dh666i/dyfire/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/dh666i/dyfire/actions/workflows/build.yml)
[![License: MIT](https://img.shields.io/github/license/dh666i/dyfire)](LICENSE)

## 功能

- 管理抖音账号、同步好友，并为已同步好友创建续火任务。
- 按计划执行任务，查看当日状态、历史记录和失败原因。
- 在网页中处理账号绑定与验证流程。
- 提供用户、订阅、兑换码、执行诊断和系统更新管理。
- 通过 GitHub Actions 验证代码并构建固定摘要容器镜像。

## 使用

- 访问[官方网站](https://dyfire.cn/)使用托管服务。
- 自行部署请从[部署与更新指南](deploy/README.md)开始。部署前先阅读[配置示例](.env.console.example)并为服务配置独立凭据。
- 查看当前变更与历史版本：[发行说明](https://github.com/dh666i/dyfire/releases)。

## 架构与构建

项目由 FastAPI 服务、React 前端和浏览器自动化服务组成。`spark-web` 提供 API 与前端；`spark-auth` 负责登录验证；`spark-worker` 执行任务；`spark-monitor` 汇总运行状态。服务共享受控的数据目录，线上更新使用经过验证的 GHCR 固定摘要镜像。

构建和完整测试以 Python 3.12、Node.js 22 为基线：

```sh
cd frontend
npm ci
npm run build
cd ..
python -m pytest tests/console tests/test_avatars.py tests/test_stickers.py tests/test_tasks.py tests/test_web_chat.py tests/test_deployment_paths.py tests/test_postgres_profile.py -q
```

本地 Docker 部署、运行时镜像和数据迁移细节见[部署指南](deploy/README.md)。GitHub Actions 会在合并和发布前执行静态检查、依赖安全扫描、浏览器验收及隔离测试。

## 安全与数据

账号登录资料属于敏感数据。自行部署时请限制主机与数据目录访问，使用 HTTPS，妥善保管密钥和数据库，并在提交前确认没有真实账号 Cookie、密码、令牌或生产配置。仓库中的环境变量示例仅供本地配置参考，不能直接作为生产凭据。

Dyfire 是独立开源项目，与字节跳动或抖音没有隶属关系。使用者需要自行确认其使用方式符合所在地法律、平台规则及账号持有人的授权。

发现安全问题请遵循 [SECURITY.md](SECURITY.md) 私下披露，不要在公开 Issue 或讨论中提交凭据、Cookie、用户数据或可利用细节。

## 参与贡献

欢迎通过 Issue 报告可复现的问题，或提交带有测试的 Pull Request。请先阅读[贡献指南](CONTRIBUTING.md)；任何会连接真实账号、执行真实发送或访问线上客户数据的测试，都不能放进常规 CI。

## 许可证

Dyfire 使用 [MIT License](LICENSE)。第三方依赖和组件的许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及随代码保留的许可文件。
