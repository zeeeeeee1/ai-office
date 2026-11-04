"""作業ディレクトリ（AI社員が働く場所）と、その場所ごとの設定を決める。

AI社員オフィスは単体でも、別のプロジェクトにサブモジュールとして入れても動く。
プロジェクト固有のこと（初期メンバー・通知の送り方・守らせるルール・LLM の起動方法）は
コードに書かず、作業ディレクトリの設定ファイルで差し込む。

作業ディレクトリの決め方（上から順に）:
  1. --workdir 引数
  2. 環境変数 AI_OFFICE_WORKDIR
  3. このフォルダが親プロジェクトのサブモジュールなら、その親プロジェクト
  4. いまのカレントディレクトリ

設定ファイル（どちらか。無くてもよい）:
  <作業ディレクトリ>/config/ai_office.json
  <作業ディレクトリ>/ai_office.json

設定できる項目（すべて省略可）:
  bots_file        初期メンバーの JSON（作業ディレクトリからの相対パス）。省略時は examples/bots.example.json
  workspace_dir    社員ごとの成果物の置き場所（既定 output/ai-office）
  house_rules      指示書の「必ず守ること」に足す行の配列
  runner_command   LLM CLI の起動コマンド（配列・指示書は標準入力）。先頭のファイルが無ければ自動選択に戻る
  runner_env       CLI に渡す環境変数。値の {workdir} {timeout} は置き換わる
  notify_command   通知コマンド（配列）。本文は最後の引数として渡る
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PKG_DIR = Path(__file__).resolve().parents[1]
EXAMPLE_BOTS = PKG_DIR / "examples" / "bots.example.json"
CONFIG_CANDIDATES = ("config/ai_office.json", "ai_office.json")


def _is_submodule_of(parent: Path) -> bool:
    gm = parent / ".gitmodules"
    if not gm.is_file():
        return False
    name = PKG_DIR.name
    for line in gm.read_text("utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("path") and line.split("=", 1)[-1].strip() == name:
            return True
    return False


def resolve_workdir(explicit: Optional[str] = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("AI_OFFICE_WORKDIR")
    if env:
        return Path(env).expanduser().resolve()
    if _is_submodule_of(PKG_DIR.parent):
        return PKG_DIR.parent
    return Path.cwd().resolve()


@dataclass
class HostConfig:
    workdir: Path
    bots_file: Path = EXAMPLE_BOTS
    workspace_dir: str = "output/ai-office"
    house_rules: list = field(default_factory=list)
    runner_command: Optional[list] = None
    runner_env: dict = field(default_factory=dict)
    notify_command: Optional[list] = None
    source: Optional[Path] = None

    def office_cli(self) -> str:
        """通知に載せるコマンドの書き方（作業ディレクトリから打てる形）。"""
        script = PKG_DIR / "office.py"
        try:
            rel = script.relative_to(self.workdir)
            return f"python3 {rel}"
        except ValueError:
            return f"python3 {script}"


def load(workdir: Path) -> HostConfig:
    cfg = HostConfig(workdir=workdir)
    for rel in CONFIG_CANDIDATES:
        p = workdir / rel
        if p.is_file():
            data = json.loads(p.read_text("utf-8"))
            cfg.source = p
            break
    else:
        return cfg
    if data.get("bots_file"):
        cfg.bots_file = (workdir / data["bots_file"]).resolve()
    cfg.workspace_dir = str(data.get("workspace_dir") or cfg.workspace_dir)
    cfg.house_rules = [str(x) for x in data.get("house_rules", []) if str(x).strip()]
    if data.get("runner_command"):
        cfg.runner_command = [os.path.expanduser(str(x)) for x in data["runner_command"]]
    cfg.runner_env = {str(k): str(v) for k, v in (data.get("runner_env") or {}).items()}
    if data.get("notify_command"):
        cfg.notify_command = [str(x) for x in data["notify_command"]]
    return cfg
