# C30 — 保存済み商品URLからの再取得

- 期待: 保存済み商品URLから正しいrun/shop/item identityで商品ページを再取得し、ROOM再検索なしでcapture済みartifactを得る。capture_failedは成功artifactに置き換わる。
- focused command: `.venv-inago/Scripts/python.exe -B -m pytest -q tests_inago/test_step21_browser.py::test_product_page_can_be_recaptured_without_repeating_room_search`
- focused result: 1 passed in 1.20s / exit 0。
- related command: `.venv-inago/Scripts/python.exe -B -m pytest -q tests_inago/test_product_page_capture.py tests_inago/test_product_page_selection.py`
- related result: 14 passed in 35.98s / exit 0。capture retryを含む。
- final regression: plan v1.23の79 selectors / 83 expanded testsに対象nodeを含め、83 passed in 174.15s / exit 0。manifest: `final-regression-v1.23-20261024.json`。
- exact wall-clock times of the earlier focused commands were not retained. No product/test source changed after those focused passes; the current-source final regression is recorded separately.
- product/test hashes at final regression: `automation/inago/adapters/public_sources.mjs` `e56313c9768ae40325b2ec6ccd486bdc615087d4984469853288e2d1bf10395c`; `automation/inago/product_page_inputs.py` `6523144a82eaf3ebd3218d1f440a13f4802fd8ed3dc9f527db7372e63d9832cd`; `tests_inago/test_step21_browser.py` `b0d1ff5f5a8de5fcbc53afe5ba2f70594d914894e27e14d9c65d35fff75e5007`; `tests_inago/test_product_page_capture.py` `12d50bb22ae49e5c84c09419c0f88495f0b1034bc41c8eb9d4eead21c3f71772`.
- evidence: `final-regression-v1.23-20261024.json`; full output `final-regression-v1.23-20261024.txt`.


## v1.24 final regression

- Current-source planned regression manifest `final-regression-v1.24-20261024.json` includes the C30 saved-URL recapture node and failed-capture retry node. The full planned set collected 79 selectors / 83 expanded tests and passed 83/83 in 170.61s / exit 0.
- The result is fixture evidence for those capture/retry nodes. It does not establish completion of the official 10/23 run. At 03:41 JST, the official run was still healthy and active; its process had loaded v1.23 before the v1.24 checkpoint fix was applied.
- Evidence: `final-regression-v1.24-20261024.json`, `final-regression-v1.24-20261024.txt`, `formal-run-status-20261024-0341.json`.
