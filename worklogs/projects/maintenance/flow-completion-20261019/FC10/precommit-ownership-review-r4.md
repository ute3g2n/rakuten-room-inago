# FC10 pre-commit ownership review R4

- Branch: `main`; pre-commit HEAD: `e499088bd065ee5a0fb16b5a3a269cc22ba6dae0`; upstream: `origin/main`.
- Cached before selection: 0 files.
- Selected tracked files: 6.
- Task-owned untracked evidence before this audit: 58.
- Selected untracked evidence (before audit files): 48.
- Excluded raw pytest files with local profile paths: 10.
- Sensitive scan: changed tracked additions and full untracked candidates have no email, private-key or credential-assignment hits; only ten raw profile-bearing logs are excluded.
- Three reserved-example email-shaped fixtures in the test source are unchanged from HEAD and outside this task diff.

## Explicit tracked selection

- `automation/inago/maintenance/production_workflow.py` (`e997bf52a2ec7b476bbf932897ece73fe7a71511e24374963e0d15a21353f30b`)
- `automation/inago/maintenance/workflow_reconciliation.py` (`47159e8eaa55fb41fa43f63f7bb3443a75efb0c37ccf16cc721ab21913d32556`)
- `plans/projects/maintenance/自動不具合対応フロー完遂と全工程追跡_実行計画_2026-10-19.md` (`c4845f864f2dd20f0c2add36b12388cc97c72cd8875e002c58ca0118cc75cc52`)
- `tests_inago/test_maintenance_report_publication_stage.py` (`efbd483fe73002ac442c8465aaec7632438c789b24177f3a2e66e3a7b67a1716`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC04/file-operation-owner-map.json` (`78d067d2b4e5f824ba2cfc3c155c092d593697c87e09bc82cf5cc1a8ff3869e8`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/controller-read-operation-failures.json` (`ce46c29235f5bda45fec44175f4dbb44f1df1c271a9c3b1d089e4331dfcdf940`)

## Excluded raw files

- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round5-focused-green.console.txt`
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round5-focused-green.junit.xml`
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round6-focused-green.console.txt`
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round6-focused-green.junit.xml`
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round7-focused-green.console.txt`
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round7-focused-green.junit.xml`
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/handoff-accepted-terminal-red.console.txt`
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/handoff-accepted-terminal-red.junit.xml`
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/handoff-waiver-red.console.txt`
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/handoff-waiver-red.junit.xml`

## Selected untracked evidence before this audit

- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round3-focused-green.console.txt` (`86bd51ef41ffe7c94a4f9d00adb660290dfddc2b69af87e4e907ea6934e5f55e`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round3-focused-green.junit.xml` (`5b5dfc17037b0206a7f6f702114e79b366b715d8ceb8fe18c51b648705ab0252`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round4-focused-green.console.txt` (`764a31e6515340e06f5a9893cf27f1dd4f4f496f6511e4073d47e7d2810a4557`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round4-focused-green.junit.xml` (`f9b6b4a6924f122acc3e3f94633e0d67b6ec05b25d9e808c57c59170088cd741`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round4-static-checks.json` (`9c07683656b6f6da2c2fdd33ec214ac3b88c285b459365562c6089b4f7962608`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round8-focused-green.console.txt` (`622ce544f3c1cff5413aacd70159582313ce0d48009c9e839c6762382b80a726`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round8-focused-green.junit.xml` (`ae7317e9aec495e271c815d24a5acc3e0e133fd29bdeebfeda10e8bc2de3fb5d`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round8-static-checks.json` (`1d649406c9a185210da938bed9f3cd4e92e9be39ed1d9e451e1c1c0731860b39`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round9-focused-green.console.txt` (`4598e1f03d8fb33f47a9278a6a3382f9bbd89c33397fae77747377f13bd3bb4e`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/fc10-round9-focused-green.junit.xml` (`09f9c4d9b105ef158117a99905fc1e1a3ad4ccd944bb66f44450c3cc2614cbb6`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/handoff-accepted-terminal-green-focused.console.txt` (`99b35867a991bd4fce4dc26daa0ad227614a123abde79bc1252b1b28bee2cbfa`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/handoff-accepted-terminal-green-focused.junit.xml` (`93a9a1f63dc15863e4ef6602f1497b5892307fa9e3bef901798eaec28729c2e9`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/precommit-review-evidence-r4.json` (`160e2ce8c55be3c96aa8888e410c285c9854d4381fe222be877dcc2dfe609424`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/precommit-review-evidence-r4.md` (`d0248dcff3cfcd5e994b596a4300182dc707b304feed4e2cdb1fee8efd71ddf2`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-execution-evidence.json` (`dfc831886b402edab3bcb5adc82be522a30fac78add37266df094650232433c4`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/fc10-round5-focused-green.console.txt` (`ff322964ff3fb73f390dd3d854f000a9e6aad2f7347c8fe7613fb480aa1899f7`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/fc10-round5-focused-green.junit.xml` (`f7b3d78189a1bd296748df7c1873a40a1bca4533f51524a54d1cbb7d62a24c8d`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/fc10-round6-focused-green.console.txt` (`7c88c2d9b80bea1bcf7e41e4a5f9e304cb1a6c8b17a8231e18cdefb45e0d96c6`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/fc10-round6-focused-green.junit.xml` (`409a5a00b8d31d130dedcbad04f011ecd6e3d57d6984b2a781ff009111040a9d`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/fc10-round7-focused-green.console.txt` (`fb3c74cf6e67c3f1bcc450d624d1cdf6385ba2d5b995fcef974a84c1547b5925`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/fc10-round7-focused-green.junit.xml` (`bfa3c7ca211f6017034ee63b5877a2444942adc7d8b2bcd1c949e5173a8d57c1`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/handoff-accepted-terminal-red.console.txt` (`bf0b2a0bb5a0837161c7bfe7103c1cbc921a2ec0d7c51acfab758e1a23e16688`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/handoff-accepted-terminal-red.junit.xml` (`0c761d1125f68cdccaa72271fab385d57e5ed29a5f4bf8b653be7f9358a3428d`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/handoff-waiver-red.console.txt` (`dd31f82a89d40bf525f1d71fd4172b7a8aabe1f5378cad8a5e5fe5b6036529ed`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/handoff-waiver-red.junit.xml` (`21f1edbac2b26f21cc57350459b4a36ed309a064b52aead3ae67e33735d84527`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/report-prompt-path-red.console.txt` (`78fb630ec388b96513b0daebe78051fa0d65fa35e47a4977fbb89af31186b297`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/redacted-public/report-prompt-path-red.junit.xml` (`d959e82b353c7e5cc54d0530cf229bd83517f476a2a118cf2e5fe3cef62322c3`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/report-prompt-path-red.console.txt` (`78fb630ec388b96513b0daebe78051fa0d65fa35e47a4977fbb89af31186b297`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/report-prompt-path-red.junit.xml` (`d959e82b353c7e5cc54d0530cf229bd83517f476a2a118cf2e5fe3cef62322c3`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/resume14-unaccepted-controller-fallback.html` (`421c1d5bf69cb5371b9fa2984abf66a17eec8650e1762cf8371193d5b8098dcf`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/runtime-apply-85b38ce.json` (`57107d182511589a4fb88af34e80285f8bc8f79e537a1b97d561cd25c9ab95e3`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/same-job-resume-14-attempt-runner.json` (`55a1fb4f9d7777bd7a1e2e341519f6905ef30b55beaade22c3148fb030ff55a7`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/same-job-resume-14-readback.json` (`08e5c963338819c0c07ae1545f9d879659471fe7fab65b41da3a1c0c4cb02d75`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/same-job-resume-14-resume.console.txt` (`74062b4a11ecd2375c4d0d180ebcf54c9daa15e29d09aaaae9f0b13146b7b631`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/same-job-resume-14-terminal.json` (`c325b3a372c4cca5706e0641885d0dcdf5f1c021cc90a8eadfbae8f11cf6829c`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/same-job-resume-14-worker.console.txt` (`f2a229f7922f7ec63c0252cec08e3d85de1166ada5a8828ca6cd4af1d1508150`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-fix-review-r4-addendum.md` (`bf7b73e0a4b8928b8ce21e1aa5d814f6ad83b4cb5112fe8efd2b8f93302fa2a6`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-fix.md` (`c9b06efef7cfb10cfa31c9ce71a3509c89d8812fbc7b646bf43a5d062df33d81`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-review-r2.json` (`26934bb4da466dbb85a68548415413cc7e786eb00a1141c6fffbbf04776ca210`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-review-r2.md` (`4763a53629d1d29094611d6ec0cf156efb861af2adc51142727577c188f40aa4`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-review-r3.json` (`ec1e7f1a5af26059ced2f06d292ba31bde6056d24021b3ca62052a8aead583c5`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-review-r3.md` (`46aff6b9967aa145961430f3fad8b31b3dc702f74edd79da0df5d3d4478668af`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-review-r4.json` (`c6f499d09edab1af109d2d05abc4fedb2876c48b1b02f315e644572e17ab6ab4`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-review-r4.md` (`a5d1907a8ad4411d2ca7d906867d7d76c3e255f427456fa347e79542cb901559`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-review-target-r1.json` (`f011c6492473aabb3a9ed8e6323e870705eede28320b052a73d3aa2e054d0ba1`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-review-target-r2.json` (`bb343ca4e34fc634c0fbdbcebc586470d182d2b828ea15a3dc430dfc56e4599b`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-review-target-r3.json` (`210adf60357834b8c4e12503b00c1578833d9331b3dd3ed4d3c7c32174149963`)
- `worklogs/projects/maintenance/flow-completion-20261019/FC10/terminal-handoff-and-report-evidence-path-review-target-r4.json` (`1476d687f26f60af85cb4a8103ff09a5206a72674e08d1a04e1280e7af660a5f`)

The JSON manifest records byte sizes, hashes and scan observations. This markdown and JSON audit pair are task-owned and are explicitly staged after creation.
