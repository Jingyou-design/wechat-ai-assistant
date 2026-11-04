import hmac
import re
from contextlib import contextmanager
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.types import Image
from mcp.types import ImageContent, TextContent, ToolAnnotations
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.schema import (
    DetailRequest,
    ImageRequest,
    ReadRequest,
    SearchRequest,
    TaskRequest,
)
from app.workflow.context import ContextStore

INSTRUCTIONS = (
    Path(__file__).resolve().parents[1] / "prompt" / "wechat_assistant.md"
).read_text(encoding="utf-8")

server = MCPServer(
    "微信助手", instructions=INSTRUCTIONS, version="0.4.0", log_level="WARNING"
)
store = ContextStore()
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)


class BridgeAuth:
    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.authorization = b"Bearer " + token.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            authorization = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(authorization, self.authorization):
                await JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


def bridge_app(token: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token):
        raise ValueError(
            "Docker 读取模式需要43至128位随机URL安全令牌，请运行start_docker_reader.cmd生成配置。"
        )
    application = server.streamable_http_app()
    application.add_middleware(BridgeAuth, token=token)
    return application


@contextmanager
def tool_errors():
    try:
        yield
    except (ValueError, RuntimeError) as error:
        raise ToolError(str(error)) from None


@server.tool(annotations=READ_ONLY, structured_output=False)
def search_sessions(request: SearchRequest) -> str:
    """按名称查找最近会话，最多20个，只返回会话元数据，不返回聊天正文。"""
    with tool_errors():
        return store.search(request).model_dump_json()


@server.tool(annotations=READ_ONLY, structured_output=False)
def start_task(request: TaskRequest) -> str:
    """限定用户指定的1至3个会话及时间范围，默认最近一天；创建30分钟有效任务，6次文字读取/18000文本字符。图片初始未获准读取，先分析文字并与用户确认任务和图片计划。禁止自动重建绕过预算。"""
    with tool_errors():
        return store.start(request).model_dump_json()


@server.tool(annotations=READ_ONLY, structured_output=False)
def read_messages(request: ReadRequest) -> str:
    """读取所选会话最新一页，默认20条、最多30条/6000字符，每条正文最多1500字符。用返回的next_cursor读取更早消息，正文标为truncated时可展开。"""
    with tool_errors():
        return store.read(request).model_dump_json()


@server.tool(annotations=READ_ONLY, structured_output=False)
def expand_message(request: DetailRequest) -> str:
    """按message_id分段展开已读消息，最多4000字符，沿用任务总预算；offset用上次返回的next_offset。"""
    with tool_errors():
        return store.detail(request).model_dump_json()


@server.tool(annotations=READ_ONLY, structured_output=False)
def read_image(request: ImageRequest) -> list[TextContent | ImageContent]:
    """按message_id获取本任务已读的一张图片，返回真实图片内容和来源信息。先按提示词与用户确认任务及所需图片数量再调用，张数不设固定上限，不占文字读取次数。最长边1280像素、每张最大512KiB；不能指定文件路径，缩略图或小字可能不清晰。"""
    with tool_errors():
        metadata, data = store.image(request)
        return [
            TextContent(type="text", text=metadata.model_dump_json()),
            Image(data=data, format="jpeg").to_image_content(),
        ]


@server.tool(annotations=READ_ONLY, structured_output=False)
def transcribe_voice(request: DetailRequest) -> str:
    """按已读message_id获取一条语音的文字，优先微信已有转写，否则本地SenseVoice识别，不上传音频。按提示词先确认任务和所需语音再调用；计入文字读取预算，最多4000字符，next_offset用于继续获取。source标记微信或本地识别，结果可能有识别错误。"""
    with tool_errors():
        return store.voice(request).model_dump_json()
