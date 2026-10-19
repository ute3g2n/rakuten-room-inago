# FC07 / FCT09 HTML・fallback独立フォローアップレビュー（第2回）

## 判定

**changes_requested（未承認）**。合成HTML4件、fallback生成・保存・再開経路、該当する標準・skill・contracts・template・schemas・promptsを実ファイルで再確認した。対象manifest SHA-256は `9f62e02d5239be4cee3e292a763602e20a99f09a0a43469be6195c62377d5623` と一致し、manifest記載の17ファイルすべてのSHA-256が実ファイルと一致した。

| identity field | value |
|---|---|
| job_id | `job-historical-9e7f0c175cc4c874c955a5dab29c4427` |
| source_launch_id | `historical-9e7f0c175cc4c874c955a5dab29c4427` |
| target_date | `2026-10-17` |
| source_run_id | `null` |
| incident_id | `352492da-ac58-4680-b1fd-fe5f24000229` |

## 前回の7指摘

1. 合成例の表示: **修正済み**。全4HTMLの冒頭に実際の10/17調査結果ではないことが見える。
2. review結果の断定: **修正済み**。未レビューの想定状態と明記し、指摘なし・承認済みとはしていない。
3. 通常例の期待・実際・影響・回復・変更: **修正済み**。予定3件と記録2件、影響・回復の不明、変更有無、試験範囲を説明。
4. 全項目不明の既知/未知: **修正済み**。開始記録、終了記録の欠如、原因候補の調査記録を区別。
5. 変更なし例の計装検討: **修正済み**。接続点がないため単独ログが原因に結び付かない理由と、接続点を探す次の計画を記載。
6. renderer失敗時の短い報告と後続状態: **修正済み**。fallback内容、review/publication未実施、次の行動を記載。
7. 区分・証拠・workflowとincidentの分離: **概ね修正済み**。全例で出来事区分を表示し、本文リンクが開いた付録の一意なIDへ解決する。workflow終了と元不具合解決も別記載。ただし具体的な残りは下記F1に記載。

## 前回のF1〜F4

| 指摘 | 判定 | 根拠 |
|---|---|---|
| F1 発生区分 | 修正済み | 全HTMLに「出来事の区分」があり、`report_fallback.py`にも `event_category` がある。 |
| F2 証拠参照の存在 | 修正済み | HTML内リンクは全て一意なIDに解決。各コード参照先もworkspace内に存在し、fallback builderは合成例の数値・記述を含む。 |
| F3 fallback指摘修正 | 一部修正 | reviewer指摘を1回の実reporter呼び出しへ渡し、再reviewする。二度目に指摘が残る場合のreview版結合にF1が残る。 |
| F4 identity欠測 | 一部修正 | `build_fallback_report` は欠測値を表示してHTMLを組み立てるが、stageで成果物保存する前に必須identity検証に当たり、HTMLを永続化できない。下記F2。 |

## 残る指摘

### F1 — 2回目のreview結果を、reviewしていない最終HTMLの版へ結び付けている

場所: `automation/inago/maintenance/production_workflow.py` 8545〜8565行。

1回のreporter修正後、reviewerが再び `changes_requested` を返すと、そのreviewer応答は修正版HTMLのhash/版に結び付く。その直後にcontrollerが別内容の未レビューfallback HTMLを生成する一方、既存 `review_document` のfindingを保持したまま、`reviewed_version` と `reviewed_sha256` を新fallback版へ上書きする（8561〜8564行）。そのためfindingが実際に指した版と、review結果が名乗る版が異なる。新fallbackを未レビューとして公開する判断は可能だが、前版の指摘は前版のhashに結び付けて保存し、新fallback版のreview statusは未レビュー/ unavailableとして指摘を誤結合しないこと。

### F2 — 欠測identityのHTMLは作れるが、report stageから保存できない

場所: `automation/inago/maintenance/production_workflow.py` 8406〜8432行、`automation/inago/maintenance/investigation.py` 158〜179行・632〜697行。

