# 微信助手 MCP：给 Agent 的部署说明

目标：在用户自己的 Windows 电脑上拉取项目，安装依赖和本地语音识别组件，启动 Windows 读取器与 Docker MCP 接入层，尽可能完成 ChatGPT 连接。**先完成所有能独立完成的步骤，再一次性向用户说明剩余操作。不要把本机代理启动成功当作 ChatGPT 端验证成功。**

## 1. 部署范围与交互原则

- 源码仓库：`https://github.com/Jingyou-design/wechat-ai-assistant.git`。目前为私有仓库，部署账号必须已有读取权限；不要修改仓库可见性来绕过权限。
- 支持环境：Windows、Python 3.12、Docker Desktop 的 Linux containers、本机 Windows 微信。Linux Docker 中只运行 MCP 代理，无法独立读取微信。
- 普通用户入口：项目根目录 `start_all.cmd`。Agent 使用后文 Python 命令，不运行带 `pause` 的交互入口。
- 保留已有源码修改、Docker 数据、微信文件、账号、隧道和密钥。不要使用 `git reset --hard`、清理工作区、删除 Docker 数据或擅自切换微信账号。
- 账号登录、MFA、UAC、需要重启系统、服务商授权或密钥输入应交给用户。遇到这些条件时，记录准确阻塞点，继续完成不依赖该步骤的工作，最后给出具体操作。
- 密钥不进入聊天、命令参数、Git、镜像或诊断输出。不要打印 `.env.docker`、完整 `docker compose config`、隧道配置中的实际凭据或聊天正文。

## 2. 检查现有环境并获取源码

选择用户指定的目录，未指定时在当前工作区创建项目目录；不要假设开发者电脑的 `D:\myself` 存在。

```powershell
git --version
py -3.12 --version
docker --version
docker compose version
```

已有源码时先运行 `git status --short`、`git remote get-url origin`，确认是目标项目。工作区干净且远端正确时可 `git pull --ff-only`；有本地修改则先保留，不能自动覆盖或重置。

新目录使用已有 Git 凭据，禁止把访问令牌嵌入 URL：

```powershell
git -c credential.interactive=never clone https://github.com/Jingyou-design/wechat-ai-assistant.git
Set-Location -LiteralPath .\wechat-ai-assistant
```

若失败是私有仓库权限或账号未登录，记录错误并在最后请用户完成 GitHub 授权/获得仓库读取权限。依然可以检查和准备 Python、Docker Desktop。不要把认证失败误报成仓库不存在。

缺少软件时，可使用已授权的软件安装渠道或官方安装器安装 Git、Python 3.12、Docker Desktop；核对实际可用的安装命令，不盲目运行网上脚本。安装产生的 UAC、WSL/虚拟化设置或系统重启需要用户配合。安装完成后重新检查命令是否可用。

## 3. 自动安装并启动本机服务

在项目根目录执行；无需手动创建虚拟环境，也无需先手工运行 Docker Compose：

```powershell
py -3.12 -m tools.start_all
```

已有项目虚拟环境时可以直接执行：

```powershell
.\.venv\Scripts\python.exe -m tools.start_all
```

该入口会：

1. 创建 `.venv`、安装 `requirements-mcp.txt`；依赖文件不变且关键模块可导入时跳过安装。
2. 检查并按需安装 rust-silk 解码器和 SenseVoice 本地识别模型；模型约230MB，首次联网下载并校验，之后复用公共模型文件。
3. 检查并按需启动 Docker Desktop；确认使用 Linux containers。
4. 创建或复用本机 `.env.docker` 访问令牌，启动 Windows 读取器；已有本项目读取器时复用，其他服务占用端口时不擅自结束它。
5. 构建、启动 Docker MCP 代理，等待容器健康，并通过真实 MCP 协议检查工具发现与无效任务拒绝。

首次下载或构建可能耗时较长，按输出继续等待，不能仅因超过一次工具等待时间就判断失败。不要同时启动第二次安装或重复下载。

读取器启动后在后台运行，关闭启动终端不会主动停止它。后台日志为 `.venv/mcp-reader.log`。入口失败时只清理本次新启动的读取器，不停止原本已在运行的读取器。

如果用户尚未登录微信，仍先完成依赖、容器和 MCP 协议检查；最后请用户登录本机微信，再验证读取权限。启动检查不会自动导出或上传聊天。

## 4. 验证本机链路

```powershell
docker compose ps
.\.venv\Scripts\python.exe tools\check_mcp.py --docker
```

通过条件：检查命令退出码为0，输出 `readonly_tools: 6` 和 `scope_guard: passed`。六个工具为：

`search_sessions`、`start_task`、`read_messages`、`expand_message`、`read_image`、`transcribe_voice`。

本机 MCP 地址是 `http://127.0.0.1:18767/mcp`。Windows 读取器监听18765并要求随机令牌，Docker 在内部添加令牌；不要把18765直接作为无需认证的 ChatGPT 入口。

容器健康仅说明代理进程正常。上述协议检查也不代表微信已登录，更不代表 ChatGPT 已成功调用。

仅在用户授权了测试会话后，才执行最小真实数据验证：查找那个会话，创建限定范围任务，读取少量消息。不自动改读其他联系人，不为测试读取全部历史，不在日志或报告复制聊天内容。图片、语音按提示词流程取得用户确认后按需读取。

