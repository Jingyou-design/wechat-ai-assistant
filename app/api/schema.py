from typing import Literal

from pydantic import BaseModel, Field

TimeRange = Literal[
    "最近一小时", "最近一天", "最近一星期", "最近一个月", "最近一年", "所有"
]


class SearchRequest(BaseModel):
    query: str = Field(default="", max_length=80)
    limit: int = Field(default=10, ge=1, le=20)


class Session(BaseModel):
    session_id: str
    name: str
    chat_type: str
    unread: int
    last_time: str


class SearchResult(BaseModel):
    sessions: list[Session]
    has_more: bool


class TaskRequest(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=3)
    time_range: TimeRange = "最近一天"


class TaskResult(BaseModel):
    task_id: str
    sessions: list[Session]
    start_time: str
    end_time: str
    expires_in_seconds: int
    remaining_chars: int
    remaining_reads: int


class ReadRequest(BaseModel):
    task_id: str = Field(max_length=64)
    session_id: str = Field(max_length=256)
    cursor: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=20, ge=1, le=30)


class Message(BaseModel):
    message_id: str
    time: str
    sender: str
    sender_status: str
    content: str
    truncated: bool


class PageResult(BaseModel):
    messages: list[Message]
    next_cursor: str | None
    has_more: bool
    remaining_chars: int
    remaining_reads: int
    coverage: str = (
        "仅包含本机缓存、所选会话和时间范围；图片、语音、视频仅返回类型提示。"
    )


class DetailRequest(BaseModel):
    task_id: str = Field(max_length=64)
    message_id: str = Field(max_length=64)
    offset: int = Field(default=0, ge=0, le=1_000_000)


class DetailResult(BaseModel):
    message_id: str
    content: str
    next_offset: int | None
    remaining_chars: int
    remaining_reads: int
