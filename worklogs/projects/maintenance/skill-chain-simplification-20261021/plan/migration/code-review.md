# STEP03 独立製品レビュー（更新）

## 対象スナップショットと範囲

- 計画 v1.1 STEP03、採用済み構成案2.0、対応表、STEP01 `implementation.md`、STEP02 `test.md` と現在のテストXMLを確認した。
- 対象は2026-10-21に固定root `C:/project/rakuten-room-inago` で読んだ共有working-tree差分。主要対象は `automation/inago/maintenance/{skill_chain_runtime.py,skill_chain.py,worker.py,cli.py,store.py,historical.py,production_workflow.py,stage_handoff.py}`、`sql/017`〜`019`、関連`.agents/skills/`と`.codex/agents/*.toml`。差分と実ファイルを読み、hashを生成・比較していない。
- STEP01作者・STEP02試験作者とは別のreviewerによる読取専用確認。製品差分以外のテストソースはレビューしておらず、試験も実行していない。

## 以前の所見の更新

- 旧CR-01（保守hash/receiptの広範な残存）は**最終checkpointの対象について大部分が解消**。`store.py`の保守hash APIとrepair等の旧モジュールは削除され、reporting/Git公開、reconciliation、watchdog、standalone skill-chain、receipt、role-config等の旧呼出しも現行worker/CLI経路から除去されたことを差分・静的記録で確認した。v19は履歴値を保持して新規NULLを許容する。STEP02 C16-diagnostic-25は隔離DB上でv18→19後の既存manifest/session値保持、対象trigger除去、新規NULLを確認し1 passed/exit 0（`test.md` C16-diagnostic-25）。これは本番DBへのmigration実施を意味しない。
- 現行`store.py:731,1786-1788,2344-2473`のSHA生成/比較は`daily_result_observation`の通常Daily観測JSON整合性用。計画の境界にある業務hashであり、このreviewでは保守完了gateとは扱わない。`historical.py`のlegacy `*_sha256`文字列は旧intake JSONの読み取り互換用keyとして残るが、生成・比較・新identity投影は確認されなかった。
- 旧CR-02（runnerの全turn必須JSON schema）は**解消**。現`skill_chain_runtime.py:356-361`は応答schemaを渡さず、`app_server.py:739`は呼出側がschemaを渡したときだけ出力schemaを付ける。
- 旧CR-03（worker失敗でもterminal complete）は**大部分解消**。現`inspect_skill_chain_terminal`はmissing/unreadable/invalid ledgerをincompleteとし、7担当のturn、process exit、エラー、応答を確認する（`skill_chain_runtime.py:462-497`）。ただし成果物実在を確認しない残件をCR-02として記録する。
- 旧CR-04（10/17/Gitの現行指示矛盾）は**対象の現行契約では解消**。関連skill/TOMLに今回の10/17固定targetやcontroller commit/push指示は見つからず、10/19除外は`worker.py:32`で維持。歴史節や旧履歴説明は現行実行指示として数えない。

## 確認できた実装

- `skill_chain_runtime.py:28-35`は通常7担当を調査→実装→試験→独立製品review→本番反映→HTML作成→独立HTML reviewの順に定義。`_prompt`は前担当のMarkdownを渡し、HTML作者へ最終保存先を指定する。`worker.py:32`の10/19除外は維持されている。
- `store.py::ensure_skill_chain_job_for_launch`（現`store.py:1650`付近）は実launch/check/queueを結び、maintenance jobを別採番する。STEP02 C16-diagnostic-23のC08 focused rerunはCLI exit 0、1 passedで、実launch identityのcheck/incident/queue/jobを検証し、job IDとworker queue IDの相違を確認。実本番launchによる7担当の実動証拠ではない。
- `skill_chain_runtime.py:366-371`はclose失敗を記録して所有processの回収を試みる。`C16-diagnostic-16`はtimeout→terminate→kill後のexit 137とclose-error node、safe output nodeがpassした記録。`C16-diagnostic-18`は7 roleのfake-session identity/timing roundtripとterminal completeの1 node pass。これらはfake process/session試験であり本番worker終了ではない。
- v19移行は`store.py:182-284`のschema-preserving rebuildと`sql/019_remove_legacy_maintenance_attestation_gates.sql`で構成。C16-diagnostic-19/20の移行fixture失敗はそれぞれSQL末尾comment、旧tableを参照するtrigger順序が原因と記録され、C16-diagnostic-21で該当3 nodeが3 passed/exit 0に再実行された。初回失敗は履歴として保持されている。
- C16-migration-01.xmlは259件中214 pass/45 fail、C16-diagnostic-01.xmlは45 failureとして記録された実物を確認。そこでの旧hash/receipt/8担当/blocked等の期待を現製品欠陥へ一括変換していない。`test.md`冒頭のC16 NOT_RUN/初期baseline記述は後続のfocused evidenceより古い。最終関係回帰は同記録上まだ未実施であり、未実施として扱う。

