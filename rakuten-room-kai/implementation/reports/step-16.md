# Step 16 独立レビューとリリース候補固定

実施日: 2026-09-25 JST。判定: **success / cutover_approved: true**。

設計§0–19、AC 60件、test-traceability、42行のfull-path-coverage、9件の実run manifest、コード、スキル、タスクXMLを独立に照合した。各ACの判定・証拠・残リスクは `release-readiness.md` に記録。CRITICAL/HIGH/MEDIUMの未解決所見0。

## レビュー所見

- タスク登録APIへ渡すXML宣言の符号化不整合（MEDIUM）を修正。PowerShell parserとExportOnlyを再確認。本番タスクはまだ登録していない。
- 旧文書の変数参照をsecret scanが誤検知する問題（LOW）を修正。実値風dummyの検出を残す回帰試験を通し、広域再走査0件。
- 制限付きの外側Codex CLIから内側CLIが待機したため、同一権限で再スモークしchat全9工程を完走。Windowsタスク経路も完走済み。
- 実在庫不足、実注文0件、物理スリープ復帰未実施は運用観測の残りとしてAC表へ明記。未確認商品を採用せず、切替後の運用監視へ渡す。

## 機械検証

| 検証 | 結果 |
|---|---|
| Python full suite + branch coverage | 164 passed、全automation 76%、core10行91.5% |
| Node full suite | 55 passed |
| Playwright dashboard E2E | 13 passed |
| Schema valid/invalid | 32 passed |
| failure matrix | 31 case、未解決0。対象試験19 passed |
| quick_validate | 新9スキル全件valid |
| artifact audit | full-path 42行、実manifest 9件、欠落0 |
| secret scan | 0 findings |
| npm / pip audit | 既知脆弱性0 |
| Python/Node/PowerShell構文 | compileall 0、Node13ファイル0、PowerShell parser 0 |

`review-findings-step16.md`にコード、Python、セキュリティ、skill構造、Windowsタスクの詳細を保存。

## staging archiveと切替ゲート

旧packageの16ファイル、189,791バイトを `archive/legacy-skills/.staging/legacy-package-precutover-20260925.zip` へ保存。ZIP内一覧と各ファイルSHA-256を再読取し、元ファイルは変更していない。`legacy-package-manifest.json`が一覧とハッシュの証拠。

切替前状態: 旧`.agents/skills/SKILL.md`有効、旧affiliate task Ready、新本番task未登録、候補task2件Disabled。`precutover-state-step16.json`にパス、サイズ、時刻、ハッシュ、タスク状態を保存。

切替条件のうち事前検証可能なものは全て成立。AC-019の実際の旧入口停止・復元とAC-040の物理スリープ復帰はそれぞれStep 17/19および初回予定実行後に再判定する。Step 17へ進んでよい。
