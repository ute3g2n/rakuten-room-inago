# 障害報告HTML 独立レビュー

- 判定: `changes_requested`（作業途中のv0.1 snapshot）
- 対象: `reports/incidents/2026/10/INC-20261017-001/日次不具合対応レポート_2026-10-17.html`
- 対象SHA-256: `a9f751f4bfd025804dcd39c5b107dc5614a43faa7ce9e1c83f05cec5977a0d81`
- 対象版: `v0.1`（HTML作成表示時刻 2026-10-19 20:37 JST）
- レビュー日時: 2026-10-19 20:45 JST
- reviewer_id: `rr-inago-incident-report-reviewer`
- 固定workspace: `C:\project\rakuten-room-inago`
- 読取専用。報告HTML、実データ、試験対象は変更していない。

## 判定概要

本文は当日の事実・変更・試験・本番確認を分け、IntegrityErrorを根本原因と断定せず、投稿ゼロや回復成功へ読み替えていない。source_run_id=null、元証拠の未再読、fixture結果と本番結果の違いも明示している。数値は提示された調査更新/計画記録にある値と整合する範囲で記載され、秘密・投稿本文・商品名・生DB値の転記は見当たらなかった。

ただし本HTML作成後に同一jobのresume-workerは実行済みで、製品変更のcommit/pushも完了している。v0.1は作成時点のsnapshotとしては読めるものの、現行の最終報告として使うには後続結果への更新が必要である。最新調査更新v2も反映されていない。従ってv0.1に対する所見を記録し、reporterが最終版へ更新する。

## 指摘

| ID / 重大度 | 箇所 | 所見・必要な対応 |
|---|---|---|
| HR-01 / medium | 概要・確認表・未確認/再開（HTML本文の「本番で同一jobを再開した結果」「CLI再開後の結果は未記録」「resume後のjob/DB結果は未確認」） | v0.1作成後の20:38:20 JSTに公式`resume-worker`がexit 0で完了し、同じjobの`resume_count=8`、status=`queued`、同じsource_launch_idが出力された。現在のS04観測記録はこの事実を保存している。従って「再開結果は未記録」は現在の報告では古い。現在起動中のone-shot Workerの最終結果は、完了記録が得られるまではpending/unknownとして分けて記載し、成功や回復を推測しない。|
| HR-02 / medium | 概要と確認範囲、Git反映・本番反映表、コード根拠 | v0.1には「working tree差分は未commit・未push」「暫定版作成時点でcommit/push前」とある。作成時刻には合うが、その後mainの`5f206cc6d3b81d4695f7d827a04b1afd1fb7f653`が`origin/main`へpush済みである。最終版では作成時点の状態と更新後の現在状態を分け、製品コードcommit/push済みと、HTML自体のGit公開/本番反映状態を混同しないよう更新する。|
| HR-03 / low | 根拠と関連文書「調査」 | HTMLはinvestigation attempt `82296e84-...`のv1と、そのv1に対する`changes_requested`を表示する。現在利用可能な調査更新は`u23-08-update-v2`で、review v1の指摘2点を反映した一方、v2再reviewは未実施である。最終版ではv2の実際の内容・レビュー履歴を識別し、旧reviewの判定をv2の判定として見せない。|
| HR-04 / low | `<style>`の`table`規則と`@media print` | 画面幅では`display:block; overflow-x:auto`により横幅の広い一覧をスクロール可能にしている。印刷規則ではこの指定を解除していないため、印刷時に表の右側が切れるブラウザがあり得る。最終HTMLで印刷時の表を全幅表示にするか、対象レンダラーで可読性を確認する。|

## 確認結果

- 期待・実結果・影響・回復・原因確度を区別し、IntegrityError/SQLite分類/pipeline捕捉位置から根本原因を断定していない。
- CLI確定0、表示12、既存draft 12、active予約12、publication event 2から実投稿ゼロを結論していない。
- fixture試験、製品コードreview、本番再開、次回自然日次を別々の状態で記載している。review/test statusを後続gateにする記述はない。
- 内部fragment 28リンク中、fragment参照の不一致0件。ID重複0件。相対リンクの参照先欠落0件。
- secret/private-dataの確認パターンで該当なし。本文に生ログ・投稿本文・商品名・個人情報は見当たらない。
- 狭幅用CSS、折返し可能なコード、印刷用CSSがある。印刷時table overflowの指摘は上記HR-04。

## 制約

これは指定HTML v0.1のレビュー記録である。HTMLは変更しておらず、v0.1以降に完了するone-shot Workerの結果を予断しない。歴史原証拠そのものはworkspaceにないため、歴史値は保存済み計画/調査記録への対応確認であり、原本再検証ではない。`changes_requested`は観測値で、reportの改訂・保存・公開やU24/U25を止める条件ではない。
