"""使い方（作業ディレクトリで。単体で使うときは python3 office.py ...）:

  python3 ai-office/office.py serve            # 画面＋ワーカーを起動 → http://127.0.0.1:8765
  python3 ai-office/office.py serve --demo     # LLM を呼ばない練習モード（画面確認用・別DB）
  python3 ai-office/office.py seed             # 初期メンバーを入れる（serve でも自動で入る）
  python3 ai-office/office.py status           # 承認待ち・作業中の一覧（画面を開かずに確認）
  python3 ai-office/office.py approve 12       # 12番の仕事を承認（スマホの Claude からも頼める）
  python3 ai-office/office.py reject 12 --note "理由"   # 却下（理由は次から守られる）
  python3 ai-office/office.py cancel 12        # 取り消す（作業中なら止める）
  python3 ai-office/office.py install-service --load    # Mac で常駐させる（launchd）
  python3 ai-office/office.py uninstall-service         # 常駐をやめる
  python3 ai-office/office.py skills           # スキルを探す場所と、見つかったスキル（📦=もらったスキル）
  python3 ai-office/office.py build-preview    # 1ファイルの画面プレビュー（デモデータ入り）を書き出す

作業ディレクトリと設定ファイルの決め方は ai_office/config.py を参照。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import config, runner
from .office import Office, OfficeError, seed
from .store import WAITING_ON_ME, Store

BASE = config.PKG_DIR


def _host(args) -> config.HostConfig:
    return config.load(config.resolve_workdir(getattr(args, "workdir", None)))


def _db_path(demo: bool) -> Path:
    env = os.environ.get("AI_OFFICE_DB")
    if env:
        return Path(env)
    return BASE / "data" / ("office-demo.db" if demo else "office.db")


def _office(args) -> Office:
    mode = "demo" if getattr(args, "demo", False) else None
    store = Store(_db_path(bool(mode)))
    host = _host(args)
    office = Office(store, runner_mode=mode, max_parallel=getattr(args, "parallel", 2), host=host)
    seed(office, host.bots_file)
    return office


def _guard_workdir(args) -> int:
    """AI社員を動かす前に、作業ディレクトリが広すぎないか確かめる（止めたら 2 を返す）。"""
    try:
        config.check_workdir(_host(args).workdir, allow_broad=getattr(args, "allow_broad_workdir", False)
                             or os.environ.get("AI_OFFICE_ALLOW_BROAD_WORKDIR") == "1")
    except config.WorkdirTooBroad as e:
        print("[ai-office] " + e.message(), file=sys.stderr)
        return 2
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    if not args.demo and _guard_workdir(args):  # 練習モードはファイルに触らないので確かめない
        return 2
    office = _office(args)
    if office.runner_mode == "none":
        print("[ai-office] AI社員を動かす CLI（claude / opencode）が見つかりません。"
              "画面確認だけなら --demo を付けてください。", file=sys.stderr)
        return 2
    office.start()
    httpd = serve(office, args.host, args.port)
    print(f"[ai-office] 起動しました → http://{args.host}:{args.port}  （頭脳: {office.runner_mode}・"
          f"作業ディレクトリ: {office.workdir}）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        office.stop()
    return 0


def cmd_seed(args) -> int:
    office = _office(args)
    print(f"[ai-office] メンバー {len(office.store.list_bots())} 人")
    return 0


def cmd_status(args) -> int:
    store = Store(_db_path(args.demo))
    bots = {b["id"]: b for b in store.list_bots()}
    waiting = store.list_tasks(status=WAITING_ON_ME)
    running = store.list_tasks(status=["running"])
    print(f"承認待ち・対応待ち: {len(waiting)} 件 / 作業中: {len(running)} 件")
    for t in waiting + running:
        name = bots.get(t["bot_id"], {}).get("name", t["bot_id"])
        extra = f" → {t['approval']['action']}" if t.get("approval") else ""
        print(f"  #{t['id']} [{t['status']}] {name}: {t['title']}{extra}")
    return 1 if waiting else 0


def cmd_task_action(args) -> int:
    office = Office(Store(_db_path(args.demo)), runner_mode="demo" if args.demo else None, notifier=None,
                    host=_host(args))
    try:
        if args.cmd == "approve":
            t = office.approve(args.task_id, args.note)
        elif args.cmd == "reject":
            t = office.reject(args.task_id, args.note)
        else:
            t = office.cancel(args.task_id)
    except OfficeError as e:
        print(f"[ai-office] #{args.task_id}: {e}", file=sys.stderr)
        return 2
    label = {"queued": "承認しました。動いているワーカーが続きを進めます", "rejected": "却下しました",
             "cancelled": "取り消しました"}.get(t["status"], t["status"])
    print(f"[ai-office] #{t['id']}「{t['title']}」: {label}")
    return 0


def cmd_install_service(args) -> int:
    from . import service

    if _guard_workdir(args):
        return 2
    print(service.install(_host(args).workdir, port=args.port, load=args.load, dry_run=args.dry_run,
                          allow_broad=args.allow_broad_workdir))
    return 0


def cmd_uninstall_service(args) -> int:
    from . import service

    print(service.uninstall())
    return 0


def cmd_skills(args) -> int:
    host = _host(args)
    print("自分のスキルを探す場所:")
    for d in host.skill_dirs:
        print(f"  {d}{'' if Path(d).is_dir() else '（無い）'}")
    print("もらったスキルを置く場所:")
    for d in host.external_skill_dirs:
        print(f"  {d}{'' if Path(d).is_dir() else '（無い）'}")
    try:
        config.check_workdir(host.workdir)
    except config.WorkdirTooBroad as e:
        print("⚠ " + e.message())
    found = host.list_skills()
    print(f"見つかったスキル: {len(found)} 個")
    for s in found:
        print(f"  {'📦' if s['kind'] == 'external' else '  '} {s['name']}  {s['path']}")
    return 0


def cmd_build_preview(args) -> int:
    from .preview import build

    host = _host(args)
    out = build(host.bots_file, Path(args.out) if args.out else host.workdir / host.workspace_dir / "preview.html")
    print(f"[ai-office] プレビューを書き出しました: {out}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ai-office", description="AI社員オフィス")
    p.add_argument("--workdir", default=None, help="AI社員が働く作業ディレクトリ（省略時は自動で決める）")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="画面とワーカーを起動")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=int(os.environ.get("AI_OFFICE_PORT", "8765")))
    s.add_argument("--parallel", type=int, default=2, help="同時に動ける社員の数")
    s.add_argument("--demo", action="store_true", help="LLM を呼ばない練習モード")
    s.add_argument("--allow-broad-workdir", action="store_true",
                   help="プロジェクトの目印が無いフォルダでも動かす（ホーム全体・OS の場所はこれでも止まる）")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("seed", help="初期メンバーを入れる")
    s.add_argument("--demo", action="store_true")
    s.set_defaults(fn=cmd_seed)
    s = sub.add_parser("status", help="承認待ち・作業中を表示（承認待ちがあれば終了コード1）")
    s.add_argument("--demo", action="store_true")
    s.set_defaults(fn=cmd_status)
    for name, help_text in (("approve", "承認待ち・対応待ちの仕事を承認する"), ("reject", "承認待ちの仕事を却下する"),
                            ("cancel", "仕事を取り消す（作業中なら止める）")):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("task_id", type=int)
        if name != "cancel":
            s.add_argument("--note", default="", help="ひとこと（却下の理由は次から守られる）")
        s.add_argument("--demo", action="store_true")
        s.set_defaults(fn=cmd_task_action, note="")
    s = sub.add_parser("install-service", help="Mac で常駐させる plist を書き出す（--load で開始）")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--load", action="store_true", help="書き出したあと常駐を始める")
    s.add_argument("--dry-run", action="store_true", help="書き出さずに中身を表示")
    s.add_argument("--allow-broad-workdir", action="store_true",
                   help="プロジェクトの目印が無いフォルダでも常駐させる（ホーム全体・OS の場所はこれでも止まる）")
    s.set_defaults(fn=cmd_install_service)
    s = sub.add_parser("uninstall-service", help="常駐をやめて plist を外す")
    s.set_defaults(fn=cmd_uninstall_service)
    s = sub.add_parser("skills", help="スキルを探す場所と、見つかったスキルを表示")
    s.set_defaults(fn=cmd_skills)
    s = sub.add_parser("build-preview", help="1ファイルの画面プレビューを書き出す")
    s.add_argument("--out", default=None, help="既定は <作業ディレクトリ>/output/ai-office/preview.html")
    s.set_defaults(fn=cmd_build_preview)
    s = sub.add_parser("runner", help="使う頭脳（CLI）を表示")
    s.set_defaults(fn=lambda a: print(runner.resolve_mode(None, _host(a).runner_command)) or 0)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
