# 新計画の開始点と確認範囲

## B01 対象と設計

- root `C:\project\rakuten-room-inago`、branch `main`、開始HEAD `fc8b0a82ee91d0c8ab3a6d2274601498455d717f`、tracking `origin/main`。
- 計画担当の書込前にcached・uncached・untrackedは全て空。
- 構成案: `docs/architecture/保守フロー_skill直列構成案_2026-10-20.html` 1.1、SHA-256 `0177ae92e0981eb1ab179b49d04739de9ffbfbc056daa3e7f7be52b79a28b3ec`、52,900 bytes。
- 6skills/8呼出し、LLMのMarkdown本文を直接渡す。起動指示は担当・入力・依頼の3項目。入口と成果物のMarkdown9書式を定義済み。
- 計画作成だけを今回実施する。schema、製品コード、skills、roles、本番、対象jobの変更は行わない。

## B02 現在の同一job

`baseline-snapshot.json` は2026-10-20 JSTにmaintenance DBをURI `mode=ro`で読んだ結果。秘密・生業務データは収録していない。

- job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、incident `352492da-ac58-4680-b1fd-fe5f24000229`。
- source launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、対象 `2026-10-17`、source_run_id `null`。
- job状態は旧結果の`complete`、resume_count29、running attempt0。これは新構成での日次修正・計装完遂を表さない。
- 過去のblocked・failed等のattempt履歴は原状保持。正しい同一job再開は将来実行STEPで行う。
- 現maintenance schema16。10/19 queueは1件queued、event0、daily launchは1件failed/nullrun/exit4。全行の比較用digestをsnapshotへ保存。
- 観測時のScheduled Task Daily/Worker/Watchdog/HumanApiはいずれもReady（State3）、Enabled、作業場所は固定root。起動・停止・再登録はしていない。

## B03 既存本番版とコード版

- 公式deployment ledgerの最新行とruntime-switch-stateはactive `6b6dc80a577cd6e9e75e0d3887b7b1bf23eba850`、pendingなし。
- 固定rootのHEADは設計文書を追加した後続commit `fc8b0a8...`。保存された稼働commitと現在HEADを同一観測として扱わない。新しい製品適用ではsource内容、試験対象、公開版、稼働実物を対応させる。
- 既存日次診断（daily_pipeline/orchestrator/trace）のsource hashをsnapshotへ保存。既存実装を重複追加しない。
- ledgerの6b6dc80から開始HEAD fc8b0a8までのautomation/.agents/.codex/docs/standards変更pathは0。比較した6code fileのうち5件はGit blobとraw bytes一致、production_workflow.pyだけは改行の違いがあり、CRLF/LF正規化後に一致する。raw SHA値はそのまま保持し、全raw bytes一致とは表現しない。

## B04 旧成功判定の不足

原典: `worklogs/projects/maintenance/flow-completion-20261019/FC11/same-job-resume-29-final-publication-r1.md/.json`。

- planningはAI transport失敗、repairはnull predecessor IDから`attempt_id_invalid`、deploymentは候補なし。
- それでも工程引継ぎ・保守trace・HTML公開が成立したことを根拠にFC-A全件completeと記録した。
- 現コード `workflow_provider.py` 最後のreport outcome判定も、report completeから全体completeを返す。
- この旧判定は、同一job担当が日次に対する原因修正または有用な診断ログを実装・試験・review・適用した証拠を満たさない。新計画はこの受入の誤りを引き継がない。
- 10/17の根本原因と業務回復は引き続き不明。原因不明であることと、修正準備を完遂していないことを混同しない。

## B05 ソース調査と未確定

- 起動差替え、低層AI再利用、実在pytest node、日次の情報欠落候補は `source-inventory.md`。
- 原調査v2とreviewの事実・限定は計画作者が原典を読んで対応させる。今回のソース上の不足候補から、10/17の原因を新たに断定しない。
- 新構成用test nodeはまだ存在しない。計画で新設予定と明記し、実装後の実node確認とfocused試験を割り当てる。
- 既存HTMLのbrowser toolによるfile URL表示は前の文書作成時にpolicy拒否されている。計画の開始・品質判定をその表示確認の可否で止めない。
