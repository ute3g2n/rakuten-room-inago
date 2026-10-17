# testing stage契約

## 入力

review approved plan_version、実装commit/version、許可case_idと完全node、初期fixture/時計/設定、T内の当該job/attempt専用basetemp、allowed_outputsを使う。test listをplanやU19凍結manifestから逸脱させない。

## 起動元・対象日の不変結合

U05の現行管理schemaはmaintenance_jobにsource_run_idのみを保持し、source_launch_idとtarget_dateを保持しない。incident_runには同一incidentに複数の起動が関連し得るため、担当はincident_idや最新行から起動元を推測できない。U07のcontrollerはincident_id / source_launch_id / source_run_id（nullを含む）/ target_date / job_idの5項目を当該jobへ厳密に結び付け、値とnullnessを照合した検証済み不変job-input manifestを渡す（または同等の正本store契約を追加する）。欠落・不一致・曖昧な結合があれば開始せずblockedを返す。source_run_idは入力どおり保持し、nullを推測値で埋めない。

## 実行許可

- 書込はcaseが明記した隔離fixture/試験DBとT/Uxx/Cxx/attempt-Nの専用領域のみ。source、app configuration、日次DB、認証、Schedulerはread-onlyまたは未接続。
- testを実行する前にnodeが実在すること、履歴上の未実施/FAILであること、basetempが専用空のattempt領域であることを確認する。
- U20前の実行は新規、未実施または前回FAILで計画が再試行を許すcaseに限る。既PASSのcase、pytest collect、全suiteを起動しない。

## 証拠

各caseにcase ID、完全node、command、開始終了JST、input/clock/config/code version、期待、実結果、exit code、console/JUnit、初期/最終件数、trace/artifact参照、reviewerを結び付ける。不要データやsecretは保存前に伏字化する。

結果statusはPASS / FAIL / SKIP / XFAIL / ERROR / NOT_RUN / UNVERIFIEDを区別する。exit 0やskipだけをPASS根拠にしない。fixture上のPASSは本番成功を意味しない。

## 再開と禁止

FAILの再実行は新attemptへ記録し、同じcase input/versionを維持する。PASSはU20全回帰で指定された一回を除いて再実行しない。隔離環境・所有範囲・許可nodeが不明なときは実行せずblockedとする。試験担当はコードを直さない。