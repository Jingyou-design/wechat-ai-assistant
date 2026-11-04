"""从本地微信会话列表选择导出目标。"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from exporter_core import list_chat_sessions


class SessionPicker(tk.Toplevel):
    def __init__(self, parent, db_dir):
        super().__init__(parent)
        self.title("选择微信会话")
        scale = max(1.0, self.winfo_fpixels("1i") / 96)
        self.geometry(f"{round(980 * scale)}x{round(600 * scale)}")
        self.minsize(round(820 * scale), round(500 * scale))
        self.transient(parent)
        self.configure(background="#f4f6f5")
        self.selected = None
        self.sessions = []
        self.results = queue.Queue()
        self.db_dir = db_dir
        self.loading = False
        self._poll_id = None
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        body = ttk.Frame(self, style="Page.TFrame", padding=24)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)
        ttk.Label(body, text="选择一段对话", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.account_var = tk.StringVar(
            value="正在连接微信本地数据，首次读取可能需要一点时间…"
        )
        ttk.Label(body, textvariable=self.account_var, style="Subtitle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(6, 18)
        )

        search_row = ttk.Frame(body, style="Page.TFrame")
        search_row.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        search_row.grid_columnconfigure(1, weight=1)
        ttk.Label(search_row, text="查找会话", style="Subtitle.TLabel").grid(
            row=0, column=0, padx=(0, 12)
        )
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.filter_sessions)
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        self.search_entry.grid(row=0, column=1, sticky="ew")
        self.refresh_btn = ttk.Button(search_row, text="刷新会话", command=self.refresh)
        self.refresh_btn.grid(row=0, column=2, padx=(10, 0))

        table_frame = ttk.Frame(body, style="Card.TFrame")
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        style = ttk.Style(self)
        style.configure(
            "Sessions.Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#314c3d",
            rowheight=round(34 * scale),
            borderwidth=0,
        )
        style.map(
            "Sessions.Treeview",
            background=[("selected", "#dceee2")],
            foreground=[("selected", "#173e35")],
        )
        style.configure(
            "Sessions.Treeview.Heading",
            background="#e9efeb",
            foreground="#45614f",
            padding=(10, 8),
            relief="flat",
        )
        self.table = ttk.Treeview(
            table_frame,
            columns=("name", "kind", "unread", "time", "summary"),
            show="headings",
            selectmode="browse",
            style="Sessions.Treeview",
        )
        for key, title, width in (
            ("name", "会话", 190),
            ("kind", "类型", 60),
            ("unread", "未读", 55),
            ("time", "最近消息", 155),
            ("summary", "消息摘要", 280),
        ):
            self.table.heading(key, text=title, anchor="w")
            self.table.column(
                key,
                width=round(width * scale),
                minwidth=round(45 * scale),
                stretch=key in {"name", "summary"},
            )
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.table.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.bind("<<TreeviewSelect>>", self.on_select)
        self.table.bind("<Double-1>", self.confirm)
        self.table.bind("<Return>", self.confirm)

        footer = ttk.Frame(body, style="Page.TFrame")
        footer.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        footer.grid_columnconfigure(0, weight=1)
        self.status_var = tk.StringVar()
        ttk.Label(
            footer,
            textvariable=self.status_var,
            style="Subtitle.TLabel",
            wraplength=round(570 * scale),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="取消", command=self.destroy).grid(
            row=0, column=1, padx=(10, 8)
        )
        self.confirm_btn = ttk.Button(
            footer,
            text="使用此会话",
            command=self.confirm,
            style="Primary.TButton",
            state="disabled",
        )
        self.confirm_btn.grid(row=0, column=2)
        self.bind("<Escape>", lambda _: self.destroy())
        self.grab_set()
        self.search_entry.focus_set()
        self.refresh()
        self._poll_id = self.after(100, self.poll_results)

    def refresh(self):
        if self.loading:
            return
        self.loading = True
        self.refresh_btn.configure(state="disabled")
        self.confirm_btn.configure(state="disabled")
        self.sessions = []
        self.table.delete(*self.table.get_children())
        self.account_var.set("正在读取本地账号和会话…")
        self.status_var.set("读取中，请稍候；不会发送消息，也不会修改微信聊天记录。")
        threading.Thread(target=self.load_sessions, daemon=True).start()

    def load_sessions(self):
        try:
            self.results.put(("done", list_chat_sessions(self.db_dir)))
        except Exception as exc:
            self.results.put(("error", f"{type(exc).__name__}: {exc}"))

    def poll_results(self):
        try:
            kind, result = self.results.get_nowait()
        except queue.Empty:
            self._poll_id = self.after(100, self.poll_results)
            return
        self.loading = False
        self.refresh_btn.configure(state="normal")
        if kind == "error":
            self.account_var.set("会话读取失败，可刷新重试或返回手动输入。")
            self.status_var.set(result)
        else:
            self.sessions = result["sessions"]
            self.account_var.set(f"账号：{result['account']}  ·  {result['username']}")
            self.filter_sessions()
        self._poll_id = self.after(100, self.poll_results)

    def filter_sessions(self, *_):
        keyword = self.search_var.get().strip().casefold()
        self.table.delete(*self.table.get_children())
        for index, session in enumerate(self.sessions):
            if (
                keyword
                and keyword not in session["name"].casefold()
                and keyword not in session["username"].casefold()
            ):
                continue
            self.table.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    session["name"],
                    session["chat_type"],
                    session["unread"] or "",
                    session["last_time"],
                    session["summary"],
                ),
            )
        self.confirm_btn.configure(state="disabled")
        if not self.loading:
            count = len(self.table.get_children())
            self.status_var.set(
                f"显示 {count} / {len(self.sessions)} 个会话 · 点选后确认，或双击选择。"
                if count
                else "没有匹配的会话，可调整搜索词或刷新列表。"
            )

    def on_select(self, _=None):
        self.confirm_btn.configure(
            state="normal"
            if self.table.selection() and not self.loading
            else "disabled"
        )

    def confirm(self, _=None):
        selection = self.table.selection()
        if selection and not self.loading:
            self.selected = self.sessions[int(selection[0])]
            self.destroy()

    def destroy(self):
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
        super().destroy()
