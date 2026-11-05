"""AI社員オフィスのテスト。LLM は呼ばない（runner=demo / 差し替え）。"""
import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_office import protocol, runner, schedule  # noqa: E402
from ai_office.office import Office, OfficeError, seed  # noqa: E402
from ai_office.server import serve  # noqa: E402
from ai_office.store import MEMORY_LIMIT, Store  # noqa: E402

SEED = ROOT / "examples" / "bots.example.json"


@pytest.fixture
def office(tmp_path):
    sent = []
    o = Office(Store(tmp_path / "office.db"), workdir=tmp_path, runner_mode="demo", notifier=sent.append)
    o.sent = sent
    seed(o, SEED)
    return o


# ---------- 初期メンバー ----------
def test_example_bots_are_valid():
    data = json.loads(SEED.read_text("utf-8"))
    assert data["bots"], "初期メンバーの例がない"
    ids = [b["id"] for b in data["bots"]]
    assert len(ids) == len(set(ids))
    for b in data["bots"]:
        assert b["name"] and b["role"] and isinstance(b.get("skills", []), list)
        for sch in b.get("schedules", []):
            schedule.parse(sch["spec"])


def test_seed_is_idempotent(office):
    n = len(office.store.list_bots())
    seed(office, SEED)
    assert len(office.store.list_bots()) == n
    assert office.store.list_schedules(), "マモルの定期の仕事が入っていない"


# ---------- 返事の読み取り ----------
def test_parse_result_needs_approval():
    text = 'できました\n```ai-office\n{"status":"needs_approval","reply":"下書き完了","approval":{"kind":"send","action":"メール配信"},"learned":["短く"]}\n```'
    r = protocol.parse_result(text)
    assert r["status"] == "needs_approval"
    assert r["approval"] == {"kind": "send", "action": "メール配信", "detail": ""}
    assert r["learned"] == ["短く"] and r["format_ok"]


def test_parse_result_without_block_is_done_but_flagged():
    r = protocol.parse_result("結果ブロックなしの返事")
    assert r["status"] == "done" and not r["format_ok"]
    assert r["reply"] == "結果ブロックなしの返事"


def test_parse_result_unknown_kind_falls_back():
    r = protocol.parse_result('```ai-office\n{"status":"needs_you","approval":{"kind":"???"}}\n```')
    assert r["status"] == "needs_you" and r["approval"]["kind"] == "handoff"


def test_prompt_carries_rules_memory_and_approval():
    bot = {"id": "x", "name": "テスト", "role": "編集", "instructions": "下書きまで", "skills": ["blog"]}
    task = {"instruction": "記事を書いて", "approved": {"action": "公開申請", "detail": "ブログ", "note": "OK"}}
    p = protocol.build_prompt(bot, task, [{"text": "タイトルは短く"}], [], "/repo", "output/ai-office/x")
    assert "AGENTS.md" in p and "needs_approval" in p
    assert "タイトルは短く" in p and "公開申請" in p and "この操作だけ実行してよい" in p


# ---------- 定期の仕事 ----------
@pytest.mark.parametrize("spec,after,expected", [
    ("daily 08:47", datetime(2026, 11, 4, 9, 0), datetime(2026, 11, 5, 8, 47)),
    ("weekdays 09:10", datetime(2026, 11, 6, 10, 0), datetime(2026, 11, 9, 9, 10)),   # 金→月
    ("weekly mon 10:05", datetime(2026, 11, 4, 0, 0), datetime(2026, 11, 9, 10, 5)),
    ("every 2h", datetime(2026, 11, 4, 9, 0), datetime(2026, 11, 4, 11, 0)),
])
def test_next_run(spec, after, expected):
    assert schedule.next_run(spec, after) == expected


@pytest.mark.parametrize("spec", ["every 5m", "daily 25:00", "sometimes"])
def test_bad_schedule(spec):
    with pytest.raises(schedule.ScheduleError):
        schedule.parse(spec)


def test_due_schedule_enqueues_once(office):
    s = office.store.list_schedules()[0]
    office.store.update_schedule(s["id"], next_run_at="2026-11-04T08:47:00")
    first = office.enqueue_due_schedules(datetime(2026, 11, 4, 8, 50))
    assert len(first) == 1 and first[0]["origin"] == "schedule"
    office.store.update_schedule(s["id"], next_run_at="2026-11-04T08:47:00")
    again = office.enqueue_due_schedules(datetime(2026, 11, 4, 8, 51))
    assert again == [], "前回分が終わっていないのに積み増した"


