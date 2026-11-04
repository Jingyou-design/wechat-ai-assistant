"""只读数据库工具；复用导出器的解析与临时密钥隔离。"""

import hashlib
import io
import re
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

import exporter_core as core
from app.tools.images import preview


class ReadError(ValueError):
    """可安全返回给用户的读取状态；上游异常仍由数据库边界隐藏。"""


@contextmanager
def database():
    with core._SENSITIVE_SANDBOX_LOCK:
        workdir = core._create_sensitive_workdir()
        try:
            # 上游诊断不能混入 stdio 协议，也不能输出密钥或聊天内容。
            with (
                core._upstream_sensitive_sandbox(workdir),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                yield core.WeChatDB(workdir=str(workdir))
        except ReadError:
            raise
        except Exception:  # noqa: BLE001 -- 工具边界隐藏上游可能包含密钥的异常内容。
            raise RuntimeError(
                "本机微信读取失败，请确认 Windows 微信已登录并在导出器检查数据目录。"
            ) from None
        finally:
            ok, _ = core._cleanup_sensitive_workdir(workdir)
            if not ok:
                raise RuntimeError("敏感临时缓存清理失败，请在导出器中清除敏感缓存。")


def sessions():
    with database() as db:
        names = db._nickname_index()
        return db.wxid, [
            {
                "session_id": row["username"],
                "name": str(names.get(row["username"]) or row["username"])[:120],
                "chat_type": core.chat_type_label(row["username"]),
                "unread": row["unread"],
                "last_time": core.fmt_time(row["last_time"])
                if row["last_time"]
                else "",
            }
            for row in db.get_sessions(limit=-1)
        ]


def row_key(row: dict) -> tuple[int, str, int]:
    return row.get("sort_seq") or 0, row["_db_rel"], row["local_id"]


def query_rows(
    db, username: str, window: tuple[int, int], limit: int, before=None, reference=None
):
    table = "Msg_" + hashlib.md5(username.encode()).hexdigest()
    shards = list(db._message_dbs())
    if username.startswith("gh_"):
        shards.extend(
            rel
            for rel, _, _ in db._db_files
            if re.fullmatch(r"message/biz_message_\d+\.db", rel.replace("\\", "/"))
            and rel not in shards
        )
    rows = []
    for rel in shards:
        if reference and rel != reference[0]:
            continue
        conn = db._open(rel)
        try:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                continue
            cols = core.table_columns(conn, table)
            if (
                not {
                    "local_id",
                    "create_time",
                    "sort_seq",
                    "local_type",
                    "real_sender_id",
                }
                <= cols
            ):
                raise ValueError("消息库结构不兼容，请更新读取器后重试。")
            wanted = [
                name
                for name in (
                    "local_id",
                    "local_type",
                    "server_id",
                    "real_sender_id",
                    "create_time",
                    "message_content",
                    "compress_content",
                    "source",
                    "packed_info_data",
                    "sort_seq",
                )
                if name in cols
            ]
            start, end = window
            where = "(create_time BETWEEN ? AND ? OR create_time BETWEEN ? AND ?)"
            params = [start, end, start * 1000, end * 1000 + 999]
            if before is not None:
                where += " AND (COALESCE(sort_seq, 0), ?, local_id) < (?, ?, ?)"
                params.extend([rel, *before])
            if reference:
                where += " AND local_id = ?"
                params.append(reference[1])
            sql = f"SELECT {', '.join(wanted)} FROM {table} WHERE {where} ORDER BY COALESCE(sort_seq, 0) DESC, local_id DESC LIMIT ?"
            cur = conn.execute(sql, [*params, limit])
            sender_index, map_status = core._sender_map(conn)
            for raw in cur.fetchall():
                row = dict(zip(wanted, raw))
                sender_id = core._sender_id(row["real_sender_id"])
                sender = core._identity_username(sender_index.get(sender_id))
                row.update(
                    _db_rel=rel,
                    _sender_username=sender,
                    _sender_status="resolved"
                    if sender
                    else map_status
                    if map_status != "resolved"
                    else "unmapped_sender_id",
                )
                rows.append(row)
        finally:
            conn.close()
    return sorted(rows, key=row_key, reverse=True)[:limit]


def messages(
    account: str,
    session: dict,
    window: tuple[int, int],
    limit: int,
    before=None,
    reference=None,
):
    with database() as db:
        if db.wxid != account:
            raise ValueError("微信账号已改变，请重新选择会话并开始任务。")
        nicks = db._nickname_index()
        rows = query_rows(db, session["session_id"], window, limit, before, reference)
        for row in rows:
            sender, strip_prefix = core.resolve_sender(
                row,
                session["chat_type"] == "群聊",
                session["session_id"],
                session["name"],
                nicks,
                db.wxid,
            )
            row["sender"] = sender["sender"]
            row["sender_status"] = sender["sender_status"]
            row["content"] = core.parse_content(
                row["local_type"],
                row.get("message_content"),
                row.get("compress_content"),
                strip_prefix,
            )
        return rows


def image(account: str, session: dict, window: tuple[int, int], reference):
    with database() as db:
        if db.wxid != account:
            raise ValueError("微信账号已改变，请重新选择会话并开始任务。")
        username = session["session_id"]
        rows = query_rows(db, username, window, 1, reference=reference)
        if not rows:
            raise ValueError("原消息已不在本机数据库中。")
        row = rows[0]
        if core.low_type(row.get("local_type")) != 3:
            raise ValueError("该消息不是图片消息。")
        output = Path(db.workdir) / "images"
        cfg_dword, _ = core._ensure_cfg_dword(db)
        downloader = core.MediaDownloader(db, save_dir=str(output), cfg_dword=cfg_dword)
        index = core._index_image_files(db, username)
        keys = core._detect_image_keys_fast(downloader) if index else None
        media, _ = core._write_image_from_row(
            db, downloader, row, username, index, output, keys
        )
        if not media or not media["previewable"]:
            raise ValueError("本机没有可解码的图片缓存，请先在微信中打开该图片再重试。")
        data, width, height = preview(Path(media["path"]))
        return data, width, height, media["variant"]


def voice(account: str, session: dict, window: tuple[int, int], reference):
    with database() as db:
        if db.wxid != account:
            raise ReadError("微信账号已改变，请重新选择会话并开始任务。")
        username = session["session_id"]
        rows = query_rows(db, username, window, 1, reference=reference)
        if not rows:
            raise ReadError("原消息已不在本机数据库中。")
        row = rows[0]
        if core.low_type(row.get("local_type")) != 34:
            raise ReadError("该消息不是语音消息。")
        transcript = core._voice_transcript(row)
        if transcript:
            return transcript, "wechat"
        if not core.rust_silk_status()[0] or not core.local_asr_status()[0]:
            raise ReadError(
                "本地语音识别组件尚未就绪，请运行start_all.cmd完成安装后重试。"
            )
        media, _, _ = core._write_voice_from_row(
            db, row, username, Path(db.workdir) / "voices"
        )
        if not media or not media["decoded"]:
            raise ReadError(
                "本机语音缓存不存在或无法解码，请先在微信中播放该语音再重试。"
            )
        recognizer = core._create_local_asr_recognizer()
        transcript, _ = core._transcribe_wav_local(recognizer, Path(media["path"]))
        if not transcript:
            raise ReadError("本地语音识别未得到文字，不能确定这条语音的内容。")
        return transcript, "local_asr"
