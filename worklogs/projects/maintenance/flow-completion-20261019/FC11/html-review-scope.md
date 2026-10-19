# FC11 HTMLレビュー範囲の限定

記録日: 2026-10-20 JST

## 本人指示

利用者はHTML本文で確認したい中心を、(1) 発生した不具合、(2) 原因（不明なら不明）、(3) 実施した修正、(4) 追加したトレース・ログと記録内容の4点と指定した。試験PASSは前提なので、本文へ「試験で確認」等の区分を要求しない。直す必要のない既存ひな形は変更しない。

過去のHTMLレビュー指摘21件のうち、レビュー対象から外す指定は 1, 2, 3, 5, 7, 8, 9, 11, 12, 13, 17, 18, 19, 20, 21。本文reviewは4点の欠落・事実誤認・根拠のない断定・理解を妨げる表現と、秘密・個人情報の露出を対象とする。内部フィールド名・識別子の表示だけ、狭幅・印刷・表・`.tree`の表示だけを独立した指摘観点にしない。

## 変更

HTML本文のレビュー範囲を次のリポジトリ内契約へ同期した。

- `docs/standards/イナゴ日次不具合レポート作成ルール.md`
- `.agents/skills/rr-inago-incident-report/SKILL.md`
- `.agents/skills/rr-inago-incident-report/references/contracts.md`
- `.codex/agents/rr-inago-incident-reporter.toml`
- `.codex/agents/rr-inago-incident-report-reviewer.toml`
- `automation/inago/maintenance/production_workflow.py` のreporter/reviewer prompt
- `automation/inago/schemas/maintenance-report-response.schema.json` の説明文
- `automation/inago/schemas/maintenance-report-review.schema.json` の説明文
- `tests_inago/test_maintenance_report_publication_stage.py` のprompt回帰テスト

除外したのはHTML文章reviewの観点であり、機械互換に必要なschema必須項目、公開同一性、job identity、DB終端の機械検証は変更していない。HTMLひな形は変更していない。

## 実測

- 先行独立製品reviewは、当時の対象差分に対しAPPROVE・指摘0。ただし同じ対象が内部フィールド名と狭幅/印刷表示を追加確認範囲に残しており、除外指定との不一致が後で判明したため、その承認を今回の訂正版には流用しない。
- 訂正版では、HTML reviewerの対象を4点と秘密・個人情報に限定し、それ以外のHTML要素をレビュー対象外とする。HTMLひな形は変更しない。
- focused pytest、schema/role parse、差分検査、訂正版への独立製品reviewはこの追記後に実施し、実結果を追記する。
- HTMLひな形の差分: なし。

## 適用状態と再開位置

訂正版の実装・review・適用状態は、以下の追記で更新する。HTMLレビュー範囲の同期だけではFC-A06/A07または全体完遂の達成根拠としない。

## 訂正版の独立review・計画同期（2026-10-20 JST）

- 前回レビュー指摘の4か所（計画第1節FC-A06、第6節、FCT09の操作・期待証拠、初回履歴）を修正し、旧APPROVEが訂正版へ適用されない履歴を明記した。
- 独立製品review `/root/fc11_html_scope_product_review_r2`：**APPROVE、指摘0**。対象はbase `abfa4482666c44d47cd2c2053c34edba9f251c2f`に対する現在差分。reviewerが対象9製品ファイルから算出したsnapshot SHA-256: `f9c88e5570eac6974f73aabf30ad004bb6608b2da32054852bdfd320e326443c`。
- reviewer確認：4点＋秘密/個人情報に限定し、管理情報、内部識別子、リンク/anchor、狭幅/印刷/レイアウト/displayの独立検査を要求しない。schema必須項目、job identity、review登録、公開照合の機械契約は保持。HTMLひな形は無変更。テストソースは読まず、テストもreview内では実行していない。
- 訂正版実装のfocused回帰2件は直前に2 passed。AST/schema JSON/role TOML parse PASS。今回計画書だけを文面同期した後の `git diff --check` はexit 0（CRLF/LF正規化警告のみ）。
- 再開位置: 訂正版HTML promptの実report/review実測、publish/terminal readbackを同一jobで実施し、FC-A01/A02/A03/A04/A07/A10の部分達成を修正・再測定する。HTML scopeの承認は全計画完了の根拠ではない。

### 訂正版の独立review・計画同期（2026-10-20 JST）

- 前回レビュー指摘の4か所（計画第1節FC-A06、第6節、FCT09の操作・期待証拠、初回履歴）を修正し、旧APPROVEが訂正版へ適用されない履歴を明記した。
- 独立製品review `/root/fc11_html_scope_product_review_r2`：**APPROVE、指摘0**。対象はbase `abfa4482666c44d47cd2c2053c34edba9f251c2f`に対する現在差分。reviewerが対象9製品ファイルから算出したsnapshot SHA-256: `f9c88e5570eac6974f73aabf30ad004bb6608b2da32054852bdfd320e326443c`。
- reviewer確認：4点＋秘密/個人情報に限定し、管理情報、内部識別子、リンク/anchor、狭幅/印刷/レイアウト/displayの独立検査を要求しない。schema必須項目、job identity、review登録、公開照合の機械契約は保持。HTMLひな形は無変更。テストソースは読まず、テストもreview内では実行していない。
- 訂正版実装のfocused回帰2件は直前に2 passed。AST/schema JSON/role TOML parse PASS。今回計画書だけを文面同期した後の `git diff --check` はexit 0（CRLF/LF正規化警告のみ）。
- 再開位置: 訂正版HTML promptの実report/review実測、publish/terminal readbackを同一jobで実施し、FC-A01/A02/A03/A04/A07/A10の部分達成を修正・再測定する。HTML scopeの承認は全計画完了の根拠ではない。

### 読み取り操作失敗と回復

- 読取専用SQLite照会で、`maintenance_job.source_launch_id` を選択して `no such column` になった。`PRAGMA query_only=ON`、`mode=ro`で副作用はない。実schemaを再確認し、job表の実在列・同一job attemptsを読み直した。launch identityは `job_input_manifest` にある。試行と回復を `FC10/controller-read-operation-failures.json` に追記した。
