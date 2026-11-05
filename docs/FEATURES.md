# 機能一覧

AI社員オフィスでできること・まだできないことと、それを確かめているテスト（`tests/test_ai_office.py`）の一覧です。
テストは GitHub Actions で Python 3.10 と 3.13 の両方で毎回実行しています。

## できること

| 機能 | 中身 | 確かめているテスト |
|---|---|---|
| AI社員の名簿 | 名前・担当・仕事の進め方・使うスキル・モデルを持つ社員を置ける。画面から増やせる | `test_example_bots_are_valid` ほか |
| 外にある AI社員 | 別の場所にある定義ファイルをコピーせずに使う。起動のたびに読み直す。無くなったらお休みにする | `test_discover_reads_headers_only` `test_sync_staff_adds_updates_and_marks_missing` `test_prompt_points_to_external_file_and_does_not_copy_it` |
| 手元だけの設定 | `~/.ai-office/config.json` に人ごとの場所を書く。どのリポジトリにも入らない | `test_local_config_adds_staff_dirs_without_touching_the_repo` |
| モデルを選ぶ | 社員ごとにモデルを持たせ、CLI に渡す | `test_model_is_passed_to_the_cli` |
| チャットで任せる | 送ったメッセージが仕事になり順番に処理される。同じ社員は同時に1件 | `test_chat_task_done_and_learns` `test_one_task_per_bot_at_a_time` |
| 承認ゲート | 公開・送る・払う・消す・外に出すの直前で止まる。承認された操作だけ実行しに戻る | `test_publish_stops_for_approval_then_resumes` |
| 却下を覚える | 却下の理由を覚え、次から守る | `test_reject_records_reason_as_memory` |
| 本人への引き継ぎ | ログインなどは回避せず、本人の操作を待つ | `test_handoff_for_login` |
| 覚える | 仕事のたびに好みを覚える（1人30件まで） | `test_memory_limit` |
| 定期の仕事 | 毎日・平日・毎週・間隔。前回分が終わっていなければ積み増さない | `test_next_run` `test_due_schedule_enqueues_once` |
| 作業中に止める | CLI を子プロセスごと止める。別プロセスからの取り消しも拾う | `test_cancel_stops_running_cli` `test_cancel_from_another_process_is_picked_up_by_worker` |
| コマンドで操作 | `approve` `reject` `cancel` `status` `skills` `staff` | `test_cli_approve_and_reject` `test_cli_lists_skills` `test_cli_lists_external_staff` |
| 通知 | 完了・承認待ち・失敗のときだけ。番号と承認コマンドを載せる | `test_approval_notice_has_task_number_and_command` |
| もらったスキル | `skills/` に置く（git に入らない）。自分のスキルを先に使う | `test_find_skill_prefers_project_then_received` ほか |
| 広すぎる場所で動かさない | ホーム全体・OS の場所などでは起動しない | `test_workdir_guard_*` `test_serve_refuses_broad_workdir_but_demo_and_flag_pass` |
| 手元だけで開く画面 | `127.0.0.1` のみ。書き込みはヘッダー必須。Host を確かめる | `test_server_rejects_writes_without_header` `test_server_rejects_foreign_host` |
| API キーを使わない | 子プロセスに `ANTHROPIC_API_KEY` を渡さない。LLM の SDK を読み込まない | `test_runner_never_passes_api_key` `test_no_anthropic_sdk_in_package` |
| 常駐（macOS） | その Mac の実パスで LaunchAgent を書き出す | `test_service_plist_is_resident_and_uses_real_paths` |
| 1ファイルのプレビュー | サーバー無しで画面を試せる | `test_preview_builds_single_file` |

## まだできないこと

| 機能 | 状態 |
|---|---|
| プロジェクト（リポジトリ）を選ぶ | 1つのオフィスで扱える作業フォルダは1つ |
| 仕事を頼むときにモデルを変える | 社員ごとのモデルだけ。頼むたびには変えられない |
| タスクごとのチャット | チャットは社員ごとに1本。仕事ごとには分かれていない |
| スマホのブラウザから画面を開く | 手元の PC だけ。Slack 通知と `approve` コマンドで代わりにしている |
| ブラウザ操作の自動テスト | 画面の動きは手で確かめている。自動テストは API とサーバーまで |
| 本物の CLI での自動テスト | テストは練習モードと差し替えたコマンドで行っている |
