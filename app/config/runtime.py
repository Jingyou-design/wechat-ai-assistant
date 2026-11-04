"""本机隧道运行密钥；只读写指定字段，不执行或展开 .env 内容。"""

from pathlib import Path

VARIABLE = "CONTROL_PLANE_API_KEY"


def load_runtime_key(path: Path) -> str:
    key = ""
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == VARIABLE:
                key = value.strip()
                if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
                    key = key[1:-1]
    if any(char.isspace() or char == "\0" for char in key):
        raise ValueError(".env 中的隧道运行密钥格式不正确，请重新填写，不能包含空白。")
    return key


def save_runtime_key(path: Path, key: str):
    if not key or any(char.isspace() or char in "\0\"'" for char in key):
        raise ValueError("运行密钥为空或格式不正确，未保存。")
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    lines = [line for line in lines if line.partition("=")[0].strip() != VARIABLE]
    lines.append(f"{VARIABLE}={key}")
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
