"""本机 Windows 读取器与 Docker 代理之间的访问令牌。"""

import re
import secrets
from pathlib import Path


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
