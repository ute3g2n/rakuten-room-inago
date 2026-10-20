# SC08 担当Markdown実読台帳

## 根拠の扱い

- 固定対象: `job-historical-9e7f0c175cc4c874c955a5dab29c4427` / `historical-9e7f0c175cc4c874c955a5dab29c4427` / `2026-10-17` / `source_run_id=null`。
- 完了した8呼出しは `chain_started_at=2026-10-20T05:50:41.047Z` の同一chain内のrole 1–8である。終了時刻は `calls.jsonl` のrole_call_finished eventで確認した。
- 各role threadの `commandExecution` itemを `read_thread` で確認し、直前担当のMarkdownを含む固定pathの `Get-Content -Raw -LiteralPath` が `completed` / `exitCode=0` であることを照合した。成功出力の本文要約も合わせて実読事実を記録した。
- 同じ8 role eventの `previous_read_acknowledged` はrunner側でfalse、`previous_read_evidence` は空だった。このraw値は書き換えず、実thread transcriptの成功commandを補助証拠として別に残す。runner flagとの不一致は未解決の計測差として扱う。

## 同一invocation内の引継ぎ実読

| 次担当 | role thread | 読んだ前担当Markdown / SHA-256 | 実読command | 読取で得た事実 |
|---|---|---|---|---|
| 2 実装 | `01a157e8-1d05-7080-8e81-83b6e27875dd` | `investigation.md` / `526468d57d3ef4efa9df3ef6094e16f2426b280aa8e2cff872e7cf2489689c9d` | `Get-Content -Raw -LiteralPath 'C:\project\rakuten-room-inago\rakuten-room-inago\runtime\maintenance-flow\job-historical-9e7f0c175cc4c874c955a5dab29c4427\investigation.md'`; completed / exit 0 | 原因・未保存理由は不明のままで、具体的な不足として二次例外cause/context連鎖と安全な発生frameが挙げられていた。 |
| 3 試験 | `01a157ec-4d3f-7b90-a63f-7cb4dedfa8a8` | `implementation.md` / `c218173dfef5d0428a9b363c59d5594d7b5a2fcb4a8c01c85815a06076753` | 前担当Markdownを含む `Get-Content -Raw` command; completed / exit 0 | 追加差分は二次診断のcause/context関係とrepository相対frameで、一次結果・業務DBは変更しない実装だった。 |
| 4 製品review | `01a157ed-c175-73c0-a620-7011a5023627` | `test.md` / `fdfc9c22da7e8ba7a4d76d153dcd7f04a24968885396b19ef24eb7b3e04e0f52` | `Get-Content -Raw -LiteralPath '<fixed job root>\test.md'`; completed / exit 0 | テスト成果と限定範囲を確認してから製品だけを独立reviewした。 |
| 5 反映 | `01a157ef-19db-7542-9bf1-b4c4ffc2d914` | `code-review.md` / `486e1559c5be25b1c4bd00fdd08575ae3da3fe86ba377327cbacfa066d2ac23c` | skillと前担当Markdownを読む `Get-Content -Raw` command; completed / exit 0 | 独立reviewは低severity CR-01（12件上限で切詰め状態を示さない）を指摘していた。 |
| 6 レポート作者 | `01a157f4-acc9-7e61-b901-f7ca978b1533` | `deployment.md` / `015eb7b171ded92742d6519141de4196060d9766d1d969906c70cdb582c78ca4` | skillと同job入力を読む `Get-Content -Raw` command; completed / exit 0 | Git index書込・本番task readに失敗し、その時点ではcommit/pushと適用は未実施と記録されていた。 |
| 7 HTML reviewer | `01a157f6-d0ab-7a23-8da9-6093705f0bd8` | `report.md` / `1dfb53632b5862bdd31662ecd3dc8a46428cf957bc5c9c6c535dcf56abe48c6a` | `report.html`・`report.md`の `Get-Content -Raw` command; completed / exit 0 | 本文は原因unknownを保ち、診断追加を原因修正・本番適用と混同しないよう書かれていた。 |
| 8 公開 | `01a157f8-f821-74c3-9d37-1d42e0f29806` | `html-review.md` / `47977eecb40851ec05e8e39e9886c06ae338522a4c8cc49f0d214cf267586a44` | `Get-Content -Raw -LiteralPath 'rakuten-room-inago/runtime/maintenance-flow/job-historical-9e7f0c175cc4c874c955a5dab29c4427/html-review.md'`; completed / exit 0 | 当該review roundでは対象report bytesが承認され、次工程は同じbytesの公開だった。 |

上記8 roleはそれぞれ `turn_status=completed` で、時刻は2026-10-20 06:03:10Z–06:36:55Z。対応する `calls.jsonl` のoutput_refs/input_refsは各前成果hashに結び付く。

## 後続の再開観測

