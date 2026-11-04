"""画面を1ファイルのプレビュー（サンプルデータ入り・サーバー不要）に書き出す。

ui-kit.css / app.css / ui-kit.js / app.js と初期メンバー設定を埋め込み、
window.AI_OFFICE_DEMO を立てて DemoBackend で動かす。LLM は呼ばない。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
KIT = WEB / "ui-kit"


def _script_safe(text: str) -> str:
    return text.replace("</", "<\\/")


def build(seed_path: Path, out: Path) -> Path:
    html = (WEB / "index.html").read_text("utf-8")
    body = re.search(r"<!--AO:BODY-->(.*)<!--/AO:BODY-->", html, re.S).group(1)
    seed = json.loads(Path(seed_path).read_text("utf-8"))
    css = (KIT / "ui-kit.css").read_text("utf-8") + "\n" + (WEB / "app.css").read_text("utf-8")
    css += "\nbody { margin: 0; background: var(--ce-bg); color: var(--ce-text); }\n"
    page = (
        "<title>AI社員オフィス</title>\n"
        f"<style>\n{css}</style>\n"
        f'<div class="ce-kit">{body}</div>\n'
        f"<script>window.AI_OFFICE_DEMO = true; window.AI_OFFICE_SEED = {_script_safe(json.dumps(seed, ensure_ascii=False))};</script>\n"
        f"<script>\n{_script_safe((KIT / 'ui-kit.js').read_text('utf-8'))}\n</script>\n"
        f"<script>\n{(WEB / 'app.js').read_text('utf-8')}\n</script>\n"
    )
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, "utf-8")
    return out
