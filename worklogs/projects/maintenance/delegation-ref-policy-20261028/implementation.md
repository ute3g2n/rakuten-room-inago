# AI-O委譲参照の指示・診断と全件一致ゲートの撤廃

対象workspace: `C:/project/rakuten-room-inago`。変更前HEAD: `bb971adf5f7a656fe71d594e153ae36761d4ee0b`。本人の2026-10-28の直接依頼を根拠とし、maintenance job IDは新規作成していない。

本人の依頼は、全参照を絶対に省かない指示の明記、省略が発生した場合に理由を追える診断、委譲欄の参照全件一致だけで結果を拒否するゲートの撤廃。その後、質問時点に存在する応答拒否条件の全件一覧が追加された。

## 変更前の全ゲート一覧

[gates-before.md](gates-before.md) は質問時点のHEADを `git show HEAD:<path>` で読み、入力前提、日次root、期限・取消・通信、応答schema/identity、native委譲、各専門payload、コピー本文と独立レビュー、保存済み応答の再採用、DB保存、SQL制約を列挙したもの。schemaの各フィールド・型・必須・enum等も掲載している。明示的な拒否条件、I/O等の故障、情報不足という正常不採用を区別している。記載行番号は変更前版に固定してある。

## 実装

1. `ai_runner.py`の実行promptとAI-O skill/contractsに、writer/reviewerへの実引渡しと最終委譲宣言の両方へ元の全参照配列をそのまま複写し、絶対に省略・選別・短縮せず、返却前に照合する指示を追加した。実使用分の`payload.used_input_refs`・`payload.evidence_refs`と区別した。
2. 省略発生時、既存の最上位`payload.details`へkind=source_ref_omission、declaration_index、agent_id、reason、detailを持つJSONエンコード文字列を任意で報告する。reasonは使用分との混同、出力短縮、context上限、資料不能、その他、不明の自己申告。不明な理由を推測で補わせない。出力schemaは変更していない。理由報告の欠測・解読不能・不正値による新しい拒否条件はない。
3. `delegation_audit.py`を追加。元参照、委譲欄の許可参照・件数・不足ID・不正参照のhash、自己申告理由、native引渡しcall/thread ID、引渡しメッセージと子自身のuserメッセージのhash・参照ID言及を独立の非公開artifactに保存する。継承親履歴は除外し、境界不明はunobserved。本文と理由自由文は診断や通常traceへ複写しない。
4. `ai_runner.py`はJSON parse後・採否検証前に診断を保存。L03にartifact参照と件数だけを記録する。診断保存や解析の例外は採否へ伝播しない。深いJSON文字列も解読不能として扱う。
5. exact全件一致ゲートを3箇所から撤廃した。runnerの`incomplete_source_refs`、`_validate_copy_review`のwriter/reviewerと元一覧の全一致、保存済みwriter宣言と元一覧の全一致。宣言を勝手に全参照で書き換えず、元の部分参照のまま保存・再採用する。型・許可範囲、root identity、writer非空参照、本文が引用する参照の範囲、対象商品・版、同一本文への独立レビュー等の別条件は変更前一覧のとおり維持する。
6. README、プロジェクトマップ、日次トレース一覧を更新。停止済み当日日次は起動・再開せず、本番DB・Scheduler設定・過去応答を変更していない。

## 診断の保存先と限界

通常日次は `runtime/artifacts/daily/<job_id>-a<attempt>-source-refs.json`。L03の`delegation_source_refs_audited`から参照でき、保存失敗は`delegation_source_ref_audit_failed`と例外classを記録する。

参照IDへの言及は資料本文全量の受領・読了を証明しない。自己申告理由もAI内部原因の確定証拠とは扱わない。実際の子への入力本文は対応する非公開X02/X04原本と照合する。現行runnerのraw応答復旧APIは診断artifactを新たに補完しない（現在の呼出箇所はKEY経路）。新診断artifactはDB未登録のため、既存DB連動retentionの自動削除対象ではない。これらは運用文書にも明記した。

## 局所再現・試験

全入力は隔離fixture。実AI呼出し、ROOM操作、当日日次再実行はない。fixtureのrunは`RUN-DAY-001`または`STEP12-RUN`、日付はfixture指定の2026-10-12。最終source/testのファイルSHA256は[validation-manifest.json](validation-manifest.json)に記録（Git改行正規化前のworking copy）。

