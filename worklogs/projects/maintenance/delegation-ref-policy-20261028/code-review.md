# 製品差分の独立レビュー

## 対象と結論

2026-10-28、base `bb971adf5f7a656fe71d594e153ae36761d4ee0b` からの作業ツリー差分を、実装担当と別の担当が読み取って確認した。適用skillは `.agents/skills/common-review/SKILL.md`。本人の直接依頼であり管理job identityは渡されていないため、job ID等を生成していない。対象製品コード・schema・AI-O指示を編集せず、このレビュー記録だけを作成した。テストコードの独立レビューは行っていない。

最終再レビューでは診断JSONの深い入れ子による解析例外が受入処理へ伝播する問題R1を検出し、実装担当へ即時共有した。実装担当による修正後、同じ合成入力と予期しない診断保存例外の非伝播を実確認し、R1を解消済みにした。未解決の所見はない。下記はschemaを変更しない最終方式の再確認を含む。変更前ゲートの一覧 `gates-before.md` はHEAD固定のまま保持し、既存gateを残すこと自体を新規不具合とはしていない。

## 読み取った変更と周辺経路

対象は `automation/inago/ai_runner.py`、`copy_service.py`、新規`delegation_audit.py`、`.agents/skills/rakuten-room-inago/SKILL.md`、同`references/contracts.md`、運用ログ説明。`schemas/ai-job.schema.json`はHEADとdiffなしに戻ったことを確認した。

`git diff --staged -- <対象>`と`git diff -- <対象>`を確認し、新規ファイルは全文を読み取った。runnerのroot schema／identity検証、ネイティブ子rollout読取とfork履歴境界、共通traceへの転記、copyサービスの新規保存・保存済みcheckpoint再採用、repositoryの最終保存契約を周辺として確認した。

| 要求・確認点 | 実差分の確認結果 |
| --- | --- |
| 全参照の省略禁止をAI-Oへ指示 | staged prompt `ai_runner.py:969`、SKILL入力出力欄、contractsに、元配列の全件複写・未使用分も維持・payload used refsとの区別を明記。SKILL停止条件も委譲欄の漏れだけで停止しない説明に整合 |
| runnerの全件一致だけの拒否撤廃 | 旧`delegations[i].incomplete_source_refs`分岐を削除。宣言型・未許可参照・role/task/skill・native子対応・review hash・専門payload schema／identity条件は維持 |
| writer/reviewer受入の全件一致だけの拒否撤廃 | `_validate_copy_review`はreview refsを許可集合の部分集合として検証。writerは呼出前の`_subjob_matches`でnonempty・重複・部分集合を継続確認。本文一致・独立role・review success・verdict条件は維持 |
| 保存済みwriter再採用 | stored writer refsはspecの部分集合に緩和。元root identity/hash/refs検証、writer検証、保存reviewの同一本文照合、repository保存契約を維持 |
| 漏れを追える診断 | 専用`<job>-a<attempt>-source-refs` artifactに元参照・申告済み許可参照・不足参照・未知refのhash、native call/thread識別、引渡message内ID言及とmessage hashを保存。L03にはartifact_refと件数のみ |
| 理由欄から新gateを作らない | schemaへの診断属性追加を撤回し、既存root payload.detailsのJSONエンコード文字列を任意解析。kind/index/roleが合う既知reasonだけ診断へコピーし、通常の欠測・不正形・不明値はnot_reported。自由detail本文は診断artifactへコピーしない。R1修正後は深い入れ子によるRecursionErrorも解読不能として飛ばす |
| 理由と観測の確度 | reason_is_self_reportedとobservation_limitを保持。ID言及はliteral_ref_mentionsとし、全文引渡し・読了証明にはしていない。未観測はunobserved／not_reported |
| 診断障害で受入を止めない | R1で例外境界の不足を再現後、parserでValueError/RecursionErrorを捕捉し、audit全体でExceptionを捕捉する修正を確認。PermissionError/RuntimeErrorの保存失敗も非伝播を実確認 |
| 機密と副作用 | 新コードは元message本文・自由理由・未知参照文字列を運用ログへ転記せず、許可refと安全化した識別値・hash・件数を記録。新しい外部接続・モデル呼出・DB更新処理は追加していない |

## 確認した試験証拠

- `local-reproduction.json`: 合成fixtureで変更前`delegations[0].incomplete_source_refs`、変更後null、subjob 1件。根拠baseが上記HEADと一致。
- `local-tests.txt`: 局所7件pass、2件はfixtureの私有領域外basetempを指定したためsetup error。製品成功や試験passと読み替えていない。
- `local-tests-retry.txt`: 当該保存／再開2ケースを私有fixture領域で再実行し2 passed。親担当の引継ぎでは完全nodeは`tests_inago/test_copy_quality_review.py::test_partial_delegation_refs_allow_commit_and_saved_response_resume[False]`および`[True]`。元エラー出力にも両nodeがある。
- `local-schema-retry.txt`: 最終のschema／監査局所10 passed。`native-local-tests.txt`: native観測1 passed。
- `regression-tests.txt`: 初回169 passed、2 failed（sanitized trace event期待とCodex strict schemaの全properties必須）。schema方式を取り消した最終版の回帰再実行は親担当が準備中。最終回帰・本番runの成功をまだ示していない。

