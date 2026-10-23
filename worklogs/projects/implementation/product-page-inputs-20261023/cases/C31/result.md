# C31 — 同一runの保存候補からの再開

- 期待: capture失敗済みの選択商品を再取得し、必要枠だけを永続化されたeligible候補から補充する。ROOM検索をやり直さず、run IDと商品identityを維持する。
- focused command: `.venv-inago/Scripts/python.exe -B -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_selection.py`
- focused result: 14 passed in 35.98s / exit 0。選択済みcapture retryとeligible候補の復帰を含む。
- focused recovery node（初期版）: `tests_inago/test_product_page_selection.py::test_resume_selects_a_recaptured_eligible_candidate_without_room_search` — 1 passed in 5.39s / exit 0。初回fixtureは候補folderが既に無い場合に失敗し、test setupを状態に合わせて修正後、該当nodeを再実行してpassした。
- v1.24 interruption/resume regression: 同じ完全nodeにcheckpoint書込み直後の中断を追加。失敗queueを消した時点で中断し、checkpointを再読込して再開する。期待は候補が「未選択・failure queue消失」の状態にならず、再開後にselectedとなること。TDD REDは旧実装で期待に反し再開後の回復件数0となり、1 failed/5.99s/exit 1。更新実装で同nodeは1 passed/5.87s/exit 0。
- focused C31 selection file: `.venv-inago/Scripts/python.exe -B -m pytest tests_inago/test_product_page_selection.py -q` — 9 passed in 37.10s / exit 0。
- final regression v1.24: 79 selectors / 83 expanded tests、83 passed in 170.61s / exit 0。manifest: `final-regression-v1.24-20261024.json`。
- independent product-code review follow-up: 前回MEDIUM所見は解消、新規コード所見なし。試験証跡は`code-review.md`に追記。
- 03:37 JST status: official same run `INAGO-2026-10-23` はhealthy、attempt 125、committed_count 4、owner PID 92436、heartbeat fresh/progress advancing。runは継続中。status evidence: `formal-run-status-20261024-0336.json`。
- 03:41 JST status update: same run is healthy, attempt 125, committed_count 5, heartbeat fresh/progress advancing, owner PID 92436, 11 child processes; finished_at is null. Evidence: `formal-run-status-20261024-0341.json`. Active process loaded v1.23 before this v1.24 checkpoint fix; the focused interruption/resume node and full v1.24 regression cover the change.
- current source/test hashes at final regression are in `final-regression-v1.24-20261024.json`. The pre-fix test hash at the TDD RED moment was not retained; the old product source hash is available from v1.23 manifest.
- exact wall-clock times of the earlier focused commands were not retained. No product/test source changed after those focused passes; the current-source final regression is recorded separately.
- product/test hashes at final regression: `automation/inago/orchestrator.py` `65de6ce13c1666a6ccd34b8387020bdfa508b3659e8044b960ba1caf217b66bb`; `automation/inago/daily_runtime.py` `c5dbfad24ec2b961c9f944ad50d14ecc8382aaf6b1ec1b3e3e87b3fd707b8428`; `automation/inago/product_page_inputs.py` `6523144a82eaf3ebd3218d1f440a13f4802fd8ed3dc9f527db7372e63d9832cd`; `tests_inago/test_product_page_selection.py` `0814178a69f91f051d02fdda2a689a5a80777d909d19b05dd1ad34d02c9275e6`.
- evidence: v1.23 `final-regression-v1.23-20261024.json`; v1.24 `final-regression-v1.24-20261024.json` and full output `final-regression-v1.24-20261024.txt`.

- 03:44:39 JST status update: same official run remains healthy, attempt 125, committed_count 7, heartbeat fresh/progress advancing, owner PID 92436, 11 children; finished_at is null. Evidence: `formal-run-status-20261024-0350.json`. The active process loaded v1.23 before the v1.24 checkpoint fix; this status does not establish that fix was exercised in the official process.
