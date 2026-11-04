"""一键准备依赖、启动 Windows 读取器和 Docker 代理，并检查本机 MCP 通路。"""

import hashlib
import io
import os
import shutil
import socket
import subprocess
import sys
import time
import venv
from pathlib import Path

from tools.start_docker_reader import prepare_token

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
LOG = ROOT / ".venv" / "mcp-reader.log"


def prepare_environment() -> Path:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("请使用 Python 3.12 启动；安装后重新双击 start_all.cmd。")
    if not PYTHON.exists():
        print("[1/4] 首次创建 Python 虚拟环境…", flush=True)
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
        print("[1/4] 安装或更新读取器依赖，首次需要联网，请稍候…", flush=True)
        subprocess.run(
            [str(PYTHON), "-m", "pip", "install", "-r", "requirements-mcp.txt"],
            cwd=ROOT,
            check=True,
        )
        marker.write_text(digest, encoding="ascii")
    else:
        print("[1/4] 依赖已就绪，跳过安装。", flush=True)
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

    print("[1/4] 检查本地语音识别组件，首次可能下载约230MB模型…", flush=True)
    if not core.rust_silk_status()[0]:
        core.install_rust_silk(progress=print)
    if not core.local_asr_status()[0]:
        core.install_local_asr_model(progress=print)


def prepare_docker() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("未找到 Docker，请先安装 Docker Desktop，再重新启动。")
    print("[2/4] 检查 Docker Desktop…", flush=True)
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
        print("[3/4] Windows 读取器已运行，复用现有进程。", flush=True)
        return None
    print("[3/4] 启动 Windows 微信读取器…", flush=True)
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
        print("[4/4] 启动 Docker MCP 接入层并检查连接…", flush=True)
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
    print("\n本机服务启动成功，MCP连接检查通过。请保持 Windows 微信登录。", flush=True)
    print(
        "服务在后台运行，此窗口可以关闭。官方隧道继续使用原有配置，ChatGPT端连接不包含在本次检查中。",
        flush=True,
    )


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("此启动入口仅支持 Windows + Docker Desktop。")
    python = prepare_environment()
    if Path(sys.executable).resolve() != python.resolve():
        return subprocess.call([str(python), "-m", "tools.start_all"], cwd=ROOT)
    prepare_voice()
    launch(prepare_docker())
    return 0


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"\n启动失败：{error}", file=sys.stderr, flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
