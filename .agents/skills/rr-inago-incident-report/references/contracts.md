# report stage契約

## 入力

job identity、target_date、利用可能なinvestigation version、plan version（approval状態は観測値）、

## 起動元・対象日の不変結合

現行controllerは既知のjob IDへsource_launch_id / source_run_id（nullを含む）/ target_dateを結び付ける。report対象jobが識別できれば、他のfield・任意manifest・証拠の欠落や不一致だけを理由にreport stageを止めない。得られた値と不足・不一致を報告へ記録する。source_run_idは入力どおり保持し、nullを推測値で埋めない。job自体を特定できない操作だけを未実施として理由を記録し、他の利用可能な報告を続ける。

## 読取と書込

- 読取: 対象jobに紐付く利用可能な資料、plan、C/LC実測、レビュー、反映・復旧結果、証拠参照、報告ルールG、既存HTML構造H。approved statusは入力状態として記録し、report開始条件にしない。
- 書込: incident HTMLを最終保存先へ直接作成し、割当済みjobのreport記録へ本文と結果を保存する。別公開workerやcontrollerによる本文合成を必須にせず、hash/bytes/version照合を行わない。Git stage/commit/pushはこの保守フローでは行わず、本人へ引き継ぐ。
- 外部向けHTMLは公開可能な要約だけとし、ログ/DB/商品/投稿/個人/secretの原文を含めない。

## 内容確認

- 案件・対象日・作成時刻・実行版を区別する。
- 報告本文の作成時刻はJSTで検証する。独立レビューの `reviewed_at` はISO-8601のtimezone-aware値を受け付けてJSTへ正規化し、timezoneがない値は受け付けない。
- HTML本文では、発生した不具合、原因（不明なら不明）、実施した修正、追加したトレース・ログと記録内容を分かる文章で示す。これらの事実に必要な背景だけを加える。
- 試験・review・反映等の正確な機械状態はcontroller記録で保持する。本文に発生区分・試験分類ラベルや管理stage一覧を必須にしない。任意に書いたPASS/反映結果は事実と一致させる。
- HTML本文は4点を平易な文章で読める形にする。管理用一覧や索引を本文に網羅する必要はない。
- 内部fragment link（`href="#..."`）は最終HTML内の一致する`id`へ解決し、リンク先は一意でなければならない。
- 工程・版の対応表は内部のreport result/source snapshotに保持し、HTML本文のレビュー要件にしない。

## 出力・レビュー・再開

reporterはincident HTMLを最終保存先へ直接作成する。別のrr-inago-incident-report-reviewerは、発生した不具合、原因（不明を含む）、実施した修正、追加したトレース・ログと記録内容の4点、秘密・個人情報を読取確認する。指摘はこの範囲の欠落・事実誤認・根拠のない断定・理解困難な表現に限り、reporterは本文を修正できる。再reviewを行う場合も回数・statusを後続処理の条件にしない。担当の実turnとprocess終了結果を記録する。保存済みresponseやHTMLのhash/bytes/version照合を開始・完了条件にしない。

入力/リンク/版が欠けても、解決できる範囲でHTMLを作成し、欠測と未完了を明記する。review statusや不足を後続stageのgateにしない。公開操作が実行できなくてもHTML作成とreport保存を続け、公開不能の理由をreportへ記録する。

report source snapshotに`workflow_trace`がある場合、それは同じjob・launch・source run（nullを含む）・invocationのoperation eventだけを含む。通常の`handoff`だけは失敗として数えず、失敗理由・例外・`failed`等の明示的失敗phase・中断記録を集計する。`recorded`は今回読めた記録の存在と内容を示すが、元のevent記録機能を今回追加した証拠にはならない。今回の改修で確認できる追加機能は、同じinvocationの記録をreport入力へ読み渡す処理である。`not_found`/`unavailable`では記録を確認できないことを伝える。operation failureと元の日次不具合の原因を混同せず、内部識別子・reason code・path・hashを本文へ出さない。trace取得失敗はreport作成を止めない。

