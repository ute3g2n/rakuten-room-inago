# FC02 契約決定 v1

基準は承認計画1.2（SHA e7434af9385cd2a33dc66fbc57004cfe7576ea76c0362847e48d129831f4f381）、実行HEAD 51cfc7503775a25af731c8f4bcf727c94f10a595、FC01の2026-10-19 22:43:53 JST読取、production schema 13。FC02は製品コード/DB/Schedulerを変更せず、契約とrole設定を同期する。原10/17原因・業務回復・実投稿件数はunknown。調査v2とreview v2の原証拠未供給・review範囲・20:44作成cutoffの制約を保持する。review approvedは計装や現在版の成功証明ではない。

## 状態を分ける

新handoffのschema_versionは maintenance-stage-handoff-v1 とする。stage_progressはpending/running/completed、operation_resultはsucceeded/failed/not_performed、content_resultはconfirmed/unknown/partial/no_change、artifact_originはagent/controller_fallback。raw agentのblocked/invalid/emptyは非公開原応答と別の品質観測に保持し、新規stage結果へ伝播しない。completedは内容あるhandoffを確定した意味であり、操作成功・review approved・元障害解消を意味しない。

workflow_completionはin_progress/completed/incomplete、incident_resolutionはunknown/unresolved/resolved、publish_resultはsucceeded/failed/not_performedで分離する。flow completedには全必須成果の永続参照、実担当の処理証拠、公開HTML版/hash/review/commit/remote read-back、job終端/finished_atの一致が必要。これらは完遂を正しく観測する条件であり、次担当起動のgateではない。公開失敗時も終端整合操作まで実行し、未達理由と同一job再開位置を残す。CLIはflow completedでexit 0、未完遂は実nonzero（既存maintenance_incompleteのexit 3を維持候補）を返し、incident unknownだけでexit 3にしない。最終番号互換性はFC05実装で確認する。

## 共通handoffと内容

identity（job/incident/launch/run/date）、schema_version、invocation_id、stage、正式attempt_id、operation_id/parent_operation_id、input_refsと版、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs（path/hash/version）、artifact_origin、作者と実担当/独立reviewer、review対象版、開始/終了/観測時刻、code revisionを必須意味とする。未知値はnull+missing_reasonとし、IDや時刻を推測しない。本文は各担当の実操作・判明事項・不足・次操作を含む。DB未登録のfallbackをDB登録済みと称さない。

## 失敗追跡・保存

全操作にbegin/end/error/abnormal_end/reconciledをappend-only記録し、job→invocation→stage→attempt→operation→eventを結ぶ。AIのthread/turn/requestは実取得値だけを記録する。errorはreason_codeに加え安全な例外class、cause/context関係、module/function/repo-relative file/lineのstack frames、発生と捕捉の区別、elapsed、最後の成功操作、入力/出力refs、code SHA/dirty差分参照を持つ。例外文字列・SQL/値・credential・商品/投稿/個人情報を含めず、任意code文字列も許可分類へ安全化する。

通常DB/JSONLの書込失敗は独立failure_journalへ最小標準I/Oで保存し、通常loggerを再帰利用しない。固定workspace内runtime maintenance専用spoolへ、temporary→flush/fsync→atomic replaceでintentとartifact参照を残す。stderr監視側採取も使い、失敗したsink自身と元操作のcauseを別項目にする。容量/件数/lease/receiptで本処理を止めない。DB復帰時はevent_id/operation_idで重複を避け参照を追記整合し、artifact/hashをread-backする。強制終了後はbegin-only区間へ検出時刻付きabnormal_endを追記し、架空の元終了時刻や例外を作らない。全保存先同時喪失の区間はunknownとして復帰時に追記する。

## 今回の実装と同一job

日次SQLite分類・許可constraint・transaction・proposed/persisted run・capture tuple/hashは既存のため再追加しない。FC03はmaintenance操作相関/例外連鎖/trace writer独立fallback、AI adapter parent B high、DB/artifact/Git失敗境界を実差分化する。FC04は通常/空/不正/失敗から有効handoffと専門担当実起動を実装する。FC05は中断再開、試験/review/反映/終端を分離、FC06は平易な最小HTMLと実reporter/reviewer/修正/公開を結ぶ。外側で先行実装された候補は、同一jobのplanner/implementer/tester/reviewer/deploymentが実際に検証・正式適用し、新traceを同一jobで採取する。jobがその差分を新規作成したとは表現しない。

## 役割・範囲・Git

