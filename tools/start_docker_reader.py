"""生成本机令牌文件并启动 Windows 读取器，不修改 PowerShell 执行策略。"""

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare_token(path: Path) -> str:
    if not path.exists():
        with path.open("x", encoding="utf-8") as output:
            output.write(f"WECHAT_BRIDGE_TOKEN={secrets.token_urlsafe(32)}\n")
    match = re.fullmatch(
        r"WECHAT_BRIDGE_TOKEN=([A-Za-z0-9_-]{43,128})",
        path.read_text(encoding="utf-8").strip(),
    )
    if match is None:
        raise ValueError("Invalid .env.docker; expected one WECHAT_BRIDGE_TOKEN line.")
    return match[1]


if __name__ == "__main__":
    env = {**os.environ, "WECHAT_BRIDGE_TOKEN": prepare_token(ROOT / ".env.docker")}
    print("Windows WeChat reader is starting. Keep this terminal open.", flush=True)
    print("In another terminal run: docker compose up -d --build", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "mcp_server.py"), "--docker-reader"],
            env=env,
            check=False,
        )
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        sys.exit(130)
