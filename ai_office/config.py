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
  skill_dirs       自分のスキルを探すフォルダ（作業ディレクトリからの相対・~ 可）。既定 skills/ と .claude/skills/
  external_skill_dirs  もらったスキルを置くフォルダ。既定はこのフォルダの skills/（中身は git に入らない）

スキルは「名前のフォルダ/SKILL.md」の形で置く。同じ名前があれば自分のスキルを先に使う。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PKG_DIR = Path(__file__).resolve().parents[1]
EXAMPLE_BOTS = PKG_DIR / "examples" / "bots.example.json"
EXTERNAL_SKILLS_DIR = PKG_DIR / "skills"
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
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
    skill_dirs: list = field(default_factory=list)
    external_skill_dirs: list = field(default_factory=lambda: [EXTERNAL_SKILLS_DIR])
    source: Optional[Path] = None

    def __post_init__(self) -> None:
        if not self.skill_dirs:
            self.skill_dirs = [self.workdir / "skills", self.workdir / ".claude" / "skills"]

    def find_skill(self, name: str) -> dict:
        """スキル名から SKILL.md を探す。自分のスキル → もらったスキルの順。"""
        name = str(name).strip()
        if not SKILL_NAME_RE.match(name):
            return {"name": name, "path": None, "kind": None}
        for kind, dirs in (("project", self.skill_dirs), ("external", self.external_skill_dirs)):
            for d in dirs:
                f = Path(d) / name / "SKILL.md"
                if f.is_file():
                    return {"name": name, "path": str(f), "kind": kind}
        return {"name": name, "path": None, "kind": None}

    def list_skills(self) -> list:
        """探す場所にあるスキルを全部並べる（同じ名前は先に見つかった方だけ）。"""
        seen, out = set(), []
        for kind, dirs in (("project", self.skill_dirs), ("external", self.external_skill_dirs)):
            for d in dirs:
                d = Path(d)
                if not d.is_dir():
                    continue
                for f in sorted(d.glob("*/SKILL.md")):
                    name = f.parent.name
                    if name in seen or not SKILL_NAME_RE.match(name):
                        continue
                    seen.add(name)
                    out.append({"name": name, "path": str(f), "kind": kind})
        return out

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
    if data.get("skill_dirs"):
        cfg.skill_dirs = [_path(workdir, x) for x in data["skill_dirs"]]
    if data.get("external_skill_dirs"):
        cfg.external_skill_dirs = [_path(workdir, x) for x in data["external_skill_dirs"]]
    return cfg


def _path(workdir: Path, value) -> Path:
    p = Path(os.path.expanduser(str(value)))
    return p if p.is_absolute() else (workdir / p).resolve()


# ---------- 作業ディレクトリが広すぎないか ----------
# AI社員は作業ディレクトリの中を読み書きできる。広すぎる場所で動かすと、関係ないファイルまで触れてしまう。

# 中に入るのも止める場所（OS の場所）。上書きできない
SYSTEM_DIRS = ("/System", "/usr", "/bin", "/sbin", "/etc", "/var", "/private", "/Library", "/Applications",
               "/opt", "/dev", "/proc", "/sys", "/boot", "/root", "/Volumes")
# そのものだと止める、ホームの下のよくあるフォルダ（中のプロジェクトフォルダなら使える）
HOME_BROAD_NAMES = ("Desktop", "Documents", "Downloads", "Library", "Pictures", "Movies", "Music", "Public",
                    "Applications", "Dropbox", "Google Drive", "OneDrive", "iCloud Drive", "workspace", "src",
                    "dev", "projects", "Projects", "code", "repos", "github")
# プロジェクトのフォルダらしい目印。どれも無いフォルダは止める（上書きできる）
PROJECT_MARKERS = (".git", "AGENTS.md", "CLAUDE.md", "config/ai_office.json", "ai_office.json")


class WorkdirTooBroad(Exception):
    """作業ディレクトリが広すぎる。overridable が True なら、わかったうえで許可できる。"""

    def __init__(self, path: Path, reason: str, overridable: bool):
        super().__init__(reason)
        self.path, self.reason, self.overridable = path, reason, overridable

    def message(self) -> str:
        lines = [f"作業ディレクトリが広すぎるので止めました: {self.path}",
                 f"  理由: {self.reason}",
                 "  AI社員は作業ディレクトリの中を読み書きできます。プロジェクトのフォルダを --workdir で指定してください。"]
        if self.overridable:
            lines.append("  このフォルダで良いとわかっているなら --allow-broad-workdir を付けてください。")
        return "\n".join(lines)


def check_workdir(workdir: Path, allow_broad: bool = False, home: Optional[Path] = None) -> None:
    """広すぎる作業ディレクトリなら WorkdirTooBroad を投げる。"""
    p = Path(workdir).expanduser().resolve()
    home = Path(home or Path.home()).expanduser().resolve()
    root = Path(p.anchor or "/")

    if p == root:
        raise WorkdirTooBroad(p, "ディスク全体（ルート）です", overridable=False)
    if p == home:
        raise WorkdirTooBroad(p, "ホームフォルダ全体です", overridable=False)
    if p in home.parents:
        raise WorkdirTooBroad(p, "ホームフォルダより上の場所です（ほかの人のホームも入ります）", overridable=False)
    temp_roots = {Path("/tmp"), Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
    if p in temp_roots:
        raise WorkdirTooBroad(p, "一時ファイルの置き場所全体です", overridable=False)
    in_home = home in p.parents
    in_temp = any(t in p.parents for t in temp_roots)  # 一時フォルダの中の作業用フォルダは使える
    for d in SYSTEM_DIRS:
        sd = Path(d)
        if (p == sd or sd in p.parents) and not (in_home or in_temp):
            raise WorkdirTooBroad(p, f"OS が使う場所（{d}）です", overridable=False)

    if allow_broad:
        return
    if p.parent == home and p.name in HOME_BROAD_NAMES:
        raise WorkdirTooBroad(p, f"ホームの「{p.name}」フォルダ全体です（中のプロジェクトフォルダを指定してください）",
                              overridable=True)
    if not any((p / m).exists() for m in PROJECT_MARKERS):
        raise WorkdirTooBroad(p, "プロジェクトのフォルダの目印（.git・AGENTS.md・CLAUDE.md・config/ai_office.json）が"
                              "見つかりません", overridable=True)
