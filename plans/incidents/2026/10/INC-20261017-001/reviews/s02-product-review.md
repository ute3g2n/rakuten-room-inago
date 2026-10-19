# S02 製品コード独立レビュー

- 判定: `changes_requested`
- レビュー日時: 2026-10-19 JST
- 対象ブランチ / HEAD: `main` / `d7241547d5e6111962177105c9149d6414a2ddee`
- 対象: HEADからの unstaged 製品差分3ファイル。S01 `store.py`、テストコード、staged差分は対象外。
- 読取専用レビュー。製品コード、テスト、DB、Daily、Schedulerは変更・起動していない。
- 対象blob / SHA-256:
  - `automation/inago/orchestrator.py`: blob `eff6296ac44ffc06bee8823a9c833e1c64bf44c6`; SHA-256 `F615A5C3C8DF6A94412791F95273560CE45D023BEBA34932D80616C295D7F2A7`
  - `automation/inago/daily_pipeline.py`: blob `ccfb75ee545859acd3a88a854e1ac9047e69f304`; SHA-256 `48CC59099CF8E4B20424B4C096F6E7DCFE0E04B7576303A085CEF632F622D251`
- `automation/inago/trace.py`: blob `4212d0308a8d562c6243ab36693dea9dba0bada0`; SHA-256 `8B6743C6ED975D6C581E8F917EA5E4F41CDF89983F061A38E445D2F8513D786F`

## 確認範囲

trace field allowlistと型検査、error enumとlabel検査、提案IDと永続IDの分離、transaction begin/commit/rollback/rollback_failedの報告、code hash/line、例外境界と副作用を確認した。自由文の例外メッセージやtraceback本文を記録せず、例外クラス・制約enum・数値error code・行番号・ハッシュを使う設計は妥当。提案ID/永続IDは別フィールドで記録される。

K02〜K04の最終実測は `S03-K02-K04-focused-test-record.md` にある。計画に書かれたnullable-deadline node名は存在せず、初回の誤記指定は0 testsで終了した。既存node `test_n20f_daily_started_after_1800_runs_with_nullable_new_deadlines` はPASS。K03 `test_c09_contract`、K04 `test_n03` もPASS。K02は安全関数名ラベルでの拒否、null ID省略、試験の隔離emitter接続を修正後、現行trace hashで隔離SQLite emitter integrationが1 passedとなった。最終K02確認ではrollback後のJSONLに proposed ID・明示null persisted ID・false persisted flag・PRIMARYKEY分類があり、structured logでtrace persisted=true/status=writtenだった。これらは計装のfocused確認で、10/17 incidentの根本原因が特定・修正されたことを示さない。

## 指摘

### S02-R01 — P2: daily_pipelineの診断logging例外がDaily結果を変え得る

- 場所: `automation/inago/daily_pipeline.py:837-856`、`1015-1030`
- `daily_pipeline_failure` の `logger.error()` は、例外ハンドラ内でDB rollback (`857-866`) より前に呼ばれ、logging handler由来の例外を捕捉しない。handlerの書込み障害が例外として表出した場合、本来の失敗結果を組み立てる前に制御が抜け、rollbackもスキップされ得る。
- `daily_pipeline_trace_result` の `logger.info()` も例外を捕捉しないため、traceが利用不能でも診断loggingの障害で成功したDaily実行の戻り値を失敗に変え得る。
- orchestrator側では `safe_log()` で同種の例外を隔離しているが、pipeline側は非対称。
- 対応案: 両logger呼出しを例外隔離する共通のbest-effort helperで包み、特にfailure loggingはrollback後またはrollbackを妨げない保護境界内で実行する。診断I/Oの失敗をDailyの業務結果へ伝播させない。

### S02-R02 — P2: pipelineの失敗行番号が固定されたmodule/functionと一致しない場合がある

- 場所: `automation/inago/daily_pipeline.py:835-852`
- `traceback.extract_tb(...)[-1].lineno` は最も内側の例外発生フレームの行番号を返す一方、記録する `failure_module` / `failure_function` / `code_sha256` は常に `daily_pipeline.py` / `run_daily_execution_core` / pipelineファイルのhashとして固定される。`ports.preflight`、`ports.prepare`、`ports.run_daily` 等のcallback内部で例外が起きると、行番号はcallbackのファイルを指し得るのにmodule/function/hashはpipelineを指し、将来の診断で誤った位置を案内する。
- 対応案: tracebackの最終frameから取得したファイル・関数も、許可されたモジュール/関数形式へ正規化して記録し、対応するソースのhashと結び付ける。任意パスを保存できない場合は、pipeline内の捕捉行を記録するなど、同一対象を指す一貫した組にする。

## 旧試行で判明し、最終対象版で解消された点

- 先頭underscoreを含む `_create_run` が旧field label regexに拒否された問題は、`trace.py` の関数label用regexで先頭underscoreを許可して修正された。
- nullable `persisted_run_id` がJSONLから省略されていた問題は、最終対象版で明示JSON nullを保持するよう修正された。
- 最終K02 isolated SQLite emitter testは現行trace hashに対してPASSし、前述の値とtrace write結果を確認した。

## 良好だった点

- `trace.py` は追加fieldを明示allowlistに加え、bool・bounded int・SHA-256・safe identifier・enum/labelを個別検証する。未許可fieldを拒否し、自由文の例外メッセージ/traceback文字列を保存しない。
- `orchestrator.py` は提案IDと永続IDを別々に出し、begin/commit/rollback/rollback_failedを列挙値で示す。trace writeとloggingは例外境界で隔離され、diagnostic failureが元例外を置き換えない。
- DBトランザクションの結果を診断用に観測しつつ、元の例外を再raiseする構造は保持されている。

## 後続への事実

この判定は後続工程のgateではない。残る指摘を実装担当へ渡し、対応可否・対象hash・再試験の実測を記録すること。incident root causeは引き続きunknownとして扱い、計装を原因修正と表現しない。
