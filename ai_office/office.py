"""AI社員オフィスの中心。仕事の受付・承認・却下と、24時間まわるワーカー。

流れ:
  本人がチャットで頼む → 仕事（task）ができる → ワーカーが CLI で AI社員を動かす
  → 完了 or 承認待ち or 本人の手が要る、のときだけ本人に戻ってくる（通知）
  → 承認されたら、承認された操作だけを実行しに再開する
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import protocol, runner, schedule
from .config import HostConfig
from .store import OPEN_STATUSES, WAITING_ON_ME, Store, now_iso


class OfficeError(ValueError):
    """本人の操作が受け付けられないとき（画面にそのまま出す文言）。"""


def _local_iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


class Office:
    def __init__(self, store: Store, workdir: Optional[Path] = None, runner_mode: Optional[str] = None,
                 max_parallel: int = 2, notifier: Optional[Callable[[str], None]] = None,
                 host: Optional[HostConfig] = None):
        self.store = store
        self.host = host or HostConfig(workdir=Path(workdir or Path.cwd()).resolve())
        self.workdir = Path(workdir).resolve() if workdir else self.host.workdir
        self.runner_mode = runner.resolve_mode(runner_mode, self.host.runner_command)
        self.max_parallel = max(1, max_parallel)
        self.notifier = notifier if notifier is not None else self._default_notifier()
        self._running: dict[int, str] = {}  # task_id -> bot_id
        self._procs: dict[int, object] = {}  # task_id -> 実行中の CLI プロセス（止めるため）
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ================= 本人の操作 =================
    def send_message(self, bot_id: str, body: str) -> dict:
        bot = self._bot(bot_id)
        body = (body or "").strip()
        if not body:
            raise OfficeError("メッセージが空です")
        task = self.store.create_task(bot_id, body)
        self.store.add_message(bot_id, "me", body, task_id=task["id"])
        if bot["paused"]:
            self.store.add_message(bot_id, "system", f"{bot['name']} はお休み中です。再開すると取りかかります。",
                                   task_id=task["id"])
        return task

    def approve(self, task_id: int, note: str = "") -> dict:
        task = self._task(task_id)
        if task["status"] not in WAITING_ON_ME:
            raise OfficeError("この仕事は承認待ちではありません")
        approved = dict(task.get("approval") or {})
        approved["note"] = note.strip()
        approved["at"] = now_iso()
        verb = "対応しました" if task["status"] == "needs_you" else "承認しました"
        self.store.update_task(task_id, status="queued", approved=approved, approval=None)
        self.store.add_message(task["bot_id"], "me", f"{verb}: {approved.get('action', '')}"
                               + (f"（{note.strip()}）" if note.strip() else ""), task_id=task_id)
        self.store.log_event("task_approved", f"「{task['title']}」を{verb}", bot_id=task["bot_id"], task_id=task_id)
        return self.store.get_task(task_id)

    def reject(self, task_id: int, note: str = "") -> dict:
        task = self._task(task_id)
        if task["status"] not in WAITING_ON_ME:
            raise OfficeError("この仕事は承認待ちではありません")
        self.store.update_task(task_id, status="rejected", finished_at=now_iso())
        self.store.add_message(task["bot_id"], "me", "却下します" + (f": {note.strip()}" if note.strip() else ""),
                               task_id=task_id)
        if note.strip():  # 却下の理由は次から守る進め方として覚えさせる
            self.store.add_memories(task["bot_id"], [f"却下された進め方: {note.strip()}"], task_id=task_id)
        self.store.log_event("task_rejected", f"「{task['title']}」を却下", bot_id=task["bot_id"], task_id=task_id)
        return self.store.get_task(task_id)

    def cancel(self, task_id: int) -> dict:
        """順番待ち・判断待ちは取り消し、作業中なら CLI を止めて取り消す。"""
        task = self._task(task_id)
        if task["status"] not in OPEN_STATUSES:
            raise OfficeError("この仕事はもう終わっています")
        was_running = task["status"] == "running"
        # 先に状態を変える（止めた CLI の結果で上書きされないように、run_task はこの状態を見て捨てる）
        self.store.update_task(task_id, status="cancelled", finished_at=now_iso(),
                               error="本人が途中で止めました" if was_running else None)
        if was_running:
            proc = self._procs.get(task_id)
            if proc is not None:
                runner.stop_process(proc)
            self.store.add_message(task["bot_id"], "system", f"「{task['title']}」を途中で止めました", task_id=task_id)
        self.store.log_event("task_cancelled", f"「{task['title']}」を{'途中で止めました' if was_running else '取り消し'}",
                             bot_id=task["bot_id"], task_id=task_id)
        return self.store.get_task(task_id)

    def retry(self, task_id: int) -> dict:
        task = self._task(task_id)
        if task["status"] not in ("failed", "rejected", "cancelled"):
            raise OfficeError("やり直せるのは、失敗・却下・取り消しの仕事だけです")
        self.store.update_task(task_id, status="queued", error=None, approved=None, approval=None,
                               finished_at=None)
        self.store.log_event("task_retried", f"「{task['title']}」をやり直し", bot_id=task["bot_id"], task_id=task_id)
        return self.store.get_task(task_id)

    def add_schedule(self, bot_id: str, spec: str, instruction: str) -> dict:
        self._bot(bot_id)
        if not instruction.strip():
            raise OfficeError("定期の仕事の中身が空です")
        try:
            nxt = schedule.next_run(spec)
        except schedule.ScheduleError as e:
            raise OfficeError(str(e)) from e
        return self.store.create_schedule(bot_id, " ".join(spec.split()), instruction, _local_iso(nxt))

    def overview(self) -> dict:
        bots = self.store.list_bots()
        open_tasks = self.store.list_tasks(status=OPEN_STATUSES, limit=500)
        for b in bots:
            mine = [t for t in open_tasks if t["bot_id"] == b["id"]]
            running = next((t for t in mine if t["status"] == "running"), None)
            waiting = [t for t in mine if t["status"] in WAITING_ON_ME]
            b["state"] = ("paused" if b["paused"] else "working" if running else
                          "waiting" if waiting else "queued" if mine else "idle")
            b["current_task"] = running
            b["waiting_count"] = len(waiting)
            b["queued_count"] = len([t for t in mine if t["status"] == "queued"])
            b["memories"] = self.store.list_memories(b["id"])
            b["skill_info"] = [self.host.find_skill(n) for n in b.get("skills") or []]
        schedules = self.store.list_schedules()
        for s in schedules:
            try:
                s["label"] = schedule.describe(s["spec"])
            except schedule.ScheduleError:
                s["label"] = s["spec"]
        return {
            "runner": self.runner_mode,
            "bots": bots,
            "approvals": self.store.list_tasks(status=WAITING_ON_ME, limit=100),
            "tasks": self.store.list_tasks(limit=100),
            "schedules": schedules,
            "events": self.store.list_events(limit=80),
            "generated_at": now_iso(),
        }

    # ================= ワーカー =================
    def start(self, poll_seconds: float = 2.0) -> None:
        orphans = self.store.fail_orphaned_running()
        if orphans:
            print(f"[ai-office] 前回作業中のまま止まった仕事 {len(orphans)} 件を失敗にしました", file=sys.stderr)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(poll_seconds,), daemon=True, name="ai-office-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self, poll_seconds: float) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # ワーカーは止めない。原因は実行ログに残す
                self.store.log_event("worker_error", f"ワーカーで例外: {e}")
            self._stop.wait(poll_seconds)

    def tick(self, now: Optional[datetime] = None, background: bool = True) -> list[int]:
        """定期の仕事を積み、空きがあれば仕事を始める。始めた仕事のIDを返す。"""
        self.stop_cancelled_processes()
        self.enqueue_due_schedules(now)
        started = []
        while True:
            with self._lock:
                if len(self._running) >= self.max_parallel:
                    break
                task = self.store.claim_next_task(set(self._running.values()))
                if not task:
                    break
                self._running[task["id"]] = task["bot_id"]
            started.append(task["id"])
            if background:
                threading.Thread(target=self._run_and_release, args=(task,), daemon=True).start()
            else:
                self._run_and_release(task)
        return started

    def stop_cancelled_processes(self) -> None:
        """別プロセス（office.py cancel）で止められた仕事の CLI を、このワーカーで止める。"""
        for tid, proc in list(self._procs.items()):
            t = self.store.get_task(tid)
            if t and t["status"] == "cancelled":
                runner.stop_process(proc)

    def enqueue_due_schedules(self, now: Optional[datetime] = None) -> list[dict]:
        now = (now or datetime.now()).replace(microsecond=0)
        created = []
        for s in self.store.list_schedules():
            if not s["enabled"] or not s["next_run_at"]:
                continue
            if datetime.fromisoformat(s["next_run_at"]) > now:
                continue
            bot = self.store.get_bot(s["bot_id"])
            already = [t for t in self.store.list_tasks(status=OPEN_STATUSES, bot_id=s["bot_id"], limit=200)
                       if t["schedule_id"] == s["id"]]
            nxt = _local_iso(schedule.next_run(s["spec"], now))
            if bot and not already:  # 前回分が終わっていなければ積み増さない（多重実行を防ぐ）
                task = self.store.create_task(s["bot_id"], s["instruction"], origin="schedule", schedule_id=s["id"])
                self.store.add_message(s["bot_id"], "system", f"定期の仕事（{schedule.describe(s['spec'])}）を始めます",
                                       task_id=task["id"])
                created.append(task)
            self.store.update_schedule(s["id"], last_run_at=_local_iso(now), next_run_at=nxt)
        return created

    def _run_and_release(self, task: dict) -> None:
        try:
            self.run_task(task)
        finally:
            with self._lock:
                self._running.pop(task["id"], None)

    def run_task(self, task: dict) -> dict:
        bot = self.store.get_bot(task["bot_id"])
        workspace = Path(self.host.workspace_dir) / bot["id"]
        if self.runner_mode != "demo":
            (self.workdir / workspace).mkdir(parents=True, exist_ok=True)
        self.store.log_event("task_started", f"{bot['name']} が「{task['title']}」に取りかかりました",
                             bot_id=bot["id"], task_id=task["id"])
        history = [m for m in self.store.list_messages(bot["id"], limit=30)]
        prompt = protocol.build_prompt(bot, task, self.store.list_memories(bot["id"]), history,
                                       str(self.workdir), str(workspace), self.host.house_rules,
                                       [self.host.find_skill(n) for n in bot.get("skills") or []])
        if self.store.get_task(task["id"])["status"] == "cancelled":  # 着手前に止められた
            return self.store.get_task(task["id"])

        def _on_start(proc) -> None:
            self._procs[task["id"]] = proc
            # 登録より先に「止める」が押されていたら、ここで止める
            if self.store.get_task(task["id"])["status"] == "cancelled":
                runner.stop_process(proc)

        try:
            res = runner.run(prompt, task, bot, self.workdir, mode=self.runner_mode, on_start=_on_start,
                             host_command=self.host.runner_command, host_env=self.host.runner_env,
                             model=bot.get("model") or None)
        finally:
            self._procs.pop(task["id"], None)

        if self.store.get_task(task["id"])["status"] == "cancelled":  # 途中で止められた
            return self.store.get_task(task["id"])

        if not res.ok:
            self.store.update_task(task["id"], status="failed", error=res.error, finished_at=now_iso())
            self.store.add_message(bot["id"], "bot", f"うまくいきませんでした。{res.error[:300]}", task_id=task["id"])
            self.store.log_event("task_failed", f"「{task['title']}」が失敗: {res.error[:120]}",
                                 bot_id=bot["id"], task_id=task["id"])
            self._notify(f"❌ #{task['id']} {bot['name']}：「{task['title']}」が失敗しました\n{res.error[:300]}")
            return self.store.get_task(task["id"])

        result = protocol.parse_result(res.output)
        fields = {"summary": result["summary"], "artifacts": result["artifacts"], "approval": result["approval"]}
        if result["status"] in ("done", "failed"):
            fields["finished_at"] = now_iso()
        if result["status"] == "failed":
            fields["error"] = result["reply"][:500]
        self.store.update_task(task["id"], status=result["status"], **fields)
        self.store.add_message(bot["id"], "bot", result["reply"], task_id=task["id"])
        learned = self.store.add_memories(bot["id"], result["learned"], task_id=task["id"])
        if learned:
            self.store.log_event("memory_added", f"{bot['name']} が覚えた: " + " / ".join(learned),
                                 bot_id=bot["id"], task_id=task["id"])
        if not result["format_ok"]:
            self.store.log_event("format_warning", "返事に結果ブロックが無かったため『完了』として扱いました",
                                 bot_id=bot["id"], task_id=task["id"])

        label = {"done": "✅ 完了", "needs_approval": "🙋 承認待ち", "needs_you": "✋ 対応のお願い",
                 "failed": "❌ 失敗"}[result["status"]]
        self.store.log_event(f"task_{result['status']}", f"「{task['title']}」{label}", bot_id=bot["id"],
                             task_id=task["id"])
        lines = [f"{label} #{task['id']}｜{bot['name']}：「{task['title']}」", result["summary"]]
        if result["approval"]:
            lines.append(f"承認してほしいこと: {result['approval']['action']}")
            cli = self.host.office_cli()
            lines.append(f"承認: {cli} approve {task['id']} ／ 却下: {cli} reject {task['id']} --note \"理由\"")
        self._notify("\n".join(x for x in lines if x))
        return self.store.get_task(task["id"])

    # ================= 内部 =================
    def _bot(self, bot_id: str) -> dict:
        bot = self.store.get_bot(bot_id)
        if not bot:
            raise OfficeError("その AI社員は見つかりません")
        return bot

    def _task(self, task_id: int) -> dict:
        task = self.store.get_task(task_id)
        if not task:
            raise OfficeError("その仕事は見つかりません")
        return task

    def _notify(self, text: str) -> None:
        if self.notifier:
            try:
                self.notifier(text)
            except Exception as e:  # 通知の失敗で仕事を落とさない
                self.store.log_event("notify_failed", f"通知に失敗: {e}")

    def _default_notifier(self) -> Optional[Callable[[str], None]]:
        """通知は設定ファイルの notify_command で送る（本文は最後の引数）。無ければ通知しない。"""
        flag = os.environ.get("AI_OFFICE_NOTIFY", "0" if self.runner_mode == "demo" else "1")
        if flag == "0" or not self.host.notify_command:
            return None
        command = list(self.host.notify_command)
        if command[0] in ("python", "python3"):
            command[0] = sys.executable

        def _send(text: str) -> None:
            proc = subprocess.run([*command, text], cwd=str(self.workdir), capture_output=True, text=True,
                                  timeout=60)
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout).strip()[-300:])
        return _send


def load_seed(path: Path) -> dict:
    return json.loads(Path(path).read_text("utf-8"))


def sync_staff(office: Office) -> dict:
    """外にある AI社員（staff_dirs）を名簿に合わせる。中身はコピーせず、ファイルの場所と見出しだけを持つ。

    - 新しく見つかった人は入社させる
    - もういる人は、名前・担当・モデル・使うスキル・ファイルの場所を、ファイルの見出しに合わせる
      （覚えたこと・会話・仕事・お休みはそのまま）
    - ファイルが無くなった人は「定義が見つからない」にしてお休みにする（消さない）
    """
    from . import staff as staff_mod

    found = staff_mod.discover(office.host.staff_dirs)
    ids = {f["id"] for f in found}
    result = {"added": [], "updated": [], "missing": []}
    sort_base = len(office.store.list_bots())
    for i, f in enumerate(found):
        bot = office.store.get_bot(f["id"])
        if not bot:
            office.store.create_bot(f["name"], f["role"], f["avatar"], "", f["skills"], "slate", bot_id=f["id"],
                                    sort=sort_base + i, model=f["model"], source_path=f["source_path"])
            office.store.add_message(f["id"], "bot", f"{f['name']}です。仕事の進め方は {f['source_path']} に書いてあります。")
            result["added"].append(f["id"])
            continue
        changes = {k: f[k] for k in ("name", "role", "model", "skills", "source_path") if bot.get(k) != f[k]}
        if bot.get("source_missing"):
            changes["source_missing"] = False
        if changes:
            office.store.update_bot(f["id"], **changes)
            result["updated"].append(f["id"])
    for bot in office.store.list_bots():
        if bot.get("source_path") and bot["id"] not in ids and not bot.get("source_missing"):
            office.store.update_bot(bot["id"], source_missing=True, paused=True)
            office.store.log_event("staff_missing", f"{bot['name']} の定義ファイルが見つからないのでお休みにしました"
                                   f"（{bot['source_path']}）", bot_id=bot["id"])
            result["missing"].append(bot["id"])
    return result


def seed(office: Office, seed_path: Path) -> list[dict]:
    """初期メンバーを入れる（同じIDがいればスキップ）。"""
    data = load_seed(seed_path)
    created = []
    for i, b in enumerate(data.get("bots", [])):
        if office.store.get_bot(b["id"]):
            continue
        created.append(office.store.create_bot(
            b["name"], b.get("role", ""), b.get("avatar", "🤖"), b.get("instructions", ""),
            b.get("skills", []), b.get("color", "indigo"), bot_id=b["id"], sort=i, hints=b.get("hints", [])))
        for s in b.get("schedules", []):
            office.add_schedule(b["id"], s["spec"], s["instruction"])
        if b.get("greeting"):
            office.store.add_message(b["id"], "bot", b["greeting"])
    return created
