"""stdio 默认供本机客户端/官方隧道使用；HTTP 仅绑定回环地址。"""

import argparse
import io
import logging
import os
import sys

import uvicorn
from wechatauto import WxParam

from app.api.mcp import bridge_app, server

WxParam.ENABLE_FILE_LOGGER = False
logging.getLogger("wechatauto").disabled = True
logging.getLogger().setLevel(logging.WARNING)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="微信助手只读 MCP 服务")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument(
        "--http", action="store_true", help="使用 http://127.0.0.1:18765/mcp"
    )
    transport.add_argument(
        "--docker-reader",
        action="store_true",
        help="绑定0.0.0.0:18765供Docker连接，强制WECHAT_BRIDGE_TOKEN鉴权",
    )
    args = parser.parse_args()
    # 即便第三方库打印普通诊断，也不能污染 stdio 的 JSON-RPC。
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")
    if args.docker_reader:
        uvicorn.run(
            bridge_app(os.environ.get("WECHAT_BRIDGE_TOKEN", "")),
            host="0.0.0.0",
            port=18765,
            access_log=False,
            log_level="warning",
        )
    elif args.http:
        server.run(transport="streamable-http", host="127.0.0.1", port=18765)
    else:
        server.run()
