# S02 製品コード独立再レビュー（R01/R02対応後）

- 判定: `approved`
- レビュー日: 2026-10-19 JST
- 対象HEAD: `d7241547d5e6111962177105c9149d6414a2ddee` (`main`)
- 対象: HEADとの差分にある製品コード3ファイルの現行working tree版。staged差分はなし。
- レビュー範囲: R01 logger例外の隔離、rollbackと元Daily結果への影響、R02 pipeline log/JSONLのfailure module/function/line/hash対応。テストコードはレビュー対象外。Daily、Scheduler、本番DBは起動・変更していない。
- 再レビューは前回レビュー記録を変更せず、別記録として保存した。

## 対象版

| File | Git blob | SHA-256 |
|---|---|---|
| `automation/inago/daily_pipeline.py` | `16f5e06cde3362bd6b7f7b8742420440eea2aaa2` | `AEAB818F9C50F98ABB1B603578BB6DD9CFF0BBD96B3E728DA31F4A15BECB290E` |
| `automation/inago/orchestrator.py` | `eff6296ac44ffc06bee8823a9c833e1c64bf44c6` | `F615A5C3C8DF6A94412791F95273560CE45D023BEBA34932D80616C295D7F2A7` |
| `automation/inago/trace.py` | `4212d0308a8d562c6243ab36693dea9dba0bada0` | `8B6743C6ED975D6C581E8F917EA5E4F41CDF89983F061A38E445D2F8513D786F` |

## 確認結果

### R01 — logger handler例外

`_best_effort_log()` (`daily_pipeline.py:48-56`) はloggerの呼び出しとextra mappingの評価をtry内に置き、handler等からの通常の例外を捕捉する。失敗処理ではDB rollbackが `daily_pipeline.py:885-894`、元の結果mappingとstatus算出が `895-918` で先に済み、その後に `_best_effort_log(logging.ERROR, ...)` (`919-938`) を呼ぶ。したがってloggerのI/O例外はrollbackを飛ばさず、既に組み立てた業務結果も置き換えない。最終trace結果のinfo logも同じbest-effort helperを通す (`1064-1080`)。

この実装確認により前回のS02-R01は解消と判断する。

### R02 — log/JSONLの捕捉位置とcode hash

`_pipeline_capture_location()` (`daily_pipeline.py:35-46`) は呼び出し元frameからmodule/function/lineを取得し、moduleをdaily_pipeline内に限定する。outer exception handler (`868-870`) で一度取得した `(module, function, line)` を `phase_error()` (`873-883`) に渡し、JSONL eventとfailure logger (`931-934`) の両方で同一座標を使う。code SHA-256も同じexcept内で一度取得し、timeline eventとfailure logへ同一値を渡す (`871-882`, `934`)。JSONL writerはerror eventに格納済みの値をそのまま使用する (`657-670`)。ハッシュの読取が失敗した場合はloggerにnull、JSONLでは任意field欠測として同じ「未取得」を表す。任意callbackのtraceback位置をpipeline file内の行として誤表示することもない。

trace writerでは各error eventに記録済みfailure fieldがそのまま保存される (`daily_pipeline.py:657-672`)。callback内の任意例外行をpipelineファイルの行として誤表示していた前回S02-R02は解消と判断する。

## Findings

再レビュー対象のR01/R02について、現行差分では追加指摘なし。

レビュー担当はテストコードを読まず、テストを実行していない。既存の試験結果はこの判定のgateとして使用していない。本再レビューは計装の例外境界とソース位置の整合性を確認したものであり、2026-10-17 incidentのroot cause、修正、再発防止が確認されたことを意味しない。
