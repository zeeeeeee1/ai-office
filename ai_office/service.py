"""AI社員オフィスを Mac で常駐させる（launchd の LaunchAgent）。

その Mac の実パス（Python・このフォルダ・作業ディレクトリ・PATH）を入れた plist を
~/Library/LaunchAgents/ に書き出す。リポジトリには plist を置かない（パスが人ごとに違うため）。
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import PKG_DIR

LABEL = os.environ.get("AI_OFFICE_SERVICE_LABEL", "local.ai-office")
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
STDOUT_LOG = "/tmp/ai-office.log"
STDERR_LOG = "/tmp/ai-office-error.log"
EXTRA_PATHS = ["~/.opencode/bin", "~/.local/bin", "~/.claude/local", "/opt/homebrew/bin", "/usr/local/bin",
               "/usr/bin", "/bin", "/usr/sbin", "/sbin"]


def build_plist(workdir: Path, python: str = sys.executable, port: int = 8765,
                path_env: str | None = None, allow_broad: bool = False) -> dict:
    paths: list[str] = []
    for p in (path_env if path_env is not None else os.environ.get("PATH", "")).split(":") + EXTRA_PATHS:
        p = os.path.expanduser(p)
        if p and p not in paths:
            paths.append(p)
    env = {"PATH": ":".join(paths), "PYTHONUNBUFFERED": "1"}
    # 起動時に付けていた AI_OFFICE_* の設定（頭脳の選び方など）は常駐にも引き継ぐ
    env.update({k: v for k, v in os.environ.items() if k.startswith("AI_OFFICE_") and k != "AI_OFFICE_SERVICE_LABEL"})
    return {
        "Label": LABEL,
        "ProgramArguments": [python, str(PKG_DIR / "office.py"), "--workdir", str(workdir), "serve", "--port", str(port),
                             *(["--allow-broad-workdir"] if allow_broad else [])],
        "WorkingDirectory": str(workdir),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        # 落ちたら起こし直す。CLI が見つからない等で起動に失敗し続けるときは60秒おきに留める
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 60,
        "StandardOutPath": STDOUT_LOG,
        "StandardErrorPath": STDERR_LOG,
    }


def plist_path() -> Path:
    return AGENTS_DIR / f"{LABEL}.plist"


def install(workdir: Path, port: int = 8765, load: bool = False, dry_run: bool = False,
            allow_broad: bool = False) -> str:
    data = build_plist(workdir, port=port, allow_broad=allow_broad)
    raw = plistlib.dumps(data).decode("utf-8")
    if dry_run:
        return raw
    target = plist_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(raw, "utf-8")
    msg = [f"書き出しました: {target}"]
    if load:
        _launchctl(["bootout", f"gui/{os.getuid()}/{LABEL}"], check=False)
        _launchctl(["bootstrap", f"gui/{os.getuid()}", str(target)])
        msg.append(f"常駐を始めました → http://127.0.0.1:{port}（ログ: {STDERR_LOG}）")
    else:
        msg.append(f"常駐を始めるとき: launchctl bootstrap gui/$(id -u) {target}")
    return "\n".join(msg)


def uninstall() -> str:
    _launchctl(["bootout", f"gui/{os.getuid()}/{LABEL}"], check=False)
    target = plist_path()
    if target.exists():
        target.unlink()
        return f"常駐を止めて取り外しました: {target}"
    return "常駐は登録されていませんでした"


def _launchctl(args: list[str], check: bool = True) -> None:
    if sys.platform != "darwin":
        raise SystemExit("launchd は macOS だけで使えます（plist の中身は --dry-run で確認できます）")
    proc = subprocess.run(["launchctl", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"launchctl {' '.join(args)} に失敗: {(proc.stderr or proc.stdout).strip()}")
