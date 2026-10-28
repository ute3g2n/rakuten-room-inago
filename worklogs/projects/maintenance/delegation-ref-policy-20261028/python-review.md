# 製品コードの独立レビュー

- reviewer_id: `/root/python_review`（実装作者とは別担当）
- 日付: 2026-10-28 JST
- scope: delegation-ref-policy-20261028 / 本番製品コード・指示・schema
- reviewed_version: HEAD `bb971adf5f7a656fe71d594e153ae36761d4ee0b` に対する作業ツリー差分。下表は読取時のSHA-256であり、後続工程の開始条件ではない。
- status: **approved / Approve**（修正後再レビュー）。未解決CRITICAL 0、HIGH 0、MEDIUM 0。再レビューのHIGH 1件は修正を再読して解消確認。下記LOW所見2件は採用制御の不具合ではない。review状態は後続工程の開始条件ではない。
- 所有: 本記録のみ。製品コード、試験コード、本番データ、schedulerの編集・実行は行っていない。commit/pushは親担当へ委ねる。
- 適用: AGENTS.md、プロジェクト全体マップ、common-review、python-patterns。`settings/language.md` は固定rootに存在しないため未読。会話・指示に従い日本語で記録。

## 読み取った対象版

| 対象 | SHA-256 |
| --- | --- |
| automation/inago/delegation_audit.py | 0532BD72C2B24C207AC5C0918DC514DE18DD16C877BB1E451E55170AFA7C1F6A |
| automation/inago/ai_runner.py | 48B426985A09F892B6750618C74FB0D269948A00ECD86D4E0D1D068A268D176E |
| automation/inago/copy_service.py | 0FBA9CB79AEBD7F3C5EC3458576EC329D51B2ACB0F9A267AA3C63F74E049B1CF |
| automation/inago/schemas/ai-job.schema.json（HEADと差分なし） | A856290832E705BB5C6BFA3C307BF97168F615A55639B3FAC5A2665715A761AC |
| .agents/skills/rakuten-room-inago/SKILL.md | 62B9E8B99EC59AD8419C124DDA63B0F741DE0EF08138454A08B0C04ED6B5EDA5 |
| .agents/skills/rakuten-room-inago/references/contracts.md | 26EACF19759CDA73830498563D077F19551D2AFC85082262B6FC29158490C5C5 |

## 要求と差分の照合

1. ai_runner.py:966、SKILL.md:26、references/contracts.md:23に、writerとreviewerの元配列全件をネイティブ引渡しと最終委譲欄へ省略せず複写する指示がある。未使用参照とpayload採用参照を区別し、返却前の照合、原因不明はunknownも明記している。
2. ai_runner.pyの`_validate_delegations`、copy_service.pyの`_validate_copy_review`、`_copy_checkpoint_request`の全件一致拒否が撤廃されている。委譲一覧の非空部分集合が短いことだけで拒否する条件は、この3箇所の採用経路・保存済みcheckpoint再採用経路・runner復旧時の共通検証に残っていない。
3. root identityの完全一致、root checkpointのinput hashとrefs一致、未許可参照の拒否、writer input_refs非空、payload.used_input_refs/evidence_refsの許可範囲検証、本文と独立reviewの結合は保持。これらが別条件で拒否する場合は「全件一致gateが残っている」とは扱わない。変更前の一覧は同案件のgates-before.mdに別記されている。
4. 初回レビューのoptional診断属性追加は撤回された。Codex strict schema既存条件（全propertiesをrequiredに含める）と両立しなかったため、出力schemaはHEADと同じ版へ戻している。現行は既存root payload.detailsのJSONエンコード文字列へkind=source_ref_omission、declaration_index、agent_id、reason、detailを任意記録し、診断欄自体をschemaへ追加していない。パーサーはkind・0始まり整数index・該当役で宣言へ結び、未報告・解読不能・未知reasonをnot_reportedとする。自由文detailは生応答に残すだけでtraceへ転記しない。再レビューで検出した深いネスト解読例外は両診断パーサーで捕捉されるよう修正済み。
5. delegation_audit.pyは期待ID、不足ID、未許可値のhash、ネイティブ引渡しのID言及、message hashを保存する。商品本文・会話本文を診断出力へコピーしない。ID言及を資料受領・読了の証明としない限界を明記している。
6. 子ログはsession_metaの異なる親履歴があれば自身のthread_settings_applied境界以降だけを観測し、境界不明はunobservedとする。日次rootは既存trace_window_by_jobによってjob時間内へ限定される。ネイティブ観測不能を「全資料受領」と補完しない。
7. `_audit_source_refs`は最終版でExceptionを捕捉し、予期しない診断処理・保存エラーも例外classだけを安全な既存traceへ記録する。理由パーサー自体はValueErrorとRecursionErrorを解読不能として読み飛ばす。成功traceへ出す追加値はartifact IDと件数のみ。監査I/O失敗をAI応答拒否条件にしていない。既存trace実装もI/O失敗を捕捉する。

