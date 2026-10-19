# 障害報告HTML 独立レビュー v0.2

- 判定: `changes_requested`（現行v0.2 snapshot）
- 対象: `reports/incidents/2026/10/INC-20261017-001/日次不具合対応レポート_2026-10-17.html`
- 対象SHA-256: `20a249c40f14e50d9d2dc7a3e4e9329dfe2cefe1ec7ec8cce2cf0c2bdec390e2`
- 対象版: `v0.2`（本文表示時刻 2026-10-19 20:49 JST）
- レビュー日時: 2026-10-19 20:52:16 JST
- reviewer_id: `rr-inago-incident-report-reviewer`
- 固定workspace: `C:\project\rakuten-room-inago`
- 読取専用。HTML、製品コード、試験コード、DBは変更していない。

## 判定概要

v0.1のHR-01〜04は、resume-workerとone-shot Workerの実値、commit/pushと正式deployment ledgerの区別、調査v2の識別、印刷時テーブルCSSという形で反映されている。ただし、調査v2の再レビューは20:49:37 JSTに`approved`として完了しており、HTMLの「v2独立再reviewは未実施」はレビュー時点で古い。加えて「試験・レビュー・反映」表で4行の列数がヘッダーと合わず、状態と根拠リンクの位置が定まりにくい。以下2点を反映した版を再確認する。

## 指摘

| ID / 重大度 | 箇所 | 所見・必要な対応 |
|---|---|---|
| HR-05 / low | `#evidence` 調査項目、「報告review・HTML公開」行、「未確認・再開」 | HTMLは調査v2について「独立再reviewは未実施」と記し、v1の`changes_requested`はv2に適用しないとしている。しかしv2独立再レビュー記録は既に保存され、20:49:37 JST、対象v2の判定は`approved`、IR-U23-01/02解消、残りは証拠・review範囲などの制限/観測で修正要求なし。v2のMD SHAは`b221b23c23cb20b8e8dea4ff418fdcf319d47c2756bbe742c65bbca6fcb1dda2`。v1の判定は別版として保持し、v2 reviewの結果・限界とリンクを反映する。HTML v0.2のreview状態はこのレビュー後`changes_requested`になるため、report review pendingの箇所も最新化する。 |
| HR-06 / low | `#checks` の試験・レビュー・反映テーブル | ヘッダーは5列だがresume-worker、one-shot Worker、次回自然日次、報告review・HTML公開の4行は各4セルしかない。明示的な`colspan`もなく、状態と根拠リンクを別列に配置できていない。全行を5列に揃えるか、意図するセル結合を明示し、状態/根拠が正しい列に表示されるよう修正する。 |

## v0.1 指摘の再確認

| 旧指摘 | 結果 |
|---|---|
| HR-01: resume/Worker/DB結果が未反映 | 解消。exit 0のresume-workerとexit 3のone-shot Worker、CLIのblocked結果、DBの`running`とstage/attempt状態の差を別々に記載し、完了/回復と誤認していない。 |
| HR-02: Git状態が古い | 解消。`5f206cc6d3b81d4695f7d827a04b1afd1fb7f653`の`origin/main` pushと、ledgerの別SHA・正式反映未確認、HTML公開の別状態を区別している。 |
| HR-03: investigation v2と旧reviewの版差 | 部分解消。v2とv1の`changes_requested`を分けているが、v2再review結果が完了したため現状説明の更新が必要（HR-05）。 |
| HR-04: print table overflow | 解消。印刷規則に`display:table; width:100%; overflow:visible`がある。ソース確認のみで実レンダリングはしていない。 |

## 確認結果

- 対象HTML SHA-256はレビュー時に現物から計算した。
- 34リンク、内部ID 13件。重複IDなし。内部fragmentおよび相対ファイル/fragmentの参照切れなし。v0.1 snapshotも存在する。
- 当日IntegrityError、実投稿数、原因、回復をunknownのまま記載している。resume出力、Worker出力、post-run DBの`running`、stage blocked値、report attempt不在を別の観測としており、「terminal-blocked/完了」とはしていない。
- 10/19除外、10/19 queue未処理、Daily手動起動なし、10/20自然runの`not_observed`を記載。原因修正・再発防止・正式反映を過大主張していない。
- focused test、製品コードreview、Git push、正式deployment ledger、自然日次観測、HTML公開を区別し、test/review状態を後続gateとしていない。
- 調査v2は`approved`だが、原証拠7件未提供と独立reviewの入力範囲制限などが残る。HTMLは歴史原証拠が未再読である限界も記す。
- 狭幅CSSに横スクロールと折返し指定がある。画面レンダリング/実機の狭幅確認はしていない。
- 簡易secret/private-data確認で秘密、個人情報、投稿本文は見当たらない。
- HTMLテーブルの列数は、概要テーブル6列は全行一致、試験・レビュー表の4行で5列中1列不足。

## 制約

報告書レビューのみ。Task Scheduler/DB再読、ブラウザ/印刷レンダリング、pytestは実行していない。時系列値はリンクされた保存済み記録との整合確認であり、元の歴史原証拠は今回再検証していない。`changes_requested`は観測値で、reporterによる修正や公開、U24/U25のgateではない。
