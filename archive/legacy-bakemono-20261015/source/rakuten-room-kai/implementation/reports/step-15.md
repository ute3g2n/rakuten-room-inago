# Step 15 実環境スモークと全Path受け入れ

実施日: 2026-09-25 JST。結論: **合格**。`full-path-coverage.json` は42行、未合格0行。

## 実経路

| 経路 | run_id | 結果 | 証拠 |
|---|---|---|---|
| setup/manual | `20260925-053047-setup-442e00` | `action_required`、プロフィール本人レビュー待ち | `step15-setup.json` |
| performance/manual | `20260925-054858-performance-765af3` | success、実affiliate/公開ROOM観測 | `step15-performance-retry.json` |
| research/manual | `20260925-060055-research-b69e3b` | partial、実楽天API候補44件→3ページ確認→LLM判定。販売状態未確認につき掲載認定0件 | `step15-research-retry2.json` |
| daily/manual | `20260925-060821-daily-9b1d9d` | partial、9工程成功、在庫不足で予定0件 | `step15-daily.json` |
| daily/chat | `20260925-085526-daily-6b55b2` | partial、Codex CLIが新スキルから正しい入口を選び9工程成功 | `step15-chat-full-result.txt`、run manifest |
| daily/scheduler | `20260925-061947-daily-2bb23f` | partial、Task Scheduler終了コード2、9工程成功 | `step15-task-results.json`、run manifest |
| config-change/manual | `step15-config-change.json` | success、dry-runのみ | run manifest |
| recovery/manual | `20260925-090319-recovery-155564` | partial、失敗工程から継続run `20260925-090426-daily-044508` を生成し9工程成功 | `step15-recovery.json`、run manifests |
| status/manual | `step15-status-flow.json` | success | run manifest |

setupの`action_required`は設定・プロフィール確認を本人に残す仕様。research/daily/recoveryの`partial`は実在庫・販売状態の不足であり、処理エラーは0件。未確認の販売状態を`in_stock`へ推測しなかった。

## 入口、分岐、モデル

- Codex CLIへ新スキルの絶対パスを渡し、`run_daily.py --source chat --dry-run` の実実行を確認。research、performance、status、recoveryの選択は `step15-chat-route-only-result.json` に保存した。
- 7 flow × 3 sourceの21組と在庫十分／不足のfixtureを、同じオーケストレータ実装・設定・Schemaで検証。`step15-path-tests.txt`: 25 passed。
- モデル最小Schemaジョブ: Luna/low/daily-plan、Sol/medium/quality-gate、Astra/high/quality-gateの昇格を実呼出しし、全てsuccess。各manifestにmodel、effort、Schema名、usageを保存。Astraは通常日次のmodel_attemptsに現れない。
- `show_status` のlatest、flow+date、failed-only+days、run-id、flow+daysを実履歴で確認し、全て終了コード0。健康確認候補タスクもオンデマンド終了コード0。
- 候補Windowsタスク2件は未来のトリガーで一時登録し、固定Python、作業ディレクトリ、実行ユーザーSID一致、source=scheduler、ログ、終了コードを確認後、両方Disabled。候補XMLは `step15-task-xml/` に保存。
- 最初の対象日は2026-09-25。9月24日の新運用計画は生成せず、9月25日のdry-run計画はrun内のsandboxに保存した。

## 障害と修正

- 公開ROOMの2ページ目が空の`not found`終端となるケースを受け入れ、実取得を再試行して成功。
- モデルAPIがJSON Schemaの`format: uri`を拒否したため、モデル送信用互換Schemaを生成し、保存データは元Schemaで再検証するよう修正。
- 販売状態未取得の商品をモデルが`verified`としたため、指示とローカル判定を合わせて`unavailable`へ分類。確認済み商品0件なら空の原稿・品質結果を確定し、不要な再生成要求を避けた。
- Task Scheduler環境ではCodex DesktopのCLIパスがPATHにないため、ユーザー領域の実行体発見を追加。候補タスクを再実行して完走。
- 初回のCodex CLIチャット試験は外側CLIの既定サンドボックス内で内部CLIが待機した。中断runをfailedとして閉じ、同じ権限の`-s danger-full-access`で再実行し、9工程完走を確認。

## 保護と検証

- `products.json`、`drafts.json`、`post-status.json`、本番`posting-dashboard.html`のSHA-256は事前・事後で一致。外部状態への書込操作0。
- secret scan: 0 findings。資格情報・認証状態ファイルの本文は表示・コピーしていない。
- Python全試験163 passed、Node全試験55 passed、path統合試験25 passed。
- 旧`.agents/skills/SKILL.md`は有効、旧`RakutenRoomBakemonoAffiliateDaily`はReadyのまま。切替は未実施。

`full-path-coverage.json` をStep 16の独立監査へ渡す。
