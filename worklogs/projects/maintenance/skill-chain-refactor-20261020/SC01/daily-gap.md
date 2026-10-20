# SC01 日次処理の不足観測点

観測元は開始時HEAD `0f73627dea4c5b1f913ff79bf0021212c321695b` の実ファイル。いずれも10/17障害や10/20の競合原因と確認されたものではない。既存pipeline外側の失敗記録、SQLite分類、捕捉場所、コードhash、提案runと永続runの区別は再利用し、重複計装をしない。

| 実コード境界 | 現在の動作 | 日次で追加する観測 | 隔離確認 |
|---|---|---|---|
| `daily_pipeline.py::_result_mapping` | `failure_result` callbackがraiseすると例外を捨ててdefault failure resultを返す。一次障害結果は保たれるが、callbackの二次障害は別記録にない。 | 固定operation `failure_result_callback`、二次例外class、捕捉module/function/line、pipeline SHAを通常loggerへsafe fieldsで記録。default戻り値と一次エラーを変えない。 | callbackに`ValueError`を起こし、元の一次status/reasonを維持し、二次diagnosticが1件だけ残ることを確認。 |
| `daily_pipeline.py::_retry_summary_for_run` | ROOM search、Trends acquisition、Trends attemptの3 queryを一つのtryで実行。SQLite例外は4集計値をnull化するが失敗query名・捕捉場所は残らない。 | 各queryの固定operation label (`retry_summary_room_attempts`, `retry_summary_trend_acquisitions`, `retry_summary_trend_attempts`)、SQLite exception class、捕捉位置、pipeline SHA。SQL・parameters・runの生値は記録しない。 | それぞれのqueryを個別に失敗させ、既定のnull 4値と安全eventを確認。他query成功時も現行集計値を保つ。 |
| `daily_pipeline.py::refresh_history_after_commit` | commit後にexport callbackが例外を出すと、`failed`と例外型名だけ返してcommit後の処理を続ける。 | 固定operation `dashboard_export_after_commit`、例外class、callback捕捉位置、pipeline SHAを記録。commit/rollback動作と返す件数を変えない。 | 合成commit後にexport callbackを`OSError`にし、commit・draft件数を維持、rollback 0、safe diagnostic 1件を確認。 |

すべてのログ値を自由文字列から作らず、安全なラベルと例外クラス、関数名・行番号、SHAだけに制限する。logger自体が失敗しても日次結果を置き換えない。今後の試験結果と実装は同jobの`implementation.md`/`test.md`へ結び、本書は開始時の評価を保持する。