# ---------- 仕事の流れ（承認ゲート） ----------
def test_chat_task_done_and_learns(office):
    t = office.send_message("minato", "AIエージェントの動向を調べて。今後は結論から書いて。")
    office.tick(background=False)
    t = office.store.get_task(t["id"])
    assert t["status"] == "done" and t["runs"] == 1
    assert any("結論から" in m["text"] for m in office.store.list_memories("minato"))
    assert office.sent and "完了" in office.sent[-1]


def test_publish_stops_for_approval_then_resumes(office):
    t = office.send_message("tsumugi", "ブログ記事の下書きを作って公開まで進めて")
    office.tick(background=False)
    t = office.store.get_task(t["id"])
    assert t["status"] == "needs_approval" and t["approval"]["kind"] == "publish"
    assert "承認待ち" in office.sent[-1]
    assert office.overview()["approvals"][0]["id"] == t["id"]

    office.approve(t["id"], note="subで")
    assert office.store.get_task(t["id"])["approved"]["note"] == "subで"
    office.tick(background=False)
    t = office.store.get_task(t["id"])
    assert t["status"] == "done" and t["runs"] == 2


def test_reject_records_reason_as_memory(office):
    t = office.send_message("hibiki", "メールで告知文を配信して")
    office.tick(background=False)
    office.reject(t["id"], note="配信は夜にする")
    assert office.store.get_task(t["id"])["status"] == "rejected"
    assert any("配信は夜にする" in m["text"] for m in office.store.list_memories("hibiki"))
    with pytest.raises(OfficeError):
        office.approve(t["id"])


def test_handoff_for_login(office):
    t = office.send_message("tsumugi", "Noteにログインして下書きを確認して")
    office.tick(background=False)
    assert office.store.get_task(t["id"])["status"] == "needs_you"


def test_one_task_per_bot_at_a_time(office):
    office.send_message("minato", "調べて1")
    office.send_message("minato", "調べて2")
    office.send_message("mamoru", "見回って")
    with office._lock:
        office._running = {}
    first = office.store.claim_next_task(set())
    second = office.store.claim_next_task({first["bot_id"]})
    assert first["bot_id"] != second["bot_id"]


def test_paused_bot_does_not_start(office):
    office.store.update_bot("minato", paused=True)
    t = office.send_message("minato", "調べて")
    assert office.tick(background=False) == []
    assert office.store.get_task(t["id"])["status"] == "queued"


def test_orphaned_running_is_failed_not_retried(office):
    t = office.send_message("minato", "調べて")
    office.store.update_task(t["id"], status="running")
    office.store.fail_orphaned_running()
    assert office.store.get_task(t["id"])["status"] == "failed"
    office.retry(t["id"])
    assert office.store.get_task(t["id"])["status"] == "queued"


def test_runner_failure_marks_failed(office, monkeypatch):
    monkeypatch.setattr(runner, "run", lambda *a, **k: runner.RunResult(ok=False, output="", error="終了コード 1: boom"))
    t = office.send_message("minato", "調べて")
    office.tick(background=False)
    t = office.store.get_task(t["id"])
    assert t["status"] == "failed" and "boom" in t["error"]


def test_memory_limit(office):
    office.store.add_memories("minato", [f"好み{i}" for i in range(MEMORY_LIMIT + 5)])
    mem = office.store.list_memories("minato")
    assert len(mem) == MEMORY_LIMIT and mem[-1]["text"] == f"好み{MEMORY_LIMIT + 4}"


# ---------- LLM実行ルール ----------
def test_runner_never_passes_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    seen = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            seen["env"] = kw["env"]
            self.returncode = 0
            self.pid = 0

        def communicate(self, input=None, timeout=None):
            seen["input"] = input
            return '```ai-office\n{"status":"done","reply":"ok"}\n```', ""

    monkeypatch.setattr(runner.subprocess, "Popen", FakePopen)
    monkeypatch.setenv("AI_OFFICE_RUNNER_CMD", "cat")
    res = runner.run("指示書", {"instruction": "x", "title": "x"}, {"id": "b", "role": ""}, tmp_path)
    assert res.ok and "ANTHROPIC_API_KEY" not in seen["env"] and seen["input"] == "指示書"


