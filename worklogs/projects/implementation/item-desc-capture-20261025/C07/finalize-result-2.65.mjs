import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';

const root = path.resolve('worklogs/projects/implementation/item-desc-capture-20261025');
const resultPath = path.join(root, 'C07/result.json');
const result = JSON.parse(await readFile(resultPath, 'utf8'));
if (result.case_id !== 'C07') throw new Error('Unexpected worklog case');

const safeRoot = String.raw`$privateFixtureRoot = Join-Path $env:TEMP 'inago-safe-fixtures-20261025-step5-v265'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) | Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot;`;
const c07Root = String.raw`$privateFixtureRoot = Join-Path $env:TEMP 'inago-c07-final-20261025-step5-v265'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) | Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot;`;
const finalRegressionCommand = `${safeRoot} .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_inputs.py tests_inago/test_product_page_url_safety.py tests_inago/test_product_ocr.py tests_inago/test_product_page_copy.py tests_inago/test_product_page_selection.py tests_inago/test_product_page_cleanup.py tests_inago/test_copy_quality_review.py tests_inago/test_copy_quality_selection.py --basetemp $pytestTempRoot`;
const captureCommand = `$privateFixtureRoot = Join-Path $env:TEMP 'inago-item-desc-capture-final-20261025-v265'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) | Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_item_desc_browser.py tests_inago/test_product_page_url_safety.py --basetemp $pytestTempRoot`;
const integrationCommand = `${c07Root} .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_copy.py::test_item_desc_capture_to_ocr_to_copy_inputs tests_inago/test_product_page_selection.py::test_resume_reuses_selected_registry_and_completed_ocr tests_inago/test_product_page_cleanup.py::test_resume_cleans_committed_images_without_regenerating_copy --basetemp $pytestTempRoot`;

const addedRuns = [
  {
    name: 'v264_deadline_and_invalid_percent_red',
    command: "$privateFixtureRoot = Join-Path $env:TEMP 'inago-item-desc-red-20261025'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) | Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_invalid_percent_escape_in_data_uri_is_not_saved_as_downloaded tests_inago/test_product_page_capture.py::test_animation_frame_waits_share_the_finite_observation_deadline --basetemp $pytestTempRoot",
    exit_code: 1,
    passed: 0,
    failed: 2,
    duration: '0.85s',
    observation: 'Invalid %ZZ was downloaded; unbounded rAF left unsettled top-level await and Node exited 13.'
  },
  {
    name: 'v264_deadline_and_invalid_percent_green',
    command: "$privateFixtureRoot = Join-Path $env:TEMP 'inago-item-desc-green-20261025'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) | Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_invalid_percent_escape_in_data_uri_is_not_saved_as_downloaded tests_inago/test_product_page_capture.py::test_animation_frame_waits_share_the_finite_observation_deadline tests_inago/test_product_page_capture.py::test_unresolved_lazy_image_is_marked_incomplete_after_finite_wait --basetemp $pytestTempRoot",
    exit_code: 0,
    passed: 3,
    failed: 0,
    duration: '5.86s'
  },
  {
    name: 'v265_uppercase_data_scheme_red',
    command: "$privateFixtureRoot = Join-Path $env:TEMP 'inago-data-scheme-red-20261025'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) | Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_uppercase_data_scheme_is_decoded_case_insensitively --basetemp $pytestTempRoot",
    exit_code: 1,
    passed: 0,
    failed: 1,
    duration: '0.76s',
    observation: 'DATA: was classified as data but rejected by the decoder.'
  },
  {
    name: 'v265_uppercase_data_scheme_green',
    command: "$privateFixtureRoot = Join-Path $env:TEMP 'inago-data-scheme-green-20261025'; $pytestTempRoot = Join-Path $privateFixtureRoot 'pytest'; [System.IO.Directory]::CreateDirectory($pytestTempRoot) | Out-Null; $env:INAGO_SAFE_FIXTURE_BASE = $privateFixtureRoot; .venv-inago/Scripts/python.exe -m pytest -q tests_inago/test_product_page_capture.py::test_uppercase_data_scheme_is_decoded_case_insensitively --basetemp $pytestTempRoot",
    exit_code: 0,
    passed: 1,
    failed: 0,
    duration: '0.65s'
  },
  {
    name: 'v265_capture_browser_url_safety',
    command: captureCommand,
    exit_code: 0,
    passed: 19,
    failed: 0,
    duration: '18.21s',
    browser: 'Chromium local synthetic fixture'
  },
  {
    name: 'v265_ocr_copy_resume_cleanup_and_evidence_refresh',
    command: integrationCommand,
    exit_code: 0,
    passed: 3,
    failed: 0,
    duration: '12.40s'
  },
  {
    name: 'v265_planned_full_regression',
    command: finalRegressionCommand,
    exit_code: 1,
    passed: 82,
    failed: 4,
    errors: 0,
    duration: '100.83s',
    failed_nodes: [
      'tests_inago/test_product_ocr.py::test_real_engine_uses_runtime_assets_and_handles_japanese_image_path',
      'tests_inago/test_product_ocr.py::test_study_fifty_nine_images_round_trip_through_product_format',
      'tests_inago/test_copy_quality_selection.py::test_information_shortage_is_replaced_and_remaining_products_continue',
      'tests_inago/test_copy_quality_selection.py::test_resume_never_regenerates_or_reselects_information_shortage'
    ],
    repeated_from_v264: true
  }
];
const addedNames = new Set(addedRuns.map(run => run.name));
result.runs = result.runs.filter(run => !addedNames.has(run.name)).concat(addedRuns);
result.status = 'PARTIAL_WITH_KNOWN_UNRELATED_REGRESSION_FAILURES';
result.specification_version = '2.65';

