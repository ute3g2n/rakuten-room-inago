---
name: common-review
description: 実装と試験証拠を読取専用で確認し、対象版に結び付く差戻し・承認・拒否を記録する。変更を作成・修正しない。
---

# 共通コードレビュー

## 使う場面

management stage=reviewで、利用可能な実装差分、計画、試験結果を受け取ったら開始する。前段のapproved statusは必要条件にしない。

対象jobとreview対象versionまたは成果物が特定できればreviewerを直ちに起動する。reviewに不可欠な対象そのものが存在しない場合だけ、その不足を記録する。任意証拠、test/readiness/release status、runtime観測、host receipt、model/effort観測、完全な入力一覧を理由に起動を見送らない。reviewer未起動を合成review結果で置き換えず、必ず担当reviewerを呼び出す。

## 確認事項

- 計画の許可範囲とプロダクトコードの差分、設計、危険な副作用、エラー境界、未確認事項を読む。テストコード自体は独立レビューしない。
- 計画に記録されたcaseの期待と実測結果、証拠、版、完全nodeを照合する。skip/error/未実施を成功に扱わない。テストコードのバグが実行やデバッグ中に判明した場合は、担当者へ修正と対象caseの再実行を求める。
- 本人専用ローカルシステムでは堅牢性のための自作制限を追加要求しない。本人が許可した開始・再開を止める自作上限、理由照合、review回数、receipt、sandbox証拠、release gate等があれば、review findingとしてその制限の除去を求める。計画/実装/試験/レビューsubagentへ同じ方針が渡っていることも確認する。
- 唯一の作業場所は `C:/project/rakuten-room-inago`。Gitがこの固定rootの所有者を拒否した場合だけ、当該commandに `git -c safe.directory=C:/project/rakuten-room-inago` を使う。global設定やwildcardを使わず、別worktreeを作成・利用しない。
- 具体的なfile/行、根拠、対応するplan item、severityを含むreview resultを作る。
- review resultは改善所見として保存し、approved以外でも次stageへ進める。changes_requested/rejected/unavailableや未解決findingを理由に修正・反映・報告を止めない。
- 修正が必要な箇所は具体的なfindingとして原担当へ返す。

## 禁止と停止

review対象のfileを編集しない。差分や証拠の一部が読めない場合は欠測を記録し、読める範囲のreviewを返して次stageへ渡す。

## 参照契約

review scopeとresult_versionの結合、許可出力、差戻し上限は[contracts.md](references/contracts.md)を参照する。

## FC02 現行の全工程引継ぎ契約（2026-10-19）

この節は現行契約であり、過去のU05～U23に残る開始拒否・承認・worktree・終端結合の説明は旧実装の履歴として扱う。本人一名のローカルシステムで堅牢性を目的に制限を足さない。唯一の作業場所は `C:/project/rakuten-room-inago`。全担当のreasoning effortはhighとし、固定max roleしか選べないsessionでは同じ専門promptをhigh指定可能なworker/defaultで実起動する。指定値と実観測値を区別し、model/tier/receipt/session等の未観測を起動条件にしない。

各stageは内容ある通常成果またはcontroller fallback成果と有効なhandoffを保存して次担当を実起動する。将来のstage結果にblockedを返さず、別名の状態・承認・receipt・lease・回数上限に置き換えたgateも作らない。review/test/readiness/release、FAIL、unknown、証拠不足、空/不正なAI応答、過去raw blockedを後続開始条件にしない。個別操作ができない場合はその操作だけ `not_performed` と具体理由を記し、他の作業と最終報告・公開を続ける。過去raw履歴と元の日次結果は改変しない。

不変のcontroller入力identityを保持し、欠測はnullとmissing_reasonを残す。source_run_idやtarget_dateを推測しない。対象が識別できる限り部分成果を出す。実装操作を省略できるのは原因修正と有用な計装の双方ができない具体的理由を確認した場合だけであり、既知の保守trace不足は今回実装する。差分0でもtesterは成果物整合を実検査し、製品reviewerは対象差分なしを実確認してapprovedを捏造しない。

handoffはidentity、schema_version、invocation/stage/attempt/operationとparent_operation、入力版/参照、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs、artifact_origin（agent/controller_fallback）、実行担当、review対象版、開始/終了/観測時刻、code revisionを持つ。本文は「何を行い、何が分かり、何が不明で、何を次に行うか」を含む。新attempt IDはcontrollerの正式採番値だけを使う。

工程の引継ぎ完了、操作の成否、試験/review/反映/公開結果、workflow_completion、incident_resolutionを別々に保存する。jobのフロー完遂からincident resolvedを自動推論しない。原因/業務回復unknownのまま同一jobのフロー完遂は可能だが、公開・必須成果永続登録・終端read-backが揃わなければ完遂としない。公開未達でもHTML、原因と再開位置を保存し、同じjobの公開・終端整合操作を続ける。

各失敗は操作名、例外classと安全化したcause chain/stack frames、発生/捕捉位置、実行版、相関ID、入力/成果物参照へ結ぶ。例外本文、SQL/parameters、商品/投稿、token/cookie、秘密/個人情報をtraceへ出さない。通常loggerの失敗は独立fallbackへ保存して本処理を続け、復帰時に同じ相関値で追記整合する。全保存先喪失の未記録区間は復帰時にunknownと明記する。

reporterと独立report reviewerを実起動し、指摘はreporterが修正する。通常生成失敗時も平易な最小HTMLをcontrollerが生成し、その実在版をreview・公開へ渡す。本文は中学生が理解できる文章にし、内部ID/STEP/status/reason/SHAは参考付録へ置く。テストコードの独立reviewは行わず、製品コードの独立reviewを実施する。

今回対象は10/17の同一jobとlaunch、source_run_id=nullを保持する。10/19 queue/history処理、通常Daily手動起動、新job代替、日次run捏造、下書き追生成、別workspace/worktreeは禁止。Gitはcontrollerが所有file/hunkだけを確認・選択stage・commit/pushする。

## FC05 差分や試験証拠がない場合

利用可能な差分・controller fallback・unknown成果のいずれかを実際に読み、review担当自身の所見を保存する。対象がない場合は理由を記録してunavailable等の実結果を返す。review状態や指摘で後続担当を止めない。

## SC skill連鎖での担当

製品reviewでは対象コード差分、要求、試験証拠と禁止副作用だけを独立に確認し、所見を`code-review.md`へ直接記録する。HTML reviewでは別sessionから、作者更新後の実HTMLについて不具合・原因・修正・追加trace/logの事実と分かりやすさ、秘密露出だけを確認する。reviewer自身は対象ファイルを編集せず、次工程をreview状態で止めない。