## FC02 現行の全工程引継ぎ契約（2026-10-19）

この節は現行契約であり、過去のU05～U23に残る開始拒否・承認・worktree・終端結合の説明は旧実装の履歴として扱う。本人一名のローカルシステムで堅牢性を目的に制限を足さない。唯一の作業場所は `C:/project/rakuten-room-inago`。全担当のreasoning effortはhighとし、固定max roleしか選べないsessionでは同じ専門promptをhigh指定可能なworker/defaultで実起動する。指定値と実観測値を区別し、model/tier/receipt/session等の未観測を起動条件にしない。

各stageは内容ある通常成果またはcontroller fallback成果と有効なhandoffを保存して次担当を実起動する。将来のstage結果にblockedを返さず、別名の状態・承認・receipt・lease・回数上限に置き換えたgateも作らない。review/test/readiness/release、FAIL、unknown、証拠不足、空/不正なAI応答、過去raw blockedを後続開始条件にしない。個別操作ができない場合はその操作だけ `not_performed` と具体理由を記し、他の作業と最終報告・公開を続ける。過去raw履歴と元の日次結果は改変しない。

不変のcontroller入力identityを保持し、欠測はnullとmissing_reasonを残す。source_run_idやtarget_dateを推測しない。対象が識別できる限り部分成果を出す。実装操作を省略できるのは原因修正と有用な計装の双方ができない具体的理由を確認した場合だけであり、既知の保守trace不足は今回実装する。差分0でもtesterは成果物整合を実検査し、製品reviewerは対象差分なしを実確認してapprovedを捏造しない。

handoffはidentity、schema_version、invocation/stage/attempt/operationとparent_operation、入力版/参照、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs、artifact_origin（agent/controller_fallback）、実行担当、review対象版、開始/終了/観測時刻、code revisionを持つ。本文は「何を行い、何が分かり、何が不明で、何を次に行うか」を含む。新attempt IDはcontrollerの正式採番値だけを使う。

工程の引継ぎ完了、操作の成否、試験/review/反映/公開結果、workflow_completion、incident_resolutionを別々に保存する。jobのフロー完遂からincident resolvedを自動推論しない。原因/業務回復unknownのまま同一jobのフロー完遂は可能だが、公開・read-backの成否を後続担当の開始条件にしない。未実施や失敗は理由付きで記録し、可能な後続工程を続ける。

各失敗は操作名、例外classと安全化したcause chain/stack frames、例外code/reason_code連鎖、発生/捕捉位置、実行版、相関ID、入力/成果物参照へ結ぶ。必要なoperation contextはID・件数・相対pathに限定し、新しいhashを生成しない。例外本文、SQL/parameters、商品/投稿、token/cookie、秘密/個人情報をtraceへ出さない。通常loggerの失敗は独立fallbackへ保存して本処理を続け、復帰時に同じ相関値で追記整合する。全保存先喪失の未記録区間は復帰時にunknownと明記する。

reporterと独立report reviewerを実起動し、指摘はHTML作者が修正する。HTML作者は最終保存先へ直接HTMLを作成し、controller本文合成・公開専用worker・hash/bytes/version照合を必須にしない。本文は不具合、原因（不明なら不明）、修正、追加trace/logの4点を平易に説明する。テストコードの独立reviewは行わず、製品コードの独立reviewを実施する。

source_launch_id、source_run_id（nullを含む）、target_date、job_idは、このjobに渡された値をそのまま使い、推測や差替えをしない。通常Dailyの手動起動、別のlaunch/run/jobでの代用、別workspace/worktreeは禁止。Gitのstage/commit/pushは行わず、本人の作業として残す。

## FC06 continuation, review and publication

