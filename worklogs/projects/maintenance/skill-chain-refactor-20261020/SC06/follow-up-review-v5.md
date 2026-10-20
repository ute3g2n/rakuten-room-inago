# SC06 独立製品レビュー — source_run_id契約修正 v5

## 対象版と試験証拠

- 固定job identity: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`; `source_launch_id=historical-9e7f0c175cc4c874c955a5dab29c4427`; `target_date=2026-10-17`; `source_run_id=null`。
- 固定workspace: `C:/project/rakuten-room-inago`。実装担当とは別のSC06 reviewerとして製品コードと記録をread-only確認。
- runner `automation/inago/maintenance/skill_chain.py` SHA-256: `fcc23783b14f626c97d32c1f804c9dad5f0f95f1a39a8db873aca5c2b2b3976a`。
- 試験ソースSHA-256（試験コード自体は独立reviewせず、実測版の結合のみ確認）: `bceb82fcfaa2c0d408f71f3c4451c173c486828319b60c59048e0527edfbba1b`。
- SC05 F6 `test.md`, `test.log`, `junit.xml`, `node-list.txt`, `node-list.json`を読んだ。21 focused skill-chain nodeと4つの計画指定nodeの計25件が成功、失敗0/error 0/skip 0、整数exit 0。JUnitもtests=25/failures=0/errors=0/skipped=0。開始/終了は2026-10-20 14:44:32.8844349〜14:44:39.2680704 JST。F6記録hashはtest.md `b8200bf5f32093fe22e67d469ef16f5295b87398fe91739b837ccae7f89f1daa`、JUnit `e85dd17285eded7f273a0fe483314b05979413b9465a464cb334c02116d9569b`、node-list.txt `82bbe414a2fc7b708f2793a5873910053057fc2fe50d3784d98823c26ac93491`。
- F6の実行recordはrunner/testの対象hashと合成incident fixtureの現行source_run_id説明行を明記する。fixture実行でnull維持を確認している。live 10/17 jobの処理を証明するものではない。
- 実行時のGit情報はF6記録上branch `main`, HEAD `dadffcd01ee7cc7672ad7f7c446839128dbe12e7`, tracking `origin/main`。
- 今回、製品・試験・runtime job・DB・Daily・workerを変更または実行していない。FC01〜FC11を実行していない。10/19 queue/historyにも触れていない。

## 判定

**approved（今回のSC06製品review範囲）**。実incident行の追加説明に合わせた入力parser修正は、供給されたsource_run_id `null`をnullのまま維持する。regexは説明句以降の同一行のsuffixを広く許容するが、識別キーとbacktick囲み値を行頭から取得し、capture値を文字列`null`と厳密比較するため、別のsource_run_idや別keyの行を有効化しない。今回の差分による具体的な誤同定・秘密露出・回帰所見は確認しなかった。

## parserとidentity照合

- `skill_chain.py:936-951`はincident内のsource-run行を`(?m)^- 日次run: `([^`]+)` のまま。.*$`で照合し、code span内の値だけをgroup 1に取る。
- 続く`expected_incident_identity`はsource_run_idがnullの場合の正本表示を文字列`null`にし、実incidentから得たgroup 1とdict全体を比較する。関数引数も固定job/launch/dateと`source_run_id=None`の一致を先に検証する（`:894-922`）。したがって説明文の末尾から値を推測せず、nullを文字列IDやrun IDに昇格しない。
- suffixの`.*`は改行をまたがず、` のまま。`の後ろだけを受け持つ。別source_run_idをbacktick内に記載すると値比較に失敗する。想定外の末尾注記は無視されるが、ID部分を変えずにparserを通るので、今回の不変identity要件に対するfail-openではない。
- worker側callbackも固定launch/dateとrun-nullを確認し、既存job manifestを照合する。除外日は`WORKER_DISPATCH_EXCLUDED_DATES = frozenset({"2026-10-19"})`のまま（`worker.py:32`、runner callback `skill_chain.py:2058-2090`）。

## F1〜F4と秘密境界の継続確認

- F1 same-author role 6 recoveryは、初回成果/read/handoff不備後に新sessionで再試行し、失敗してもrole 7/8へ継続する実装を保持する（`:655-770`, `:1331-1355`）。
- F2 predecessor readは完了済み正確なpath読取command eventとexit code 0に結び付き、限定hash/path/method/status metadataを保存する（`:578-650`）。
- F3 role 8-only publication mismatchの再開はrole 6/7成果を現物hash確認後に再利用する。F4ではauthor revision output refsにreport.md/report.html双方を、rereview input refsにreport.md hashを含め、resume/terminalで照合する（`:1475-1505`, `:1548-1564`, `:1679-1833`, `:1976-2002`）。以前のv4レビューと同じ挙動を現行コードで確認した。
- `_append_event`のallowlist、response本文の代わりに長さ/hashを記録する処理、例外本文を含めないsafe error summaryを再確認した。今回の変更はincident identity parsingだけで、秘密・個人情報をtraceへ追加する経路はない。

## 実測範囲と引継ぎ

F6 focused testsが現行hashで成功したこと、4つの追加plan nodeの結果を記録する。synthetic fixtureをlive job完遂と混同しない。SCT06、SC07 HTML review、SCT09同job実動、本番適用・公開は別工程であり、このreviewはそれらを代替しない。SC08と後続工程はこの所見なしのreview結果とF6の証拠を入力として続行できる。