转写优先使用微信已有文字，否则用本机 SenseVoice 处理语音；只返回文字，不上传音频。临时音频、图片、数据库和密钥随读取清理；MCP 不使用导出器的长期转写缓存。识别模型本身长期保留，返回给 ChatGPT 的资料进入其聊天上下文。

## 5. 复用或配置官方隧道

先阅读最新官方说明，不凭旧界面猜测参数：

- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [tunnel-client 官方发行版](https://github.com/openai/tunnel-client/releases/latest)
- [连接并测试 MCP](https://developers.openai.com/plugins/deploy/connect-chatgpt)

先检查用户已有隧道客户端、配置和运行实例。已经就绪的实例保持运行；不能为了重配而结束持有内存密钥的现有客户端。不得复制开发者自己的隧道ID或运行密钥给新用户使用。

尚未安装客户端时，可下载适用的官方 Windows 发行版并校验官方提供的校验值，放入项目 `tools/tunnel-client`，再检查 `--help`。下载目录和 `.tunnel-profiles` 已排除出 Git。

隧道应关联目标用户的 Platform 组织和目标 ChatGPT 工作区，运行账号需要 Tunnels Read + Use 权限。**目标 MCP URL 使用 `http://127.0.0.1:18767/mcp`。** 现有 `connect_chatgpt.ps1` 默认面向旧的 stdio 路径；不要直接照抄它，把它当作 Docker 接入层配置完成。

使用当前 `tunnel-client` 帮助和官方流程配置、诊断并启动，运行密钥使用环境变量引用。让用户在本机隐藏输入或受支持的安全界面输入，不要求把密钥发到聊天；不要把实际密钥作为 CLI 参数。不要更改系统 PowerShell 执行策略来启动脚本。

若缺少账号权限、隧道ID或密钥，完成本机部署和诊断后，在最终说明中列出用户需在 Platform 隧道设置完成的具体字段和步骤。用户补齐后继续同一部署，不重新拉取、安装或创建重复隧道。

验证客户端健康、就绪和持续轮询，并确认配置实际指向18767。仅轮询正常还不能证明 ChatGPT 工具调用成功。

## 6. 配置 ChatGPT 并验证

若具备受支持的浏览器操作能力且用户已授权配置，复用登录会话继续完成可操作步骤；否则在本机与隧道工作完成后交给用户：

1. 打开 `https://chatgpt.com/plugins`，查找已有微信助手连接。
2. 已有连接：刷新工具列表，确认包含 `read_image` 和 `transcribe_voice`；不要创建重复连接。
3. 没有连接：添加自定义 MCP 服务器，连接方式选择 Tunnel，选定用户自己的隧道，身份验证按实际服务配置。当前架构由受控隧道接入，Docker代理内部负责桥接令牌。
4. 需要用户完成的账号登录、授权和确认步骤由用户完成。
5. 在新对话中 `@微信助手`，用用户授权的会话做一次最小读取。目标是 Projects 时，要在目标项目内再验证，普通聊天成功不等于 Projects 已验证。

AI流程由 `app/prompt/wechat_assistant.md` 提供：先读文字和元数据，结合时间和语境提出任务、方案及所需图片/语音数量；用户确认后取附件并处理，最后交付审核。图片数量不固定为两张，工具端不做语义筛选或额外审批系统。修改提示词后需重启读取器并刷新 ChatGPT 连接。

## 7. 常见失败的处理顺序

- **依赖安装失败**：保留安装错误，检查 Python3.12、网络和包安装环境。失败不会写入安装完成标记，修复后重跑统一入口。
- **Docker 未就绪**：查看 Desktop/WSL 状态；不要删除镜像或卷来碰运气。需要用户重启、UAC或启用虚拟化时说明具体操作。
- **18765被占用**：核对进程命令和工作目录。非本项目进程不自动结束。更新代码后仍运行旧读取器时，确认是本项目进程后再重启；不要结束隧道客户端。
- **协议检查失败**：检查 `.venv/mcp-reader.log` 和 `docker compose logs --tail 30 mcp`，输出前过滤凭据；核对桥接令牌和地址，切勿打印完整配置。
- **语音识别组件缺失**：重跑统一入口，它会补齐缺少的组件。模型下载失败时说明网络错误，不能伪造转写；缓存缺失时请用户在微信播放目标语音后重试。
- **隧道就绪但 ChatGPT 无工具**：核对工作区关联、已有连接和工具刷新，不把本地检查成功当作远端注册成功。

更新代码前保存本地修改；更新后可运行相关测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

项目已知限制：面向本机已缓存资料，不会发送微信；微信版本变化可能导致读取不兼容。引用消息中的原始XML及敏感字段过滤仍需专项修复和回归，不应把当前版本宣称为已完成生产安全审查的公开服务。

## 8. 最终交付格式

如实报告以下检查点，不重复未变化的日志：

- 源码目录和实际提交版本。
- 依赖与语音组件：已就绪或准确错误。
- Windows读取器、Docker容器、MCP协议：分别已验证或未验证。
- 隧道：已就绪、未配置或具体阻塞原因。
- ChatGPT及目标Projects：实际调用已验证或尚未验证。
- 用户剩余操作：只列 Agent 确实不能完成的步骤，写清页面、字段、操作及完成后如何复测；没有剩余操作就明确说明。

不得把“已生成安装文档”“进程已启动”“容器健康”“隧道轮询正常”任何一项单独当作端到端成功。