HTML作者は割当済みjobの最終保存先へHTMLを直接作成し、本文だけで4点を説明する。本文に必要な内容は不具合、原因、実施した修正、追加したトレース・ログと記録内容の4点とする。内部ID/status/reasonなどの管理値で本文の説明を代用しない。独立reviewerを実起動し、範囲内の指摘は作者が直して新しい版をレビューへ渡す。review不能は事実として残し、approvedを捏造しない。HTML保存に失敗した場合は具体的な操作結果と再開位置を通常記録へ残し、後続工程を続ける。report公開成功はincident解決の根拠ではない。元のcause/recoveryがunknownならincidentは未解決のままにする。Gitのstage/commit/pushは行わず、本人に引き継ぐ。別worktree/checkoutは作らない。

本文のレビューで必須なのは、発生した不具合、原因（不明なら不明）、実施した修正、追加したトレース・ログと記録内容の4点である。確認済み事実と不明を混同せず、根拠のない原因・修正を断定しない。reviewerは発生区分ラベル、合成例ラベル、管理用資料の表示名や個別リンク、工程版対応表、工程statusの言い換え、report review/publication/terminalの管理記録間の結合を、4点の理解に必要でない限り要求しない。「試験で確認」等の分類ラベルは要求せず、試験PASSは前提として扱う。レビュー不能や不実施を承認済みと偽らない。

### FC10 host-owned invocationと公開証拠

`invocation_id`はcontrollerが採番し、handoff payloadに同じ値を設定する。reporter/reviewerはこの値を生成せず、入力された正式identityと報告対象の版を維持する。bodyは平易な日本語で不具合、原因、実施した修正、追加したトレース・ログと記録内容を説明する。内部IDやstatus等は付録に置く。reportの作成、レビュー、反映、workflow終了、incident解決はそれぞれ実際の結果を記録する。

### FC10 rendererとsource statusの表示

調査・計画・review・試験・反映状態は機械記録へ個別に保存する。本文への表示や資料名・リンクの網羅は必須にせず、4点の理解に必要な場合だけ簡潔に補う。記録上のエラーだけなら実運用で起きたと断定せず、「当日の記録にあるエラー」などとする。本文に`source snapshot`などの内部用語を出さず、日常語に直す。本文にstatus/reason code、版キー、内部ID、hashが含まれる場合は技術付録へ移す。工程treeと生のsource参照は参考付録に置く。

`report_status`は元資料が示す調査・試験・反映の完了状態であり、`stage_status`と`review.status`はHTML文章レビューの状態である。各値を独立に保存・照合する。たとえば元資料がpendingでも文章レビューがapprovedなら、そのままpending/approvedの組合せを許し、レビュー承認で元資料の完了状態を昇格させない。レビュー不能の結果は、既存契約どおりcontroller fallback、pending、公開予定、専用reviewer IDと一致させる。


### HTML独立レビュー範囲の限定（2026-10-20 本人指示）

HTML独立reviewerが本文で評価する4点は、発生した不具合、原因（不明なら不明）、実施した修正、追加したトレース・ログとその記録内容である。内容上の指摘は、この4点の欠落、事実誤認、根拠のない断定、または通常のHTML表示で理解を妨げる表現に限る。秘密・個人情報の露出も確認する。それ以外のHTML要素はレビュー対象にしない。

本文の発生区分ラベル、合成例ラベル、資料一覧の名称・管理資料リンク、工程・版対応表、工程statusの表示、調査review・報告review・公開・終端の管理記録間の結合は、4点の理解に必要でない限り要求しない。特に「試験で確認」等の分類ラベルを本文に要求しない。試験PASSは前提とし、任意に記載された試験結果が虚偽でないことだけを確認する。

この限定はHTML文章reviewにだけ適用する。report publication・DB終端・同一job identity等の機械的な実行時検証は、既存のcontroller契約に従い別工程で行う。`docs/templates/incidents/日次不具合対応レポート_ひな形.html`は変更しない。
