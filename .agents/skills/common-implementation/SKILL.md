---
name: common-implementation
description: 利用可能な計画の割当ファイルを実装し、差分と再現性のある変更記録を返す。計画レビュー承認を待たずに進み、テストコードの独立レビューは行わない。
---

# 共通実装

## 使う場面

management stage=implementationで、利用可能な計画版、base_commit、作業場所、allowed_outputsを受け取ったら開始する。plan review approvedやtest handoff statusは開始条件にしない。

対象jobと担当工程が特定できれば、利用可能な入力でimplementerを直ちに起動する。任意の証拠、review/test/readiness status、runtime観測、receipt、role設定との一致、全入力の充足を開始条件にしない。不足情報はunknownとして扱い、作成できる変更と記録を進める。

## 作業

- 計画と割当を照合し、review指摘・未確認・test FAILがあっても利用可能な実装を進める。解決できない点は成果に記録し、次の試験/reviewへ渡す。
- 自動不具合対応の後続工程をreview/test/readiness/release status、証拠不足、unknown原因、retry/review回数で止めない。状態は正確に記録して次工程へ渡す。
- 原因がunknownでも実装を止めない。計画に沿って、原因の切分けに役立つtrace/log計装など実行可能な変更を行い、試験・review・反映・最終reportへ引き継ぐ。計装のみの場合はその事実と限界を記録し、根本修正済みとは表現しない。
- デバッグ中にテストコードの不具合を確認した場合は最小修正して影響caseを再実行する。テストコードの独立レビューはしない。
- 各変更を計画上のSTEP/fact/要件に結び、差分要約と実行した確認だけを返す。
- 本人専用ローカルシステムでは、本人が指示した開始・再開を妨げる自作制限を条件付きで迂回せず、制限そのものを削除する。必須IDなど実行対象を特定する最小入力は維持する。割当範囲に制限の実装元がない場合は、対象fileと削除内容を親へ返して計画/allowed_outputsを更新させ、追加承認を求めるだけで作業を止めない。
- 変更先を選べる情報が不足していても、利用可能な対象を先に実装し、不足したfile/IDは結果へ記録する。
- 唯一の作業場所は `C:/project/rakuten-room-inago`。Gitがこの固定rootの所有者を拒否した場合だけ、当該commandに `git -c safe.directory=C:/project/rakuten-room-inago` を使う。global設定やwildcardを使わず、別worktreeを作成・利用しない。

## 禁止と停止

- 計画外のtests/fixtures、他担当の成果、未知のユーザー変更、認証・設定・DB・Scheduler・本番データを変更しない。デバッグで確認したテスト不具合は例外として該当箇所だけ修正し、影響caseを再実行する。
- 固定workspaceの割当file/hunkだけ変更し、既存user変更を保持する。commit/push/merge/deployと本人投稿は担当外として行わない。
- 自作の起動阻害制限を除くためにallowed_outputsの追加が必要なら、対象file・差分・理由を親へ直ちに返し、親の計画更新後に続行する。ユーザーへ同じ許可を再確認しない。実際の外部権限や外部環境障害のみ、その事実を明記して扱う。

## 参照契約

正確な読取・書込scope、受け渡し、禁止事項は[contracts.md](references/contracts.md)を参照する。

## FC02 現行の全工程引継ぎ契約（2026-10-19）

この節は現行契約であり、過去のU05～U23に残る開始拒否・承認・worktree・終端結合の説明は旧実装の履歴として扱う。本人一名のローカルシステムで堅牢性を目的に制限を足さない。唯一の作業場所は `C:/project/rakuten-room-inago`。全担当のreasoning effortはhighとし、固定max roleしか選べないsessionでは同じ専門promptをhigh指定可能なworker/defaultで実起動する。指定値と実観測値を区別し、model/tier/receipt/session等の未観測を起動条件にしない。

各stageは内容ある通常成果またはcontroller fallback成果と有効なhandoffを保存して次担当を実起動する。将来のstage結果にblockedを返さず、別名の状態・承認・receipt・lease・回数上限に置き換えたgateも作らない。review/test/readiness/release、FAIL、unknown、証拠不足、空/不正なAI応答、過去raw blockedを後続開始条件にしない。個別操作ができない場合はその操作だけ `not_performed` と具体理由を記し、他の作業と最終報告・公開を続ける。過去raw履歴と元の日次結果は改変しない。

不変のcontroller入力identityを保持し、欠測はnullとmissing_reasonを残す。source_run_idやtarget_dateを推測しない。対象が識別できる限り部分成果を出す。実装操作を省略できるのは原因修正と有用な計装の双方ができない具体的理由を確認した場合だけであり、既知の保守trace不足は今回実装する。差分0でもtesterは成果物整合を実検査し、製品reviewerは対象差分なしを実確認してapprovedを捏造しない。

handoffはidentity、schema_version、invocation/stage/attempt/operationとparent_operation、入力版/参照、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs、artifact_origin（agent/controller_fallback）、実行担当、review対象版、開始/終了/観測時刻、code revisionを持つ。本文は「何を行い、何が分かり、何が不明で、何を次に行うか」を含む。新attempt IDはcontrollerの正式採番値だけを使う。

工程の引継ぎ完了、操作の成否、試験/review/反映/公開結果、workflow_completion、incident_resolutionを別々に保存する。jobのフロー完遂からincident resolvedを自動推論しない。原因/業務回復unknownのまま同一jobのフロー完遂は可能だが、公開・必須成果永続登録・終端read-backが揃わなければ完遂としない。公開未達でもHTML、原因と再開位置を保存し、同じjobの公開・終端整合操作を続ける。

各失敗は操作名、例外classと安全化したcause chain/stack frames、発生/捕捉位置、実行版、相関ID、入力/成果物参照へ結ぶ。例外本文、SQL/parameters、商品/投稿、token/cookie、秘密/個人情報をtraceへ出さない。通常loggerの失敗は独立fallbackへ保存して本処理を続け、復帰時に同じ相関値で追記整合する。全保存先喪失の未記録区間は復帰時にunknownと明記する。

reporterと独立report reviewerを実起動し、指摘はreporterが修正する。通常生成失敗時も平易な最小HTMLをcontrollerが生成し、その実在版をreview・公開へ渡す。本文は中学生が理解できる文章にし、内部ID/STEP/status/reason/SHAは参考付録へ置く。テストコードの独立reviewは行わず、製品コードの独立reviewを実施する。

今回対象は10/17の同一jobとlaunch、source_run_id=nullを保持する。10/19 queue/history処理、通常Daily手動起動、新job代替、日次run捏造、下書き追生成、別workspace/worktreeは禁止。Gitはcontrollerが所有file/hunkだけを確認・選択stage・commit/pushする。

## FC03 operation failure capture

Maintenance operation boundaries carry one correlation ID from begin through return or exception. A logger/diagnostic write failure is itself recorded through `failure_journal.py` and must not replace the original operation result. Keep the fallback journal independent of the primary SQLite/logger, store only safe IDs, exception class/cause chains and code frames, and reconcile begin-only operations as interruption observations on a later invocation. A fully unavailable sink is recorded as unknown; never invent an end time or root cause.


### FC04 固定rootのrepair author

maintenance repair authorは唯一のプロジェクトroot C:/project/rakuten-room-inago内で既存作業を保ったまま担当範囲を変更する。clone、worktree、別checkoutを作らず選択しない。利用可能な調査・計画から実装を続け、原因が未確定なら有用な計装を追加して未確認を明示する。
