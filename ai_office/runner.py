"""AI社員の頭脳を動かす係。LLM は手元の CLI（Claude Code / opencode など）に任せ、ここはプロセスを起動するだけ。

API キーで直接 LLM を呼ばない。子プロセスの環境からも ANTHROPIC_API_KEY を外し、
サブスクリプションでログイン済みの CLI だけが動くようにする。LLM の SDK も読み込まない。

選び方（環境変数 AI_OFFICE_RUNNER）:
  auto        既定。設定ファイルの runner_command → claude → opencode の順で、使えるものを使う
  claude      claude -p
  opencode    opencode run
  custom      AI_OFFICE_RUNNER_CMD（または設定ファイルの runner_command）を使う。指示書は標準入力
  demo        LLM を呼ばない練習用（画面確認・テスト用。ファイルも書かない）
"""
from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .protocol import RESULT_FENCE

DEFAULT_TIMEOUT = int(os.environ.get("AI_OFFICE_TIMEOUT", "3600"))
BLOCKED_ENV = ("ANTHROPIC_API_KEY",)


class RunnerError(RuntimeError):
    pass


@dataclass
class RunResult:
    ok: bool
    output: str
    error: str = ""


def _custom_command(host_command: Optional[list]) -> Optional[list]:
    env = os.environ.get("AI_OFFICE_RUNNER_CMD")
    if env:
        return shlex.split(env)
    if host_command:
        exe = host_command[0]
        if Path(exe).is_file() or shutil.which(exe):
            return list(host_command)
    return None


def resolve_mode(mode: Optional[str] = None, host_command: Optional[list] = None) -> str:
    mode = (mode or os.environ.get("AI_OFFICE_RUNNER") or "auto").strip()
    if mode != "auto":
        return mode
    if _custom_command(host_command):
        return "custom"
    if shutil.which("claude"):
        return "claude"
    if shutil.which("opencode"):
        return "opencode"
    return "none"


def _command(mode: str, prompt: str, host_command: Optional[list] = None) -> tuple[list[str], Optional[str]]:
    """(コマンド, 標準入力) を返す。"""
    if mode == "custom":
        cmd = _custom_command(host_command)
        if not cmd:
            raise RunnerError("runner_command（または AI_OFFICE_RUNNER_CMD）のコマンドが見つかりません")
        return cmd, prompt
    if mode == "claude":
        return ["claude", "-p", "--output-format", "text"], prompt
    if mode == "opencode":
        return ["opencode", "run", prompt], None
    raise RunnerError(
        "AI社員を動かす CLI が見つかりません。Claude Code（claude）か opencode を入れるか、"
        "画面確認だけなら --demo で起動してください。")


def stop_process(proc: subprocess.Popen) -> None:
    """CLI とその子プロセス（ブラウザ操作など）をまとめて止める。"""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, AttributeError):
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError):
            proc.kill()


def run(prompt: str, task: dict, bot: dict, workdir: Path, mode: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        on_start: Optional[Callable[[subprocess.Popen], None]] = None,
        host_command: Optional[list] = None, host_env: Optional[dict] = None,
        model: Optional[str] = None) -> RunResult:
    """CLI を1回動かす。on_start には起動したプロセスが渡る（途中で止めるため）。"""
    mode = resolve_mode(mode, host_command)
    if mode == "demo":
        return RunResult(ok=True, output=demo_reply(task, bot))
    try:
        cmd, stdin = _command(mode, prompt, host_command)
    except RunnerError as e:
        return RunResult(ok=False, output="", error=str(e))
    env = {k: v for k, v in os.environ.items() if k not in BLOCKED_ENV}
    env["AI_OFFICE_WORKDIR"] = str(workdir)
    if model:  # 社員ごとのモデル。claude はオプションで、ほかの CLI には環境変数で渡す
        env["AI_OFFICE_MODEL"] = model
        env["ANTHROPIC_MODEL"] = model
        if mode == "claude":
            cmd = [*cmd, "--model", model]
        elif mode == "opencode":
            cmd = [cmd[0], cmd[1], "--model", model, *cmd[2:]]
    for k, v in (host_env or {}).items():
        if k not in BLOCKED_ENV:
            env[k] = v.replace("{workdir}", str(workdir)).replace("{timeout}", str(timeout))
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                cwd=str(workdir), env=env, start_new_session=True)
    except FileNotFoundError as e:
        return RunResult(ok=False, output="", error=f"CLI が見つかりません: {e}")
    if on_start:
        on_start(proc)
    try:
        out, err = proc.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_process(proc)
        return RunResult(ok=False, output="", error=f"{timeout // 60}分で終わらなかったため止めました")
    if proc.returncode != 0:
        tail = (err or out or "").strip()[-1500:]
        return RunResult(ok=False, output=out or "", error=f"終了コード {proc.returncode}: {tail}")
    return RunResult(ok=True, output=out or "")


# ---- 練習用（demo）: LLM を呼ばずに、承認の流れまで一通り試せる返事を作る ----
# web/app.js の DemoBackend も同じ判定をする（画面プレビュー用）。
DEMO_APPROVAL_WORDS = [
    ("publish", ("公開", "出品", "予約公開")),
    ("send", ("投稿", "配信", "送って", "送信", "告知して")),
    ("pay", ("購入", "課金", "有料プラン", "支払")),
    ("delete", ("削除", "消して", "片付けて")),
]
DEMO_HANDOFF_WORDS = ("ログイン", "認証", "CAPTCHA", "二段階")
DEMO_LEARN_WORDS = ("いつも", "今後は", "次から", "毎回")


def demo_reply(task: dict, bot: dict) -> str:
    import json

    text = task["instruction"]
    learned = [line.strip() for line in text.replace("。", "。\n").splitlines()
               if any(w in line for w in DEMO_LEARN_WORDS)][:2]
    if task.get("approved"):
        result = {"status": "done", "reply": f"承認どおり「{task['approved'].get('action')}」を実行しました（練習モードなので実際には何もしていません）。",
                  "summary": f"承認後に実行: {task['approved'].get('action')}", "learned": learned, "artifacts": []}
    elif any(w in text for w in DEMO_HANDOFF_WORDS):
        result = {"status": "needs_you", "reply": "ログイン画面で止まりました。ここは代わりに通れないので、ログインをお願いします。",
                  "summary": "ログイン待ち",
                  "approval": {"kind": "handoff", "action": "ログインしてから『対応した』を押す", "detail": text[:80]},
                  "learned": learned, "artifacts": []}
    else:
        kind = next((k for k, words in DEMO_APPROVAL_WORDS if any(w in text for w in words)), None)
        if kind:
            result = {"status": "needs_approval",
                      "reply": f"下書きまで作りました。ここから先は{bot['role'] or '担当'}として勝手に進めず、承認をもらってから実行します。",
                      "summary": "下書き完了・承認待ち",
                      "approval": {"kind": kind, "action": text.splitlines()[0][:60], "detail": "練習モードの下書き"},
                      "learned": learned, "artifacts": [f"output/ai-office/{bot['id']}/draft.md"]}
        else:
            result = {"status": "done", "reply": f"「{task['title']}」を片付けました（練習モードの返事です）。",
                      "summary": f"{task['title']} 完了", "learned": learned, "artifacts": []}
    return f"{result['reply']}\n\n```{RESULT_FENCE}\n{json.dumps(result, ensure_ascii=False)}\n```\n"
