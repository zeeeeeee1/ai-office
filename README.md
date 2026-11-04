# AI社員オフィス（ai-office）

チャットで同僚に頼むように仕事を任せると、AI社員がバックグラウンドで動き続け、
**完了したとき・承認が要るとき・あなたの操作が要るときだけ戻ってくる**、手元のPCで動くエージェントの職場です。

頭脳には手元の Claude Code（`claude`）や opencode の CLI を使います。API キーで LLM を直接呼ばないので、
CLI にログインしているサブスクリプションの範囲で動きます。Python の標準ライブラリだけで書いてあり、追加のインストールはいりません。

## できること

| 機能 | 中身 |
|---|---|
| AI社員の名簿 | 名前・担当・仕事の進め方・使うスキルを持つ社員を何人でも置ける |
| チャットで任せる | 送ったメッセージが「仕事」になり、順番に処理される。同じ社員は同時に1件、全体の同時実行数は `--parallel`（既定2） |
| 24時間動く | ワーカーが常駐し、画面を閉じても仕事は止まらない。Mac なら `install-service` で常駐させられる |
| 戻ってくるのは要るときだけ | 完了／承認待ち／対応のお願い／失敗のときだけ通知する（通知の送り方は設定で差し込む） |
| 承認ゲート | 公開・送る・払う・消す・外に出すの直前で止まり、承認された操作だけ実行しに戻る。却下の理由は覚えて、次から守る |
| あなたへの引き継ぎ | ログイン・CAPTCHA・本人確認は回避せず、「対応した・続けて」を押すまで待つ |
| 覚える | 仕事のたびに好み・進め方を覚える（1人30件まで）。画面から足す・忘れさせるもできる |
| 定期の仕事 | `daily 08:47` / `weekdays 09:10` / `weekly mon 10:05` / `every 2h`。前回分が終わっていなければ積み増さない |
| 止める | 作業中の仕事を画面かコマンドで止められる（CLI を子プロセスごと止める） |
| 実行ログ | 受付・着手・完了・承認・却下・覚えたことを時系列で残す |

## はじめる

```bash
git clone https://github.com/zeeeeeee1/ai-office.git
```

```bash
cd ai-office
```

LLM を呼ばずに画面と承認の流れだけ試すとき:

```bash
python3 office.py serve --demo
```

ブラウザで http://127.0.0.1:8765 を開きます。例のメンバー4人（`examples/bots.example.json`）が入っています。

本番で動かすとき（`claude` か `opencode` にログイン済みであること）:

```bash
python3 office.py --workdir ~/your-project serve
```

`--workdir` は AI社員が働くフォルダです。AI社員はそこの `AGENTS.md` / `CLAUDE.md` を読んでから動きます。

## 自分のプロジェクトに組み込む

プロジェクトのリポジトリにサブモジュールとして入れると、そのプロジェクトが作業ディレクトリになります（`--workdir` を付けなくてよい）。

```bash
git submodule add https://github.com/zeeeeeee1/ai-office.git ai-office
```

```bash
python3 ai-office/office.py serve
```

プロジェクト固有のことは、プロジェクト側の `config/ai_office.json` に書きます。例は `examples/ai_office.example.json` です。

| 項目 | 中身 |
|---|---|
| `bots_file` | 初期メンバーの JSON（作業ディレクトリからの相対パス） |
| `workspace_dir` | 社員ごとの成果物の置き場所（既定 `output/ai-office`） |
| `house_rules` | 指示書の「必ず守ること」に足す行 |
| `runner_command` | LLM CLI の起動コマンド（指示書は標準入力）。先頭のコマンドが見つからなければ自動選択に戻る |
| `runner_env` | CLI に渡す環境変数。`{workdir}` `{timeout}` は置き換わる |
| `notify_command` | 通知コマンド。本文は最後の引数として渡る（Slack・メールなどへの送り方はプロジェクト側で用意する） |

## 画面を開かずに操作する

```bash
python3 office.py status
```

```bash
python3 office.py approve 12 --note "19時で"
```

```bash
python3 office.py reject 12 --note "配信は夜にする"
```

```bash
python3 office.py cancel 12
```

通知の本文には仕事の番号と、このコマンドが載ります。

## Mac で常駐させる

```bash
python3 office.py --workdir ~/your-project install-service --load
```

その Mac の実パスを入れた LaunchAgent を `~/Library/LaunchAgents/` に書き出して常駐を始めます。ログインすると自動で起き、落ちたら起こし直します。
中身だけ見るなら `--dry-run`、やめるときは `python3 office.py uninstall-service` です。ログは `/tmp/ai-office-error.log` に出ます。

## 頭脳（LLM CLI）の選び方

| `AI_OFFICE_RUNNER` | 使うもの |
|---|---|
| `auto`（既定） | 設定の `runner_command` → `claude -p` → `opencode run` の順で、見つかったもの |
| `claude` / `opencode` | それぞれを固定で使う |
| `custom` | `AI_OFFICE_RUNNER_CMD` か `runner_command`（指示書は標準入力） |
| `demo` | LLM を呼ばない練習用（ファイルも書かない） |

1件の上限時間は `AI_OFFICE_TIMEOUT`（秒・既定3600）です。
AI社員は返事の最後に ` ```ai-office ` の JSON ブロックを書く決まりで、それを読んで状態を決めます（`ai_office/protocol.py`）。

## 安全のために決めていること

- サーバーは `127.0.0.1` にだけ開きます。Host ヘッダーが手元以外なら拒否します。
- 書き込み系の API は `X-AI-Office: 1` ヘッダーが必要です。別サイトのページから勝手に仕事を投げられません。
- 子プロセスには `ANTHROPIC_API_KEY` を渡しません。
- ワーカーが途中で止まった仕事は「失敗」にします（同じ内容のまま自動で再実行しない）。原因を見てから「やり直す」を押します。
- 承認ゲートは指示書で AI社員に守らせるものです。指示書だけでは操作を物理的に止められないので、公開や配信の前に止まるチェックはプロジェクト側にも置いてください。

## 置き場所

| パス | 中身 |
|---|---|
| `ai_office/` | 本体（`store.py` データ／`office.py` 受付・承認・ワーカー／`runner.py` CLI の起動と停止／`protocol.py` 指示書と返事の読み取り／`schedule.py` 定期の仕事／`server.py` 画面と API／`service.py` 常駐／`config.py` 作業ディレクトリと設定） |
| `web/` | 画面（`index.html` / `app.js` / `app.css`）と共通部品 `web/ui-kit/`（配色トークン・タブ・カード・表・バッジ。ほかの管理画面からも読み込める） |
| `examples/` | 初期メンバーと設定ファイルの例 |
| `data/office.db` | 会話・仕事の記録（SQLite・git 管理外） |
| `tests/` | テスト（`python3 -m pytest tests`） |

## ライセンス

MIT License。詳しくは [LICENSE](LICENSE) を見てください。