def _slow_office(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_OFFICE_RUNNER_CMD", f"{sys.executable} -c 'import time; time.sleep(30)'")
    o = Office(Store(tmp_path / "slow.db"), workdir=tmp_path, runner_mode="custom", notifier=None)
    seed(o, SEED)
    return o


def _wait(pred, seconds=10.0):
    end = time.time() + seconds
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_cancel_stops_running_cli(tmp_path, monkeypatch):
    o = _slow_office(tmp_path, monkeypatch)
    t = o.send_message("minato", "時間のかかる調べもの")
    o.tick()  # 別スレッドで CLI が動き出す
    assert _wait(lambda: t["id"] in o._procs)
    proc = o._procs[t["id"]]
    started = time.time()
    o.cancel(t["id"])
    assert _wait(lambda: proc.poll() is not None, 15) and time.time() - started < 15
    assert _wait(lambda: t["id"] not in o._running)
    t = o.store.get_task(t["id"])
    assert t["status"] == "cancelled" and "止めました" in t["error"]


def test_cancel_from_another_process_is_picked_up_by_worker(tmp_path, monkeypatch):
    o = _slow_office(tmp_path, monkeypatch)
    t = o.send_message("minato", "時間のかかる調べもの")
    o.tick()
    assert _wait(lambda: t["id"] in o._procs)
    proc = o._procs[t["id"]]
    other = Office(Store(tmp_path / "slow.db"), workdir=tmp_path, runner_mode="demo", notifier=None)
    other.cancel(t["id"])  # office.py cancel と同じ（プロセスは持っていない）
    o.tick()
    assert _wait(lambda: proc.poll() is not None, 15)
    assert o.store.get_task(t["id"])["status"] == "cancelled"


def test_cli_approve_and_reject(tmp_path, monkeypatch, capsys):
    from ai_office import __main__ as cli
    monkeypatch.setenv("AI_OFFICE_DB", str(tmp_path / "cli.db"))
    o = Office(Store(tmp_path / "cli.db"), workdir=tmp_path, runner_mode="demo", notifier=None)
    seed(o, SEED)
    t1 = o.send_message("hibiki", "メールで告知文を配信して")
    t2 = o.send_message("tsumugi", "ブログ記事の下書きを作って公開まで進めて")
    o.tick(background=False)
    assert cli.main(["approve", str(t1["id"]), "--note", "19時で"]) == 0
    assert o.store.get_task(t1["id"])["status"] == "queued"
    assert cli.main(["reject", str(t2["id"]), "--note", "公開はまだ"]) == 0
    assert o.store.get_task(t2["id"])["status"] == "rejected"
    assert cli.main(["approve", str(t2["id"])]) == 2  # もう承認待ちではない
    assert cli.main(["status"]) == 0


def test_approval_notice_has_task_number_and_command(office):
    t = office.send_message("hibiki", "メールで告知文を配信して")
    office.tick(background=False)
    assert f"#{t['id']}" in office.sent[-1]
    assert f"office.py approve {t['id']}" in office.sent[-1]


def test_service_plist_is_resident_and_uses_real_paths(tmp_path):
    from ai_office import service
    data = service.build_plist(tmp_path, python="/usr/bin/python3", path_env="/custom/bin")
    assert data["Label"] == service.LABEL
    assert data["ProgramArguments"] == ["/usr/bin/python3", str(ROOT / "office.py"), "--workdir", str(tmp_path),
                                        "serve", "--port", "8765"]
    assert data["WorkingDirectory"] == str(tmp_path)
    assert data["RunAtLoad"] is True and data["KeepAlive"] == {"SuccessfulExit": False}
    assert data["EnvironmentVariables"]["PATH"].startswith("/custom/bin:") and "/opt/homebrew/bin" in data["EnvironmentVariables"]["PATH"]
    assert data["StandardErrorPath"].startswith("/tmp/ai-office")
    raw = service.install(tmp_path, dry_run=True)
    assert "<key>KeepAlive</key>" in raw


def test_no_anthropic_sdk_in_package():
    for f in (ROOT / "ai_office").rglob("*.py"):
        for line in f.read_text("utf-8").splitlines():
            assert not re.match(r"\s*(import anthropic|from anthropic)", line), f


# ---------- サーバー（手元のPCからだけ・画面からだけ） ----------
@pytest.fixture
def server(office):
    httpd = serve(office, "127.0.0.1", 0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _req(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}") if "json" in r.headers.get("Content-Type", "") else r.read()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_server_overview_and_chat(server):
    st, d = _req(server + "/api/overview")
    assert st == 200 and len(d["bots"]) == 4 and d["runner"] == "demo"
    st, d = _req(server + "/api/bots/minato/messages", "POST", {"body": "調べて"}, {"X-AI-Office": "1"})
    assert st == 200 and d["task"]["status"] == "queued"


def test_server_rejects_writes_without_header(server):
    st, d = _req(server + "/api/bots/minato/messages", "POST", {"body": "調べて"})
    assert st == 403


def test_server_rejects_foreign_host(server):
    st, _ = _req(server + "/api/overview", headers={"Host": "evil.example:80"})
    assert st == 403


def test_server_serves_ui_and_shared_kit(server):
    st, html = _req(server + "/")
    assert st == 200 and b"AI\xe7\xa4\xbe\xe5\x93\xa1" in html  # 「AI社員」
    st, css = _req(server + "/ui-kit/ui-kit.css")
    assert st == 200 and b"--ce-bg" in css
    st, _ = _req(server + "/ui-kit/../../.env")
    assert st == 404


def test_server_error_message_is_readable(server):
    st, d = _req(server + "/api/schedules", "POST", {"bot_id": "minato", "spec": "every 1m", "instruction": "x"},
                 {"X-AI-Office": "1"})
    assert st == 400 and "15分" in d["error"]


# ---------- 共通部品 ----------
def test_page_uses_bundled_ui_kit():
    html = (ROOT / "web" / "index.html").read_text("utf-8")
    assert 'href="/ui-kit/ui-kit.css"' in html and 'src="/ui-kit/ui-kit.js"' in html
    assert (ROOT / "web" / "ui-kit" / "ui-kit.css").is_file() and (ROOT / "web" / "ui-kit" / "ui-kit.js").is_file()


# ---------- 作業ディレクトリと設定ファイル ----------
def test_workdir_is_parent_project_when_used_as_submodule(tmp_path, monkeypatch):
    from ai_office import config
    parent = tmp_path / "host"
    pkg = parent / "ai-office"
    pkg.mkdir(parents=True)
    (parent / ".gitmodules").write_text('[submodule "ai-office"]\n\tpath = ai-office\n\turl = x\n')
    monkeypatch.setattr(config, "PKG_DIR", pkg)
    monkeypatch.delenv("AI_OFFICE_WORKDIR", raising=False)
    assert config.resolve_workdir() == parent
    monkeypatch.setenv("AI_OFFICE_WORKDIR", str(tmp_path))
    assert config.resolve_workdir() == tmp_path.resolve()
    assert config.resolve_workdir(str(pkg)) == pkg.resolve()


def test_host_config_plugs_in_rules_bots_and_notify(tmp_path):
    from ai_office import config
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "bots.json").write_text(json.dumps({"bots": [
        {"id": "x1", "name": "テスト", "role": "検証", "hints": ["これを頼む"]}]}, ensure_ascii=False))
    log = tmp_path / "notified.txt"
    (tmp_path / "config" / "ai_office.json").write_text(json.dumps({
        "bots_file": "config/bots.json",
        "house_rules": ["このプロジェクト固有の決まりを守る"],
        "notify_command": [sys.executable, "-c", f"import sys; open({str(log)!r}, 'a').write(sys.argv[1])"],
        "workspace_dir": "work",
    }, ensure_ascii=False))
    host = config.load(tmp_path)
    o = Office(Store(tmp_path / "h.db"), runner_mode="demo", host=host)
    assert o.notifier is None  # 練習モードでは通知しない
    o2 = Office(Store(tmp_path / "h2.db"), runner_mode="custom", host=host)
    assert o2.notifier is not None
    seed(o, host.bots_file)
    bot = o.store.get_bot("x1")
    assert bot["hints"] == ["これを頼む"]
    p = protocol.build_prompt(bot, {"instruction": "x"}, [], [], str(tmp_path), "work/x1", host.house_rules)
    assert "このプロジェクト固有の決まりを守る" in p and "AGENTS.md" in p
    o2.notifier("届いた")
    assert log.read_text() == "届いた"


def test_without_config_uses_examples_and_no_notify(tmp_path):
    from ai_office import config
    host = config.load(tmp_path)
    assert host.bots_file == config.EXAMPLE_BOTS and host.notify_command is None
    o = Office(Store(tmp_path / "n.db"), runner_mode="custom", host=host)
    assert o.notifier is None


def test_missing_runner_command_falls_back(monkeypatch):
    monkeypatch.delenv("AI_OFFICE_RUNNER_CMD", raising=False)
    monkeypatch.delenv("AI_OFFICE_RUNNER", raising=False)
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    assert runner.resolve_mode(None, ["/nonexistent/run.sh"]) == "none"
    assert runner.resolve_mode(None, [sys.executable]) == "custom"


def test_preview_builds_single_file(tmp_path):
    from ai_office.preview import build
    out = build(SEED, tmp_path / "p.html")
    page = out.read_text("utf-8")
    assert "window.AI_OFFICE_DEMO = true" in page and "--ce-bg" in page and "<!DOCTYPE" not in page


# ---------- もらったスキルの置き場所 ----------
def _skill(d: Path, name: str) -> Path:
    (d / name).mkdir(parents=True)
    f = d / name / "SKILL.md"
    f.write_text(f"# {name}\n")
    return f


def test_received_skills_folder_exists_and_is_not_committed():
    gi = (ROOT / "skills" / ".gitignore").read_text("utf-8").split()
    assert "*" in gi and "!README.md" in gi and "!.gitignore" in gi
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "skills"], capture_output=True, text=True).stdout.split()
    assert set(tracked) <= {"skills/README.md", "skills/.gitignore"}