const hashFile = async relative => createHash('sha256').update(await readFile(relative)).digest('hex');
result.evidence = {
  page_manifest: { path: 'C07/evidence/page.json', sha256: await hashFile(path.join(root, 'C07/evidence/page.json')) },
  ocr_payload: { path: 'C07/evidence/ocr.json', sha256: await hashFile(path.join(root, 'C07/evidence/ocr.json')) },
  job_input: { path: 'C07/evidence/job-input.json', sha256: await hashFile(path.join(root, 'C07/evidence/job-input.json')) }
};
result.code_sha256 = {
  'automation/inago/adapters/product_page_inputs.mjs': await hashFile('automation/inago/adapters/product_page_inputs.mjs'),
  'automation/inago/prompt_builder.py': await hashFile('automation/inago/prompt_builder.py'),
  'automation/inago/ai_runner.py': await hashFile('automation/inago/ai_runner.py'),
  'tests_inago/test_product_page_capture.py': await hashFile('tests_inago/test_product_page_capture.py'),
  'docs/specifications/inago/イナゴスキル要件定義書.html': await hashFile('docs/specifications/inago/イナゴスキル要件定義書.html'),
  'docs/specifications/inago/要件データ辞書.md': await hashFile('docs/specifications/inago/要件データ辞書.md'),
  'docs/specifications/inago/要件追跡表.md': await hashFile('docs/specifications/inago/要件追跡表.md')
};
Object.assign(result.assertions, {
  lazy_scroll_and_animation_waits_share_one_2500ms_deadline: true,
  invalid_non_base64_percent_escape_rejected: true,
  uppercase_data_uri_scheme_decoded_case_insensitively: true,
  item_desc_page_ocr_copy_inputs_rechecked_under_v265: true,
  planned_full_regression_v265_completed_with_four_known_unrelated_failures: true
});
result.independent_product_review = 'completed_v2.65_no_remaining_product_findings';
result.independent_product_review_scope = 'Six prior findings and uppercase DATA: boundary reviewed read-only; reviewer did not run tests or review test code.';
result.final_planned_regression = {
  specification_version: '2.65',
  passed: 82,
  failed: 4,
  errors: 0,
  exit_code: 1,
  failed_nodes: addedRuns.at(-1).failed_nodes
};

await writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
console.log(JSON.stringify({
  specification_version: result.specification_version,
  status: result.status,
  evidence: result.evidence,
  code_sha256: result.code_sha256,
  final_planned_regression: result.final_planned_regression,
  independent_product_review: result.independent_product_review
}, null, 2));