## 軽微な所見と観測限界

[HIGH / 修正済み] 診断理由JSONの深いネストで正常結果が例外終了する

File: automation/inago/delegation_audit.py:151

Issue: payload.detailsはschema上文字列なので、JSON配列等を深くネストした内容も正当な外側応答の文字列として返せる。内側のjson.loads(detail)はValueErrorしか捕捉せず、PythonのJSONデコーダがRecursionErrorを送出した場合、_audit_source_refsとrunner外側の既存捕捉にも含まれず伝播する。診断の解読不能だけで本処理を妨げない要求に反する。診断内native argsのjson.loadsにも同じ例外捕捉漏れがある。

Fix: 診断専用の両json.loadsでRecursionErrorを解読不能として捕捉し、noteは読み飛ばすことで理由をnot_reportedにする。採用処理へ例外を伝播させない。対応nodeで局所確認し、後で回帰結果へ統合する。reviewerは製品編集・試験実行をしていない。

対応要求: 診断自体で新しい拒否・失敗条件を作らない。親へ先行通知済み。

解消確認: 最終版の両診断json.loadsは`except (ValueError, RecursionError): continue`、_audit_source_refs境界は`except Exception`となっていることを実ファイルで再読した。recursion-reproduction-2.txtは該当nodeがRecursionErrorで1 failedだった実測、local-final.txtは同nodeを含む12 passed（親引継ぎ）を記録。deep JSON文字列の解読不能はnote欠測へ戻り、理由not_reportedとなる。局所証拠と修正に整合し、所見を解消とする。試験コード自体は独立レビューしていない。

[LOW] 復旧時に不足した監査artifactを補完しない

File: automation/inago/ai_runner.py:1347

Issue: recover_successful_resultはraw応答を復元して同じ委譲検証を通すが、`_audit_source_refs`を呼ばない。通常T02実行では監査が先に走るため新しい拒否条件ではない。一方、監査保存だけ失敗した既存成功rawを復旧しても診断は増えない。現行呼出はdaily_theme_planner.py:309のKEY復旧であり、copyサービスからの直接呼出はない。

Fix: 復旧経路も診断対象に拡張する場合、既存artifactを保持して宣言差分のみ補完し、ネイティブ履歴を再取得できなければunobservedと記す。診断不足を復旧拒否条件にしない。

対応要求: 今後省略理由を追えるログ（通常経路の実装は要求充足）。

[LOW] 診断artifactの期限は現行retentionへ登録されない

File: automation/inago/ai_runner.py:2372

Issue: write_temp_responseへ渡したretention_expires_atは戻り値ArtifactRecordに入るが、今回そのrecordをDB登録せず、診断JSONにも期限を入れない。既存retentionはDB artifact行と別途TFAIL-*を掃除するため、source-refs.jsonには期限に基づく自動失効が働かない。保存内容はID・hash・安全な診断値であり、本文漏えいは確認していない。

