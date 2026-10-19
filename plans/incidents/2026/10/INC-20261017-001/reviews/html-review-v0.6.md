# 障害報告HTML 独立レビュー v0.6

- 判定: `changes_requested`（v0.6 snapshot）
- 対象: `reports/incidents/2026/10/INC-20261017-001/日次不具合対応レポート_2026-10-17.html`
- 対象SHA-256: `7fd3c462bdd9ff9775c22678a7c283e9bb3c3afe61ec78383a622f7639e45cb9`
- 対象版: `v0.6`（本文表示時刻 2026-10-19 20:58 JST）
- レビュー日時: 2026-10-19 20:58:57 JST
- reviewer_id: `rr-inago-incident-report-reviewer`
- 現行HTML bytesを再読してSHAを算出。読取専用。HTML、製品コード、試験コード、DBは変更していない。

## 判定概要

HR-09の指摘箇所はv0.6で更新され、HR-01〜09の内容上の指摘は解消している。Worker/jobの観測差、原因unknown、未回復、10/20自然run未観測、formal deploymentの別状態を正しく分けている。リンク・アンカー・表構造にも参照切れはない。

ただし、HR-09のreview結果（20:57:18 JST、`changes_requested`、HR-09をv0.6で修正済み）はv0.6作成時刻20:58より前に確定しているが、`#recovery`の未確認事項は「v0.5 reviewとHTML公開」のまま。本文の状態欄はv0.5 review結果を記載し、v0.6 review待ちとしているため、この未確認事項は古い。HR-10として記録する。

## 指摘

| ID / 重大度 | 箇所 | 所見・必要な対応 |
|---|---|---|
| HR-10 / low | `#recovery` の未確認事項リスト | 「v0.5 reviewとHTML公開」は、直下の状態欄にあるv0.5 reviewの`changes_requested`結果とHR-09のv0.6修正済み記録に対して古い。v0.5 reviewは20:57:18 JSTに終了済みで、v0.6作成時刻20:58 JSTより前である。未確認項目を「v0.6 reviewとHTML公開」へ更新し、既知のreview結果と整合させる。 |

## 前回指摘の再確認

| 指摘 | 結果 |
|---|---|
| HR-01〜02 | 解消。resume/Worker/DBの各結果を区別し、コードpushと正式ledger反映、HTML公開を混同しない。 |
| HR-03〜05 | 解消。調査v2 approvedとv1旧review、調査の限界、印刷CSSを記載。 |
| HR-06〜07 | 解消。表列数が一致し、冒頭状態はv0.6 review pending。 |
| HR-08〜09 | 解消。未確認欄をv0.4/v0.5レビューからv0.5/v0.6レビューへ順次更新し、既知結果をstatus欄へ記載。ただし現在版v0.6で同様の1段遅れ（HR-10）が残る。 |

## 確認結果

- 実測SHA-256は依頼値`7fd3c462bdd9ff9775c22678a7c283e9bb3c3afe61ec78383a622f7639e45cb9`と一致。
- 46リンクに参照切れなし。13 IDに重複なし。snapshot、レビュー、計画、S04/U25、試験記録への参照を解決。
- 概要表は6列、試験・review表は5列。全行で列数が一致。
- 同一job/source_launch_idと`source_run_id=null`、resume-worker exit 0、one-shot Worker exit 3/blocked、post-run DB job=`running`、blocked stageとreport attempt不在を区別。terminal状態、成功、回復と誤記していない。
- 10/17 root cause、実投稿数、run未保存理由、OS process終了値、当日回復、job/stage差の原因、ledger同期をunknown。10/20 natural runは観測時点`not_observed`。再発なし/解決済みを主張しない。
- 10/19 exclusion、10/19 queue/history未処理、Daily手動起動なし、HTML公開未完了を維持。
- investigation v2のapprovedと残る原証拠/review scope制限、v1 reviewの別版判定を適切に記す。
- 狭幅CSSはtable横スクロールとリンク/コード折返しを指定。印刷CSSはtableをfull width表示へ戻す。実ブラウザ・狭幅・印刷renderは未実施と明記。
- 簡易API key/password/secret/bearer/emailパターン照合は全て0件。目視範囲に認証情報、個人情報、生ログ、商品名、投稿本文は見当たらない。
- test/review statusを後続gateにする記述なし。

## 制約

HTMLと保存済み記録の読取review。ブラウザ・印刷render、DB/Scheduler再観測、pytestは実行していない。歴史原証拠snapshot自体は今回再検証していない。`changes_requested`は観測値であり、reporter修正、HTML公開、U24/U25のgateではない。
