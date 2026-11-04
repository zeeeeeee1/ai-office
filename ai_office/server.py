"""AI社員オフィスの画面と API（標準ライブラリの http.server）。

- 既定で 127.0.0.1 にだけ開く（外から触れない）。
- 書き込み系（POST/PATCH/DELETE）は X-AI-Office ヘッダー必須。別サイトのページから
  勝手に仕事を投げられないようにするため（ヘッダー付きの別オリジン要求はブラウザが止める）。
- Host ヘッダーが localhost / 127.0.0.1 以外なら拒否（DNS リバインディング対策）。
"""
from __future__ import annotations

import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .office import Office, OfficeError

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MAX_BODY = 64 * 1024


def make_handler(office: Office):
    routes: list[tuple[str, re.Pattern, Any]] = []

    def route(method: str, pattern: str):
        def deco(fn):
            routes.append((method, re.compile("^" + pattern + "$"), fn))
            return fn
        return deco

    @route("GET", r"/api/overview")
    def _overview(body, **_):
        return office.overview()

    @route("GET", r"/api/bots/(?P<bot_id>[\w-]+)/messages")
    def _messages(body, bot_id):
        return {"messages": office.store.list_messages(bot_id)}

    @route("POST", r"/api/bots/(?P<bot_id>[\w-]+)/messages")
    def _send(body, bot_id):
        return {"task": office.send_message(bot_id, str(body.get("body", "")))}

    @route("POST", r"/api/bots")
    def _create_bot(body, **_):
        name = str(body.get("name", "")).strip()
        if not name:
            raise OfficeError("名前を入れてください")
        skills = [s.strip() for s in body.get("skills", []) if str(s).strip()]
        bot = office.store.create_bot(name, str(body.get("role", "")), str(body.get("avatar") or "🤖"),
                                      str(body.get("instructions", "")), skills,
                                      sort=len(office.store.list_bots()))
        greeting = f"{bot['name']}です。{bot['role'] or '仕事'}を担当します。チャットで頼んでください。"
        office.store.add_message(bot["id"], "bot", greeting)
        return {"bot": bot}

    @route("PATCH", r"/api/bots/(?P<bot_id>[\w-]+)")
    def _update_bot(body, bot_id):
        if not office.store.get_bot(bot_id):
            raise OfficeError("その AI社員は見つかりません")
        return {"bot": office.store.update_bot(bot_id, **body)}

    @route("DELETE", r"/api/bots/(?P<bot_id>[\w-]+)/memories/(?P<mid>\d+)")
    def _forget(body, bot_id, mid):
        office.store.delete_memory(bot_id, int(mid))
        return {"ok": True}

    @route("POST", r"/api/bots/(?P<bot_id>[\w-]+)/memories")
    def _remember(body, bot_id):
        return {"added": office.store.add_memories(bot_id, [str(body.get("text", ""))])}

    @route("POST", r"/api/tasks/(?P<task_id>\d+)/(?P<action>approve|reject|cancel|retry)")
    def _task_action(body, task_id, action):
        tid = int(task_id)
        note = str(body.get("note", ""))
        fn = {"approve": lambda: office.approve(tid, note), "reject": lambda: office.reject(tid, note),
              "cancel": lambda: office.cancel(tid), "retry": lambda: office.retry(tid)}[action]
        return {"task": fn()}

    @route("POST", r"/api/schedules")
    def _add_schedule(body, **_):
        return {"schedule": office.add_schedule(str(body.get("bot_id", "")), str(body.get("spec", "")),
                                                str(body.get("instruction", "")))}

    @route("PATCH", r"/api/schedules/(?P<sid>\d+)")
    def _toggle_schedule(body, sid):
        return {"schedule": office.store.update_schedule(int(sid), enabled=bool(body.get("enabled")))}

    @route("DELETE", r"/api/schedules/(?P<sid>\d+)")
    def _delete_schedule(body, sid):
        office.store.delete_schedule(int(sid))
        return {"ok": True}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ai-office"

        def log_message(self, fmt, *args):  # アクセスログは出さない（仕事の記録は実行ログに残る）
            return

        def _host_ok(self) -> bool:
            raw = self.headers.get("Host") or ""
            host = raw.split("]")[0] + "]" if raw.startswith("[") else raw.rsplit(":", 1)[0]
            return host in ALLOWED_HOSTS

        def _send_json(self, status: int, data: Any) -> None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_file(self, path: Path) -> None:
            data = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _static(self, url_path: str) -> None:
            if url_path in ("/", "/index.html"):
                return self._send_file(WEB_DIR / "index.html")
            target = (WEB_DIR / url_path.lstrip("/")).resolve()
            if WEB_DIR.resolve() in target.parents and target.is_file():
                return self._send_file(target)
            self._send_json(404, {"error": "見つかりません"})

        def _dispatch(self, method: str) -> None:
            if not self._host_ok():
                return self._send_json(403, {"error": "このサーバーは手元のPCからだけ使えます"})
            path = urlparse(self.path).path
            if method != "GET" and self.headers.get("X-AI-Office") != "1":
                return self._send_json(403, {"error": "画面からの操作だけ受け付けます"})
            if method == "GET" and not path.startswith("/api/"):
                return self._static(path)
            body: dict = {}
            if method != "GET":
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._send_json(413, {"error": "内容が長すぎます"})
                if length:
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        return self._send_json(400, {"error": "送られた内容を読めませんでした"})
                    if not isinstance(body, dict):
                        return self._send_json(400, {"error": "送られた内容を読めませんでした"})
            for m, pattern, fn in routes:
                if m != method:
                    continue
                match = pattern.match(path)
                if match:
                    try:
                        return self._send_json(200, fn(body, **match.groupdict()))
                    except OfficeError as e:
                        return self._send_json(400, {"error": str(e)})
            self._send_json(404, {"error": "見つかりません"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def serve(office: Office, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(office))
    return httpd
