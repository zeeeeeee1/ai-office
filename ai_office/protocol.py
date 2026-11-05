"""AI社員に渡す指示書（プロンプト）の組み立てと、返事の読み取り。

AI社員は返事の最後に ```ai-office で始まる JSON ブロックを1つ書く決まり。
ここで読み取って、仕事の状態（完了／承認待ち／本人の手が要る）を決める。
"""
from __future__ import annotations

import json
import re
from typing import Optional

RESULT_FENCE = "ai-office"
STATUS_MAP = {"done": "done", "needs_approval": "needs_approval", "needs_you": "needs_you", "failed": "failed"}

# 承認が要る操作の種類（取り消せない操作は本人が決める）
APPROVAL_KINDS = {
    "publish": "公開する",
    "send": "送る・投稿する",
    "pay": "払う・有料化する",
    "delete": "消す",
    "share": "外に出す",
    "handoff": "本人の操作が要る",
    "other": "その他",
}

_FENCE_RE = re.compile(r"```" + RESULT_FENCE + r"\s*\n(.*?)\n```", re.S)


def _skill_line(info: dict) -> str:
    if not info.get("path"):
        return f"- `{info['name']}`（見つからない。使わずに進め、返事でそのことを伝える）"
    label = "もらったスキル" if info.get("kind") == "external" else "このプロジェクトのスキル"
    return f"- `{info['name']}` → `{info['path']}`（{label}。使う前にこのファイルを読む）"


def _instructions(bot: dict) -> str:
    src = bot.get("source_path")
    if src:  # 外にある定義。中身は写さず、場所だけ渡して読ませる
        return (f"次のファイルに書いてある。作業を始める前に必ず Read し、その内容に全面的に従うこと: `{src}`\n"
                "そのファイルが別のファイル（`@パス` や「次のファイルを読む」）を指していれば、それも読む。")
    return bot.get("instructions") or "任された仕事を最後までやり切る。"


def build_prompt(bot: dict, task: dict, memories: list[dict], history: list[dict], workdir: str,
                 workspace: str, house_rules: Optional[list] = None,
                 skill_info: Optional[list] = None) -> str:
    if skill_info is None:  # 場所が分からないときは名前だけ渡す
        skill_info = [{"name": s, "path": f"skills/{s}/SKILL.md", "kind": "project"} for s in bot.get("skills") or []]
    skill_lines = "\n".join(_skill_line(i) for i in skill_info) \
        or "- 指定なし（作業ディレクトリの案内から必要なものを探す）"
    memory_lines = "\n".join(f"- {m['text']}" for m in memories) or "- まだ無し"
    convo = []
    for m in history[-12:]:
        who = {"me": "本人", "bot": bot["name"], "system": "システム"}.get(m["sender"], m["sender"])
        convo.append(f"{who}: {m['body'][:600]}")
    convo_text = "\n".join(convo) or "（まだ会話なし）"

    approved = task.get("approved")
    approved_text = ""
    if approved:
        approved_text = (
            "\n## 承認済みの操作\n"
            f"本人が次の操作を承認しました: {approved.get('action', '')}（{approved.get('detail', '')}）\n"
            f"本人のメモ: {approved.get('note') or 'なし'}\n"
            "この操作だけ実行してよい。ほかに承認が要る操作が出てきたら、また needs_approval で止める。\n"
        )

    extra_rules = "".join(f"- {r}\n" for r in (house_rules or []))
    return f"""あなたは AI社員「{bot['name']}」です。担当は「{bot.get('role', '')}」。
同僚として本人から仕事を任されています。作業はこの作業ディレクトリ（{workdir}）の中で行います。

## あなたの仕事の進め方
{_instructions(bot)}

## 使ってよいスキル
{skill_lines}

## 覚えていること（本人の好み・進め方。前の仕事で学んだこと）
{memory_lines}

## 最近の会話
{convo_text}

## 今回の仕事
{task['instruction']}
{approved_text}
## 必ず守ること
- 着手前に、作業ディレクトリに AGENTS.md / CLAUDE.md があれば読み、そこに書かれた決まりに従う。
{extra_rules}- 公開・送る（投稿・配信）・払う・消す・外に出す（他者の素材・個人情報・出典）は、自分で実行しない。実行の直前で止め、status を needs_approval にして、何をどこへするかを approval に書く。
- ログイン・CAPTCHA・本人確認が必要になったら回避しない。status を needs_you にして、本人にやってほしいことを approval に書く。
- 成果物は `{workspace}` に置き、パスを artifacts に書く。
- 途中で質問のために止まらない。迷ったら妥当な方を選び、選んだ理由を返事に書く。

## 返し方
本人への返事を短く書いたあと、最後に次のブロックを1つだけ書く（JSON・日本語）。
```{RESULT_FENCE}
{{"status": "done か needs_approval か needs_you か failed",
 "reply": "本人へのひとこと（何をしたか・次に何が要るか）",
 "summary": "仕事一覧に出す1行の結果",
 "approval": {{"kind": "publish/send/pay/delete/share/handoff/other", "action": "承認してほしい操作", "detail": "対象・宛先・中身"}},
 "learned": ["今回わかった本人の好み・進め方（次から使う。無ければ空）"],
 "artifacts": ["作ったファイルのパス"]}}
```
"""


def parse_result(text: str) -> dict:
    """CLI の出力から結果ブロックを読む。無い・壊れているときも落とさず扱える形にする。"""
    text = text or ""
    blocks = _FENCE_RE.findall(text)
    data: Optional[dict] = None
    if blocks:
        try:
            data = json.loads(blocks[-1])
        except json.JSONDecodeError:
            data = None
    visible = _FENCE_RE.sub("", text).strip()

    if not isinstance(data, dict):
        return {
            "status": "done",
            "reply": visible[-2000:] or "（返事がありませんでした）",
            "summary": (visible.splitlines()[0][:80] if visible else "結果ブロックなし"),
            "approval": None,
            "learned": [],
            "artifacts": [],
            "format_ok": False,
        }

    status = STATUS_MAP.get(str(data.get("status", "done")).strip(), "done")
    approval = data.get("approval") if isinstance(data.get("approval"), dict) else None
    if status in ("needs_approval", "needs_you"):
        approval = approval or {}
        kind = approval.get("kind") if approval.get("kind") in APPROVAL_KINDS else (
            "handoff" if status == "needs_you" else "other")
        approval = {"kind": kind, "action": str(approval.get("action") or "内容を確認してください"),
                    "detail": str(approval.get("detail") or "")}
    else:
        approval = None

    def _list(v) -> list[str]:
        return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []

    reply = str(data.get("reply") or visible or "").strip()
    return {
        "status": status,
        "reply": reply[-2000:] or "（返事がありませんでした）",
        "summary": str(data.get("summary") or (reply.splitlines()[0] if reply else ""))[:120],
        "approval": approval,
        "learned": _list(data.get("learned"))[:5],
        "artifacts": _list(data.get("artifacts"))[:20],
        "format_ok": True,
    }
