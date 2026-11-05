"""外にある AI社員の定義を見つける。

中身はコピーしない。ここで読むのは、一覧に出すための見出し（名前・説明・モデル・使うスキル）だけ。
仕事の進め方そのものは、仕事のたびに AI社員（Claude）にそのファイルを読ませる。

置き方（staff_dirs に書いたフォルダの中。どちらでもよい）:
  <フォルダ>/<名前>.md            Claude Code のエージェントと同じ形のファイル
  <フォルダ>/<名前>/STAFF.md      1人1フォルダ（references/ や templates/ を横に置ける）

ファイルの先頭に --- で囲んだ見出し（YAML の簡単な形）があれば使う:
  name: 名前（無ければファイル名）
  description: 担当の説明（画面に出す）
  model: 使うモデル（inherit や空なら CLI の既定）
  skills: 使うスキル名（カンマ区切り か [a, b]）
  avatar: 絵文字
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

SKIP_FILES = {"readme.md", "license.md", "changelog.md", "index.md"}
_ID_RE = re.compile(r"[^a-z0-9_-]+")


def _front_matter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        if ":" not in line or line.startswith((" ", "\t", "#")):
            continue
        k, v = line.split(":", 1)
        out[k.strip().lower()] = v.strip().strip('"').strip("'")
    return out


def _list(v: str) -> list:
    v = (v or "").strip().strip("[]")
    return [x.strip().strip('"').strip("'") for x in v.split(",") if x.strip()]


def staff_id(name: str, path: Path) -> str:
    base = _ID_RE.sub("-", (name or path.stem).lower()).strip("-") or "staff"
    return f"ext-{base}"[:40]


def read_staff(path: Path) -> Optional[dict]:
    """1つのファイルから AI社員の見出しを読む。読めないファイルは None。"""
    try:
        head = Path(path).read_text("utf-8", errors="ignore")[:8000]
    except OSError:
        return None
    fm = _front_matter(head)
    name = fm.get("name") or (path.parent.name if path.name.upper() == "STAFF.MD" else path.stem)
    model = fm.get("model", "")
    return {
        "id": staff_id(name, path),
        "name": name,
        "role": (fm.get("description") or "")[:80],
        "model": "" if model.lower() in ("", "inherit", "default") else model,
        "skills": _list(fm.get("skills", "")),
        "avatar": fm.get("avatar") or "🧩",
        "source_path": str(Path(path).resolve()),
    }


def discover(staff_dirs: list) -> list:
    """staff_dirs の中の AI社員を全部並べる（同じ id は先に見つかった方）。"""
    seen, out = set(), []
    for d in staff_dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        files = sorted(d.glob("*/STAFF.md")) + sorted(p for p in d.glob("*.md") if p.name.lower() not in SKIP_FILES)
        for f in files:
            info = read_staff(f)
            if info and info["id"] not in seen:
                seen.add(info["id"])
                out.append(info)
    return out
