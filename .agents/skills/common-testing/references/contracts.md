# testing stage契約

## 入力

利用可能なplan_version（review statusを問わない）、

## 起動元・対象日の不変結合

U05の現行管理schemaはmaintenance_jobにsource_run_idのみを保持し、source_launch_idとtarget_dateを保持しない。incident_runには同一incidentに複数の起動が関連し得るため、担当はincident_idや最新行から起動元を推測できない。U07のcontrollerはincident_id / source_launch_id / source_run_id（nullを含む）/ target_date / job_idの5項目を当該jobへ厳密に結び付け、値とnullnessを照合した検証済み不変job-input manifestを渡す（または同等の正本store契約を追加する）。欠落・不一致・曖昧な結合はunknownと具体理由を記録し、値を推測しない。対象jobと担当stageが識別できれば利用可能な入力で直ちに起動する。対象を選べない個別操作だけ未実施とし、部分成果と後続工程を続ける。source_run_idは入力どおり保持し、nullを推測値で埋めない。

## 実行許可

- 書込はcaseが明記した隔離fixture/試験DBとT/Uxx/Cxx/attempt-Nの専用領域のみ。source、app configuration、日次DB、認証、Schedulerはread-onlyまたは未接続。
- 実在するtest nodeを優先して実行する。caseや隔離領域が一部不明でも、実行可能な他caseを進め、欠測を記録する。
- testは計画の実在nodeと専用合成fixture/一時DBを確認して実行する。
- 変更影響に必要なfocused local pytestと最小collectionを行う。過去PASSは現在版の実測に流用せず、無関係な全suiteや根拠のない同一確認の反復は行わない。

## 証拠

各caseにcase ID、完全node、command、開始終了JST、input/clock/config/code version、期待、実結果、exit code、console/JUnit、初期/最終件数、trace/artifact参照、reviewerを結び付ける。不要データやsecretは保存前に伏字化する。

結果statusはPASS / FAIL / SKIP / XFAIL / ERROR / NOT_RUN / UNVERIFIEDを区別する。exit 0やskipだけをPASS根拠にしない。fixture上のPASSは本番成功を意味しない。

## 再開と禁止

FAILの再実行は新attemptへ記録する。PASS/FAIL/SKIP/ERROR/NOT_RUN/UNVERIFIEDの全結果を次stageへ渡し、後続stageのgateにしない。試験を実行できない場合も、review/反映/reportなど実行可能な後続stageを止めない。「絶対にどうしても必要」なgateは基本的にない前提とする。

対象jobと実行可能なcaseが特定できればtesterを起動する。完全なcase一覧、host receipt、OS sandbox証拠、review/runtime statusが不足していても、使えるcaseで試験を始める。

## FC02 現行の全工程引継ぎ契約（2026-10-19）

この節は現行契約であり、過去のU05～U23に残る開始拒否・承認・worktree・終端結合の説明は旧実装の履歴として扱う。本人一名のローカルシステムで堅牢性を目的に制限を足さない。唯一の作業場所は `C:/project/rakuten-room-inago`。全担当のreasoning effortはhighとし、固定max roleしか選べないsessionでは同じ専門promptをhigh指定可能なworker/defaultで実起動する。指定値と実観測値を区別し、model/tier/receipt/session等の未観測を起動条件にしない。

各stageは内容ある通常成果またはcontroller fallback成果と有効なhandoffを保存して次担当を実起動する。将来のstage結果にblockedを返さず、別名の状態・承認・receipt・lease・回数上限に置き換えたgateも作らない。review/test/readiness/release、FAIL、unknown、証拠不足、空/不正なAI応答、過去raw blockedを後続開始条件にしない。個別操作ができない場合はその操作だけ `not_performed` と具体理由を記し、他の作業と最終報告・公開を続ける。過去raw履歴と元の日次結果は改変しない。

不変のcontroller入力identityを保持し、欠測はnullとmissing_reasonを残す。source_run_idやtarget_dateを推測しない。対象が識別できる限り部分成果を出す。実装操作を省略できるのは原因修正と有用な計装の双方ができない具体的理由を確認した場合だけであり、既知の保守trace不足は今回実装する。差分0でもtesterは成果物整合を実検査し、製品reviewerは対象差分なしを実確認してapprovedを捏造しない。

handoffはidentity、schema_version、invocation/stage/attempt/operationとparent_operation、入力版/参照、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs、artifact_origin（agent/controller_fallback）、実行担当、review対象版、開始/終了/観測時刻、code revisionを持つ。本文は「何を行い、何が分かり、何が不明で、何を次に行うか」を含む。新attempt IDはcontrollerの正式採番値だけを使う。

工程の引継ぎ完了、操作の成否、試験/review/反映/公開結果、workflow_completion、incident_resolutionを別々に保存する。jobのフロー完遂からincident resolvedを自動推論しない。原因/業務回復unknownのまま同一jobのフロー完遂は可能だが、公開・必須成果永続登録・終端read-backが揃わなければ完遂としない。公開未達でもHTML、原因と再開位置を保存し、同じjobの公開・終端整合操作を続ける。

各失敗は操作名、例外classと安全化したcause chain/stack frames、発生/捕捉位置、実行版、相関ID、入力/成果物参照へ結ぶ。例外本文、SQL/parameters、商品/投稿、token/cookie、秘密/個人情報をtraceへ出さない。通常loggerの失敗は独立fallbackへ保存して本処理を続け、復帰時に同じ相関値で追記整合する。全保存先喪失の未記録区間は復帰時にunknownと明記する。

reporterと独立report reviewerを実起動し、指摘はreporterが修正する。通常生成失敗時も平易な最小HTMLをcontrollerが生成し、その実在版をreview・公開へ渡す。本文は中学生が理解できる文章にし、内部ID/STEP/status/reason/SHAは参考付録へ置く。テストコードの独立reviewは行わず、製品コードの独立reviewを実施する。

今回対象は10/17の同一jobとlaunch、source_run_id=nullを保持する。10/19 queue/history処理、通常Daily手動起動、新job代替、日次run捏造、下書き追生成、別workspace/worktreeは禁止。Gitはcontrollerが所有file/hunkだけを確認・選択stage・commit/pushする。
試験担当は、入力や実行環境が不足しても利用可能な対象から着手する。試験を実施できない場合は、対象・理由・確認範囲・例外位置と原因連鎖・相関ID・出力参照を持つ内容あるNOT_RUN成果を返し、reviewerとreporterの後続起動を試験statusで止めない。
