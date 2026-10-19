# 障害報告HTML 独立レビュー v0.3

- 判定: `changes_requested`（v0.3 snapshot）
- 対象: `reports/incidents/2026/10/INC-20261017-001/日次不具合対応レポート_2026-10-17.html`
- 対象SHA-256: `aad4731c3e0528823c758a0706cc55f32b59c5b76f428e0011ea163c0eef116c`
- 対象版: `v0.3`（本文表示時刻 2026-10-19 20:53 JST）
- レビュー日時: 2026-10-19 20:54:52 JST
- reviewer_id: `rr-inago-incident-report-reviewer`
- 固定workspace: `C:\project\rakuten-room-inago`
- 現行HTML bytesを読み直してSHAを計算。読取専用。HTML、製品コード、試験コード、DBは変更していない。

## 判定概要

v0.2のHR-05/06は解消した。v0.1のHR-01/02/04も解消し、HR-03は調査v2のapproved結果まで反映された。resume/Worker/DBの時系列はS04の記録と整合し、未確認事項を成功・回復と読み替えていない。両テーブルの行列数も揃い、リンク/anchorの欠落はない。

ただし、ページ冒頭の全体状態が「pending · v0.2 report review」となっており、本文はv0.2 reviewを`changes_requested`として完了済み、HR-05/06をv0.3で反映、v0.3 review待ちと説明している。表示中の報告版とも一致しないため、HR-07を記録する。

## 指摘

| ID / 重大度 | 箇所 | 所見・必要な対応 |
|---|---|---|
| HR-07 / low | header `.facts` の「全体状態」 | `pending · v0.2 report review / HTML公開未完了`はv0.3作成時点の本文内容と不一致。v0.2 reviewは完了し、本v0.3はレビュー対象として作成されている。v0.3 review待ち/実施状況とHTML公開未完了を表す現在版に合わせる。レビュー状態を後続処理のgateにはしない。 |

## 前回指摘の再確認

| 指摘 | 結果 |
|---|---|
| HR-01（resume、Worker、DB実測） | 解消。resume-worker exit 0、Worker exit 3/maintenance_incomplete、post-run DB job runningとstage/attempt観測が分離され、terminal failure/completionや回復と誤記していない。 |
| HR-02（Git pushとformal deployment） | 解消。mainの5f206cc… pushとledgerの22d687…を区別し、正式反映/ledger同期未確認を維持。HTML公開状態も別に記載。 |
| HR-03（調査v2と旧reviewの版差） | 解消。v1 changes_requestedをv2と混同せず、v2 approved（20:49:37 JST）、IR-U23-01/02解消、残るreview範囲/原証拠制限を記載し、v2 reviewへリンクしている。 |
| HR-04（印刷時のtable overflow） | 解消。印刷CSSでtableを`display:table; width:100%; overflow:visible`へ戻す。ソース上の確認であり、印刷レンダリングは未実施と報告にも明記。 |
| HR-05（v2再review結果の未反映） | 解消。結果・時刻・解消項目・限界とartifact linkを反映。 |
| HR-06（確認表のセル不足） | 解消。両表ともheaderを含め全行6列/5列で一致し、セル結合に依存しない。 |

## 確認結果

- 現行HTMLの実測SHA-256は`aad4731c3e0528823c758a0706cc55f32b59c5b76f428e0011ea163c0eef116c`。依頼時提示SHAと一致。
- 40リンクに参照切れなし。13 IDに重複なし。2表の全行の列数はそれぞれ6列、5列で一致。v0.1/v0.2 snapshot、v2 review、S04/U25、plan/review/試験記録のリンクが解決する。
- resume-worker出力のqueued/resume_count=8とone-shot Workerのexit 3/blocked、後続DB job status=running・stage blocked・report attempt不在を区別。status差の原因を断定していない。
- 10/17のroot cause、run未保存理由、OS実process exit、実投稿件数、10/17回復、診断計装による原因特定、10/20自然run、ledger同期をunknown/not_observedとしている。原因修正・再発防止・回復済みと主張していない。
- 同一job/source_launch_id/source_run_id=null、重複受付なし、Daily手動起動なし、10/19除外とqueue未処理を保持。Worker/Watchdog状態、自然日次結果、Git状態を混同しない。
- v2独立reviewはapprovedだが、歴史原証拠未提供やreview入力範囲などの限界を維持。v1のchanges_requestedは過去版として区別。
- 狭幅では`max-width:520px`、table横スクロール、リンク/コード折返しを指定。実ブラウザ/幅別レンダリングは未実施。
- 印刷CSSでtable display/overflowを解除。実印刷プレビュー/レンダラー確認は未実施。
- 簡易secret/PIIパターン照合（API key、password、secret、bearer、email）は0件。本文の目視範囲に個人情報、認証情報、生ログ、商品名、投稿本文なし。
- focused test、製品コードreview、Git push、formal deployment ledger、本番workflow、自然日次、HTML公開を別状態として記録。review/test statusを後続gateにする記述なし。

## 制約

HTMLと保存済み根拠の読取review。ブラウザ画面・印刷実render、DB/Scheduler再観測、pytestは実行していない。時系列は保存済み記録との照合であり、元のhistorical原証拠は今回再検証していない。`changes_requested`は観測値で、reporterの小修正やHTML公開、U24/U25のgateではない。