Fix: 保管運用を明記するか、後日DB登録または専用清掃を追加する場合は採用制御から独立させる。今回の要求に保管期間指定はないため軽微な制限として残す。

対応要求: 診断ログの運用範囲。

## 機械検証・試験証拠

- 実行: `git diff -- '*.py'`。modified Python全体を把握し、製品の差分と新規delegation_audit.pyをレビュー。テストコードの独立レビューはしていない。
- 実行: `black --check automation/inago/delegation_audit.py automation/inago/ai_runner.py automation/inago/copy_service.py`。再確認の終了コード1、既存ai_runner.pyとcopy_service.pyはwould reformat、新規delegation_audit.pyは変更不要。既存2ファイルを含む全面書式チェックであり、製品機能の失敗とは区別する。書式の自動修正は未実施。
- 実行: venv Pythonのimportlib.util.find_specでruff/mypy/pylint/blackの存在確認。ruff/mypy/pylintは利用不可、blackのみ利用可能。追加インストールは行っていない。
- 実行: `git diff --check -- automation/inago/ai_runner.py automation/inago/copy_service.py automation/inago/schemas/ai-job.schema.json .agents/skills/rakuten-room-inago/SKILL.md .agents/skills/rakuten-room-inago/references/contracts.md`。終了コード0。CRLF→LFのGit警告あり。
- 読取証拠: local-reproduction.jsonは合成入力の変更前`delegations[0].incomplete_source_refs`→変更後null、subjob 1件を記録。コード実装と整合する。
- 読取証拠: local-tests.txtは7 passed / 2 setup errors。local-tests-retry.txtは2 passed。最初の2件はfixture許可領域外のbasetemp指定で実行不能だったもの。reviewer自身はpytestを実行していない。これらのテキスト単独には全コマンド・完全node・対象hashがなく、本記録で回帰試験全体の成功とは断定しない。親担当の最終試験記録と合わせて利用する。
- 再レビュー読取証拠: local-schema-retry.txtは10 passed（親引継ぎ: 新schema既存testを含む局所）、native-local-tests.txtは1 passed。regression-tests.txtは初回2 failed / 169 passedであり、失敗はイベント一覧期待値とstrict schema適合性。既存schema復元と局所合格は確認したが、最終回帰は再実行予定であり、この時点で171件合格と扱わない。初回optional schema設計を承認した前回静的レビューではstrict出力互換を見落としていたため、本記録の説明と対象版を訂正した。
- 最終修正読取証拠: local-final.txtは12 passed / 2.85s。親引継ぎでは深さ10000のdetails解読不能、PermissionErrorとRuntimeErrorの監査保存障害でもresult successとなる局所caseを含む。追加の`black --check automation/inago/delegation_audit.py`は終了コード0、新規ファイルの書式問題なし。最終回帰7ファイルは親担当が再実行中であり、その結果は親の実装・試験記録へ統合する。
- 通常daily、モデル呼出、画面操作、本番DBの読書きは未実施。画面依存調査ではないためスクリーンショット・動画・Playwright traceは未取得。

## 自己評価

agent-self-evaluationを適用。平均4.0。正確性4（静的差分とI/O例外境界は照合、モデル実観測は対象外）、網羅性4（通常・checkpoint・runner復旧を確認、完全な回帰結果は親の記録待ち）、明瞭性4（撤廃と保持条件を明記、詳細表は別gate一覧へ分離）、実行可能性4（軽微所見に具体箇所と対処を提示、製品修正は所有外）、簡潔性4（版の証拠と制限の記載で記録は長め）。改善優先は最終試験の完全node・版・終了コードの記録統合、その次に診断保管運用の明示。ユーザーの期待する範囲と評価は整合すると判断する。
