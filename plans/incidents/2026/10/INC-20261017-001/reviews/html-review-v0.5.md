# 障害報告HTML 独立レビュー v0.5

- 判定: `changes_requested`（v0.5 snapshot）
- 対象: `reports/incidents/2026/10/INC-20261017-001/日次不具合対応レポート_2026-10-17.html`
- 対象SHA-256: `2027d26cf2cd56b293183fb19e6e5daccf70c7a1f7b3c059bfc0bd4023a1c74d`
- 対象版: `v0.5`（本文表示時刻 2026-10-19 20:57 JST）
- レビュー日時: 2026-10-19 20:57:18 JST
- reviewer_id: `rr-inago-incident-report-reviewer`
- 現行HTML bytesを再読し、実SHAを算出。読取専用。HTML、製品コード、試験コード、DBは変更していない。

## 判定概要

v0.4でのHR-08は、該当箇所をv0.4 reviewと公開へ更新して解消している。HR-01〜08の指摘事項も反映済み。時系列、job/source_run identity、resume/Worker/DB状態、原因unknown・回復未確認の扱いに誤読を招く断定は見当たらず、リンク、ID、表列数も確認した。

ただしv0.4独立reviewは20:56:09 JSTに終了して`changes_requested`（HR-08）と判定され、v0.5作成時刻20:57 JSTはその後である。それにもかかわらず、`#recovery`の「未確認事項」には「v0.4 review」が残る。v0.4 reviewの実結果は既知として本文別行に記載されているため、HR-09を記録する。

## 指摘

| ID / 重大度 | 箇所 | 所見・必要な対応 |
|---|---|---|
| HR-09 / low | `#recovery` の「未確認事項」リスト | 「v0.4 reviewとHTML公開」が未確認事項となっているが、直下の状態欄、`#checks`、`#evidence`にv0.4 reviewの`changes_requested`結果とHR-08のv0.5反映を記録している。v0.5作成時刻もv0.4 review完了後である。未確認事項を「v0.5 reviewとHTML公開」へ更新し、既知のv0.4 review状態と整合させる。 |

## 過去指摘の再確認

| 指摘 | 結果 |
|---|---|
| HR-01（resume/Worker/DB結果） | 解消。exit 0のresume-worker、exit 3のone-shot Worker、post-run DBのjob=`running`とstage/attempt記録を別の実値として記載。完了・回復と扱わない。 |
| HR-02（Git pushとformal deployment） | 解消。origin/mainへのコードpushとformal ledgerの別SHA、正式反映未確認、HTML公開状態を区別。 |
| HR-03（investigation v2と旧review） | 解消。v1 changes_requestedをv2へ適用せず、v2 approvedと残る限界を説明。 |
| HR-04（print table） | 解消。印刷CSSでtableを全幅・表示可能に戻す。実render未実施も記載。 |
| HR-05（v2 re-review結果） | 解消。結果、時刻、v2 reviewリンクを記載。 |
| HR-06（表の列不足） | 解消。概要表6列、試験・review表5列で全行統一。 |
| HR-07（冒頭の古いreview版表示） | 解消。全体状態はv0.5 review pending。 |
| HR-08（未確認事項にv0.3 review） | 解消。未確認事項はv0.4 reviewと公開に更新済み（ただしv0.4 review後の状態を再更新するHR-09あり）。 |

## 確認結果

- 現行HTMLの実測SHA-256は依頼値`2027d26cf2cd56b293183fb19e6e5daccf70c7a1f7b3c059bfc0bd4023a1c74d`と一致。
- 44リンクに参照切れなし。13 IDに重複なし。内部fragment、旧snapshotと各証跡/reviewへの相対リンクが解決。
- 概要表は全6列、試験・review表は全5列で、headerを含む全行の列数が一致。
- 同一job/source_launch_id、`source_run_id=null`、resume_count=8、resume-worker exit 0、Worker exit 3/blocked、DB job=`running`の時系列は保存済みS04記録と整合。DB job状態とWorker CLI/stage結果の差を維持し、原因を推測していない。
- 10/17 IntegrityError、実投稿数、OS終了値、run未保存理由、当日回復、将来計装の有効性、ledger同期はunknown。10/20は観測時点`not_observed`。解決・再発防止・自然日次成功を主張していない。
- 10/19除外、同一履歴jobのみ、重複受付なし、Daily手動起動なしの説明を保持。
- HR-01〜08の旧指摘は解消。v0.4 review結果の版別表示も正しい。
- 狭幅でtable横スクロール、コード/リンク折返しを指定。印刷CSSはtable表示/overflowを再設定。実ブラウザ/狭幅/印刷レンダリングは実施していない。
- 簡易secret/PIIパターン照合でAPI key、password、secret、bearer、emailは各0件。目視範囲に認証情報、個人情報、生ログ、商品名、投稿本文は見当たらない。
- test/review/readinessのstatusで後続工程をgateする記述なし。

## 制約

報告HTMLと保存済み記録の読取review。ブラウザ/印刷render、DB/Scheduler再観測、pytestは未実施。歴史原証拠snapshotは未提供のため再検証していない。`changes_requested`は実review値であり、reporterの修正、publication、U24/U25のgateではない。
