---
name: rr-inago-incident-report
description: 利用可能な調査・計画・試験・反映記録から、平易で根拠付きの障害対応HTMLを作る。未確認事項を成功と表現しない。
---

# イナゴ障害対応HTML

## 使う場面

management stage=reportで、利用可能な調査・計画・試験・反映記録を入力として使用する。各stageのapproved状態は開始条件にしない。

対象jobが特定できればreport担当を直ちに起動する。調査・計画・試験・reviewの全入力やstatusが揃うまで待たず、利用可能な内容でHTMLを作る。報告書のreview対象HTMLが存在すればreport reviewerも必ず起動し、runtime観測やreceiptを理由に省略しない。

## 作成

- docs/templates/incidents/日次不具合対応レポート_ひな形.htmlの構造・文体・安全な記載ルールを守る。
- 本文は、発生した不具合、原因（不明なら不明）、実際に行った修正、追加したトレース・ログと記録内容を平易に伝える。
- その他の工程状態や管理情報は、上記4点を説明するために必要な範囲にとどめる。内部schemaが要求する補助項目は機械互換のため保持してよいが、本文の中心にしたりreview要件にしたりしない。
- 報告書作成担当は報告内容の所有者である。reviewerの指摘が入力されたら、根拠で確認できる範囲でreport responseの内容を修正し、hostに再描画させる。hostがHTMLを描画・保存する契約は、担当が内容を直せないという意味ではない。
- rendererが出力するHTMLの内部アンカーは壊さず、秘密・個人情報・生ログを公開本文へ複製しない。根拠資料の表示名や管理リンク一覧の詳しさは本文の必須要件にしない。
- 入力記録に基づく原因・修正・計装の説明で未確認事項を成功と表現しない。管理工程の状態は機械記録へ保持し、本文には4点の理解に役立つ場合だけ記載する。
- `report_status`は元資料の完了状態、`stage_status`と`review.status`はHTML文章レビューの状態として独立に保つ。文章レビューがapprovedでも調査・試験・反映が未完了なら`report_status=pending`を維持し、この組合せを矛盾として扱わない。
- rendererは本文の状態コード、reason code、内部版・ID・hashを参考付録へ寄せる。試験未実施や本番未反映には、状態と記録の有無を日常語で説明する。
- 工程表の版対応、資料索引の表示名、発生区分ラベルはreviewerから求められない。本文の4点を誤解させないために必要な場合だけ簡潔に補う。
- 記録上のエラーだけでは、基となる操作が実際に失敗したと断定しない。本文では `source snapshot` や `report_input` のような内部用語を使わず、「当日の記録」「調査資料」などに言い換える。
- 未確認、保留、欠測を明示し、本文・商品名・個人情報・秘密・生データを公開出力へ複製しない。
- 根本原因がunknownでも報告を中断しない。trace/log追加だけを行った場合は、実施した計装が記録する内容を説明し、元の原因修正が未確認であることを混同しない。計装を根本修正や原因判明として表現しない。
- U23.08 safe diagnosticのSQLite `error_name`/`constraint_label`はエラー分類、`failure_module`/`failure_function`/`failure_line`/`code_sha256`はpipeline内の捕捉位置を表す。callbackの根本原因や発生行を証明する値として説明しない。提案run IDと永続run ID、trace requested/persistedも別に記す。raw exception、SQL、params、秘密、個人情報を本文へ複写しない。

## HTML独立レビューの限定範囲（2026-10-20 本人指示）

reporterは本文を次の4点が伝わる内容にまとめます。①どのような不具合が発生したか、②原因（不明なら不明）、③実施した修正、④追加したトレース・ログと記録できる内容です。原因が不明な場合も、推測せず、実施した修正・計装と未解決事項を正確に書きます。

独立reviewerは、この4点の欠落・事実誤認・根拠のない断定・理解を妨げる文章だけを内容上の指摘にします。秘密・個人情報の露出も確認します。それ以外のHTML要素はレビュー対象にしません。

発生区分ラベル、合成例ラベル、資料一覧の名前や管理資料へのリンク、全工程の版対応表、工程statusの言い換え、調査review・報告review・公開・終端間の管理情報の対応は、4点の理解に必要でない限り指摘してはいけません。試験PASSは前提であり、「試験で確認」等の区分を本文へ追加する必要はありません。HTMLが任意で試験結果を述べる場合に限り、虚偽の合格説明がないか確認します。テンプレートはこの指示を理由に変更しません。

## 禁止と停止

- 元の日次結果、投稿、元証拠、計画、review、実装、設定、DBを変更しない。
- fixture成功を本番成功と表現しない。反映、再発防止、次回観測を推測で完了にしない。
調査/plan/review版の不一致、根拠不足、FAIL、未反映はHTMLに未確認/未完了として記録する。review statusやfindingsを公開・後続処理のgateにしない。作成可能なHTMLを保存・公開する。

## 参照契約

入力版、HTML章、公開範囲、reviewと再開の詳細は[contracts.md](references/contracts.md)を参照する。

## FC02 現行の全工程引継ぎ契約（2026-10-19）

