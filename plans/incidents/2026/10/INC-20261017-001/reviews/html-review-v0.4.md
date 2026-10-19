# 障害報告HTML 独立レビュー v0.4

- 判定: `changes_requested`（v0.4 snapshot）
- 対象: `reports/incidents/2026/10/INC-20261017-001/日次不具合対応レポート_2026-10-17.html`
- 対象SHA-256: `c5b3573c11c539e3e4b3e767d402ffaadff9fad52a80b732d523df059ed46ca9`
- 対象版: `v0.4`（本文表示時刻 2026-10-19 20:55 JST）
- レビュー日時: 2026-10-19 20:56:09 JST
- reviewer_id: `rr-inago-incident-report-reviewer`
- 現行HTML bytesを読み直しSHAを計算。読取専用。HTML、製品コード、試験コード、DBは変更していない。

## 判定概要

v0.3時点のHR-07は解消した。v0.1〜v0.3で記録されたHR-01〜07の指摘事項は、Worker/resume結果、Gitとledgerの区別、investigation v2のreview結果、印刷CSS、review表の列数、ページ状態ラベルについて修正されている。表の行列数、全リンク/fragment、unknownと回復状態の説明を確認した。

ただし`#recovery`の未確認事項に「v0.3 review」が残る一方、同節の次項および他の箇所ではv0.3 reviewの`changes_requested`結果とHR-07のv0.4修正を記録している。v0.4作成時点でv0.3 reviewは既に完了していたため、HR-08を記録する。

## 指摘

| ID / 重大度 | 箇所 | 所見・必要な対応 |
|---|---|---|
| HR-08 / low | `#recovery` の「未確認事項」リスト | リスト末尾が「v0.3 reviewとHTML公開」としているが、本文はv0.3 reviewを`changes_requested`、HR-07をv0.4で修正済み、v0.4 review待ちと記載する。さらにv0.4作成時刻20:55は、v0.3 review時刻20:54:52の後である。未確認項目を「v0.4 reviewとHTML公開」に更新し、reviewの既知結果と整合させる。 |

## 前回指摘の再確認

| 指摘 | 結果 |
|---|---|
| HR-01（resume/Worker/DB結果） | 解消。resume-worker exit 0、one-shot Worker exit 3/maintenance_incomplete、post-run DB job running、stageとattempt結果を別記し、完了/回復とはしていない。 |
| HR-02（Git pushと正式反映） | 解消。main commit/pushとformal deployment ledgerの別SHA・同期未確認を区別。 |
| HR-03（調査v2と旧reviewの版差） | 解消。v1 changes_requestedとv2 approvedを分離し、v2の残るreview範囲・原証拠制限も記載。 |
| HR-04（印刷時のtable表示） | 解消。print CSSでtable display/overflowを戻している。実印刷renderは未実施と明記。 |
| HR-05（investigation v2 re-review未反映） | 解消。20:49:37 JSTのapproved結果、IR-U23-01/02解消、残る制限を記載しリンクしている。 |
| HR-06（確認表のセル不足） | 解消。概要表は全行6列、試験・review表は全行5列。 |
| HR-07（全体状態ラベルがv0.2を指す） | 解消。冒頭は`pending · v0.4 report review / HTML公開未完了`で、表示版と一致。 |

## 確認結果

- 実測HTML SHA-256は`c5b3573c11c539e3e4b3e767d402ffaadff9fad52a80b732d523df059ed46ca9`。依頼時SHAと一致。
- 42リンクに参照切れなし。13 IDに重複なし。内部fragment、v0.1/v0.2/v0.3 snapshot、各review/計画/試験記録リンクを解決確認。
- 2表の全行で列数が揃う（概要6列、試験・review5列）。
- 同一job/source_launch_id、`source_run_id=null`、resume_count 8、resume-worker exit 0、Worker exit 3/blocked、post-run DB job runningを別状態として記載。stage/attempt差の理由やroot causeを推測していない。
- 10/17 root cause、実投稿数、run未保存理由、OS process exit、回復、再発防止、formal ledger同期、自然10/20 runを未確認/unknown/not_observedとしている。Worker結果を日次回復や投稿成功と解釈しない。
- 10/19除外、queue/historyを処理していないこと、Daily手動起動なしを維持。
- v2 investigation reviewのapproved結果と残る証拠・review範囲制限を正しく分け、v1 reviewをv2へ適用していない。
- 狭幅用の横スクロール/折返しCSS、印刷用table全幅CSSを確認。実ブラウザ、狭幅viewport、印刷プレビューのrenderは未実施。
- 簡易secret/PII pattern scanでAPI key/password/secret/bearer/email形式は各0件。目視でも個人情報、認証情報、生ログ、商品名、投稿本文は見当たらない。
- test/review/readiness statusを後続処理のgateとする記述なし。

## 制約

HTMLおよび保存済み根拠の読取review。DB/Schedulerを再観測せず、pytest、ブラウザ表示・印刷renderも実行していない。歴史事実の元snapshotは今回再検証していない。`changes_requested`はreview結果の観測であり、reporterのHR-08修正、公開、U24/U25のgateではない。