## 所見

### CR-01 — outer-worker fallbackが禁止された次担当JSON handoffを再導入する

- **severity: high**。計画 R03/R04/R10、C02/C04/C10/C18、構成案2.0のhandoff撤廃。
- **確認済み製品gap:** `automation/inago/maintenance/worker.py:292-350` の `_record_outer_workflow_failure` は例外時、旧8工程名（`check_incident`, `evidence`, `job`, `investigation`, `planning`, `repair`, `deployment`, `report`）のJSONを `workflow-handoffs/<job>/<invocation>/<stage>.json` に書く。`stage_handoff.py::make_stage_handoff`は`invocation_id`, `next_actions`, `stage_progress`を持つ構造化handoffを作り、workerがそれを保存する。通常7担当Markdown runnerとは別のlegacy fallback routeであり、catch時の呼出箇所は`worker.py:677`で確認した。
- **影響:** 外側workflow例外時に次担当指定/旧8工程JSONが成果物と再開指示として残る。依頼は内容あるMarkdown fallbackと新runnerの明示担当からの継続を定め、このJSON routeを撤廃対象としている。
- **直接修正先:** `worker.py::_record_outer_workflow_failure`、その`stage_handoff.py`依存と該当fallback呼出しを、内容ある読めるMarkdown記録へ置換する。外側失敗を成功に偽装せず、可能な次担当を続ける。

### CR-02 — terminal completeが担当成果物の保存実体を確認しない

- **severity: medium**。計画 R02/R04/R14/R15、C02/C15/C18。
- **確認済み製品gap:** `automation/inago/maintenance/skill_chain_runtime.py:377-419`は各roleに期待artifact名を記録するだけで、担当別ファイルの有無/内容を記録しない。`inspect_skill_chain_terminal` (`:482-490`) は7 turn completed・process exit 0・応答ありでcompleteを返すが、`investigation.md`, `implementation.md`, `test.md`, `code-review.md`, `deployment.md`, `report.md`, `html-review.md`または最終HTMLの実在を確認しない。
- **影響:** turnが成功応答を返しても担当の割当成果を保存しないケースでworkflow completeとなり得る。これはhash/marker gateを追加せず、実作業と未実施を分けるという完了報告の正確性に関わる。
- **直接修正先:** terminal結果を実turn/process記録と分け、各担当が実際に保存した自然文pathを記録する。保守証明用hash、marker必須化、全成果物再検査は追加しない。

### CR-03 — HTML修正の起動が定型marker文字列に依存する

- **severity: medium**。計画 R03/R13/R15、C14/C15。
- **確認済み製品gap:** `skill_chain_runtime.py:430-448`はreview文に正規表現 `指摘: あり` がある場合だけ、HTML作者→reviewerの追加入力を起動する。一方 `.codex/agents/rr-inago-incident-report-reviewer.toml:22`は同じmarkerを要求する。
- **影響:** 指摘本文が存在してもmarker表記が一致しない場合、作者修正と更新版reviewが省略される。ユーザーはmarker gate/必須schemaを撤廃し、実物の内容指摘を作者へ渡すよう指定している。
- **直接修正先:** review本文の内容を作者へ渡し、実際の所見に応じて同job HTML作者と別reviewerを起動する。marker有無・review statusを後続開始条件にしない。

### CR-04 — 担当skill/TOMLが応答本文を次担当指定JSONに固定する

- **severity: medium**。計画 R03/R10、C01/C10。
- **確認済み契約gap:** `.codex/agents/rr-inago-incident-investigator.toml:19`と`.agents/skills/rr-inago-incident-investigation/SKILL.md`のrunner overrideは、成果物保存後に`担当`/`入力`/`依頼`の厳密3-key JSON応答を要求する。reporterのTOML/SKILLにも同形式応答要求が残る。runner自身はresponse JSONをparseしないため次担当選択に使ってはいないが、自然文とpathで渡す契約および「次担当指定JSON撤廃」と整合しない。
- **影響:** 担当応答に不要な構造化handoffを課し、responseが新たな管理契約として残る。
- **直接修正先:** investigator/reporterのactive SKILLとTOMLを、通常の短い完了説明と保存pathを返す指示へ更新する。runnerは固定順を維持する。

## 結果と後続への引継ぎ