fallbackは、最初に `report-input-fallback-<hash>.json` を `save_attempt_artifact` で保存し、その後にHTMLを保存する。保存関数の `_validate_context` はincident_id/source_launch_idを有効ID、target_dateを実在日付として要求する。manifestの必須値が欠けていれば、HTMLを作れる `build_fallback_report` に到達しても、入力artifact保存で失敗してHTMLはattemptへ残らない。外側で失敗traceを追記する処理はあるが、内容あるHTML成果物・公開へ進めない。欠測値を保持したままreport artifactを残せるcontext/schemaを用意するか、ユーザー規則どおり利用できるidentityだけで継続できる保存経路が必要。今回の固定jobではsource_run_id=null以外のidentityは利用可能なので、今回の通常実行結果を否定する指摘ではない。

### F3 — completed fallback reportの再開読取がfallback input形式に対応していない

場所: `automation/inago/maintenance/production_workflow.py` 7766〜7801行、9607〜9681行。

fallbackは `maintenance-report-fallback-input-v1` のinputを保存し、`result.json.report_input` にそのpath/hashを記録する。ところがcompleted jobの `_restore_completed_daily_report_context` はそのresult内のpathを使わず、`_daily_report_attempt_input` が返すファイルを通常report inputの必須key集合と `maintenance-report-input-v1` に照らして検証する（9655〜9681行）。fallback inputには `source_sha256`、`report_round`、`source_snapshot` 等がなく、schema_versionも異なるため、completed fallback reportを同一jobで再開する際に `report_input_artifact_invalid` となる。resultの `report_input.path/sha256` から正しいhash-version artifactを読み、fallback schemaを分岐復元する必要がある。また `_daily_report_attempt_input` はfallback inputを複数見つけた場合に名前順の最初を返すため、resultが指す版へ結び付ける必要がある。

## 確認できた点・未確認範囲

- 全4HTMLの本文は平易で、何が起きたか、確認範囲、変更、未知点、次の行動が分かる。根本原因や利用者への実影響を断定していない。合成例表示、fixtureと製品試験の区別、review/publication/workflow/incidentの分離も確認。
- 4HTML内の全fragment anchorは一意なidへ解決し、全 `code` 内パスは実在する。主な根拠には作成ルール、受入契約、トレース契約、fallback builderまたはproduction workflowを指定している。
- fallback reviewerのtransport失敗は `unavailable` / `pending` とし、resultは `next_action=publish`。author修正失敗時も、修正未完了を明示したfallbackを保存し、承認済みとはせず公開へ進める。
- HTML/入力/review入力/review応答/review artifact名はattempt配下で20桁hashにより版別になり、保存は同内容再利用・異内容衝突拒否。result schemaとterminal readbackはfallback HTML名を許容する。completed fallback context復元の不整合はF3。
- 欠測identityはfallback builder単体では対象日不明と欠測項目を表示する。stage保存全体ではF2。
- 秘密・個人情報は見つからなかった。HTMLはviewport、折返し、印刷時の幅調整を持つ。ソースとアンカーの静的確認のみで、ブラウザー表示・印刷レンダリングは実施していない。実本番HTMLのレビューでもない。

## reviewed HTML版

| ファイル | SHA-256 |
|---|---|
| `通常.html` | `31ecb4e0d115b3fd82cfa789e5cf3862a5e126f1e0a663d91e3336eeeeb2c8d4` |
| `全項目不明.html` | `751bafa96778e878e3cea113b4d9e64c6758fa83f983ce4b6815af7bde881059` |
| `変更なし.html` | `514db4f1fd61b03201f231074c4a871cf669df0a30765d676ec49f235eb10ff8` |
| `通常renderer失敗.html` | `1171f80698567fe6ae19b041944d7d4f19b9f01846e15e9d6272cd68facdd66b` |

以上は今回の合成HTMLとfallback実装に対する独立レビューで、実本番報告書への承認ではない。
