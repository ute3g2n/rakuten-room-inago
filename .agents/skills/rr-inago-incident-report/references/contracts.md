# report stage契約

## 入力

job identity、target_date、利用可能なinvestigation version、plan version（approval状態は観測値）、

## 起動元・対象日の不変結合

現行controllerは既知のjob IDへsource_launch_id / source_run_id（nullを含む）/ target_dateを結び付ける。report対象jobが識別できれば、他のfield・任意manifest・証拠の欠落や不一致だけを理由にreport stageを止めない。得られた値と不足・不一致を報告へ記録する。source_run_idは入力どおり保持し、nullを推測値で埋めない。job自体を特定できない操作だけを未実施として理由を記録し、他の利用可能な報告を続ける。

## 読取と書込

- 読取: 対象jobに紐付く利用可能な資料、plan、C/LC実測、レビュー、反映・復旧結果、証拠参照、報告ルールG、既存HTML構造H。approved statusは入力状態として記録し、report開始条件にしない。
- 書込: allowed_outputs内のincident HTMLおよび構造化result.jsonのみ。報告担当はworktreeを直接commit/pushせず、controllerへ検証可能な出力を返す。
- 外部向けHTMLは公開可能な要約だけとし、ログ/DB/商品/投稿/個人/secretの原文を含めない。

## 内容確認

- 案件・対象日・作成時刻・実行版を区別する。
- 報告本文の作成時刻はJSTで検証する。独立レビューの `reviewed_at` はISO-8601のtimezone-aware値を受け付けてJSTへ正規化し、timezoneがない値は受け付けない。
- 事象一覧と詳細で区分（当日発生 / 調査で判明 / 試験で確認）、工程、操作、期待、実結果、影響、回復、原因の確度、変更後動作、試験範囲、根拠リンクを維持する。
- 調査、作業場所での検証、release gate、反映、次回日次観測は個別の状態として表す。未実施は未実施、skip/errorはPASSでない。
- HTMLは意味的見出し、症状の一覧と詳細、索引、相対リンク、狭幅/印刷向けCSS、pre/codeの改行・字下げ保持、未確認状態の視認性を含む。
- 内部fragment link（`href="#..."`）は最終HTML内の一致する`id`へ解決し、リンク先は一意でなければならない。

## 出力・レビュー・再開

reporterはincident HTMLとresult.jsonをattempt専用pathへ返す。別のrr-inago-incident-report-reviewerが内容、数値、版、リンク、秘密、HTML構造を読取確認する。reviewerの指摘は改善情報として返し、reporterは自分のresponseと本文を修正できる。再reviewを行う場合も回数・statusを後続処理の条件にしない。同じformal attempt内のresponse読取は、検証済みの全候補を返す。今回起動したauthor/reviewer turnはthread_idとturn_idの両方が一致する候補だけを一意に選ぶ。再開時の保存済みresponseはreport result_versionとcreated_at_jst、またはreview対象のreviewed_versionとreviewed_sha256が今回の入力に一致する候補だけを一意に選ぶ。一致なし・重複一致の場合は担当を起動し直し、以前のresponseやファイル名順を根拠に修正版を拒否しない。

入力/リンク/版が欠けても、解決できる範囲でHTMLを作成し、欠測と未完了を明記する。review statusや不足を後続stageのgateにしない。公開操作が実行できなくてもHTML作成とreport保存を続け、公開不能の理由をreportへ記録する。

## FC02 現行の全工程引継ぎ契約（2026-10-19）

この節は現行契約であり、過去のU05～U23に残る開始拒否・承認・worktree・終端結合の説明は旧実装の履歴として扱う。本人一名のローカルシステムで堅牢性を目的に制限を足さない。唯一の作業場所は `C:/project/rakuten-room-inago`。全担当のreasoning effortはhighとし、固定max roleしか選べないsessionでは同じ専門promptをhigh指定可能なworker/defaultで実起動する。指定値と実観測値を区別し、model/tier/receipt/session等の未観測を起動条件にしない。

各stageは内容ある通常成果またはcontroller fallback成果と有効なhandoffを保存して次担当を実起動する。将来のstage結果にblockedを返さず、別名の状態・承認・receipt・lease・回数上限に置き換えたgateも作らない。review/test/readiness/release、FAIL、unknown、証拠不足、空/不正なAI応答、過去raw blockedを後続開始条件にしない。個別操作ができない場合はその操作だけ `not_performed` と具体理由を記し、他の作業と最終報告・公開を続ける。過去raw履歴と元の日次結果は改変しない。