全9 roleのeffort highを設定/本文で照合し、実model/tierは未観測をunknownとする。5 maxのbefore値/SHAをrole-high-sync-result.jsonへ保存。ai_adapter製品設定max→highはFC03担当でありFC02適用済みとはしない。親A現採用model gpt-6.1-sol、親B gpt-6-lunaを候補とし、実使用可能性を捏造しない。本人一人のローカルシステム方針は全skill/contractsと全 downstream promptへ引継ぐ。所有外変更は保持し、所有表追加をcontrollerへ連絡して可能作業を続ける。GitはFC09/controllerだけが所有file/hunkを確認して選択stage/commit/pushする。

## 継続と未達

FC02でpytest・製品review・migration適用・本番job再開・公開は未実施。独立plan reviewerが本成果物を次に実確認するが、review結果をFC03開始条件にしない。schema fieldは本契約の意味へ実装で結び、実装で統合/変更した場合は同STEPで本所有表とschema一覧へ理由を追記する。実在しないnodeや正式attempt IDを作らない。次操作はFC03のfailure_journal/diagnostic_trace/AI adapter/CLI境界実装。

## FC02 現行の全工程引継ぎ契約（2026-10-19）

この節は現行契約であり、過去のU05～U23に残る開始拒否・承認・worktree・終端結合の説明は旧実装の履歴として扱う。本人一名のローカルシステムで堅牢性を目的に制限を足さない。唯一の作業場所は `C:/project/rakuten-room-inago`。全担当のreasoning effortはhighとし、固定max roleしか選べないsessionでは同じ専門promptをhigh指定可能なworker/defaultで実起動する。指定値と実観測値を区別し、model/tier/receipt/session等の未観測を起動条件にしない。

各stageは内容ある通常成果またはcontroller fallback成果と有効なhandoffを保存して次担当を実起動する。将来のstage結果にblockedを返さず、別名の状態・承認・receipt・lease・回数上限に置き換えたgateも作らない。review/test/readiness/release、FAIL、unknown、証拠不足、空/不正なAI応答、過去raw blockedを後続開始条件にしない。個別操作ができない場合はその操作だけ `not_performed` と具体理由を記し、他の作業と最終報告・公開を続ける。過去raw履歴と元の日次結果は改変しない。

不変のcontroller入力identityを保持し、欠測はnullとmissing_reasonを残す。source_run_idやtarget_dateを推測しない。対象が識別できる限り部分成果を出す。実装操作を省略できるのは原因修正と有用な計装の双方ができない具体的理由を確認した場合だけであり、既知の保守trace不足は今回実装する。差分0でもtesterは成果物整合を実検査し、製品reviewerは対象差分なしを実確認してapprovedを捏造しない。

handoffはidentity、schema_version、invocation/stage/attempt/operationとparent_operation、入力版/参照、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs、artifact_origin（agent/controller_fallback）、実行担当、review対象版、開始/終了/観測時刻、code revisionを持つ。本文は「何を行い、何が分かり、何が不明で、何を次に行うか」を含む。新attempt IDはcontrollerの正式採番値だけを使う。

工程の引継ぎ完了、操作の成否、試験/review/反映/公開結果、workflow_completion、incident_resolutionを別々に保存する。jobのフロー完遂からincident resolvedを自動推論しない。原因/業務回復unknownのまま同一jobのフロー完遂は可能だが、公開・必須成果永続登録・終端read-backが揃わなければ完遂としない。公開未達でもHTML、原因と再開位置を保存し、同じjobの公開・終端整合操作を続ける。

各失敗は操作名、例外classと安全化したcause chain/stack frames、発生/捕捉位置、実行版、相関ID、入力/成果物参照へ結ぶ。例外本文、SQL/parameters、商品/投稿、token/cookie、秘密/個人情報をtraceへ出さない。通常loggerの失敗は独立fallbackへ保存して本処理を続け、復帰時に同じ相関値で追記整合する。全保存先喪失の未記録区間は復帰時にunknownと明記する。

reporterと独立report reviewerを実起動し、指摘はreporterが修正する。通常生成失敗時も平易な最小HTMLをcontrollerが生成し、その実在版をreview・公開へ渡す。本文は中学生が理解できる文章にし、内部ID/STEP/status/reason/SHAは参考付録へ置く。テストコードの独立reviewは行わず、製品コードの独立reviewを実施する。

今回対象は10/17の同一jobとlaunch、source_run_id=nullを保持する。10/19 queue/history処理、通常Daily手動起動、新job代替、日次run捏造、下書き追生成、別workspace/worktreeは禁止。Gitはcontrollerが所有file/hunkだけを確認・選択stage・commit/pushする。