- 同じchainを2026-10-20T06:51:27.982Zにrole 1から再開した。role 1–7はcompletedだが、role 8は2026-10-20T07:49:35.124Zに `turn_status=failed`、runner結果は `maintenance_incomplete / skill_chain_role_call_incomplete`。`publication.md` は存在するが、role 8成功やworkflow完遂とは扱わない。
- その後、同じjobのrole 2/3/4追補でCR-01実装・境界試験・独立reviewを行い、同じjobのrole 5追補で本番適用を成功させた。更新されたreport.htmlは別reviewerの再読を待っており、旧review hashを流用しない。
- 次の再開位置は、適用後の `report.html` を別reviewerが実物で確認し、作者指摘修正があれば再review、最終review済みbytesを公開先へ同一bytesで反映・read-backする工程である。

## 同一chainの第2再開 — 2026-10-20 05:50:41Z start

この節は直前の古いchain記録を置き換えず、今回の最終8-call連鎖を追加する。`calls.jsonl`の`chain_started_at`は全callで`2026-10-20T05:50:41.047Z`、source launchは固定値、source runはnull。各role eventの`previous_read_acknowledged=false`かつ`previous_read_evidence=[]`というraw値を保持する。別途、8 role threadの実記録を読み、前担当Markdownを固定pathから`Get-Content -Raw -LiteralPath`で開くcompleted commandExecution（exit 0）を確認した。

| 次担当 | role thread | 直前Markdown / final artifact SHA-256 | 実読確認 |
|---|---|---|---|
| 2 実装 | `01a15861-7545-7922-9e87-6040efff3860` | `investigation.md` / `5e5f7163928f297e9968ea8559fe211280f4382b0288de663bede0e41c632c6f` | 固定job rootで`Get-Content -Raw -LiteralPath ...\investigation.md` completed / exit 0。原因はunknownのまま、具体的な日次差分候補を確認。 |
| 3 試験 | `01a15865-14b8-71f2-98e2-e371b7a3925d` | `implementation.md` / `4cadb6b242d0c65c0b899519a3ce5a237b0bfa7f8c978934f13d31a61ef3f9b5` | 固定job rootの`implementation.md`を読むcommandExecution completed / exit 0。日次変更を試験する内容を確認。 |
| 4 製品review | `01a1586b-8992-76c3-a12a-820fb4aab867` | `test.md` / `f896509648e3f35140f52cb47ec2dcd28fc244f4edad98ca2a08cc54b920b5d0` | 固定job rootの`test.md`を読むcommandExecution completed / exit 0。試験記録を確認してCR-01を指摘。 |
| 5 反映 | `01a1586d-e6fd-7b22-b5ae-7d8ddac41d95` | `code-review.md` / `30d3a309fa18d5a154959042b566b95a32650c56a6ff1e00e9fd1239fc4e7696` | 固定job rootのreview記録を読むcommandExecution completed / exit 0。CR-01解消を確認。deploymentにはGit index書込とScheduled Task読取の失敗、未適用を記録。 |
| 6 レポート作者 | 初回 `01a15876-38a3-72b1-ac15-9acda30f1b45`、minimal retry `01a15878-ffed-7473-93a7-bc0147187a31` | `deployment.md` / `9400641438ba926f02ffbc3e0266cfafc64a20dbee5a796e10b0fe85753f69c2`; 最終`report.md` / `28916d8ba357cdde45bf184fec1759bd892add1b3f78d6e4dbe7dab137c43215`; `report.html` / `a1275018b28c303630e78130d90a9926ded192173a2aaa68d457ef5e0bde88a8` | `deployment.md`の実読command completed / exit 0。初回reporter出力が不完全でminimal retryは`handoff_target_mismatch`となったが、作者修正出力が残り次のreviewerが起動。 |
| 7 HTML reviewer | `01a1587b-1bf2-7023-b55e-8658a0aa43c9` | 最終HTML / `a1275018b28c303630e78130d90a9926ded192173a2aaa68d457ef5e0bde88a8`; `html-review.md` / `a4fc2237381c100d22a9a175bb01c3374999d0cbdebd05b79367c30a453dd00b` | `report.md`と実HTMLを読むcommandExecution completed / exit 0。reviewerは4事実と秘密露出を確認してapproved。 |
| 8 公開 | `01a1587f-9aca-7ae0-9dd5-710d617cfd0f` | `html-review.md` / `a4fc2237381c100d22a9a175bb01c3374999d0cbdebd05b79367c30a453dd00b` | 最終review記録を読むcommandExecution completed / exit 0。local public copyはHTML hashと一致し、remote blobは旧hashのままと記録。 |

`calls.jsonl`ではrole 1–8のturnはそれぞれcompletedだが、role 6 minimal retry eventはfailed (`handoff_target_mismatch`)。role 7/8は後続し、最終`chain_finished`は`incomplete / skill_chain_role_call_incomplete`。したがって表は人間のtranscript上の実読を示すもので、runnerの前成果確認flagが成立した証明ではない。job terminalも未完了であり、先行chain結果やlocal report公開で補わない。
