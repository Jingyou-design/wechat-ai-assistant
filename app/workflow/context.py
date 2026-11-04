"""仅保存范围、游标与证据引用；语义筛选和任务规划由 ChatGPT 完成。"""

import secrets
import threading
import time
from dataclasses import dataclass, field

import exporter_core as core
from app.api.schema import (
    DetailRequest,
    DetailResult,
    Message,
    PageResult,
    ReadRequest,
    SearchRequest,
    SearchResult,
    Session,
    TaskRequest,
    TaskResult,
)
from app.tools import wechat


@dataclass
class Task:
    account: str
    sessions: dict[str, dict]
    window: tuple[int, int]
    expires: float
    chars: int = 18000
    reads: int = 6
    cursors: dict[str, tuple[str, tuple[int, str, int]]] = field(default_factory=dict)
    references: dict[str, tuple[str, str, int]] = field(default_factory=dict)


class ContextStore:
    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self.lock = threading.RLock()

    def search(self, request: SearchRequest) -> SearchResult:
        _, rows = wechat.sessions()
        query = request.query.strip().casefold()
        matches = [
            row
            for row in rows
            if query in row["name"].casefold() or query in row["session_id"].casefold()
        ]
        return SearchResult(
            sessions=[Session(**row) for row in matches[: request.limit]],
            has_more=len(matches) > request.limit,
        )

    def start(self, request: TaskRequest) -> TaskResult:
        with self.lock:
            self.tasks = {
                key: value
                for key, value in self.tasks.items()
                if value.expires > time.monotonic()
            }
            if len(self.tasks) >= 32:
                raise ValueError("活动任务过多，请等待旧任务过期。")
            account, rows = wechat.sessions()
            catalog = {row["session_id"]: row for row in rows}
            ids = list(dict.fromkeys(request.session_ids))
            if any(session_id not in catalog for session_id in ids):
                raise ValueError("会话不存在，请先查找会话并使用返回的 session_id。")
            end = int(time.time())
            duration = core.EXPORT_TIME_RANGES[request.time_range]
            task = Task(
                account,
                {key: catalog[key] for key in ids},
                (end - duration if duration else 0, end),
                time.monotonic() + 1800,
            )
            task_id = secrets.token_urlsafe(24)
            self.tasks[task_id] = task
            return TaskResult(
                task_id=task_id,
                sessions=[Session(**row) for row in task.sessions.values()],
                start_time=core.fmt_time(task.window[0]) if duration else "所有历史",
                end_time=core.fmt_time(end),
                expires_in_seconds=1800,
                remaining_chars=task.chars,
                remaining_reads=task.reads,
            )

    def _task(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        if task is None or task.expires <= time.monotonic():
            self.tasks.pop(task_id, None)
            raise ValueError("任务不存在或已过期，请重新开始任务。")
        if task.reads <= 0 or task.chars <= 0:
            raise ValueError(
                "本任务读取预算已用完，请先向用户汇报，需要追加读取时取得用户同意再开始新任务。"
            )
        return task

    def read(self, request: ReadRequest) -> PageResult:
        with self.lock:
            task = self._task(request.task_id)
            if request.session_id not in task.sessions:
                raise ValueError("该会话不在本任务范围内。")
            budget = min(5600, task.chars - 400)
            if budget < 400:
                raise ValueError("剩余预算不足以读取消息，请先向用户汇报。")
            before = None
            if request.cursor:
                saved = task.cursors.get(request.cursor)
                if saved is None or saved[0] != request.session_id:
                    raise ValueError("游标不属于本任务的该会话。")
                before = saved[1]
            rows = wechat.messages(
                task.account,
                task.sessions[request.session_id],
                task.window,
                request.limit + 1,
                before,
            )
            output = []
            used = 0
            last = None
            for row in rows[: request.limit]:
                # 包括元数据的序列化体积也计入预算，避免许多短消息绕过限制。
                message_id = secrets.token_urlsafe(12)
                item = Message(
                    message_id=message_id,
                    time=core.fmt_time(row["create_time"]),
                    sender=row["sender"][:120],
                    sender_status=row["sender_status"],
                    content=row["content"][:1500],
                    truncated=len(row["content"]) > 1500,
                )
                cost = len(item.model_dump_json())
                if used + cost > budget:
                    if output:
                        break
                    while cost > budget and item.content:
                        item.content = item.content[: len(item.content) // 2]
                        item.truncated = True
                        cost = len(item.model_dump_json())
                    if cost > budget:
                        raise ValueError(
                            "剩余预算不足以返回消息元数据，请先向用户汇报。"
                        )
                output.append(item)
                used += cost
                last = row
                task.references[message_id] = (
                    request.session_id,
                    row["_db_rel"],
                    row["local_id"],
                )
            has_more = len(rows) > len(output)
            cursor = None
            if has_more and last is not None:
                cursor = secrets.token_urlsafe(18)
                task.cursors[cursor] = (request.session_id, wechat.row_key(last))
            task.reads -= 1
            result = PageResult(
                messages=list(reversed(output)),
                next_cursor=cursor,
                has_more=has_more,
                remaining_chars=0,
                remaining_reads=task.reads,
            )
            task.chars -= len(result.model_dump_json()) + 10
            result.remaining_chars = task.chars
            return result

    def detail(self, request: DetailRequest) -> DetailResult:
        with self.lock:
            task = self._task(request.task_id)
            reference = task.references.get(request.message_id)
            if reference is None:
                raise ValueError("只能展开本任务已读取过的消息。")
            session_id, rel, local_id = reference
            rows = wechat.messages(
                task.account,
                task.sessions[session_id],
                task.window,
                1,
                reference=(rel, local_id),
            )
            if not rows:
                raise ValueError("原消息已不在本机数据库中。")
            content = rows[0]["content"]
            chunk = content[
                request.offset : request.offset + min(4000, max(0, task.chars - 300))
            ]
            end = request.offset + len(chunk)
            result = DetailResult(
                message_id=request.message_id,
                content=chunk,
                next_offset=end if end < len(content) else None,
                remaining_chars=0,
                remaining_reads=task.reads - 1,
            )
            # JSON 转义也消耗上下文，逐步收缩以保证总预算。
            while (
                len(result.model_dump_json()) + 10 > min(4000, task.chars)
                and result.content
            ):
                result.content = result.content[: len(result.content) // 2]
                result.next_offset = request.offset + len(result.content)
            if len(result.model_dump_json()) + 10 > task.chars or (
                not result.content and request.offset < len(content)
            ):
                raise ValueError("剩余预算不足以展开消息，请先向用户汇报。")
            task.chars -= len(result.model_dump_json()) + 10
            task.reads -= 1
            result.remaining_chars = task.chars
            return result
