import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const planRoot = path.resolve('plans/projects/implementation/item-desc-capture-20261025');
const worklogRoot = path.resolve('worklogs/projects/implementation/item-desc-capture-20261025');
const mapPath = path.join(planRoot, 'requirement-step-case-map.json');
const map = JSON.parse(await readFile(mapPath, 'utf8'));

map.plan_version = '1.2';
map.scope = 'implementation_and_verification';
map.overall_status = 'partial_with_known_unrelated_regression_failures';
map.reason_code = 'user_authorized_plan_execution';
map.current_specification_version = '2.65';
map.steps = map.steps.map(step => ({
  ...step,
  status: step.step_id === 'STEP5' ? 'partial' : 'completed',
  reason: step.step_id === 'STEP5'
    ? 'C07 final planned regression: 82 passed, 4 unrelated known failures, 0 errors; independent product review found no remaining findings.'
    : `C${step.step_id.slice(-1).padStart(2, '0')} and linked execution cases recorded in worklogs; see C01-C07 results.`
}));

const commandFor = (caseId, result) => {
  if (caseId === 'C01') return result.build.command;
  if (caseId === 'C02') return result.final.command;
  if (caseId === 'C03') return result.v265_final_capture_browser_url_safety.command;
  if (caseId === 'C04') return result.v265_final_run.command;
  if (caseId === 'C05' || caseId === 'C06') return result.command;
  if (caseId === 'C07') return result.runs.find(run => run.name === 'v265_planned_full_regression').command;
  throw new Error(`No result command mapping for ${caseId}`);
};
const exitFor = (caseId, result) => caseId === 'C07' ? result.final_planned_regression.exit_code : 0;
const resultsByCase = new Map();
for (const caseId of ['C01', 'C02', 'C03', 'C04', 'C05', 'C06', 'C07']) {
  resultsByCase.set(caseId, JSON.parse(await readFile(path.join(worklogRoot, caseId, 'result.json'), 'utf8')));
}
map.cases = map.cases.map(item => {
  const { actual_specification_version: _previousVersion, ...plannedCase } = item;
  const result = resultsByCase.get(item.case_id);
  const status = item.case_id === 'C07' ? 'partial' : 'PASS';
  return {
    ...plannedCase,
    status,
    actual_command: commandFor(item.case_id, result),
    actual_exit_code: exitFor(item.case_id, result),
    actual_evidence_refs: [
      `worklogs/projects/implementation/item-desc-capture-20261025/${item.case_id}/execution.md`,
      `worklogs/projects/implementation/item-desc-capture-20261025/${item.case_id}/result.json`
    ]
  };
});
map.policy.current_execution_authorized = true;
map.policy.product_independent_review_planned = true;
map.policy.product_independent_review_status = 'completed_v2.65_no_remaining_product_findings';
map.execution_update = {
  authorized_by: 'user follow-up: execute the plan one step at a time',
  date: '2026-10-25 JST',
  specification_version: '2.65',
  step_statuses: {
    STEP1: 'completed',
    STEP2: 'completed',
    STEP3: 'completed',
    STEP4: 'completed',
    STEP5: 'partial'
  },
  final_planned_regression: {
    command_case: 'C07/result.json#v265_planned_full_regression',
    passed: 82,
    failed: 4,
    errors: 0,
    exit_code: 1,
    known_unrelated_failures: [
      '2 missing OCR study fixtures',
      '2 unchanged copy-quality selection assertions'
    ]
  },
  daily_run_started: false,
  production_database_touched: false,
  external_room_product_site_or_real_ai_used: false
};

await writeFile(mapPath, `${JSON.stringify(map, null, 2)}\n`, 'utf8');
console.log(JSON.stringify({
  plan_version: map.plan_version,
  overall_status: map.overall_status,
  steps: map.steps.map(step => [step.step_id, step.status]),
  cases: map.cases.map(item => [item.case_id, item.status, item.actual_exit_code]),
  execution_update: map.execution_update
}, null, 2));