- 旧HEADのrunnerを`git show`からメモリー上へ読み、同じ合成応答の不足参照を検証。旧版は`delegations[0].incomplete_source_refs`、新版はerrorなしで1子結果を返した。コマンド内では`AIJobRunner._validate_delegations`を直接呼び、dailyは起動していない。rc=0。[local-reproduction.json](local-reproduction.json)。
- 初回局所: 新診断7件pass、保存/再開2件はfixture私有領域外の`--basetemp=.pytest-tmp/delegation-ref-policy-local`を指定したためsetup error。製品動作の失敗や合格に数えない。[local-tests.txt](local-tests.txt)。
- failed 2 nodeだけを正しい私有fixture領域で再実行し2pass/rc=0。[local-tests-retry.txt](local-tests-retry.txt)。nodeは`tests_inago/test_copy_quality_review.py::test_partial_delegation_refs_allow_commit_and_saved_response_resume`（2パラメータ）。
- native引渡しと子観測のrunner結合nodeを追加し局所1pass/rc=0。[native-local-tests.txt](native-local-tests.txt)。
- 初回回帰は169pass/2fail/rc=1。新eventの期待値未更新と、Codex strict output schemaは全properties必須という既存条件が原因。event期待値を更新し、任意属性追加を撤回して既存details欄へ診断を移した。[regression-tests.txt](regression-tests.txt)、[regression.junit.xml](regression.junit.xml)。影響nodeだけと診断nodeを再実行し10pass/rc=0。[local-schema-retry.txt](local-schema-retry.txt)。
- 独立製品reviewで診断JSON文字列の深いネストによりRecursionErrorが本処理へ漏れる点を検出。2000段fixtureは実行環境の許容内で1pass（その記録は問題否定の証拠にはしない）。10000段で同じ動作を局所再現し1fail/rc=1。[recursion-reproduction.txt](recursion-reproduction.txt)、[recursion-reproduction-2.txt](recursion-reproduction-2.txt)。両診断parseと診断境界を修正した。
- 最終局所は下記コマンドで12pass/rc=0。深ネスト、PermissionError、RuntimeErrorの診断失敗、理由欠測/想定外値、親履歴除外、native観測結合、許可外参照、既存schemaと安全ログを確認。[local-final.txt](local-final.txt)。

```powershell
& .venv-inago/Scripts/python.exe -B -m pytest -q tests_inago/test_delegation_ref_audit.py tests_inago/test_step08.py::test_ai_job_output_schema_uses_codex_strict_json_schema_subset tests_inago/test_step08.py::test_runner_persists_sanitized_per_job_trace_events --basetemp=C:/project/rakuten-room-inago/非公開作業データ/イナゴ実装試験/delegation-ref-policy-local-4 -p no:cacheprovider
```

## 最終回帰試験

```powershell
& .venv-inago/Scripts/python.exe -B -m pytest -q tests_inago/test_step08.py tests_inago/test_step12.py tests_inago/test_step21_ai_root.py tests_inago/test_copy_quality_review.py tests_inago/test_product_page_copy.py tests_inago/test_step23_08_copy_flow.py tests_inago/test_delegation_ref_audit.py --basetemp=C:/project/rakuten-room-inago/非公開作業データ/イナゴ実装試験/delegation-ref-policy-regression-2 -p no:cacheprovider --junitxml=worklogs/projects/maintenance/delegation-ref-policy-20261028/regression-final.junit.xml
```

期待: 既存の許可・identity・本文/レビュー結合・checkpoint等を保ち、部分参照宣言だけによる拒否を行わず、診断障害も採否へ影響しない。実結果: **173 passed in 25.07s、終了コード0**。[regression-final.txt](regression-final.txt)、[regression-final.junit.xml](regression-final.junit.xml)。初回失敗結果と最終合格を混同しない。

ブラウザー画面に依存しない隔離の契約・保存試験のため、スクリーンショット、動画、ブラウザーtraceは未取得。全体daily runは本人の停止指示を維持して未実行。最終確認時のSchedulerはReadyであり、実日次の正常完走・実AIの将来の参照遵守を試験済みとは表現しない。

## レビュー・機械確認・Git

[code-review.md](code-review.md)と[python-review.md](python-review.md)で独立製品レビューを実施。診断例外漏れの所見を作者が修正し、同入力を両reviewerが再確認してAPPROVE/approved。テストコードの独立レビューはしていない。

`git diff --check`は終了コード0、追加Pythonファイルの`black --check`は2ファイル変更不要、source hash照合とJUnit parseは成功、共有証拠のユーザープロファイル/credential形式scanは検出0。伏字前の証拠は非公開fixture領域へ保持し、共有コピーの伏字対象はvalidation-manifestに記録。認証情報・本番商品本文は共有成果へ含めない。

新規証拠をstageした後の`git diff --cached --check`ではpytest失敗出力中の行末空白を検出した。共有コピーの4ファイルだけで行末空白を除き、原本を非公開領域へ保持。再確認は終了コード0。加工ファイル名はmanifestの`evidence_whitespace_normalized`へ記録した。

当初から存在した`worklogs/projects/implementation/item-desc-capture-20261025/C07/.pytest-tmp/`は今回の所有外であり変更・stageしない。今回作成・変更した製品・試験・文書・本案件worklogだけを一覧化してstageし、現在の`main`から追跡先`origin/main`へcommit/pushする。実成否は最終回答とGit履歴で報告する。