不変のcontroller入力identityを保持し、欠測はnullとmissing_reasonを残す。source_run_idやtarget_dateを推測しない。対象が識別できる限り部分成果を出す。実装操作を省略できるのは原因修正と有用な計装の双方ができない具体的理由を確認した場合だけであり、既知の保守trace不足は今回実装する。差分0でもtesterは成果物整合を実検査し、製品reviewerは対象差分なしを実確認してapprovedを捏造しない。

handoffはidentity、schema_version、invocation/stage/attempt/operationとparent_operation、入力版/参照、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs、artifact_origin（agent/controller_fallback）、実行担当、review対象版、開始/終了/観測時刻、code revisionを持つ。本文は「何を行い、何が分かり、何が不明で、何を次に行うか」を含む。新attempt IDはcontrollerの正式採番値だけを使う。

工程の引継ぎ完了、操作の成否、試験/review/反映/公開結果、workflow_completion、incident_resolutionを別々に保存する。jobのフロー完遂からincident resolvedを自動推論しない。原因/業務回復unknownのまま同一jobのフロー完遂は可能だが、公開・必須成果永続登録・終端read-backが揃わなければ完遂としない。公開未達でもHTML、原因と再開位置を保存し、同じjobの公開・終端整合操作を続ける。

各失敗は操作名、例外classと安全化したcause chain/stack frames、例外code/reason_code連鎖、発生/捕捉位置、実行版、相関ID、入力/成果物参照へ結ぶ。必要なoperation contextはID・件数・hash・相対pathだけを許し、自由文を保存しない。例外本文、SQL/parameters、商品/投稿、token/cookie、秘密/個人情報をtraceへ出さない。通常loggerの失敗は独立fallbackへ保存して本処理を続け、復帰時に同じ相関値で追記整合する。全保存先喪失の未記録区間は復帰時にunknownと明記する。

reporterと独立report reviewerを実起動し、指摘はreporterが修正する。通常生成失敗時も平易な最小HTMLをcontrollerが生成し、その実在版をreview・公開へ渡す。本文は中学生が理解できる文章にし、内部ID/STEP/status/reason/SHAは参考付録へ置く。テストコードの独立reviewは行わず、製品コードの独立reviewを実施する。

今回対象は10/17の同一jobとlaunch、source_run_id=nullを保持する。10/19 queue/history処理、通常Daily手動起動、新job代替、日次run捏造、下書き追生成、別workspace/worktreeは禁止。Gitはcontrollerが所有file/hunkだけを確認・選択stage・commit/pushする。

## FC06 continuation, review and publication

通常HTMLを作れない時も平易な短いHTMLをcontrollerが作り、同じ正式job/attempt identity、入力参照、HTML hash、review対象版、result sidecarへ保存する。HTML本文は日常語の5項目で書き、内部ID/status/reason/hash/版は参考付録だけに置く。独立reviewerを実起動し、指摘はreporterが直して新しい版をレビューへ渡す。review不能時はstatus=unavailableと理由を保存し、approvedを捏造しない。その状態でも公開処理を試みる。target/identity不一致、push、sidecar保存などの公開操作失敗は具体的reason、例外連鎖、捕捉位置、相関ID、入力/成果物参照をfailure journalとhandoffへ保存し、元のreason付きunknown/failedで終端照合を続ける。公開後の保存が中断した場合は、同じjobを再開した時にremoteのreport pathとhashを読み直してpublication sidecarを復元する。report公開成功はincident解決の根拠ではない。元のcause/recoveryがunknownならincidentは未解決のままにする。公開commitは一時Git indexでHTML一ファイルだけから構成し、作業treeや既存indexを変更しない。別worktree/checkoutは作らない。

各事象の本文は、区分・期待・実際・影響・回復・変更・試験分類/範囲・根拠参照をそろえる。確認済み事実と不明を、実際に確認した資料の範囲に沿って分ける。「不明はない」「review指摘なし」は、根拠がある場合だけ記載する。review/publicationはHTML作成時点と後続の記録を区別し、workflow完了とincident解決も別々に記載する。合成例は最初の本文より前に見える位置で合成例と明記し、例のreview/publication状態を実行結果と混同させない。本文には、確認に使った資料の読める種類名と固有のリンク先一覧を表示し、根拠本文を折りたたみ内へ隠さない。リンク後に根拠本文が読める状態にする。

### FC10 host-owned invocationと公開証拠

`invocation_id`はcontrollerが採番し、handoff payloadに同じ値を設定する。reporter/reviewerはこの値を生成せず、入力された正式identityと報告対象の版を維持する。bodyは平易な日本語で何が起き、何を確認し、何を変え、何が不明で、次に何をするかを説明する。内部IDやstatus等は付録に置く。reviewerは実際の公開HTML本文だけでも説明が成り立つか読み、用語で平易さを代用させない。公開版のhash/read-backとDB終端登録は別操作の成果であり、reportの存在だけを完遂根拠にしない。
