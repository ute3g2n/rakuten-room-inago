"""FC02 document/role synchronization; never opens runtime databases or product code."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('C:/project/rakuten-room-inago').resolve()
assert Path.cwd().resolve() == ROOT
OUT = ROOT / 'worklogs/projects/maintenance/flow-completion-20261019/FC02'
PLAN = 'plans/projects/maintenance/自動不具合対応フロー完遂と全工程追跡_実行計画_2026-10-19.md'
BASE = '51cfc7503775a25af731c8f4bcf727c94f10a595'
IDENTITY = dict(job_id='job-historical-9e7f0c175cc4c874c955a5dab29c4427',
                incident_id='352492da-ac58-4680-b1fd-fe5f24000229',
                source_launch_id='historical-9e7f0c175cc4c874c955a5dab29c4427',
                source_run_id=None, target_date='2026-10-17')
START = datetime.now(timezone.utc).isoformat()
roles = ['common-execution-planner', 'common-execution-plan-reviewer',
         'rr-inago-incident-investigator', 'rr-inago-incident-investigation-reviewer',
         'common-implementer', 'common-tester', 'common-reviewer',
         'rr-inago-incident-reporter', 'rr-inago-incident-report-reviewer']
skills = ['rr-inago-incident-investigation', 'common-execution-planning',
          'common-implementation', 'common-testing', 'common-review', 'rr-inago-incident-report']
specs = ['AT-MAINT-01_保守対応要件受入契約.md', 'AT-TRACE-01_共通トレース契約.md',
         '要件データ辞書.md', '要件追跡表.md', '保守運用設定仕様.md']
HTML = 'docs/specifications/inago/イナゴスキル要件定義書.html'
FLOW = 'tools/docs/inago-requirements/flow-descriptions.mjs'
TASKFLOW = 'tools/docs/inago-requirements/task-flows.mjs'
SVG = 'docs/specifications/inago/図解/F12.svg'
LOGDOC = 'docs/operations/日次タスクのトレース・ログ出力一覧.md'
owned = ([f'.codex/agents/{r}.toml' for r in roles]
         + [f'.agents/skills/{s}/{tail}' for s in skills for tail in ('SKILL.md', 'references/contracts.md')]
         + [f'docs/specifications/inago/{s}' for s in specs]
         + [HTML, FLOW, TASKFLOW, SVG, LOGDOC, PLAN])
outputs = ['contract-decision.md', 'stage-output-matrix.md', 'schema-change-list.md',
           'file-operation-owner-map.json', 'requirement-step-case-map.json', 'instrumentation-allocation.md',
           'role-high-sync-result.json', 'handoff.json', 'plan.md', 'result.json',
           'write-intent.json', 'synchronization-evidence.json']


def sha(path: str) -> str | None:
    p = ROOT / path
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def write(path: str, text: str) -> None:
    p = (ROOT / path).resolve()
    assert p.is_relative_to(ROOT)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8', newline='\n')


def output(name: str, content) -> None:
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2) + '\n'
    write(str((OUT / name).relative_to(ROOT)), content)


before = {p: sha(p) for p in owned}
original_plan_bytes = subprocess.run(['git', 'show', 'HEAD:'+PLAN], cwd=ROOT, capture_output=True, check=True).stdout
original_plan = original_plan_bytes.decode('utf-8')
approved_plan_sha256 = 'e7434af9385cd2a33dc66fbc57004cfe7576ea76c0362847e48d129831f4f381'
assert hashlib.sha256(original_plan_bytes).hexdigest() == approved_plan_sha256
source_match = re.search(r'<script id="requirements-source" type="application/json">(.*?)</script>', (ROOT/HTML).read_text(encoding='utf-8'), re.S)
assert json.loads(source_match.group(1))['version'] == '2.49', 'This is a baseline-specific record, not a repeatable migration; inspect current diff before running.'
assert subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip() == BASE
output('write-intent.json', dict(step='FC02', started_at=START, base_commit=BASE,
       identity=IDENTITY, owned_documents_roles=owned, outputs=outputs,
       helper_script='worklogs/projects/maintenance/flow-completion-20261019/FC02/execute_contract_sync.py',
       before_sha256=before, boundary='No product code, database, Scheduler, tests, Git index, commit or remote mutation.',
       controller_scope_extension='flow-descriptions.mjs F12 S-node text only; controller message approved'))

common = '''## FC02 現行の全工程引継ぎ契約（2026-10-19）

この節は現行契約であり、過去のU05～U23に残る開始拒否・承認・worktree・終端結合の説明は旧実装の履歴として扱う。本人一名のローカルシステムで堅牢性を目的に制限を足さない。唯一の作業場所は `C:/project/rakuten-room-inago`。全担当のreasoning effortはhighとし、固定max roleしか選べないsessionでは同じ専門promptをhigh指定可能なworker/defaultで実起動する。指定値と実観測値を区別し、model/tier/receipt/session等の未観測を起動条件にしない。

各stageは内容ある通常成果またはcontroller fallback成果と有効なhandoffを保存して次担当を実起動する。将来のstage結果にblockedを返さず、別名の状態・承認・receipt・lease・回数上限に置き換えたgateも作らない。review/test/readiness/release、FAIL、unknown、証拠不足、空/不正なAI応答、過去raw blockedを後続開始条件にしない。個別操作ができない場合はその操作だけ `not_performed` と具体理由を記し、他の作業と最終報告・公開を続ける。過去raw履歴と元の日次結果は改変しない。

不変のcontroller入力identityを保持し、欠測はnullとmissing_reasonを残す。source_run_idやtarget_dateを推測しない。対象が識別できる限り部分成果を出す。実装操作を省略できるのは原因修正と有用な計装の双方ができない具体的理由を確認した場合だけであり、既知の保守trace不足は今回実装する。差分0でもtesterは成果物整合を実検査し、製品reviewerは対象差分なしを実確認してapprovedを捏造しない。

handoffはidentity、schema_version、invocation/stage/attempt/operationとparent_operation、入力版/参照、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs、artifact_origin（agent/controller_fallback）、実行担当、review対象版、開始/終了/観測時刻、code revisionを持つ。本文は「何を行い、何が分かり、何が不明で、何を次に行うか」を含む。新attempt IDはcontrollerの正式採番値だけを使う。

工程の引継ぎ完了、操作の成否、試験/review/反映/公開結果、workflow_completion、incident_resolutionを別々に保存する。jobのフロー完遂からincident resolvedを自動推論しない。原因/業務回復unknownのまま同一jobのフロー完遂は可能だが、公開・必須成果永続登録・終端read-backが揃わなければ完遂としない。公開未達でもHTML、原因と再開位置を保存し、同じjobの公開・終端整合操作を続ける。

各失敗は操作名、例外classと安全化したcause chain/stack frames、発生/捕捉位置、実行版、相関ID、入力/成果物参照へ結ぶ。例外本文、SQL/parameters、商品/投稿、token/cookie、秘密/個人情報をtraceへ出さない。通常loggerの失敗は独立fallbackへ保存して本処理を続け、復帰時に同じ相関値で追記整合する。全保存先喪失の未記録区間は復帰時にunknownと明記する。

reporterと独立report reviewerを実起動し、指摘はreporterが修正する。通常生成失敗時も平易な最小HTMLをcontrollerが生成し、その実在版をreview・公開へ渡す。本文は中学生が理解できる文章にし、内部ID/STEP/status/reason/SHAは参考付録へ置く。テストコードの独立reviewは行わず、製品コードの独立reviewを実施する。

今回対象は10/17の同一jobとlaunch、source_run_id=nullを保持する。10/19 queue/history処理、通常Daily手動起動、新job代替、日次run捏造、下書き追生成、別workspace/worktreeは禁止。Gitはcontrollerが所有file/hunkだけを確認・選択stage・commit/pushする。
'''
identity_old = '欠落・不一致・曖昧な結合があれば開始せずblockedを返す。'
identity_new = '欠落・不一致・曖昧な結合はunknownと具体理由を記録し、値を推測しない。対象jobと担当stageが識別できれば利用可能な入力で直ちに起動する。対象を選べない個別操作だけ未実施とし、部分成果と後続工程を続ける。'
role_results = []
for role in roles:
    rel = f'.codex/agents/{role}.toml'
    text = (ROOT / rel).read_text(encoding='utf-8')
    prior = re.search(r'model_reasoning_effort = "(.*?)"', text).group(1)
    model = re.search(r'^model = "(.*?)"', text, re.M).group(1)
    text = text.replace('model_reasoning_effort = "max"', 'model_reasoning_effort = "max"').replace('effort max', 'effort high')
    text = text.replace('Changes assigned product files using the available plan and controller-provided worktree.', 'Changes assigned product files using the available plan in the fixed repository workspace.')
    text = re.sub(r'If Git reports dubious ownership.*?Git configuration files\.',
                  'The only workspace is C:/project/rakuten-room-inago. Do not create, select, or use a different worktree or checkout. Preserve all existing changes.', text)
    text = text.replace('Check that only report complete maps to job complete/incident resolved/Worker success, while pending or blocked remains noncomplete in the persisted outcome.',
                        'Check that stage handoff completion, operation results, publication, workflow completion and incident resolution are separate; unknown cause or recovery stays unknown after workflow completion. Do not map report status to incident resolved or a future blocked stage. Verify exact published version and durable terminal read-back.')
    text = re.sub(r'Only `report.status=complete` maps to job complete, incident resolved, and Worker success\..*?Worker maintenance_incomplete\.',
                  'Keep stage handoff completion, operation outcomes, publication, workflow completion and incident resolution separate. Unknown cause or recovery stays unknown; do not map report status to a future blocked stage or incident resolved. Persist exact published version and terminal read-back.', text)
    text = text.replace('\n"""', '\nFor every future stage, return a substantive normal or controller fallback artifact and handoff, then launch the next assigned role. Never return blocked as a new stage result or replace it with an approval, receipt, lease or count gate. Preserve historical raw statuses and keep operation failure, publication, workflow completion and incident resolution separate.\n"""')
    if text != (ROOT / rel).read_text(encoding='utf-8'):
        write(rel, text)
    role_results.append(dict(role=role, file=rel, model=model, before_effort=prior,
                             after_effort='high', embedded_high='effort high' in text,
                             actual_runtime_model=None, actual_runtime_effort=None, actual_runtime_tier=None,
                             observation='not observed; configuration is not runtime proof',
                             before_sha256=before[rel], after_sha256=sha(rel)))

for skill in skills:
    for tail in ('SKILL.md', 'references/contracts.md'):
        rel = f'.agents/skills/{skill}/{tail}'
        text = (ROOT / rel).read_text(encoding='utf-8').replace(identity_old, identity_new)
        text = text.replace('原因確度unknownのfactに対して許すactionは`verify_only`または`hold`だけとし、原因を前提とする修正は計画しない。',
                            '原因確度unknownのfactから原因修正を断定しない。有用なtrace/log計装を具体的な捕捉操作・期待event・試験・review・適用へ割り当てる。計装も不能なら候補と理由、変更しなかった説明HTMLの検証・公開まで計画し、holdを後続gateにしない。')
        text = text.replace('blockは担当作業に絶対不可欠な対象/入力が実在せず、その作業自体を始められない場合だけにする。',
                            '対象/入力がなく実行できない個別操作だけ未実施と記録し、将来stageへblockedを返さず部分成果を作る。')
        text = text.replace('blockにできるのは、担当する調査の対象job/事象そのものを特定できず、どの事象を調べるか選べない場合だけ。',
                            '対象job/事象を選べない場合も、その個別操作だけ未実施と記録し、利用可能情報による部分成果と次担当への引継ぎを作る。')
        text = text.replace('controllerが作業用管理worktreeを用意し、', '唯一の固定workspaceでcontrollerが担当file/hunkを割り当てる。')
        if skill == 'rr-inago-incident-report':
            text = '\n'.join(line for line in text.split('\n') if not line.startswith('- `report.status=complete`だけ') and not line.startswith('`report.status=complete`だけ'))
        if skill == 'common-testing':
            text = text.replace('- testを実行する前に\n', '- testは計画の実在nodeと専用合成fixture/一時DBを確認して実行する。\n')
            text = text.replace('- U20前の実行は新規、未実施または前回FAILで計画が再試行を許すcaseに限る。既PASSのcase、pytest collect、全suiteを起動しない。',
                                '- 変更影響に必要なfocused local pytestと最小collectionを行う。過去PASSは現在版の実測に流用せず、無関係な全suiteや根拠のない同一確認の反復は行わない。')
        write(rel, text.rstrip() + '\n\n' + common)

decision = '''# FC02 契約決定 v1

基準は承認計画1.2（SHA e7434af9385cd2a33dc66fbc57004cfe7576ea76c0362847e48d129831f4f381）、実行HEAD 51cfc7503775a25af731c8f4bcf727c94f10a595、FC01の2026-10-19 22:43:53 JST読取、production schema 13。FC02は製品コード/DB/Schedulerを変更せず、契約とrole設定を同期する。原10/17原因・業務回復・実投稿件数はunknown。調査v2とreview v2の原証拠未供給・review範囲・20:44作成cutoffの制約を保持する。review approvedは計装や現在版の成功証明ではない。

## 状態を分ける

新handoffのschema_versionは maintenance-stage-handoff-v1 とする。stage_progressはpending/running/completed、operation_resultはsucceeded/failed/not_performed、content_resultはconfirmed/unknown/partial/no_change、artifact_originはagent/controller_fallback。raw agentのblocked/invalid/emptyは非公開原応答と別の品質観測に保持し、新規stage結果へ伝播しない。completedは内容あるhandoffを確定した意味であり、操作成功・review approved・元障害解消を意味しない。

workflow_completionはin_progress/completed/incomplete、incident_resolutionはunknown/unresolved/resolved、publish_resultはsucceeded/failed/not_performedで分離する。flow completedには全必須成果の永続参照、実担当の処理証拠、公開HTML版/hash/review/commit/remote read-back、job終端/finished_atの一致が必要。これらは完遂を正しく観測する条件であり、次担当起動のgateではない。公開失敗時も終端整合操作まで実行し、未達理由と同一job再開位置を残す。CLIはflow completedでexit 0、未完遂は実nonzero（既存maintenance_incompleteのexit 3を維持候補）を返し、incident unknownだけでexit 3にしない。最終番号互換性はFC05実装で確認する。

## 共通handoffと内容

identity（job/incident/launch/run/date）、schema_version、invocation_id、stage、正式attempt_id、operation_id/parent_operation_id、input_refsと版、stage_progress、content_result、operation_results、errors、unknowns、next_actions、output_refs（path/hash/version）、artifact_origin、作者と実担当/独立reviewer、review対象版、開始/終了/観測時刻、code revisionを必須意味とする。未知値はnull+missing_reasonとし、IDや時刻を推測しない。本文は各担当の実操作・判明事項・不足・次操作を含む。DB未登録のfallbackをDB登録済みと称さない。

## 失敗追跡・保存

全操作にbegin/end/error/abnormal_end/reconciledをappend-only記録し、job→invocation→stage→attempt→operation→eventを結ぶ。AIのthread/turn/requestは実取得値だけを記録する。errorはreason_codeに加え安全な例外class、cause/context関係、module/function/repo-relative file/lineのstack frames、発生と捕捉の区別、elapsed、最後の成功操作、入力/出力refs、code SHA/dirty差分参照を持つ。例外文字列・SQL/値・credential・商品/投稿/個人情報を含めず、任意code文字列も許可分類へ安全化する。

通常DB/JSONLの書込失敗は独立failure_journalへ最小標準I/Oで保存し、通常loggerを再帰利用しない。固定workspace内runtime maintenance専用spoolへ、temporary→flush/fsync→atomic replaceでintentとartifact参照を残す。stderr監視側採取も使い、失敗したsink自身と元操作のcauseを別項目にする。容量/件数/lease/receiptで本処理を止めない。DB復帰時はevent_id/operation_idで重複を避け参照を追記整合し、artifact/hashをread-backする。強制終了後はbegin-only区間へ検出時刻付きabnormal_endを追記し、架空の元終了時刻や例外を作らない。全保存先同時喪失の区間はunknownとして復帰時に追記する。

## 今回の実装と同一job

日次SQLite分類・許可constraint・transaction・proposed/persisted run・capture tuple/hashは既存のため再追加しない。FC03はmaintenance操作相関/例外連鎖/trace writer独立fallback、AI adapter parent B high、DB/artifact/Git失敗境界を実差分化する。FC04は通常/空/不正/失敗から有効handoffと専門担当実起動を実装する。FC05は中断再開、試験/review/反映/終端を分離、FC06は平易な最小HTMLと実reporter/reviewer/修正/公開を結ぶ。外側で先行実装された候補は、同一jobのplanner/implementer/tester/reviewer/deploymentが実際に検証・正式適用し、新traceを同一jobで採取する。jobがその差分を新規作成したとは表現しない。

## 役割・範囲・Git

全9 roleのeffort highを設定/本文で照合し、実model/tierは未観測をunknownとする。5 maxのbefore値/SHAをrole-high-sync-result.jsonへ保存。ai_adapter製品設定max→highはFC03担当でありFC02適用済みとはしない。親A現採用model gpt-6.1-sol、親B gpt-6-lunaを候補とし、実使用可能性を捏造しない。本人一人のローカルシステム方針は全skill/contractsと全 downstream promptへ引継ぐ。所有外変更は保持し、所有表追加をcontrollerへ連絡して可能作業を続ける。GitはFC09/controllerだけが所有file/hunkを確認して選択stage/commit/pushする。

## 継続と未達

FC02でpytest・製品review・migration適用・本番job再開・公開は未実施。独立plan reviewerが本成果物を次に実確認するが、review結果をFC03開始条件にしない。schema fieldは本契約の意味へ実装で結び、実装で統合/変更した場合は同STEPで本所有表とschema一覧へ理由を追記する。実在しないnodeや正式attempt IDを作らない。次操作はFC03のfailure_journal/diagnostic_trace/AI adapter/CLI境界実装。
'''
output('contract-decision.md', decision + '\n' + common)

stages = [
('対象確認・証拠', 'controller', '固定identity、観測時刻、取得refs/欠測', '読めたrefsと読めなかった理由のbundle', 'investigator'),
('調査', 'rr-inago-incident-investigator', '9章MD、facts、影響/不明/仮説、次の計装候補', '取得事実と調べ切れなかった範囲を9章へ記す', 'investigation reviewer→planner'),
('計画', 'common-execution-planner', '0～14節、STEP14項目/全文prompt、case12項目、所有/試験/適用/報告', '今回の不足計装の最小具体計画、不能なら候補/理由と説明HTMLの検証・公開計画', 'plan reviewer→implementer'),
('実装', 'common-implementer', '実diff、捕捉operation、期待event、版、所有一覧', '原因修正不能でも計装、双方不能だけ個別操作not_performed+理由', 'tester'),
('試験', 'common-tester', '完全node、clock/版、期待/実測/exit、件数、証拠、未実施理由', '差分0でもschema/引継ぎ/報告データを実検査、対象なしは空PASSにしない', 'product reviewer'),
('製品review', 'common-reviewer（作者と別）', '製品diff/実測対象版、所見、修正要求', '差分0を実確認して対象差分なし、approvedを捏造しない', '元作者修正→deployment'),
('反映', 'controller/deployment', '候補/現用/反映SHA、schema、正式ledger、read-back', '候補なし/失敗操作の理由、既存版維持の実値と観測', 'reporter'),
('報告', 'rr-inago-incident-reporter', '平易なversion付きHTML、result、技術付録', 'controller最小HTMLに出来たこと/未確認/次行動、原生成失敗を残す', 'independent report reviewer'),
('報告review/修正', 'rr-inago-incident-report-reviewer→原reporter', 'exact版/hash、読解/根拠/リンク/狭幅/秘密の所見と作者対応', 'fallback実在版にも独立review依頼し、実起動失敗と未確認を残す', 'publication'),
('公開・終端', 'controller/publication', 'HTML版/hash/review/commit/remote/DB結合、終端/finished_at', 'push失敗でもHTML/原因/再開位置保存と終端整合操作、未達をcompleteにしない', 'same-job recovery or FC11照合')]
output('stage-output-matrix.md', '# FC02 stage成果契約\n\n全行は共通handoffを持つ。stage progressと操作成否は別、後続は品質結果にかかわらず実起動する。\n\n| 工程 | 実担当 | 通常必須内容 | 失敗時の内容あるfallback | 次担当 |\n|---|---|---|---|---|\n' + '\n'.join('| ' + ' | '.join(row) + ' |' for row in stages) + '\n\n本表は実行契約であり全担当実行済みの記録ではない。FC02で実起動したのはplannerのみ。その他は各STEP/同一jobの正式attemptで記録する。\n')

output('schema-change-list.md', '''# FC02 schema差分設計

確認したsource migrationは001～013、FC01 productionはschema 13。旧migration/旧raw enumを書換えない。次の未使用番号014をFC03の新規source `automation/inago/maintenance/sql/014_flow_completion_trace.sql` に割当てる（現時点予定・未作成・未適用）。FC05で同migrationを調整する場合はFC03担当と順次所有し、適用前sourceの最終hashを記録する。別migrationが必要なら直前に番号を再確認して所有表へ追加する。

| 対象 | 新規/既存 | 決定内容 | 担当/受入 |
|---|---|---|---|
| maintenance operation journal | 新table予定 | event_id、identity、invocation/stage/attempt、operation/parent、phase、時刻、code revision、safe errors、refs、sink/recovery。append-onlyで旧observationを変更しない | FC03 / FCT02/05/10/12 |
| stage handoff/publication/reconciliation | 新tableまたは既存tableの加算extension | stage_progress、content_result、operation result、artifact origin、版/hash/refs、workflow_completion、incident_resolution、公開版/remote/finished_atを独立記録 | FC04/05/06 / FCT01/03/04/06/07/08/10/11 |
| maintenance-diagnostic-event.schema.json | 既存拡張 | correlation、operation、safe cause/stack/capture、code/artifact/sink fields、旧raw reader互換 | FC03 / FCT02/05 |
| maintenance-stage-handoff.schema.json | 新file予定 | maintenance-stage-handoff-v1。必須意味はcontract-decision、stage_progressにblockedなし | FC04 / FCT01/03/04 |
| investigation/planning/repair schemas | 既存拡張 | raw statusを保持し、新handoffのcompleted/failed/not_performed/contentを分離 | FC04 / FCT03/04/12 |
| test/review/deployment schemas | 既存拡張 | FAIL/ERROR/NOT_RUNやreview所見とstage継続/適用操作実値を分離、承認等を開始gateにしない | FC05 / FCT04/06/07 |
| report response/review/result schemas | 既存拡張 | 通常/最小HTML、作者と別reviewer、exact版、指摘反映、公開/終端参照、元障害unknown | FC06 / FCT08/09/11 |

追加の正式table名/JSON storage形式は実装担当が既存Store責務と照合して確定し、変更理由と実在path/hashを同STEPで追記する。意味契約を削減しない。FC02はmigrationコード/製品schema/本番DBを変更していない。schema 14を本番現行値と称さない。
''')

# Parse only IDs and confirmed nodes already in the approved plan; never fabricate pytest node names.
mapping = []
for line in original_plan.splitlines():
    if not re.match(r'\| R\d\d/A\d\d ', line):
        continue
    cells = [v.strip() for v in line.split('|')[1:-1]]
    n = re.match(r'R(\d\d)/A\d\d', cells[0]).group(1)
    # Slash shorthand is anchored by the initial FC/FCT prefix in each table cell.
    steps = [f'FC{i:02}' for i in range(1, 12)] if '～' in cells[2] else ['FC'+v for v in re.findall(r'\d\d', cells[2])]
    cases = ['FCT'+v for v in re.findall(r'\d\d', cells[3])]
    mapping.append(dict(requirement='FC-R'+n, acceptance='FC-A'+n, facts=['F'+v for v in re.findall(r'\d\d', cells[1].split('；')[0])],
                        evidence=[cells[1], 'FC01/baseline.md', 'FC01/failure-path-map.md'], steps=steps, cases=cases,
                        owner=cells[4], output=cells[5], reviewer=cells[6], completion_evidence=cells[7],
                        existing_requirements={'01':['MR-008','MR-012','MR-025','MA-025'], '02':['MR-014','MR-021','MA-021'], '03':['MR-011','MR-015','MR-016','MA-029'],
                          '04':['MR-010','MR-017','MR-025','MA-025'], '05':['MR-023','MR-029','MA-029'], '06':['MR-020','MR-026','MA-020'],
                          '07':['MR-019','MR-026','MA-026'], '08':['MR-007','MR-027','MA-027'], '09':['MR-024','MR-027','MA-027'],
                          '10':['MR-003','MR-009','MR-010','MR-011','MR-021','MA-029']}[n], status='NOT_RUN; contract only'))
nodes = re.findall(r'^- (tests_inago/\S+::\S+)\s*$', original_plan, re.M)
output('requirement-step-case-map.json', dict(schema_version='fc02-requirement-step-case-map-v1', identity=IDENTITY,
       approved_plan_version='1.2', base_commit=BASE, source='approved plan §10.1', mappings=mapping,
       existing_node_inventory=[dict(node=n, status='definition observed in approved plan; not executed in FC02') for n in nodes],
       new_failure_case_nodes='not created; FC07 must bind FCT01–13 to exact collected real nodes',
       existing_requirement_mapping='compatibility cross-reference; no new approved MR/MA IDs invented',
       investigation_limits=['IR-U23-03', 'IR-U23-04', 'IR-U23-V2-01']))

file_operations = []
def owner(path, step, boundary, operation, status='existing', mode='write', cases=None):
    line = None
    p=ROOT/path
    if p.exists():
        for i,t in enumerate(p.read_text(encoding='utf-8').splitlines(),1):
            if boundary in t: line=i; break
    file_operations.append(dict(file=path, existence=status, owner=step, access=mode,
        boundary=dict(symbol_or_hunk=boundary, baseline_line=line, base_commit=BASE), operation=operation,
        execution_order=int(step[2:]) if step.startswith('FC') else 2,
        output=f'worklogs/projects/maintenance/flow-completion-20261019/{step}/; same-job formal attempt refs in FC10',
        reviewer='independent plan reviewer' if step=='FC02' else 'FC08 independent product reviewer',
        evidence=['FC01/failure-path-map.md','FC01/instrumentation-backlog.md'], cases=cases or [],
        in_step_sync=['requirements-source HTML + builder output','AT-MAINT-01','AT-TRACE-01','dictionary','traceability','trace log list','relevant skill/role','F12 where changed']))

boundaries = {
'diagnostic_trace.py':('FC03','def require_written','non-gating trace writes, safe cause/capture, independent fallback and recovery'),
'workflow_provider.py':('FC03','class MaintenanceWorkflowProvider','outer invocation/operation correlation, observation persistence failure journal'),
'ai_adapter.py':('FC03','PARENT_SETTINGS','parent B max→high; actual launch/transport/timeout/empty/parse failure correlation'),
'safe_file.py':('FC03','def publish_file_beneath','artifact write/hash/replace failure and intent recovery correlation'),
'paths.py':('FC03','def resolve_maintenance_database_path','fixed workspace journal path resolution, no inferred identity'),
'settings.py':('FC03','class MaintenanceSettings:','trace fallback configuration and compatibility without new gate'),
'controller.py':('FC03','class MaintenanceController:','trace begin/catch/final per controller operation; retain prior edits for FC04/05'),
'store.py':('FC03','class MaintenanceStore','append-only operation events and persistence read-back; FC05 later lifecycle'),
'production_workflow.py':('FC04','def run_daily_planning_author_stage','check/evidence/investigation/planning/repair handoff and actual launch; remove approval gates'),
'investigation.py':('FC04','def validate_investigation_response','unknown/AI failure output with substantive handoff'),
'planning.py':('FC04','def validate_planning_response','available-input instrumentation plan and no review prerequisite'),
'repair.py':('FC04','class RepairController:','useful instrumentation or reasoned no-change; FC05 later test/review connection'),
'deployment.py':('FC05','def push_candidate_commit','candidate/no-change/failure deployment evaluation and ledger/active version observation'),
'runtime_switcher.py':('FC05','class TaskSchedulerRuntimeSwitcher:','apply/read-back/failure records without status gate'),
'worker.py':('FC05','WORKER_DISPATCH_EXCLUDED_DATES','workflow/incident separation and true terminal/exit; preserve exact 10/19 exclusion'),
'cli.py':('FC03','def main','remove trace receipt gates; FC05 terminal/CLI outcome continuation'),
'reporting.py':('FC06','def prepare_incident_report','plain normal/minimal HTML, reviewer/author revision/publication exact version')}
for name,(step,boundary,op) in boundaries.items(): owner('automation/inago/maintenance/'+name,step,boundary,op,cases=['FCT02','FCT05'] if step=='FC03' else ['FCT01','FCT03','FCT04','FCT06','FCT07','FCT08','FCT10','FCT11'])
for step,name,op in [('FC03','failure_journal.py','independent fallback sink and recovered-event reconciliation'),('FC04','stage_handoff.py','meaningful fallback envelope; next agent consumes exact version'),('FC05','workflow_reconciliation.py','abandoned attempt reconciliation and terminal/publication/incident separation'),('FC06','report_fallback.py','deterministic plain minimum HTML with normal review/publication')]:
    owner('automation/inago/maintenance/'+name,step,'new module',op,'planned_new',cases=['FCT01','FCT02','FCT05','FCT08','FCT10'])
owner('automation/inago/maintenance/sql/014_flow_completion_trace.sql','FC03','new additive migration','schema13→14 append-only event/handoff/lifecycle fields; FC05 sequential coordination','planned_new',cases=['FCT05','FCT10','FCT12'])
for name in ['daily_pipeline.py','orchestrator.py','trace.py']:
    owner('automation/inago/'+name,'FC03','existing SQLite/capture instrumentation','inspect and reuse existing daily classification/constraint/transaction/proposed-persisted/capture; no duplicate edits planned',mode='read',cases=['FCT02','FCT12'])
schema_groups={
'FC03':['diagnostic-event','check-result','check-response','evidence','settings'],
'FC04':['investigation-response','investigation-review','planning-response','planning-review','repair-result','repair-patch','repair-patch-assessment','repair-handoff','stage-handoff'],
'FC05':['repair-test-ledger','repair-test-evidence','repair-review','repair-review-target','repair-review-claim','deployment-candidate','deployment-result'],
'FC06':['report-response','report-review','report-result']}
for step,names in schema_groups.items():
    for name in names: owner('automation/inago/schemas/maintenance-'+name+'.schema.json',step,'properties','additive stage/operation/progress/quality separation; legacy raw enum reader retained','planned_new' if name=='stage-handoff' else 'existing',cases=['FCT01','FCT02','FCT03','FCT06','FCT08','FCT13'])
for path in owned: owner(path,'FC02','FC02 contract / high / F12 hunk','contract synchronization only; subsequent steps amend their behavior in sequence',cases=['FCT13'])
owner('automation/inago/maintenance/production_workflow.py','FC05','test/review/deployment/terminal runners','actual tester/reviewer/deployment operation on diff/no-change/fallback; no gates',cases=['FCT04','FCT06','FCT07','FCT10'])
owner('automation/inago/maintenance/production_workflow.py','FC06','report/review/revision/publication runners','normal and fallback report author/reviewer/revision/publish exact version',cases=['FCT08','FCT09','FCT11'])
for path in ['automation/inago/maintenance/controller.py','automation/inago/maintenance/store.py']:
    owner(path,'FC04' if 'controller' in path else 'FC05','stage handoff / terminal lifecycle','sequential edit after FC03: handoff dispatch or immutable raw-history reconciliation',cases=['FCT01','FCT03','FCT10','FCT11'])
output('file-operation-owner-map.json',dict(schema_version='fc02-file-operation-owner-map-v1',identity=IDENTITY,base_commit=BASE,
    order=['FC02','FC03','FC04','FC05','FC06','FC07','FC08','FC09','FC10','FC11'],
    shared_file_rule='sequential ownership, preserve predecessor diff; function/hunk boundary wins over stale line after edits',
    fc10_implementation='Use FC03 candidate exact diff/version for same-job validation, product review, official apply and new trace. If genuinely uncovered operation is found, controller appends exact file/hunk here before edit; do not duplicate daily SQLite trace.',
    scope_extension=dict(file=FLOW,boundary='F12 maintenance controller/store S-node current terminal semantics',controller_message='approved minimal extension'),
    operations=file_operations))
output('instrumentation-allocation.md', '''# FC02 計装の既存/不足と担当

| 区分 | 実在file/境界 | 処置 | owner / evidence / case |
|---|---|---|---|
| 既存 | daily_pipeline.py、orchestrator.py、trace.pyのSQLite分類/error_name/constraint/transaction/proposed/persisted/capture tuple/hash | 既存読取を再利用。日次側同内容の再追加をしない | FC03 read-only / FC01 failure map / FCT02/12 |
| 不足I-01/02 | diagnostic_trace.py require_written/audit_maintenance_stage、CLI main、provider stage dispatch | logger失敗で本処理を止めずsafe chain/stack/captureとoperation相関、fallback記録を追加 | FC03 / 新diff・FCT02/05・FC08 review・FC09適用・FC10新event |
| 不足I-03 | ai_adapter.py PARENT_SETTINGS/transport/parseとproduction planning author捕捉 | parent B highとtransport/timeout/cancel/empty/malformed区別、actual AI IDs、原因連鎖を残す | FC03+FC04 / FCT02/03 |
| 不足I-04/05 | store/safe_file/paths/failure_journal新module | DB・artifact保存/hash・通常trace/fallback自身の失敗を個別operationへ結ぶ。独立spoolとintent→artifact→DB復帰照合 | FC03 / FCT05/10 |
| 不足I-06 | production_workflow/controller/stage_handoff新module | 通常/不正/空/失敗の内容あるhandoff、次担当が実際に入力版を読んだ起動/戻り/保存証拠 | FC04 / FCT01/03/04 |
| 不足I-07 | workflow_reconciliation/store/worker | planning/report running孤立をraw改変なく追記中断観測、同一job再開、flow/incident分離 | FC05+FC10 / FCT10/11 |
| 不足I-08 | deployment/runtime_switcher/CLIとGit境界 | 適用/未反映/失敗・remote・現用/対象/適用SHA・schema read-backを実測 | FC05+FC09 / FCT06/07/11 |
| 不足I-09 | reporting/production report runner/report_fallback | 平易最小HTML、実作者/独立reviewer/修正、exact版とpush/DB途中中断reconcile | FC06+FC10 / FCT08/09/11 |

同一10/17 jobに渡す具体候補は今回FC03のmaintenance操作相関・例外連鎖・writer fallbackの実diff。先行実装済みなら同一jobがcandidate版を実検証・製品review・正式反映して新eventを残す。外側作成差分をjob新規作成と偽らない。既存日次分類の重複追加、既知不足の先送りは行わない。実装・試験・製品review・適用・same-job eventのいずれもFC02現在は未実施。
''')

spec_append = '''\n\n## FC02 全工程完遂・失敗追跡の現行契約（2026-10-19）

要件正本HTML 2.50 §27.18と承認実行計画1.2のFC-R01～10/FC-A01～10を局所対応として追加する。既存MR/MAを新規承認IDへ置換しない。旧U05～U23のblocked/approval/worktree/incident resolved結合は旧仕様履歴であり、現行は次の契約を使う。

- `stage_progress=pending/running/completed`、`operation_result=succeeded/failed/not_performed`、`content_result=confirmed/unknown/partial/no_change`、`artifact_origin=agent/controller_fallback`を分離する。新規stageにblockedを返さず、raw旧履歴は改変しない。
- 全stageは内容ある成果/handoffを保存し次専門担当を実起動する。FAIL/unknown/review/test/readiness/release/receipt/lease/回数/実設定観測で後続を止めない。個別操作不能だけ理由付き未実施とする。
- `workflow_completion`、公開結果と版、`incident_resolution`、元daily結果は別に保持する。原因/業務回復unknownのままflow完遂可能だが、公開・必須成果永続登録・終端read-backなしでcompleteにしない。job completeからincident resolvedを推論しない。
- 全失敗はoperation、cause/context連鎖と安全なstack frames、発生/捕捉位置、code revision、相関ID、input/output refsへ結ぶ。例外本文・SQL/値・秘密/個人情報は除外する。通常logger失敗は独立fallback spoolへ保存して本処理を続け、復帰時に追記照合する。begin-only中断と全sink喪失区間は検出時にunknown/abnormal_endを追記する。
- 通常/最小HTMLとも実reporterと独立reviewerを起動し、指摘は原作者が修正する。本文は平易に、技術記号は付録へ置く。公開版/hash/review/commit/remote/DB/finished_atを同一jobで照合する。テストコードの独立reviewは行わない。
- 固定workspace `C:/project/rakuten-room-inago`、10/17同一job/launch/null run、10/19除外、全role effort highを維持する。model/tier実観測欠測を起動gateにしない。schema13がFC01実測、014はsource追加予定で本番未適用。FC02は文書とrole設定だけを同期し、製品成功/受入PASSとはしない。

詳細field・責務・case・所有は `worklogs/projects/maintenance/flow-completion-20261019/FC02/` のcontract-decision、stage-output-matrix、schema-change-list、file-operation-owner-map、requirement-step-case-mapを参照する。FC03～06は実装と同じSTEPで本文/schema/skills/図の変更を同期する。
'''
for name in specs:
    rel='docs/specifications/inago/'+name
    text=(ROOT/rel).read_text(encoding='utf-8')
    if name.startswith('AT-MAINT'): text=text.replace('正本：要件定義書HTML 2.49 §27.17。','正本：要件定義書HTML 2.50 §27.17～27.18。')
    write(rel,text.rstrip()+spec_append)
write(LOGDOC,(ROOT/LOGDOC).read_text(encoding='utf-8').rstrip()+spec_append+'''

### FC operation event追加項目

| 記録 | 必須の意味 | 出力先/復旧 |
|---|---|---|
| maintenance operation | job/invocation/stage/attempt/operation/parent/event、begin/end/error、code/時刻、入力/成果物参照 | 通常diagnostic/store、独立failure journal。sourceはFC03予定、本番現在schema13 |
| error relation | 元例外class/cause/context/安全stack、発生と捕捉の別、sink failure、最後の成功操作 | SQL/例外本文/商品/認証/個人情報なし。公開は分類と参照だけ |
| interruption/reconcile | durable begin、検出時刻、unknown区間、artifact/hash、同一job再開位置 | append-only。元終了時刻/例外/runを推測しない |
| publication | report/review version/hash、commit/remote read-back、DB参照、terminal | push後DB前中断は公開版を再読して結び直す。未公開を完遂扱いしない |
''')

old_flow='U23の現在方針ではrelease/readiness gateで後続stageを止めません。report completeだけがjob complete/incident resolved/Worker成功へ対応し、pending/blockedはartifactを保持して未完了statusを記録します。同一起動・同一事象の確認記録は重複させず、元の日次結果を保持します'
new_flow='各工程は失敗や不明を内容ある成果に残して次担当を実起動します。工程の引継ぎ完了、操作の成否、公開結果、フロー完遂と元障害の解消を分けて記録します。必須成果・公開版・終了登録を照合して完遂を確認し、原因や回復が不明なら不明のまま保ちます。記録の失敗は独立した保存先へ残して処理を続け、同一起動の元の日次結果と旧履歴は保持します'
flow=(ROOT/FLOW).read_text(encoding='utf-8');assert old_flow in flow
write(FLOW,flow.replace(old_flow,new_flow))
task=(ROOT/TASKFLOW).read_text(encoding='utf-8')
task=task.replace('正常日を最小checkで終え、異常だけを別担当の調査・修正・反映・報告へつなぎます。', '同じ案件で内容ある成果を次担当へ渡し、失敗を記録しながら調査から報告の公開と終了登録まで進みます。')
# Existing flowRoles overwrites desc; preserve the F12 contract in its effective description too.
task=task.replace('flows.sort((a,b)', "flows.find(flow => flow.id === 'F12').desc += '工程の完遂、操作の成否、公開、元障害の解消を別々に記録し、原因不明でも成果を渡して次担当を実起動します。';\nflows.sort((a,b)")
write(TASKFLOW,task)

html=(ROOT/HTML).read_text(encoding='utf-8')
match=re.search(r'(<script id="requirements-source" type="application/json">)(.*?)(</script>)',html,re.S);assert match
doc=json.loads(match.group(2))
doc['source']=doc['source'].replace('親A・Sol/xhigh設定','親A・Sol/high設定').replace('親B・Luna/max/Fast設定','親B・Luna/high設定（tier実値は別観測）')
doc['source']=doc['source'].replace('原因unknownの修正を確認/保留に限定する。','原因unknownでも有用なtrace/log計装を具体化し、確認済みでない原因修正と区別する。')
doc['source']=re.sub(r'report.status=completeだけをjob complete・incident resolved・Worker成功として扱う。.*?これらのstatusは次工程のgateにしない。', '工程の引継ぎ完了、操作の成否、公開結果、workflow完遂とincident解消を別々に記録する。旧report pending/blockedはraw履歴として保持し、将来stage結果へ伝播しない。内容ある通常またはcontroller fallbackを保存して次担当を実起動し、必須成果・公開版・終端read-backが揃った時だけflow完遂とする。原因/業務回復unknownをincident resolvedへ変えない。',doc['source'])
doc['source']=re.sub(r'親Aはgpt-6.1-sol/xhigh/defaultでFastを無効にする。親Bはgpt-6-luna/maxでFast.*?親sessionの実接続LC01/LC02はU18で確認する。','親Aはgpt-6.1-sol/high、親Bはgpt-6-luna/highを採用設定候補とし、tierの実利用値は別に観測する。指定設定が使えない場合もhigh指定可能なworker/default経路で同じ専門promptを実起動し、実環境の拒否結果と設定観測欠測を記録する。未知のmodel対応やtierを推測せず、設定・receipt・session欠測を後続起動gateにしない。実接続の過去LC01/LC02は当時の対象版に限定する。',doc['source'])
doc['source']=doc['source'].replace('artifact保存後にjob completeへ進み、同じtransactionでreport status complete/pending/blockedをincident status resolved/in_progress/blockedへ反映する。','artifact保存後に公開版とDBの参照・終了登録を照合し、workflow_completionとincident_resolutionを別に保存する。元障害の解消根拠がなければunknown/unresolvedを保持する。')
source_add='''### 27.18 自動不具合対応を同じ案件の最後まで進める

この節は2026-10-19の現行契約です。本人一人のローカルシステムとして、前工程の失敗や原因不明、証拠不足、reviewや試験の結果、receipt、lease、回数を理由に後続担当を止めません。唯一の作業場所は C:/project/rakuten-room-inago です。全担当のreasoning effortはhighとし、実際のmodelやtierを確認できない場合は未観測と記録して担当を実起動します。

各工程は、何を調べたか、何が分かったか、何が分からないか、次に何をするかを含む成果物を必ず作ります。通常担当が失敗した場合はcontrollerが内容のある代替成果を保存して次担当へ渡します。代替成果を担当AIの成功と表現しません。今後の工程結果にblockedを使わず、過去のblockedと元の日次結果はそのまま残します。

工程の引継ぎ完了、操作の成否、試験とreviewの実結果、反映と公開、フロー全体の完遂、元の障害の解消を別々に記録します。原因や業務回復が不明でも同じ案件のフローを完遂できます。必須成果物の永続参照、実担当による処理、最終HTMLの公開版とreview版、Git公開先、管理DBと終了時刻を照合して完遂を確認します。job完遂を理由にincidentを解決済みに変えません。

失敗記録には操作名、例外の種類と原因の連鎖、安全な呼出位置、発生と捕捉の場所、実行版、相関番号、入力と成果物の参照を含めます。秘密、個人情報、SQLと値、例外本文は記録しません。通常ログの保存に失敗した場合は独立保存先へ記録して元処理を続け、復帰時に同じ相関番号で追記整合します。中断して記録できなかった範囲は不明と明記します。既存の日次SQLite分類を重複追加せず、保守工程の操作相関と独立保存の不足を今回実装します。

報告担当と別のreviewerを実起動し、指摘は報告担当が修正します。通常生成に失敗しても、何が起きたか、調べた範囲、変更内容、不明な点、次の行動を平易な最小HTMLにまとめ、reviewと公開を続けます。内部IDやコードは参考付録に置きます。テストコードの独立reviewは行いません。

今回の実行は2026-10-17の同じjobとlaunchを使い、source_run_id=nullを保ちます。10/19のqueue/history処理、通常Dailyの手動起動、新jobへの代替、日次runの捏造、不足下書きの追生成、別workspaceを禁止します。詳細のFC-R01～10/FC-A01～10、工程とcaseの対応は承認実行計画1.2とFC02成果物に記録します。文書契約の同期だけを実装・試験・適用・同一jobの完遂と表現しません。

'''
assert '### 27.18 ' not in doc['source']
head=re.search(r'^## 28[.． ]',doc['source'],re.M);assert head
doc['source']=doc['source'][:head.start()]+source_add+doc['source'][head.start():]
doc['version']='2.50';doc['updated']='2026-10-19'
doc['source']+='\n\n版2.50：2026-10-19 FC02で同一jobの全工程成果・失敗追跡・公開/終端と元障害解消の分離、highと固定workspaceを現行契約へ同期した。製品実装と本番適用は後続STEPの実測で確認する。\n'
encoded=json.dumps(doc,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
write(HTML,html[:match.start(2)]+encoded+html[match.end(2):])

build=subprocess.run(['node','tools/docs/inago-requirements/build.mjs'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
output('role-high-sync-result.json',dict(identity=IDENTITY,base_commit=BASE,roles=role_results,
    changed_max_roles=[r['role'] for r in role_results if r['before_effort']=='max'], actual_runtime_observation='unknown',
    parent_b_product_setting='ai_adapter.py PARENT_SETTINGS remains max until FC03; not changed by FC02',
    fc02_planner_launch='actual worker/default child assigned high; runtime model/tier not observed',
    launch_gate='none; use high-capable worker/default when fixed specialist role cannot request high'))
output('synchronization-evidence.json',dict(identity=IDENTITY,started_at=START,finished_at=datetime.now(timezone.utc).isoformat(),
    base_commit=BASE,before_sha256=before,after_sha256={p:sha(p) for p in owned},
    command='node tools/docs/inago-requirements/build.mjs',exit_code=build.returncode,stdout=build.stdout,stderr=build.stderr,
    flow_change=dict(file=FLOW,boundary='F12 maintenance controller / store S-node',old=old_flow,new=new_flow),
    product_tests='NOT_RUN in FC02',production_mutations='none',
    command_failure='PowerShell brace expansion syntax was rejected before any read/write; corrected to explicit directories'))

# Preserve the approved plan as an input snapshot rather than inventing another execution plan.
(OUT/'plan.md').write_bytes(original_plan_bytes)
artifacts=[str((OUT/n).relative_to(ROOT)).replace('\\','/') for n in outputs if n not in ('result.json','handoff.json')]
result=dict(schema_version='fc02-contract-result-v1',identity=IDENTITY,step='FC02',result_version='fc02-contract-v1',
    status='completed_with_unknowns',stage_progress='completed',artifact_origin='agent',base_commit=BASE,
    started_at=START,finished_at=datetime.now(timezone.utc).isoformat(),artifacts=artifacts,
    product_implementation='not performed in FC02',product_tests='NOT_RUN',product_review='not performed in FC02',
    deployment='not performed in FC02',same_job_execution='not performed in FC02',
    build=dict(command='node tools/docs/inago-requirements/build.mjs',exit_code=build.returncode),
    unknowns=['original root cause/business recovery/post count','actual runtime model/effort/tier','FC03–06 final schema storage implementation','new failure-injection pytest nodes pending FC07'],
    review=dict(required=True,status='not_yet_observed',reviewer='separate FC02 independent reviewer'),
    next=dict(step='FC03',operations=['implement missing operation trace and independent failure journal','parent B product settings high','non-gating writer and safe cause-chain capture'],start_gate='none'),
    git=dict(staged=False,committed=False,pushed=False,owner='controller FC09'),
    owned_files=owned+artifacts+['worklogs/projects/maintenance/flow-completion-20261019/FC02/execute_contract_sync.py'])
output('result.json',result)
output('handoff.json',dict(schema_version='fc02-handoff-v1',identity=IDENTITY,step='FC02',stage_progress='completed',
    artifact_origin='agent',result_version='fc02-contract-v1',base_commit=BASE,operation_results=[
      dict(operation='contract/role/document synchronization',result='succeeded'),
      dict(operation='requirements builder',result='succeeded' if build.returncode==0 else 'failed',exit_code=build.returncode),
      dict(operation='product changes/tests/deploy/job-resume',result='not_performed',reason='FC02 scope; assigned FC03–FC10')],
    output_refs=[dict(path=p,sha256=sha(p)) for p in artifacts],unknowns=result['unknowns'],
    next=result['next'],review=result['review'],notes=['raw history preserved','schema13 is observed; 014 source planned only','original approved plan remains execution authority']))
print(json.dumps(dict(step='FC02',builder_exit=build.returncode,roles_high=len(role_results),artifacts=len(artifacts),owner_entries=len(file_operations)),ensure_ascii=False))