現行移行で7担当の直列起動、close回収、安全化ログ、正規launch受付、v19歴史保持の主要部分は実装され、focused test結果も確認できた。一方、上記4件は製品差分で確認した未解消点であり、結果は**所見あり**。これらは後続工程の開始・本番反映・10/20処置・HTML作成を止めるgateではない。後続担当が直接修正する箇所として渡す。

未確認/未実施: v19の本番DB適用、10/20 Worker CLI本番実動、7担当の実turn/実process終了、最終C16関係回帰。これらを成功と扱っていない。レビュー時点では、実装記録のSTEP01最終checkpointと共有working treeが存在する一方、最終C16 regressionが後続試験記録へまだ追加されていなかった。

## STEP01追加修正の独立再レビュー

対象を親指定の追加変更箇所に限定して再確認した。製品コードの作者とは別の読取専用レビューであり、pytest/テストソースレビューは行っていない。

- **CR-01 解消:** `worker.py:292-379`の外側失敗fallbackは旧8工程JSONと`stage_handoff`を使わず、7担当の未実施/未確認を区別した`workflow-fallback.md`を書き、同じjobの再開位置を自然文で渡す。秘密を含む例外原文はfallback本文に複写せず、failure journalへ既存のsafe handlingで送る。
- **CR-02 更新:** `skill_chain_runtime.py:66-75,389-437,477-520`はworkerの自然文から保存path報告を分離し、terminal結果へ各担当の報告path、全path報告有無、内容を再読していない事実を明示する。path/内容をhash・marker・bytes・全件read-backで検査せず完了gateに戻さない設計は指定と整合する。`reported_save_path`が報告文中の自然語ラベルから抽出できない場合でも担当実行や後続を止めず、観測はnullとして残る。レビュー対象の現実物確認はHTML reviewerに直接割り当てられている。**残findingなし**（内容未再検査を実在確認と取り違えないことは後続報告で引き続き明記する）。
- **CR-03 解消:** `skill_chain_runtime.py:447-466`はmarker有無にかかわらずreviewerの通常文章をHTML作者へ渡し、その後同じ別reviewerを再実行する。`_prompt` (`:282-305`)はreview本文を作者の入力へ含め、自然文で必要修正を判断させる。reviewer TOML `rr-inago-incident-report-reviewer.toml:22,29`およびreport skill `rr-inago-incident-report/SKILL.md:39,83`も定型markerを不要とし、作者修正後の実HTML再読を指示する。
- **CR-04 解消:** investigator/reportのactive skillとTOMLは固定3-key JSONを要求せず、通常文章と実保存pathを返す指示へ変更された（`rr-inago-incident-investigation/SKILL.md:78`, `rr-inago-incident-report/SKILL.md:83`, `.codex/agents/rr-inago-incident-investigator.toml`末尾, `rr-inago-incident-reporter.toml:27`）。現runnerは応答をJSONとしてparseしない。
- **C08 schema差分確認:** `store.py:122-162,1001-1008`は現行必須table群を確認し、v19移行後も残る16種の既知historical tableを許容する。未知の別tableは従来どおりunsupported schemaとして拒否する。C08-09失敗で報告された16個の`actual_not_expected`はこのhistorical allowlistへ全て含まれ、必要table不足 (`expected_not_actual`) は元の診断で空だった。これは確認されたC08失敗原因への直接修正である。
- **finalization差分:** `skill_chain_runtime.py:567-605`はterminal incomplete時の実reason codeを`finalize_job`へ渡し、storeが要求するfailure reasonを欠落させない。また実戻りstatusまたは例外classを`finalization_status`として記録し、incident statusは`in_progress`のまま保つ。worker outcomeの`unknown`と保存操作結果を混同しない。

### 再レビュー時点の試験証拠と残件

再読時点で`test.md`の最新保存内容は、C08-09の2 runtime nodeがschema allowlist修正前に`database_schema_unrecognized`で失敗（pytest exit 1）した記録までだった。C08-11として親から伝達された`job_terminal_reason_required`はSTEP01が上記reason forwardingで修正したとの報告を受けたが、その後のSTEP02 focused rerun結果はまだ記録されていない。従って修正後のC08 runtime/node結果は**未確認**と記録する。C16-migration-02もtest.md上NOT_RUNのままで、最終関係回帰完了とは扱わない。

今回の追加再レビューで、worker Markdown fallback、marker-free HTML author/reviewer follow-up、investigator/reporter自然文handoff、既知historical schema recognition、terminal failure reason forwardingについて、指定された変更範囲内の新しい製品所見は見つからなかった。後続工程はこれら未確定試験結果を正確に引き継いで進める。