## 所見

[HIGH・解消済み] R1: 診断JSONの深い入れ子で応答受入まで失敗する

- File: `automation/inago/delegation_audit.py:150`、`automation/inago/ai_runner.py:2403`。
- Issue: 既存schemaで許される`payload.details`の文字列として`"[" * 10000 + "0" + "]" * 10000`を渡すと、追加された`json.loads(detail)`が`RecursionError`を送出する。parserはValueErrorのみ、audit境界とrun外側もRecursionErrorを捕捉しないため、診断の解読失敗がAI応答処理へ伝播する。通常のschemaに合う応答でも診断だけによりrunner呼出が失敗し、ユーザーの「診断解析失敗は採否に影響させない」要求を満たさない。
- Reproduction: 合成request、合成artifact、in-memory `_trace_event`、artifact書込のmockで`_audit_source_refs`を呼び、RecursionError流出とtrace event 0件を実測した。終了コード0の確認スクリプト内で期待どおり例外を捕捉し、`REPRODUCED`を出力。製品・DB・モデルへ書込みや呼出なし。
- Fix: note parserのRecursionErrorを通常の解読不能として飛ばし、診断境界でも非致命の診断例外を捕捉して本来の受入へ戻す。新たなnoteサイズや深さを応答採否gateにしない。
- Status: **解消済み**。両json.loadsがValueError/RecursionErrorを捕捉し、audit全体もExceptionを捕捉する最終コードを再読。同じ10000段detailsはnot_reported、同じ深さのnative argsはskipとなり、通常の診断記録へ戻った。PermissionErrorと予期しないRuntimeErrorのartifact保存失敗も例外classだけを失敗eventへ残し、呼出元へ伝播しないことを独立に確認（exit 0）。新しい応答採否gateは追加していない。

## 独立の合成確認

実行形式はPowerShellのhere-string `$script | & .venv-inago/Scripts/python.exe -`。いずれもインメモリーで製品関数を呼び、モデル・DB・本番データへ接続していない。テストコードの独立レビューは行っていない。

- 前回版: expected=`html-1,ocr-2`／declared=`html-1`で不足`ocr-2`が記録されること、diagnostic artifact OSErrorが非伝播で失敗eventになることを確認（exit 0）。旧方式のoptional schema属性の確認結果は最終方式へ流用しない。
- 最終版: 既存payload.detailsのvalid noteからreason=`used_refs_only`を取得。欠測・不正JSON・非object・bool index・別role・未知reason objectはnot_reportedになること、不足IDは維持することを確認（exit 0）。
- 最終schema: `git diff --quiet -- automation/inago/schemas/ai-job.schema.json`がexit 0で、HEADとの差分なし。
- R1の修正前再現: 2000段は当環境でparse成功、10000段でRecursionErrorが診断境界から伝播。修正後は同じ10000段のnote/native argsとPermissionError/RuntimeErrorを注入し、すべて診断境界内に収まった（exit 0）。
- `local-final.txt`: 親担当のR1修正後局所12 passed。最初の再現failを消していない。

## レビューした実ファイル版

SHA256はレビュー対象を識別する記録であり、後続工程の開始gateではない。

| ファイル | SHA256 |
| --- | --- |
| automation/inago/ai_runner.py | 48b426985a09f892b6750618c74fb0d269948a00ecd86d4e0d1d068a268d176e |
| automation/inago/copy_service.py | 0fba9cb79aebd7f3c5ec3458576ec329d51b2acb0f9a267aa3c63f74e049b1cf |
| automation/inago/delegation_audit.py | 0532bd72c2b24c207ac5c0918dc514de18dd16c877bb1e451e55170afa7c1f6a |
| automation/inago/schemas/ai-job.schema.json | a856290832e705bb5c6bfa3c307bf97168f615a55639b3fac5a2665715a761ac |
| .agents/skills/rakuten-room-inago/SKILL.md | 62b9e8b99ec59ad8419c124dda63b0f741de0ef08138454a08b0c04ed6b5eda5 |
| .agents/skills/rakuten-room-inago/references/contracts.md | 26eacf19759cda73830498563d077f19551d2afc85082262b6fc29158490c5c5 |
| docs/operations/日次タスクのトレース・ログ出力一覧.md | 874822e0bc9fc86a142553c99621d07e26d3e0ab62e2a67fea1d9184872224f2 |

## Review Summary

| Severity | Count | Status |
| --- | --- | --- |
| CRITICAL | 0 | pass |
| HIGH | 0 | pass（R1解消済み） |
| MEDIUM | 0 | info |
| LOW | 0 | note |

Verdict: APPROVE — 検出したR1は修正後の合成再確認で解消済み。未解決の製品所見はない。最終回帰試験・本番runの成否は別途記録する。