def test_find_skill_prefers_project_then_received(tmp_path):
    from ai_office import config
    own, got = tmp_path / "skills", tmp_path / "received"
    _skill(own, "blog")
    _skill(got, "blog")
    recv = _skill(got, "shared-skill")
    host = config.HostConfig(workdir=tmp_path, skill_dirs=[own], external_skill_dirs=[got])
    assert host.find_skill("blog")["kind"] == "project"
    assert host.find_skill("shared-skill") == {"name": "shared-skill", "path": str(recv), "kind": "external"}
    assert host.find_skill("nothing")["path"] is None
    assert host.find_skill("../etc")["path"] is None  # フォルダの外は見ない
    assert [s["name"] for s in host.list_skills()] == ["blog", "shared-skill"]


def test_skill_dirs_can_be_set_in_config(tmp_path):
    from ai_office import config
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "ai_office.json").write_text(json.dumps({
        "skill_dirs": ["my-skills"], "external_skill_dirs": [str(tmp_path / "gift")]}))
    _skill(tmp_path / "gift", "present")
    host = config.load(tmp_path)
    assert host.skill_dirs == [(tmp_path / "my-skills").resolve()]
    assert host.find_skill("present")["kind"] == "external"


def test_prompt_and_overview_show_skill_paths(tmp_path):
    from ai_office import config
    recv = _skill(tmp_path / "gift", "present")
    host = config.HostConfig(workdir=tmp_path, skill_dirs=[tmp_path / "none"], external_skill_dirs=[tmp_path / "gift"])
    o = Office(Store(tmp_path / "s.db"), runner_mode="demo", host=host, notifier=None)
    o.store.create_bot("テスト", "検証", skills=["present", "missing"], bot_id="t1")
    info = [b for b in o.overview()["bots"] if b["id"] == "t1"][0]["skill_info"]
    assert info[0]["kind"] == "external" and info[1]["path"] is None
    p = protocol.build_prompt(o.store.get_bot("t1"), {"instruction": "x"}, [], [], str(tmp_path), "w", [], info)
    assert str(recv) in p and "もらったスキル" in p and "見つからない" in p


def test_cli_lists_skills(tmp_path, monkeypatch, capsys):
    from ai_office import __main__ as cli
    _skill(tmp_path / "skills", "mine")
    assert cli.main(["--workdir", str(tmp_path), "skills"]) == 0
    out = capsys.readouterr().out
    assert "mine" in out and "もらったスキルを置く場所" in out
