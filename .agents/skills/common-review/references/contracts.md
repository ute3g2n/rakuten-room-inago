# review stage契約

## 読取対象

利用可能なplan version（review状態を問わない）、

対象jobとreview対象versionまたは成果物があればreviewerを起動する。任意証拠、runtime観測、host receipt、前段statusが欠けても開始する。review対象そのものがない場合だけその必須入力欠落を記録する。reviewerが実際に起動していない状態を、合成した`unavailable` reviewで置き換えない。

## テストコードの独立レビュー方針

レビュアーはテストコード自体を独立レビューしない。計画上の期待と、hostが記録した完全node・実測結果・版・証拠は照合する。テスト実行またはデバッグでテストコードの不具合が判明した場合は、担当者に修正と影響caseの再実行を求め、古いテスト版の結果を流用しない。これはテストコードの独立レビュー工程を追加するものではない。

## 起動元・対象日の不変結合

U05の現行管理schemaはmaintenance_jobにsource_run_idのみを保持し、source_launch_idとtarget_dateを保持しない。incident_runには同一incidentに複数の起動が関連し得るため、担当はincident_idや最新行から起動元を推測できない。U07のcontrollerはincident_id / source_launch_id / source_run_id（nullを含む）/ target_date / job_idの5項目を当該jobへ厳密に結び付け、値とnullnessを照合した検証済み不変job-input manifestを渡す（または同等の正本store契約を追加する）。欠落・不一致・曖昧な結合はunknownと具体理由を記録し、値を推測しない。対象jobと担当stageが識別できれば利用可能な入力で直ちに起動する。対象を選べない個別操作だけ未実施とし、部分成果と後続工程を続ける。source_run_idは入力どおり保持し、nullを推測値で埋めない。

## 出力

allowed_outputsに列挙されたreview.jsonとreview.mdだけを作る。reviewにはreviewer_id、scope_type/scope_id、reviewed_version、status（approved / changes_requested / rejected / unavailable）、時刻、findingsを含める。findingはseverity、file/lineまたは証拠参照、具体的な問題、plan/case対応、修正要求を記録する。

レビューを正確なresult_versionに紐付ける。実装または試験結果が変われば新しいレビューを要求する。レビュー結果を管理DBへ直接登録しない。controllerがschemaと独立性を検証して記録する。

## 判定

- approved: 計画範囲、プロダクトコード、関連試験の実測結果、禁止副作用、証拠が一致。テストコードの独立レビューは承認条件にしない。
- changes_requested: 修正可能な不足がある。具体的な修正と再確認対象を書く。
- rejected: 重大な不許可副作用、契約迂回、秘密漏えい、証拠改変など。
- unavailable: 根拠不足や対象版不明で判定できない。成功扱いにせず、欠測を記録する。いずれのstatusも後続stageを止めない。

review差戻し回数の上限を設けず、review statusやfindingsを後続stageへのgateにしない。実行不能な個別操作があってもその事実を次担当とreportへ返してworkflowを続ける。「絶対にどうしても必要」な後続gateは基本的に存在しない前提とする。

## FC02 現行の全工程引継ぎ契約（2026-10-19）

この節は現行契約であり、過去のU05～U23に残る開始拒否・承認・worktree・終端結合の説明は旧実装の履歴として扱う。本人一名のローカルシステムで堅牢性を目的に制限を足さない。唯一の作業場所は `C:/project/rakuten-room-inago`。全担当のreasoning effortはhighとし、固定max roleしか選べないsessionでは同じ専門promptをhigh指定可能なworker/defaultで実起動する。指定値と実観測値を区別し、model/tier/receipt/session等の未観測を起動条件にしない。

各stageは内容ある通常成果またはcontroller fallback成果と有効なhandoffを保存して次担当を実起動する。将来のstage結果にblockedを返さず、別名の状態・承認・receipt・lease・回数上限に置き換えたgateも作らない。review/test/readiness/release、FAIL、unknown、証拠不足、空/不正なAI応答、過去raw blockedを後続開始条件にしない。個別操作ができない場合はその操作だけ `not_performed` と具体理由を記し、他の作業と最終報告・公開を続ける。過去raw履歴と元の日次結果は改変しない。

不変のcontroller入力identityを保持し、欠測はnullとmissing_reasonを残す。source_run_idやtarget_dateを推測しない。対象が識別できる限り部分成果を出す。実装操作を省略できるのは原因修正と有用な計装の双方ができない具体的理由を確認した場合だけであり、既知の保守trace不足は今回実装する。差分0でもtesterは成果物整合を実検査し、製品reviewerは対象差分なしを実確認してapprovedを捏造しない。

handoffはidentity、schema_version、invocation/stage/attempt/operationとparent_operation、入力版/参照、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs、artifact_origin（agent/controller_fallback）、実行担当、review対象版、開始/終了/観測時刻、code revisionを持つ。本文は「何を行い、何が分かり、何が不明で、何を次に行うか」を含む。新attempt IDはcontrollerの正式採番値だけを使う。

工程の引継ぎ完了、操作の成否、試験/review/反映/公開結果、workflow_completion、incident_resolutionを別々に保存する。jobのフロー完遂からincident resolvedを自動推論しない。原因/業務回復unknownのまま同一jobのフロー完遂は可能だが、公開・必須成果永続登録・終端read-backが揃わなければ完遂としない。公開未達でもHTML、原因と再開位置を保存し、同じjobの公開・終端整合操作を続ける。

各失敗は操作名、例外classと安全化したcause chain/stack frames、発生/捕捉位置、実行版、相関ID、入力/成果物参照へ結ぶ。例外本文、SQL/parameters、商品/投稿、token/cookie、秘密/個人情報をtraceへ出さない。通常loggerの失敗は独立fallbackへ保存して本処理を続け、復帰時に同じ相関値で追記整合する。全保存先喪失の未記録区間は復帰時にunknownと明記する。

reporterと独立report reviewerを実起動し、指摘はreporterが修正する。通常生成失敗時も平易な最小HTMLをcontrollerが生成し、その実在版をreview・公開へ渡す。本文は中学生が理解できる文章にし、内部ID/STEP/status/reason/SHAは参考付録へ置く。テストコードの独立reviewは行わず、製品コードの独立reviewを実施する。

今回対象は10/17の同一jobとlaunch、source_run_id=nullを保持する。10/19 queue/history処理、通常Daily手動起動、新job代替、日次run捏造、下書き追生成、別workspace/worktreeは禁止。Gitはcontrollerが所有file/hunkだけを確認・選択stage・commit/pushする。
review担当は、差分や試験結果が不足しても、利用可能なcontroller fallback/unknown成果を実際に確認してレビュー成果を作る。対象を読めない時は読めない箇所と理由を記録し、reviewer未起動を合成結果で置き換えず、statusや指摘を後続stageのgateにしない。
