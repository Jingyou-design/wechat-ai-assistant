"""一键准备依赖、启动 Windows 读取器和 Docker 代理，并检查本机 MCP 通路。"""

import getpass
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import venv
from pathlib import Path
from urllib.request import getproxies

from app.config.bridge import prepare_token

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
LOG = ROOT / ".venv" / "mcp-reader.log"
TUNNEL = ROOT / "tools" / "tunnel-client" / "tunnel-client.exe"


def prepare_environment() -> Path:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("请使用 Python 3.12 启动；安装后重新双击 start_all.cmd。")
    if not PYTHON.exists():
        print("[1/5] 首次创建 Python 虚拟环境…", flush=True)
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
    digest = hashlib.sha256(
        (ROOT / "requirements.txt").read_bytes()
        + (ROOT / "requirements-mcp.txt").read_bytes()
    ).hexdigest()
    marker = ROOT / ".venv" / "wechat-mcp-deps.sha256"
    ready = (
        marker.exists()
        and marker.read_text(encoding="ascii") == digest
        and subprocess.run(
            [str(PYTHON), "-c", "import mcp, wechatauto, PIL, psutil"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )
    if not ready:
        print("[1/5] 安装或更新读取器依赖，首次需要联网，请稍候…", flush=True)
        subprocess.run(
            [str(PYTHON), "-m", "pip", "install", "-r", "requirements-mcp.txt"],
            cwd=ROOT,
            check=True,
        )
        marker.write_text(digest, encoding="ascii")
    else:
        print("[1/5] 依赖已就绪，跳过安装。", flush=True)
    return PYTHON


def docker_type(docker: str) -> str:
    result = subprocess.run(
        [docker, "info", "--format", "{{.OSType}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def prepare_voice():
    import exporter_core as core

    print("[1/5] 检查本地语音识别组件，首次可能下载约230MB模型…", flush=True)
    if not core.rust_silk_status()[0]:
        core.install_rust_silk(progress=print)
    if not core.local_asr_status()[0]:
        core.install_local_asr_model(progress=print)


def prepare_docker() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("未找到 Docker，请先安装 Docker Desktop，再重新启动。")
    print("[2/5] 检查 Docker Desktop…", flush=True)
    if not docker_type(docker):
        print("正在启动 Docker Desktop…", flush=True)
        subprocess.run(
            [docker, "desktop", "start", "--timeout", "120"], check=True, timeout=130
        )
    if docker_type(docker) != "linux":
        raise RuntimeError(
            "Docker 尚未就绪，请确认 Docker Desktop 使用 Linux containers。"
        )
    subprocess.run(
        [docker, "compose", "version"], check=True, stdout=subprocess.DEVNULL
    )
    return docker


def reader_running() -> bool:
    import psutil

    listeners = {
        item.pid
        for item in psutil.net_connections(kind="tcp")
        if item.status == "LISTEN" and item.laddr.port == 18765
    }
    if not listeners:
        return False
    for pid in listeners:
        try:
            process = psutil.Process(pid)
            arguments = process.cmdline()
            directory = Path(process.cwd())
        except (psutil.Error, OSError):
            raise RuntimeError(
                "18765端口已被占用，无法确认是本项目读取器，请手动检查。"
            ) from None
        if "--docker-reader" not in arguments or not any(
            (directory / argument).resolve() == ROOT / "mcp_server.py"
            for argument in arguments
            if argument.endswith("mcp_server.py")
        ):
            raise RuntimeError("18765端口被其他服务占用，请先停止它，再重新启动。")
    return True


def start_reader() -> subprocess.Popen | None:
    token = prepare_token(ROOT / ".env.docker")
    if reader_running():
        print("[3/5] Windows 读取器已运行，复用现有进程。", flush=True)
        return None
    print("[3/5] 启动 Windows 微信读取器…", flush=True)
    with LOG.open("a", encoding="utf-8") as output:
        return subprocess.Popen(
            [str(PYTHON), str(ROOT / "mcp_server.py"), "--docker-reader"],
            cwd=ROOT,
            env={**os.environ, "WECHAT_BRIDGE_TOKEN": token},
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def wait_reader(process: subprocess.Popen | None):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"读取器启动失败，详情见本机日志：{LOG}")
        try:
            with socket.create_connection(("127.0.0.1", 18765), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"等待读取器启动超时，详情见本机日志：{LOG}")


def launch(docker: str):
    process = start_reader()
    try:
        wait_reader(process)
        print("[4/5] 启动 Docker MCP 接入层并检查连接…", flush=True)
        subprocess.run(
            [
                docker,
                "compose",
                "up",
                "-d",
                "--build",
                "--wait",
                "--wait-timeout",
                "60",
            ],
            cwd=ROOT,
            check=True,
        )
        result = subprocess.run(
            [str(PYTHON), "tools/check_mcp.py", "--docker"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            timeout=45,
        )
        if result.returncode:
            raise RuntimeError(
                "MCP连接检查失败，请检查读取器日志和Docker状态；也请确认.env.docker令牌没有被更改。"
            )
    except (OSError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt):
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        raise
    print("本机读取器和Docker已就绪，MCP连接检查通过。", flush=True)


def tunnel_status() -> dict:
    result = subprocess.run(
        [str(TUNNEL), "runtimes", "status", "wechat-assistant", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=15,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            "无法检查隧道状态，请确认已安装官方tunnel-client。"
        ) from None


def start_tunnel():
    print("[5/5] 检查官方隧道…", flush=True)
    if not TUNNEL.exists():
        raise RuntimeError(
            "本机服务已启动，但未安装官方隧道客户端。请按CHATGPT_SETUP.md完成首次配置。"
        )
    status = tunnel_status()
    if not status.get("tunnel_id") or not status.get("profile_dir"):
        raise RuntimeError(
            "本机服务已启动，但尚未配置隧道。请按CHATGPT_SETUP.md完成首次连接。"
        )
    if Path(status["profile_dir"]).resolve() != ROOT / ".tunnel-profiles":
        raise RuntimeError("同名隧道连接属于其他项目目录，请核对配置，不自动覆盖。")
    if all(status.get(key) for key in ("process_running", "healthy", "ready")):
        print("官方隧道已就绪，复用现有后台连接。", flush=True)
        return
    key = os.environ.get("CONTROL_PLANE_API_KEY")
    if not key:
        if not sys.stdin.isatty():
            raise RuntimeError(
                "本机服务已启动，但隧道已停止且缺少运行密钥。请双击start_all.cmd，在窗口中隐藏输入隧道运行密钥；不要把密钥发到聊天。"
            )
        key = getpass.getpass("Tunnel runtime key (hidden, not saved): ").strip()
    if not key:
        raise RuntimeError("未提供运行密钥，隧道未连接。本机读取器和Docker保持运行。")
    env = {**os.environ, "CONTROL_PLANE_API_KEY": key}
    for scheme, proxy in getproxies().items():
        if scheme in {"http", "https"}:
            env.setdefault(f"{scheme.upper()}_PROXY", proxy)
    env["NO_PROXY"] = ",".join(
        filter(None, [env.get("NO_PROXY"), "127.0.0.1", "localhost"])
    )
    result = subprocess.run(
        [
            str(TUNNEL),
            "runtimes",
            "connect",
            "--alias",
            "wechat-assistant",
            "--tunnel-id",
            status["tunnel_id"],
            "--profile-dir",
            str(ROOT / ".tunnel-profiles"),
            "--profile",
            "wechat-docker",
            "--runtime-api-key",
            "env:CONTROL_PLANE_API_KEY",
            "--mcp-server-url",
            "http://127.0.0.1:18767/mcp",
            "--json",
        ],
        env=env,
        capture_output=True,
        check=False,
        timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError(
            "本机服务已启动，但隧道重连失败。请检查运行密钥权限和网络代理；不输出可能含敏感信息的完整客户端响应。"
        )
    status = tunnel_status()
    if not all(status.get(key) for key in ("process_running", "healthy", "ready")):
        raise RuntimeError(
            "隧道尚未就绪，请检查官方客户端诊断，不能视为ChatGPT已连接。"
        )
    print("官方隧道已在后台连接并通过就绪检查。", flush=True)


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("此启动入口仅支持 Windows + Docker Desktop。")
    python = prepare_environment()
    if Path(sys.executable).resolve() != python.resolve():
        return subprocess.call([str(python), "-m", "tools.start_all"], cwd=ROOT)
    prepare_voice()
    launch(prepare_docker())
    start_tunnel()
    print(
        "读取器、Docker和隧道已就绪，可关闭启动窗口。请保持微信登录，ChatGPT端实际调用仍需验证。",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"\n启动失败：{error}", file=sys.stderr, flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
