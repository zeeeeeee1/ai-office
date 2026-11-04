"""AI社員オフィスのデータ層（SQLite・標準ライブラリのみ）。

ここはデータの読み書きだけを行う。LLM は呼ばない（呼ぶのは runner.py が起動する CLI）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# 状態の一覧（画面・テストもこの名前を使う）
TASK_STATUSES = (
    "queued",          # 順番待ち
    "running",         # 作業中
    "needs_approval",  # 本人の承認待ち（公開・送る・払う・消す・外に出す）
    "needs_you",       # 本人の手が要る（ログイン・CAPTCHA・本人確認など）
    "done",            # 完了
    "failed",          # 失敗
    "rejected",        # 本人が却下
    "cancelled",       # 本人が取り消し
)
OPEN_STATUSES = ("queued", "running", "needs_approval", "needs_you")
WAITING_ON_ME = ("needs_approval", "needs_you")

MEMORY_LIMIT = 30  # 1人が覚えておく好み・進め方の上限（古いものから忘れる）

SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT '',
  avatar TEXT NOT NULL DEFAULT '🤖',
  color TEXT NOT NULL DEFAULT 'indigo',
  instructions TEXT NOT NULL DEFAULT '',
  skills TEXT NOT NULL DEFAULT '[]',
  hints TEXT NOT NULL DEFAULT '[]',
  paused INTEGER NOT NULL DEFAULT 0,
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT NOT NULL,
  text TEXT NOT NULL,
  task_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT NOT NULL,
  task_id INTEGER,
  sender TEXT NOT NULL,           -- me / bot / system
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT NOT NULL,
  title TEXT NOT NULL,
  instruction TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  origin TEXT NOT NULL DEFAULT 'chat',   -- chat / schedule
  schedule_id INTEGER,
  approval TEXT,                          -- JSON: {kind, action, detail}
  approved TEXT,                          -- JSON: 承認済みの操作（再開時にプロンプトへ渡す）
  summary TEXT,
  artifacts TEXT NOT NULL DEFAULT '[]',
  error TEXT,
  runs INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT NOT NULL,
  spec TEXT NOT NULL,
  instruction TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  next_run_at TEXT,
  last_run_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT,
  task_id INTEGER,
  kind TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_messages_bot ON messages(bot_id, id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row(r: Optional[sqlite3.Row]) -> Optional[dict]:
    if r is None:
        return None
    d = dict(r)
    for key in ("skills", "artifacts", "hints"):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key] or "[]")
    for key in ("approval", "approved"):
        if key in d and isinstance(d[key], str) and d[key]:
            d[key] = json.loads(d[key])
    if "paused" in d:
        d["paused"] = bool(d["paused"])
    if "enabled" in d:
        d["enabled"] = bool(d["enabled"])
    return d


class Store:
    """1つの SQLite ファイルを、サーバーとワーカーのスレッドで共有する。"""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL" if self.path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self._db.executescript(SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(bots)").fetchall()}
        if "hints" not in cols:  # 古いデータベースに列を足す
            self._db.execute("ALTER TABLE bots ADD COLUMN hints TEXT NOT NULL DEFAULT '[]'")
        self._db.commit()

    # ---- 低レベル ----
    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, tuple(params))
            self._db.commit()
            return cur

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [_row(r) for r in self._db.execute(sql, tuple(params)).fetchall()]

    def _one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        with self._lock:
            return _row(self._db.execute(sql, tuple(params)).fetchone())

    # ---- AI社員 ----
    def create_bot(self, name: str, role: str = "", avatar: str = "🤖", instructions: str = "",
                   skills: Optional[list[str]] = None, color: str = "indigo", bot_id: Optional[str] = None,
                   sort: int = 0, hints: Optional[list[str]] = None) -> dict:
        bot_id = bot_id or uuid.uuid4().hex[:8]
        self._exec(
            "INSERT INTO bots (id, name, role, avatar, color, instructions, skills, hints, sort, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (bot_id, name.strip(), role.strip(), avatar or "🤖", color, instructions.strip(),
             json.dumps(skills or [], ensure_ascii=False), json.dumps(hints or [], ensure_ascii=False),
             sort, now_iso()),
        )
        self.log_event("bot_created", f"{name} が入社しました", bot_id=bot_id)
        return self.get_bot(bot_id)

    def get_bot(self, bot_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM bots WHERE id=?", (bot_id,))

    def list_bots(self) -> list[dict]:
        return self._all("SELECT * FROM bots ORDER BY sort, created_at")

    def update_bot(self, bot_id: str, **fields: Any) -> Optional[dict]:
        allowed = {"name", "role", "avatar", "color", "instructions", "skills", "hints", "paused"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in ("skills", "hints"):
                v = json.dumps(list(v), ensure_ascii=False)
            if k == "paused":
                v = 1 if v else 0
            sets.append(f"{k}=?")
            params.append(v)
        if sets:
            self._exec(f"UPDATE bots SET {', '.join(sets)} WHERE id=?", (*params, bot_id))
        return self.get_bot(bot_id)

    # ---- 記憶（好み・進め方） ----
    def add_memories(self, bot_id: str, items: Iterable[str], task_id: Optional[int] = None) -> list[str]:
        existing = {m["text"] for m in self.list_memories(bot_id)}
        added = []
        for raw in items:
            text = " ".join(str(raw).split())[:200]
            if not text or text in existing:
                continue
            self._exec("INSERT INTO memories (bot_id, text, task_id, created_at) VALUES (?,?,?,?)",
                       (bot_id, text, task_id, now_iso()))
            existing.add(text)
            added.append(text)
        # 上限を超えたら古いものから忘れる
        self._exec(
            "DELETE FROM memories WHERE bot_id=? AND id NOT IN"
            " (SELECT id FROM memories WHERE bot_id=? ORDER BY id DESC LIMIT ?)",
            (bot_id, bot_id, MEMORY_LIMIT),
        )
        return added

    def list_memories(self, bot_id: str) -> list[dict]:
        return self._all("SELECT * FROM memories WHERE bot_id=? ORDER BY id", (bot_id,))

    def delete_memory(self, bot_id: str, memory_id: int) -> None:
        self._exec("DELETE FROM memories WHERE bot_id=? AND id=?", (bot_id, memory_id))

    # ---- 会話 ----
    def add_message(self, bot_id: str, sender: str, body: str, task_id: Optional[int] = None) -> dict:
        cur = self._exec("INSERT INTO messages (bot_id, task_id, sender, body, created_at) VALUES (?,?,?,?,?)",
                         (bot_id, task_id, sender, body, now_iso()))
        return self._one("SELECT * FROM messages WHERE id=?", (cur.lastrowid,))

    def list_messages(self, bot_id: str, limit: int = 200) -> list[dict]:
        rows = self._all("SELECT * FROM messages WHERE bot_id=? ORDER BY id DESC LIMIT ?", (bot_id, limit))
        return list(reversed(rows))

    # ---- 仕事 ----
    def create_task(self, bot_id: str, instruction: str, origin: str = "chat",
                    schedule_id: Optional[int] = None, title: Optional[str] = None) -> dict:
        instruction = instruction.strip()
        title = (title or instruction.splitlines()[0])[:60]
        ts = now_iso()
        cur = self._exec(
            "INSERT INTO tasks (bot_id, title, instruction, origin, schedule_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (bot_id, title, instruction, origin, schedule_id, ts, ts),
        )
        task = self.get_task(cur.lastrowid)
        self.log_event("task_queued", f"「{task['title']}」を受け付けました", bot_id=bot_id, task_id=task["id"])
        return task

    def get_task(self, task_id: int) -> Optional[dict]:
        return self._one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def list_tasks(self, status: Optional[Iterable[str]] = None, bot_id: Optional[str] = None,
                   limit: int = 100) -> list[dict]:
        where, params = [], []
        if status:
            st = list(status)
            where.append(f"status IN ({','.join('?' * len(st))})")
            params += st
        if bot_id:
            where.append("bot_id=?")
            params.append(bot_id)
        sql = "SELECT * FROM tasks" + (f" WHERE {' AND '.join(where)}" if where else "")
        sql += " ORDER BY id DESC LIMIT ?"
        return self._all(sql, (*params, limit))

    def update_task(self, task_id: int, **fields: Any) -> Optional[dict]:
        allowed = {"status", "approval", "approved", "summary", "artifacts", "error",
                   "started_at", "finished_at", "runs"}
        sets, params = ["updated_at=?"], [now_iso()]
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in ("approval", "approved", "artifacts") and v is not None:
                v = json.dumps(v, ensure_ascii=False)
            if k == "status" and v not in TASK_STATUSES:
                raise ValueError(f"unknown status: {v}")
            sets.append(f"{k}=?")
            params.append(v)
        self._exec(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", (*params, task_id))
        return self.get_task(task_id)

    def claim_next_task(self, busy_bots: set[str]) -> Optional[dict]:
        """順番待ちの仕事を1件取り、作業中にする（同じ社員は同時に1件だけ）。"""
        with self._lock:
            rows = self._db.execute(
                "SELECT t.* FROM tasks t JOIN bots b ON b.id=t.bot_id"
                " WHERE t.status='queued' AND b.paused=0 ORDER BY t.id"
            ).fetchall()
            for r in rows:
                if r["bot_id"] in busy_bots:
                    continue
                ts = now_iso()
                self._db.execute(
                    "UPDATE tasks SET status='running', started_at=?, updated_at=?, runs=runs+1 WHERE id=?",
                    (ts, ts, r["id"]),
                )
                self._db.commit()
                return self.get_task(r["id"])
        return None

    def fail_orphaned_running(self) -> list[dict]:
        """前回の停止で作業中のまま残った仕事を失敗にする（無修正の自動リトライはしない）。"""
        orphans = self.list_tasks(status=["running"], limit=1000)
        for t in orphans:
            self.update_task(t["id"], status="failed", finished_at=now_iso(),
                             error="ワーカーが途中で止まりました。原因を確かめてから『やり直す』を押してください。")
            self.log_event("task_failed", f"「{t['title']}」は途中で止まりました", bot_id=t["bot_id"], task_id=t["id"])
        return orphans

    # ---- 定期の仕事 ----
    def create_schedule(self, bot_id: str, spec: str, instruction: str, next_run_at: Optional[str]) -> dict:
        cur = self._exec(
            "INSERT INTO schedules (bot_id, spec, instruction, next_run_at, created_at) VALUES (?,?,?,?,?)",
            (bot_id, spec, instruction.strip(), next_run_at, now_iso()),
        )
        return self._one("SELECT * FROM schedules WHERE id=?", (cur.lastrowid,))

    def list_schedules(self, bot_id: Optional[str] = None) -> list[dict]:
        if bot_id:
            return self._all("SELECT * FROM schedules WHERE bot_id=? ORDER BY id", (bot_id,))
        return self._all("SELECT * FROM schedules ORDER BY id")

    def update_schedule(self, schedule_id: int, **fields: Any) -> Optional[dict]:
        allowed = {"enabled", "next_run_at", "last_run_at", "spec", "instruction"}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                params.append((1 if v else 0) if k == "enabled" else v)
        if sets:
            self._exec(f"UPDATE schedules SET {', '.join(sets)} WHERE id=?", (*params, schedule_id))
        return self._one("SELECT * FROM schedules WHERE id=?", (schedule_id,))

    def delete_schedule(self, schedule_id: int) -> None:
        self._exec("DELETE FROM schedules WHERE id=?", (schedule_id,))

    # ---- 実行ログ ----
    def log_event(self, kind: str, body: str, bot_id: Optional[str] = None, task_id: Optional[int] = None) -> None:
        self._exec("INSERT INTO events (bot_id, task_id, kind, body, created_at) VALUES (?,?,?,?,?)",
                   (bot_id, task_id, kind, body, now_iso()))

    def list_events(self, limit: int = 100) -> list[dict]:
        return self._all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
