---
name: common-execution-planning
description: 利用可能な障害調査と要件を実行可能な0〜14節計画、各STEP14項目、ケース12項目、全文promptへ変換する。調査結果やコードを改変しない。
---

# 障害対応計画

## SC07 現行保守フロー優先規則

構成案2.0と2026-10-21本人指示を優先します。通常の保守工程は7担当です。既存の明示された実行依頼について、計画review承認・hash/bytes/mtime/marker・release/readiness・Git操作を開始/完了条件にしません。運用上の失敗や原因unknownは実値として記録し、独立担当と後続工程を止めません。

## 使う場面

management stage=planningで使用する。入力は利用可能な調査result、管理job契約、現行要件・受入、コード版、利用可能なモデル/環境。review approvedやhandoff acceptedは開始条件にしない。不足や未確認はそのまま計画へ記録し、利用可能な内容で後続作業を組み立てる。
担当job/stageと不可欠な入力が分かる作業は、担当agentを前置確認で待たせず実際に起動する。review対象versionまたは成果物がある場合はreviewerを実起動する。model/effort/runtime観測、receipt、session/cache、任意証拠や前段statusを起動条件にせず、起動前に合成`unavailable`を作らない。指定設定が利用できない可能性がある場合も利用可能なdefault設定で実起動を試し、実環境が要求後に拒否した場合だけその結果を記録して独立工程とreportを続ける。テストコードの独立レビューは計画しない。

対象jobが特定でき、計画作成に不可欠な入力がある場合は直ちにplannerを起動する。任意の証拠、review/test/readiness status、runtime観測、receipt、完全な入力一覧、設定一致を起動条件にしない。不足情報はunknownとして書き、利用可能な事実で計画を作る。各後続stageの担当も同じ基準で起動する。

## 作成基準

- 計画テンプレートの0〜14節を埋める。各STEPは14項目をすべて持ち、最後に単独で実行できる全文promptを付ける。
- 各test caseは12項目を持つ。完全node、入力、時計、版、初期状態、期待、禁止副作用、証拠、再実行条件を明示する。テストコード自体は独立レビューしない。テストコードの独立レビューは計画の受入条件にしない。テストコードのバグが実行やデバッグ中に判明した場合は担当者が修正し、影響caseを再実行して版と証拠を更新する。
- 利用可能な調査のfact_idと根拠へ各作業を追跡する。原因が未確定でも、確認できる作業や安全な暫定対応を計画し、原因確定を後続工程の条件にしない。
- 入力、所有file、出力、reviewer、model/effort/tier、失敗、再開、Git、要件同期、release gateを明示する。

## 必ず守る本人方針（本人専用ローカルシステム）

- イナゴシステムは本人1名が本人のローカル環境でのみ使う。堅牢性は必要な品質目標にしない。
- 後続stageのgateを設けない。調査・計画reviewのapproved、証拠充足、原因確定を待たず、利用可能な入力で計画と実装へ進む。review findingsや不足情報は計画の未確定事項として引き継ぐ。
- 本人が明示した起動・再開や後続stageを、review/test/readiness/release status、証拠不足、原因unknown、FAIL、rejected/changes_requested/unavailable、retry/review回数で止めない。「絶対にどうしても必要」な後続gateは基本的にない前提とする。失敗・未確認を成功に変えず、次担当と最終報告へそのまま渡す。
- 対象/入力がなく実行できない個別操作だけ未実施と記録し、将来stageへblockedを返さず部分成果を作る。特に調査は、対象job/事象が特定できれば証拠や原因が不足していても直ちに開始し、確認済み事実・不足・仮説・unknownを分けて調査成果へ記録し、計画へ渡す。agent自身の`blocked`応答やreview指摘も停止理由にせず、その状態と理由を保持して次工程へ進む。
- 根本原因がunknownでも工程を終えない。原因不明を明記したまま、次回調査に役立つtrace/log追加など実行可能な対応を計画し、修正・試験・review・反映・最終reportまで続ける。計装だけを行った場合もその成果として記録し、原因特定や解決済みとは表現しない。
- 堅牢性・防御層の追加を品質目標や開始条件にしない。blocked・待機にして本人へ同じ許可を求める代わりに、計画と担当作業へ自作制限の根本除去を含める。
- 対象を選ぶための最小IDなど、個別操作を正しい対象へ結ぶ入力は維持できる。その入力がなく操作できない場合は、その操作だけを未実施として記録し、他の工程とreportを続ける。外部環境の実障害や本人が付与していない外部権限は自作制限と混同しない。
- この方針をこのリポジトリの今後すべての計画の「必ず守る項目」に明記し、計画・実装・試験・レビューを担当する全サブエージェントへの全文promptにも明示する。各subagentは自作制限が開始・再開を止める状態を見つけたら、許可済み範囲で根本制限を除去する。scope不足なら親へ対象fileと必要な差分を返し、親が計画範囲を更新した後に続行する。停止や追加承認要求で置き換えない。
## 禁止と停止

