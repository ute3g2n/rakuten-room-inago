# 障害報告HTML 独立レビュー v0.9

- 判定: `approved`
- 対象: `reports/incidents/2026/10/INC-20261017-001/日次不具合対応レポート_2026-10-17.html`
- 対象SHA-256: `2efefd4cb6dff2d90360f04d7f324b39145b5af14dd48669fb468b8453f12947`
- 対象版: `v0.9`（本文freeze時刻 2026-10-19 21:10 JST）
- レビュー日時: 2026-10-19 21:12:22 JST
- reviewer_id: `rr-inago-incident-report-reviewer`
- 現行HTML bytesを再読してSHAを計算。読取専用。report、製品コード、試験コード、DBは変更していない。

## 判定概要

v0.9の追加説明は確認した。v0.7は21:00:12 JSTにapproved、v0.8は「v0.7 review pending」を残している21:09 JST版で、事実誤記を含むdraftとして独立reviewを発行しなかったこと、v0.9はfreeze時点21:10 JSTでreview前だったことを版/時刻に分けている。commit `ec9557c`は非versionedの現行HTML pathをpushし、その後保存した`_v0.7.html`コピーはcommitに含まれないという説明もGit treeとCRLF/LF正規化hashで確認した。

HR-01〜10の履歴指摘は反映済み。報告内のjob/Worker/DB状態、root cause/recovery/投稿数のunknown、Git公開と正式deploymentの区別、内部リンク、表構造、responsive/print CSSを確認し、追加修正所見はない。

## 確認結果

- 現行HTMLの実測SHA-256は`2efefd4cb6dff2d90360f04d7f324b39145b5af14dd48669fb468b8453f12947`。依頼SHAと一致。
- v0.7 review artifactは対象SHA `7e94679a1d04eedb2e106dac99594e92a869ab1fc7bd50f529fa1fbb806f9076`、status `approved`、finding 0、時刻 `2026-10-19T21:00:12+09:00`。v0.9はこれを反映。
- v0.8 snapshotは`2026-10-19 21:09 JST`のv0.8作成表示に対し、上部に「v0.7 report review pending」、下部に「v0.7 review pending」と記載する。v0.7 reviewは21:00:12に完了済みのため当該表示は事実誤記。v0.9がv0.8を未review draftと区別する説明を確認した。`html-review-v0.8.json`は存在せず、v0.8の独立review判定があったとは表現していない。
- Git: `main`と`origin/main`の両refは`ec9557cdb52749ce1fa8f82ed57dfa8dce1f60ba`。commit messageは`Publish historical incident report v0.7`。commitは非versioned path `日次不具合対応レポート_2026-10-17.html`を追加する。blob SHA-256はLF bytesで`287fb23366cc636e8a658ea03bdf4b5f557b4497d915825433e9f8fad80a6e2c`。作業フォルダの`_v0.7.html`コピーSHA `7e94679a...`をCRLFからLFへ正規化するとGit blobのSHAと一致し、内容も正規化後byte一致。コピー自体がcommitに含まれないことと、同じv0.7 report内容が当時の非versioned pathにpushされたことを区別したv0.9の説明は正確。
- v0.9は21:10 JST時点で未review、後続review結果を別artifact/runlogと明示。対象reviewを21:12に行う本記録は別artifactであり、freeze時の状態を上書きしていない。HTML Git公開済みとしている点もGit refsと整合する。
- 50リンクに参照切れなし。13 IDに重複なし。概要tableは全行6列、試験/review tableは全行5列。v0.1〜v0.8 snapshot、plan/review/test、investigation、S04/U25 linksが解決。
- 数値と状態: 同一historical job/source launch、`source_run_id=null`、resume-worker exit 0/queued、one-shot Worker exit 3/maintenance_incomplete、post-run DB `running`、stage blockedとreport attempt不在を分けて記載。10/19 queue未処理、除外日維持、Daily手動起動なし、U25は観測時点`not_observed`。
- 根本原因、run未保存理由、実投稿数、OS process exit、10/17回復、job/stage差原因、正式deployment ledger一致はunknown/unconfirmed。解決済み、回復済み、再発なしと主張しない。
- 試験/review/push/deployment/natural runの結果を別項目で記し、後続工程のgateとしていない。
- 狭幅向けtable横スクロール、リンク/コード折返しを指定。印刷CSSでtableを全幅表示に戻す。実ブラウザ/印刷renderは未実施と明記。
- 簡易API-key/password/secret/bearer/email pattern scanは全て0件。目視範囲でも個人情報、認証情報、生ログ、商品名、投稿本文は見当たらない。

## HR-01〜10再確認

- HR-01/02: resume/Worker/DBとcode push/formal ledger/HTML Git公開を区別。解消。
- HR-03〜05: investigation v1/v2 reviewの版差、v2 approved、review限界、print table CSSを反映。解消。
- HR-06/07: table列数とtop statusの版整合。解消。
- HR-08〜10: 未確認事項の版を次のreportへ進め、完了review結果を残す。v0.9はv0.7 approvedを示し、v0.8誤記draftはunreviewedとして区別。解消。

## 制約

読取専用の文書/Git metadata review。ブラウザ/印刷render、DB/Scheduler再観測、pytest、元historical snapshot再読は実行していない。`approved`はv0.9 HTMLの報告整合性に対する判定であり、cause解明、障害回復、正式deployment、自然日次成功を意味しない。Git公開やU24/U25をgateしない。
