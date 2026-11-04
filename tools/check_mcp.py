"""协议自检；--wechat 额外读取工信微报最近一星期最多3条，只输出统计。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import TextContent

ROOT = Path(__file__).resolve().parents[1]


async def call(client: Client, name: str, request: dict) -> dict:
    result = await client.call_tool(name, {"request": request})
    if result.is_error:
        raise RuntimeError(
            f"{name} failed; check WeChat login and local data directory."
        )
    block = result.content[0]
    if not isinstance(block, TextContent):
        raise TypeError("Expected JSON text result")
    return json.loads(block.text)


async def check(url: str | None, wechat: bool):
    target = (
        url
        if url
        else StdioServerParameters(
            command=sys.executable, args=[str(ROOT / "mcp_server.py")], cwd=ROOT
        )
    )
    async with Client(target) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "search_sessions",
            "start_task",
            "read_messages",
            "expand_message",
        }
        assert all(
            tool.annotations and tool.annotations.read_only_hint for tool in tools.tools
        )
        invalid = await client.call_tool(
            "read_messages",
            {"request": {"task_id": "invalid", "session_id": "invalid"}},
        )
        assert invalid.is_error
        summary = {
            "transport": url or "stdio",
            "readonly_tools": len(tools.tools),
            "scope_guard": "passed",
        }
        if wechat:
            found = await call(
                client, "search_sessions", {"query": "工信微报", "limit": 2}
            )
            sessions = [
                row
                for row in found["sessions"]
                if row["name"] == "工信微报" and row["chat_type"] == "公众号"
            ]
            if len(sessions) != 1:
                raise RuntimeError(
                    "Public-account test session is missing or ambiguous; no messages read."
                )
            session_id = sessions[0]["session_id"]
            task = await call(
                client,
                "start_task",
                {"session_ids": [session_id], "time_range": "最近一星期"},
            )
            page = await call(
                client,
                "read_messages",
                {"task_id": task["task_id"], "session_id": session_id, "limit": 3},
            )
            assert len(page["messages"]) <= 3
            summary.update(
                messages_read=len(page["messages"]),
                has_more=page["has_more"],
                remaining_reads=page["remaining_reads"],
                remaining_chars=page["remaining_chars"],
                article_urls=sum(
                    "mp.weixin.qq.com" in row["content"] for row in page["messages"]
                ),
            )
        print(json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument(
        "--http", dest="url", action="store_const", const="http://127.0.0.1:18765/mcp"
    )
    transport.add_argument(
        "--docker", dest="url", action="store_const", const="http://127.0.0.1:18767/mcp"
    )
    parser.add_argument("--wechat", action="store_true")
    args = parser.parse_args()
    asyncio.run(check(args.url, args.wechat))