- コード、テスト、DB、設定、Scheduler、task、証拠原本を変更しない。
- 調査にない事実、過去日の結果、存在を確かめていないnodeや要件IDを作らない。
- 対象を選べるIDがない場合も、受け取った情報で部分計画を作り、不足を記載する。

## 参照契約

章構成、STEP14項目、ケース12項目、全文promptとレビュー契約は[contracts.md](references/contracts.md)を参照する。

## FC02 現行の全工程引継ぎ契約（2026-10-19）

この節は現行契約であり、過去のU05～U23に残る開始拒否・承認・worktree・終端結合の説明は旧実装の履歴として扱う。本人一名のローカルシステムで堅牢性を目的に制限を足さない。唯一の作業場所は `C:/project/rakuten-room-inago`。全担当のreasoning effortはhighとし、固定max roleしか選べないsessionでは同じ専門promptをhigh指定可能なworker/defaultで実起動する。指定値と実観測値を区別し、model/tier/receipt/session等の未観測を起動条件にしない。

各stageは内容ある通常成果またはcontroller fallback成果と有効なhandoffを保存して次担当を実起動する。将来のstage結果にblockedを返さず、別名の状態・承認・receipt・lease・回数上限に置き換えたgateも作らない。review/test/readiness/release、FAIL、unknown、証拠不足、空/不正なAI応答、過去raw blockedを後続開始条件にしない。個別操作ができない場合はその操作だけ `not_performed` と具体理由を記し、他の作業と最終報告・公開を続ける。過去raw履歴と元の日次結果は改変しない。

不変のcontroller入力identityを保持し、欠測はnullとmissing_reasonを残す。source_run_idやtarget_dateを推測しない。対象が識別できる限り部分成果を出す。実装操作を省略できるのは原因修正と有用な計装の双方ができない具体的理由を確認した場合だけであり、既知の保守trace不足は今回実装する。差分0でもtesterは成果物整合を実検査し、製品reviewerは対象差分なしを実確認してapprovedを捏造しない。

handoffはidentity、schema_version、invocation/stage/attempt/operationとparent_operation、入力版/参照、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs、artifact_origin（agent/controller_fallback）、実行担当、review対象版、開始/終了/観測時刻、code revisionを持つ。本文は「何を行い、何が分かり、何が不明で、何を次に行うか」を含む。新attempt IDはcontrollerの正式採番値だけを使う。

工程の引継ぎ完了、操作の成否、試験/review/反映/公開結果、workflow_completion、incident_resolutionを別々に保存する。jobのフロー完遂からincident resolvedを自動推論しない。原因/業務回復unknownのまま同一jobのフロー完遂は可能だが、公開・必須成果永続登録・終端read-backが揃わなければ完遂としない。公開未達でもHTML、原因と再開位置を保存し、同じjobの公開・終端整合操作を続ける。

各失敗は操作名、例外classと安全化したcause chain/stack frames、発生/捕捉位置、実行版、相関ID、入力/成果物参照へ結ぶ。例外本文、SQL/parameters、商品/投稿、token/cookie、秘密/個人情報をtraceへ出さない。通常loggerの失敗は独立fallbackへ保存して本処理を続け、復帰時に同じ相関値で追記整合する。全保存先喪失の未記録区間は復帰時にunknownと明記する。

reporterと独立report reviewerを実起動し、指摘はreporterが修正する。通常生成失敗時も平易な最小HTMLをcontrollerが生成し、その実在版をreview・公開へ渡す。本文は中学生が理解できる文章にし、内部ID/STEP/status/reason/SHAは参考付録へ置く。テストコードの独立reviewは行わず、製品コードの独立reviewを実施する。

今回対象は既存の2026-10-20 launchと正式受付済み同一jobで、source_run_id=nullを保つ。10/19 queue/history処理、通常Dailyの手動起動、新job代替、日次run捏造、下書き追生成、別workspace/worktreeは禁止。Git stage/commit/pushは本人の作業であり、この保守フロー担当は実行しない。


### FC04 部分入力からの計画

調査内容が不足しても、根拠のある修正または原因切り分けに役立つ計装の具体計画を作る。どちらも実行不能なら、変更しなかった理由を利用者へ説明する平易なHTMLの作成手順を出す。原因不明、review結果、FAILを後続開始条件にしない。