この節は現行契約であり、過去のU05～U23に残る開始拒否・承認・worktree・終端結合の説明は旧実装の履歴として扱う。本人一名のローカルシステムで堅牢性を目的に制限を足さない。唯一の作業場所は `C:/project/rakuten-room-inago`。全担当のreasoning effortはhighとし、固定max roleしか選べないsessionでは同じ専門promptをhigh指定可能なworker/defaultで実起動する。指定値と実観測値を区別し、model/tier/receipt/session等の未観測を起動条件にしない。

各stageは内容ある通常成果またはcontroller fallback成果と有効なhandoffを保存して次担当を実起動する。将来のstage結果にblockedを返さず、別名の状態・承認・receipt・lease・回数上限に置き換えたgateも作らない。review/test/readiness/release、FAIL、unknown、証拠不足、空/不正なAI応答、過去raw blockedを後続開始条件にしない。個別操作ができない場合はその操作だけ `not_performed` と具体理由を記し、他の作業と最終報告・公開を続ける。過去raw履歴と元の日次結果は改変しない。

不変のcontroller入力identityを保持し、欠測はnullとmissing_reasonを残す。source_run_idやtarget_dateを推測しない。対象が識別できる限り部分成果を出す。実装操作を省略できるのは原因修正と有用な計装の双方ができない具体的理由を確認した場合だけであり、既知の保守trace不足は今回実装する。差分0でもtesterは成果物整合を実検査し、製品reviewerは対象差分なしを実確認してapprovedを捏造しない。

handoffはidentity、schema_version、invocation/stage/attempt/operationとparent_operation、入力版/参照、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs、artifact_origin（agent/controller_fallback）、実行担当、review対象版、開始/終了/観測時刻、code revisionを持つ。本文は「何を行い、何が分かり、何が不明で、何を次に行うか」を含む。新attempt IDはcontrollerの正式採番値だけを使う。

工程の引継ぎ完了、操作の成否、試験/review/反映/公開結果、workflow_completion、incident_resolutionを別々に保存する。jobのフロー完遂からincident resolvedを自動推論しない。原因/業務回復unknownのまま同一jobのフロー完遂は可能だが、公開・必須成果永続登録・終端read-backが揃わなければ完遂としない。公開未達でもHTML、原因と再開位置を保存し、同じjobの公開・終端整合操作を続ける。

各失敗は操作名、例外classと安全化したcause chain/stack frames、例外code/reason_code連鎖、発生/捕捉位置、実行版、相関ID、入力/成果物参照へ結ぶ。必要なoperation contextはID・件数・hash・相対pathのみに限定し、自由文を含めない。例外本文、SQL/parameters、商品/投稿、token/cookie、秘密/個人情報をtraceへ出さない。通常loggerの失敗は独立fallbackへ保存して本処理を続け、復帰時に同じ相関値で追記整合する。全保存先喪失の未記録区間は復帰時にunknownと明記する。

reporterと独立report reviewerを実起動し、指摘はreporterが修正する。通常生成失敗時も平易な最小HTMLをcontrollerが生成し、その実在版をreview・公開へ渡す。本文は中学生が理解できる文章にし、内部ID/STEP/status/reason/SHAは参考付録へ置く。テストコードの独立reviewは行わず、製品コードの独立reviewを実施する。

今回対象は10/17の同一jobとlaunch、source_run_id=nullを保持する。10/19 queue/history処理、通常Daily手動起動、新job代替、日次run捏造、下書き追生成、別workspace/worktreeは禁止。Gitはcontrollerが所有file/hunkだけを確認・選択stage・commit/pushする。

## FC06 report continuation

本文は中学生にも分かる短い日本語で、不具合、原因、実施した修正、追加したトレース・ログと記録内容を説明する。内部ID、STEP、状態コード、reason_code、SHA、版キーは参考付録に置く。通常reporterが失敗してもcontrollerが最小HTMLを作り、同じreport attemptへinput/output/reviewを版とhash付きで保存する。独立report reviewerを実起動し、4点の範囲内の指摘を反映した版を再reviewする。review不能はunavailableとして残し、承認を作らず公開を試す。公開結果とworkflow完了、incident解決は別の機械記録に残す。
本文で必ず伝える内容は、発生した不具合、原因（不明なら不明）、実際に行った修正、追加したトレース・ログと記録内容です。発生区分や管理工程の表示、資料一覧の詳しさ、工程版対応表、review/publicationと終端の管理記録の説明は、この4点を正しく伝えるために必要な場合を除いて本文・reviewの要件にしません。reportと独立reviewerは指定範囲だけを対象にし、範囲外の管理詳細を理由に差し戻しません。

入力記録のreview・反映等の管理状態は機械記録に正確に保持します。本文への表示、資料リンクの一覧、版対応表は、4点の理解に必要な場合だけにします。停止理由がnull/欠測なら「記録がないため未確認」とし、「理由なし」と断定しません。本文に内部status、reason code、版キー、ID、SHAを出さず、詳しい工程treeと識別値は参考付録へ置きます。


HTML本文の独立レビューは4点の内容確認に絞る。reviewerはその4点と、秘密・個人情報の露出だけを確認する。それ以外のHTML要素はレビュー対象にしない。
