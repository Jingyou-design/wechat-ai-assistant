# Docker 运行微信 MCP 入口

当前打包方式：**Windows 负责实时读取微信，Docker 运行 MCP 反向代理入口**。任务范围、分页和读取预算仍由 Windows MCP 服务统一执行。镜像里不包含微信客户端、读取库或聊天数据库，不能独立部署到 Linux 后直接读取微信。

读取库依赖 Windows 的进程和桌面环境，直接把现有 requirements 放入 Linux 镜像无法运行。

## 启动

推荐双击项目根目录的 **`start_all.cmd`**，统一启动，无需分别打开读取器和 Docker 终端。

- 前置条件：安装 Python 3.12 和 Docker Desktop，并登录 Windows 微信。
- 首次自动创建 `.venv` 并安装 `requirements-mcp.txt`；以后依赖文件不变且关键模块可导入时跳过安装。
- 按需安装本地语音解码器和 SenseVoice 识别模型，首次模型下载约230MB，之后复用。
- 自动检查并按需启动 Docker Desktop，启动 Windows 读取器，再运行 Docker Compose 构建/启动 MCP 接入层。
- 已运行的本项目读取器会复用；端口被其他服务占用时明确报错，不停止其他进程。
- 通过真实 MCP 协议检查工具发现和范围保护，再检查已配置的官方隧道；停止时尝试后台重连，缺少运行密钥时在本机隐藏输入。全部就绪后可关闭启动窗口。
- 读取器后台日志位于 `.venv/mcp-reader.log`。失败时只清理本次新启动的读取器，不停止原本已经运行的服务。
- 官方隧道和 ChatGPT 首次连接仍按下方说明配置；本入口复用已有隧道，不保存运行密钥正文，也不代表已验证 ChatGPT 端调用。非交互的Agent缺少密钥时会收到明确错误，本机服务保持运行，最后请用户双击唯一入口输入密钥。

Agent 或终端中使用同一入口：`.venv\Scripts\python.exe -m tools.start_all`。项目根目录只保留 `start_all.cmd` 一个双击启动文件。

容器入口：`http://127.0.0.1:18767/mcp`。

需要让 Agent 从源码自行部署时，直接提供 [AGENT_DEPLOY.md](AGENT_DEPLOY.md)。它包含自动部署、验证及用户交接步骤。

```powershell
.venv\Scripts\python.exe tools\check_mcp.py --docker
.venv\Scripts\python.exe tools\check_mcp.py --docker --wechat
```

第一条验证 MCP 工具发现和无效任务拒绝；第二条额外读取“工信微报”最近一星期最多3条，只输出统计，不上传到外部服务。

停止容器：`docker compose down`。读取器为后台进程，停止前必须核对命令和工作目录确属本项目的 `mcp_server.py --docker-reader`，不能按进程名批量结束 Python。

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
