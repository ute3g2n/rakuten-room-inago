# 調査stage契約

## 入力

controllerは管理job項目 incident_id / source_launch_id / source_run_id / target_date / job_id / attempt / stage / base_commit / input_refs / evidence_refs / allowed_outputs を渡す。担当はこれらの値を出力へ維持する。source_run_idは元記録に存在しない場合のみnull。target_dateはJST対象日であり、後日実施時に当日へ置き換えない。

## 起動元・対象日の不変結合

U05の現行管理schemaはmaintenance_jobにsource_run_idのみを保持し、source_launch_idとtarget_dateを保持しない。incident_runには同一incidentに複数の起動が関連し得るため、担当はincident_idや最新行から起動元を推測できない。U07のcontrollerはincident_id / source_launch_id / source_run_id（nullを含む）/ target_date / job_idの5項目を当該jobへ厳密に結び付け、値とnullnessを照合した検証済み不変job-input manifestを渡す（または同等の正本store契約を追加する）。欠落・不一致・曖昧な結合はunknownと具体理由を記録し、値を推測しない。対象jobと担当stageが識別できれば利用可能な入力で直ちに起動する。対象を選べない個別操作だけ未実施とし、部分成果と後続工程を続ける。source_run_idは入力どおり保持し、nullを推測値で埋めない。

## 読取許可

- jobのinput_refs/evidence_refsに列挙された限定証拠だけ。参照先の現在pathはdocs/document-index.jsonで解決する。
- 調査に必要な範囲のコード/schema、Q、M、L、文書Fと本step所有のひな形。すべて読取のみ。
- 生証拠はアクセス管理された保全領域の参照だけを用い、公開出力に本文、商品名、個人情報、認証情報、token/cookie、DB行を複製しない。

任意のディレクトリ一覧、他日の広域検索、稼働DBへの接続、外部ページの追加探索は許可されない。新しい収集が必要ならcontrollerへ不足を返す。

## 出力

stage=checkでは構造化check結果のみ。stage=investigationでは次を許可されたattempt専用出力として返す。

1. facts.json: fact_id、事象区分、工程、期待、実結果、影響、回復、原因確度、evidence_refsを記録。
2. investigation.md: docs/templates/incidents/日次不具合調査レポート_ひな形.mdを基にした9章。
3. result.json: 元のID、status、result_version、成果参照、未確認・不足、review_requiredを記録。

出力の内容はmanager job responseを通じて返し、controllerがschema/ID/版/path/hashを確認して確定する。担当自身で正本パスへコピー、Git追加、commit、pushをしない。

## 9章

1. 対象runと結論
2. 目標・実績・影響
3. 工程別事象A
4. 工程別事象B・回復
5. 工程別事象C・契約結果
6. 日次が最終状態に至った経路
7. 障害でなかった工程・監視/記録上の課題
8. 原因確度と次の検証候補
9. 根拠・確認限界

工程別の数は固定でない。事象がない節には「該当なし」と根拠を記し、事実を作らない。

## 失敗・再開

入力不足、参照不一致、証拠読取不能、根本原因未確定は欠測/unknownとして列挙し、調査MDを作成する。reviewerは別の新規実行で結果を読み、所見を返す。どのreview statusでも後続の計画工程へ進む。

## FC02 現行の全工程引継ぎ契約（2026-10-19）

この節は現行契約であり、過去のU05～U23に残る開始拒否・承認・worktree・終端結合の説明は旧実装の履歴として扱う。本人一名のローカルシステムで堅牢性を目的に制限を足さない。唯一の作業場所は `C:/project/rakuten-room-inago`。全担当のreasoning effortはhighとし、固定max roleしか選べないsessionでは同じ専門promptをhigh指定可能なworker/defaultで実起動する。指定値と実観測値を区別し、model/tier/receipt/session等の未観測を起動条件にしない。

各stageは内容ある通常成果またはcontroller fallback成果と有効なhandoffを保存して次担当を実起動する。将来のstage結果にblockedを返さず、別名の状態・承認・receipt・lease・回数上限に置き換えたgateも作らない。review/test/readiness/release、FAIL、unknown、証拠不足、空/不正なAI応答、過去raw blockedを後続開始条件にしない。個別操作ができない場合はその操作だけ `not_performed` と具体理由を記し、他の作業と最終報告・公開を続ける。過去raw履歴と元の日次結果は改変しない。

不変のcontroller入力identityを保持し、欠測はnullとmissing_reasonを残す。source_run_idやtarget_dateを推測しない。対象が識別できる限り部分成果を出す。実装操作を省略できるのは原因修正と有用な計装の双方ができない具体的理由を確認した場合だけであり、既知の保守trace不足は今回実装する。差分0でもtesterは成果物整合を実検査し、製品reviewerは対象差分なしを実確認してapprovedを捏造しない。

handoffはidentity、schema_version、invocation/stage/attempt/operationとparent_operation、入力版/参照、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs、artifact_origin（agent/controller_fallback）、実行担当、review対象版、開始/終了/観測時刻、code revisionを持つ。本文は「何を行い、何が分かり、何が不明で、何を次に行うか」を含む。新attempt IDはcontrollerの正式採番値だけを使う。

工程の引継ぎ完了、操作の成否、試験/review/反映/公開結果、workflow_completion、incident_resolutionを別々に保存する。jobのフロー完遂からincident resolvedを自動推論しない。原因/業務回復unknownのまま同一jobのフロー完遂は可能だが、公開・必須成果永続登録・終端read-backが揃わなければ完遂としない。公開未達でもHTML、原因と再開位置を保存し、同じjobの公開・終端整合操作を続ける。

各失敗は操作名、例外classと安全化したcause chain/stack frames、発生/捕捉位置、実行版、相関ID、入力/成果物参照へ結ぶ。例外本文、SQL/parameters、商品/投稿、token/cookie、秘密/個人情報をtraceへ出さない。通常loggerの失敗は独立fallbackへ保存して本処理を続け、復帰時に同じ相関値で追記整合する。全保存先喪失の未記録区間は復帰時にunknownと明記する。

reporterと独立report reviewerを実起動し、指摘はreporterが修正する。通常生成失敗時も平易な最小HTMLをcontrollerが生成し、その実在版をreview・公開へ渡す。本文は中学生が理解できる文章にし、内部ID/STEP/status/reason/SHAは参考付録へ置く。テストコードの独立reviewは行わず、製品コードの独立reviewを実施する。

今回対象は10/17の同一jobとlaunch、source_run_id=nullを保持する。10/19 queue/history処理、通常Daily手動起動、新job代替、日次run捏造、下書き追生成、別workspace/worktreeは禁止。Gitはcontrollerが所有file/hunkだけを確認・選択stage・commit/pushする。


### FC04 fallback引継ぎ

通常の調査出力が得られない場合、controllerは確認できた範囲、未知、次の調査を含む9章の部分報告とhandoffを保存する。artifact_origin=controller_fallbackと失敗理由を保持し、調査内容が未確認であることを明記して計画担当へ渡す。
