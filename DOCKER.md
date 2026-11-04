# Docker 运行微信 MCP 入口

当前打包方式：**Windows 负责实时读取微信，Docker 运行 MCP 反向代理入口**。任务范围、分页和读取预算仍由 Windows MCP 服务统一执行。镜像里不包含微信客户端、读取库或聊天数据库，不能独立部署到 Linux 后直接读取微信。

读取库依赖 Windows 的进程和桌面环境，直接把现有 requirements 放入 Linux 镜像无法运行。

## 启动

1. 启动 Docker Desktop，使用 Linux containers；保持 Windows 微信登录。
2. 在项目目录双击 `start_docker_reader.cmd`，保持该窗口打开。它首次生成 `.env.docker` 随机访问令牌，然后启动鉴权读取器。不要同时运行原来的 `start_mcp.cmd`，两者都使用18765端口。
3. 在另一个终端进入项目目录，运行：

```powershell
docker compose up -d --build
```

容器入口：`http://127.0.0.1:18767/mcp`。

```powershell
.venv\Scripts\python.exe tools\check_mcp.py --docker
.venv\Scripts\python.exe tools\check_mcp.py --docker --wechat
```

第一条验证 MCP 工具发现和无效任务拒绝；第二条额外读取“工信微报”最近一星期最多3条，只输出统计，不上传到外部服务。

停止容器：`docker compose down`。在读取器窗口按 Ctrl+C 停止 Windows 进程。

## 连接与边界

- Windows 读取器以 `--docker-reader` 模式监听 `0.0.0.0:18765`，必须带正确的 Bearer 令牌才能访问。默认 stdio 和原有本机HTTP模式不变。
- Docker 通过 `host.docker.internal:18765` 访问读取器，代理在内部注入令牌。容器对宿主机只发布 `127.0.0.1:18767`，不直接向局域网或公网开放。
- Docker 构建上下文仅允许 Dockerfile、许可证和 Nginx 配置。导出结果、数据库、密钥、日志、虚拟环境都不会进入镜像。
- 基础镜像使用 Docker 官方镜像的 ECR 公共分发源，并固定已验证的镜像摘要；避免本机 Docker Hub 网络异常影响构建。
- `.env.docker` 已被 Git 和 Docker 构建排除。不要提交、分享或输出 `docker compose config` 的完整结果；完整配置可能包含令牌。
- 容器健康检查仅表示代理进程正常；Windows读取器和真实消息读取是否可用，以 `check_mcp.py --docker` 的结果为准。
- 当前配置针对同一台 Windows 上的 Docker Desktop。它不代表远程Linux服务器已经能访问你的本机微信。

## 接入 ChatGPT

仍需按 [CHATGPT_SETUP.md](CHATGPT_SETUP.md) 配置官方 Secure MCP Tunnel。Docker 本身不会让 localhost 变成 ChatGPT 可以访问的远程服务。

配置隧道时可将 MCP HTTP 目标设为 `http://127.0.0.1:18767/mcp`，隧道客户端运行在这台 Windows 电脑上。此前使用 stdio 的隧道配置不需要改成 Docker 模式，也不能将带鉴权的18765端口直接当成无需认证的入口。

## GitHub

[Jingyou-design/wechat-ai-assistant](https://github.com/Jingyou-design/wechat-ai-assistant) 是个人私有仓库，保留原项目许可证和来源说明。聊天导出、访问令牌、解密数据库及本机配置均已被 Git 排除。

部署参考：[Docker Desktop 宿主机网络](https://docs.docker.com/desktop/features/networking/networking-how-tos/)。
