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
- 何が起きたか、期待と実結果、影響、回復、原因確度、実際に変わった動作、試験と反映の確認範囲を平易に書く。
- 当日事実、調査で判明した不足、試験で見つかったリスク、作業場所の合格、本番反映、次回日次確認を別々に示す。
- 報告書作成担当は報告内容の所有者である。reviewerの指摘が入力されたら、根拠で確認できる範囲でreport responseの内容を修正し、hostに再描画させる。hostがHTMLを描画・保存する契約は、担当が内容を直せないという意味ではない。
- 最終HTML内の`href="#..."`は、同じHTML内に同じ`id`を持つ要素がちょうど1つあることを確認する。事象一覧と根拠欄のリンク先を一致させる。
- 根拠として使った資料は、本文に読める種類名を表示し、本文から一意のアンカーへ移動できる一覧にする。元資料本文や内部の生ログを転載せず、技術参照pathは参考付録に置く。
- 入力記録の調査・計画・コードreview状態を個別に表示し、固定文言で承認を作らない。資料が未作成ならリンクを付けず、停止理由が入力にない場合は未確認と表示する。
- rendererは本文の状態コード、reason code、内部版・ID・hashを参考付録へ寄せる。試験未実施や本番未反映には、状態と記録の有無を日常語で説明する。
- 工程表の対象版欄を参考付録へ案内するときは、source別の試験・反映・翌日確認の版と、報告response内の各checkの工程名・版を同じ付録に載せる。
- 記録にエラーが残っていることだけを確認した場合、「当日発生」と断定せず「当日の記録にあるエラー」などと書く。本文では `source snapshot` や `report_input` のような内部用語を使わず、「当日の記録」「調査資料」などに言い換える。
- 根拠索引の見出しと参照行には、入力された資料名から資料の種類が分かる名称を使い、「確認に使った資料一覧」のように一覧名だけで済ませない。狭幅画面で表を横スクロールさせても、印刷時は通常の表表示と横あふれなしへ戻す。
- 未確認、保留、欠測を明示し、本文・商品名・個人情報・秘密・生データを公開出力へ複製しない。
- 根本原因がunknownでも報告を中断しない。trace/log追加だけを行った場合も、確認できた成果、原因未特定の事実、追加計装の目的、試験/レビュー/反映状況を記載してreport作成/公開まで続ける。計装を根本修正や原因判明として表現しない。
- U23.08 safe diagnosticのSQLite `error_name`/`constraint_label`はエラー分類、`failure_module`/`failure_function`/`failure_line`/`code_sha256`はpipeline内の捕捉位置を表す。callbackの根本原因や発生行を証明する値として説明しない。提案run IDと永続run ID、trace requested/persistedも別に記す。raw exception、SQL、params、秘密、個人情報を本文へ複写しない。

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

本文は中学生にも分かる短い日本語で、「何が起きた」「何を調べた」「何を変えた」「何が不明」「次にどうする」を説明する。内部ID、STEP、状態コード、reason_code、SHA、版キーは参考付録に置く。通常reporterが失敗してもcontrollerが最小HTMLを作り、同じreport attemptへinput/output/reviewを版とhash付きで保存する。独立report reviewerを実起動し、指摘を反映した版を再reviewする。review不能はunavailableとして残し、承認を作らず公開を試す。公開結果とworkflow完了、incident解決を分ける。
各事象には区分・期待・実際・影響・回復・変更・試験範囲・根拠を含めます。known factsとunknownsを確認した資料の範囲に結び付け、裏付けのない「不明点なし」「指摘なし」を書きません。合成例は最初の本文より前に見える合成表示を出し、review/publicationの想定状況を実結果と区別します。根拠リンクの遷移先は折りたたまず、遷移後に本文が読める状態にします。review・公開・workflow終端・元障害解決は別状態として示します。

入力記録が示すreview状態は工程ごとにそのまま日本語へ置き換えます。未作成資料は資料一覧へリンクせず、リンク名はファイル形式と資料の役割に合わせます。停止理由がnull/欠測なら「記録がないため未確認」とし、「理由なし」と断定しません。本文に内部status、reason code、版キー、ID、SHAを出さず、詳しい工程treeと識別値は参考付録へ置きます。


### FC10 本文フィールド名と印刷ツリー
本文に`source_run_id`などの内部フィールド名・null値を出さず、日本語に続く表記も避ける。nullは「日次の実行記録との紐づけはありません」など平易に説明し、正確値は付録に置く。印刷時は`.tree`も折り返して横あふれをなくす。
