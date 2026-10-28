# 変更前のAI応答受入ゲート一覧

- 根拠版: `bb971adf5f7a656fe71d594e153ae36761d4ee0b`（調査開始時HEAD、2026-10-28）。以下の行番号はすべてこの版。
- 調査方法: `git show HEAD:<path>` と `git grep ... HEAD -- automation/inago` でコード・schema・呼出箇所を静的照合。通常daily、モデル呼出、本番DBの読書きは未実施。
- 所有: この文書だけ。親担当が製品コードを変更するため、作業ツリーの製品コードを根拠にはしていない。commit/pushは親担当の統合処理に委ねる。
- 適用指示: AGENTS.md、プロジェクト全体マップ、`common-review`。本人の直接依頼であり、保守controllerのjob identityは渡されていないため作成しない。テストコードの独立レビューはしていない。
- 範囲: 日次のAI-O応答→AG-COPY／AG-COPY-REVIEW→下書き保存／保存済み応答の再採用。共通runnerで同時に有効な他専門役条件は別記。投稿実行、Human API、候補検索・商品選定、保守skill chain全体の開始制約は範囲外。
- 分類: **明示gate** は条件分岐・schema違反により受入／保存を拒否する実装。**実行失敗** は通信・ファイル・DB等の故障で受入まで到達できないもの。**正常不採用** は情報不足。これらを同一の「品質拒否」とは扱わない。

## 1. 呼出経路と主要な重複

`orchestrator.py:4173`（商品slot経路）／`:4516`（旧経路）→`copy_service.generate_and_commit_copy:985`→`DailyAIRootRunner.run:231`→`AIJobRunner.run:1959`→root schema／identity→`_validate_delegations:1716`→`_validate_role_payload:1879`→copy serviceのroot／writer／payload／独立review検証→応答artifactとcheckpoint保存→`repository.commit_draft:2114`。

保存済み成功checkpointは`copy_service.py:1044`→`_copy_checkpoint_request:688`→`_subjob_matches:515`と`_validate_copy_review:601`→同じ`commit_draft`。既存下書きの再取得は`read_existing_draft_result:1381`。runner単独の`recover_successful_result:1034`はraw応答＋traceを再検証する別経路だが、HEADの実呼出は`daily_theme_planner.py:309`のKEY復旧のみ。copyサービスから直接は呼んでいない。

**参照全一致だけで拒否する箇所**:

| 層・変更前位置 | 拒否する条件 | 表面化するcode／例外 | 再採用への影響 |
| --- | --- | --- | --- |
| `ai_runner.py:1801` | AG-COPY／AG-COPY-REVIEWの委譲`input_refs`集合または`evidence_refs`集合がroot要求と不一致 | `delegations[i].incomplete_source_refs`／`rejected_contract` | `recover_successful_result:1306`も同じ関数へ戻る |
| `copy_service.py:623` | reviewerまたはwriterの上記集合がroot要求と不一致 | `RejectedContract("copy review identity or source inputs differ")`→`copy_review_contract_invalid` | checkpoint再採用`:760`でも同一検証 |
| `copy_service.py:735` | 保存済みwriterの上記集合が現在のspecと不一致 | `copy_checkpoint_inputs_changed` | 新しいAI呼出なしで再採用拒否 |
| `copy_service.py:717` | 保存済みroot refs集合が現在のspecと不一致（input hash不一致も同じ分岐） | `copy_checkpoint_inputs_changed` | root入力自体の変化検証。委譲refs一覧の全一致とは別 |

これとは別に、rootのidentity refsは順序込み完全一致、委譲refs／payload refsは提供済み集合の部分集合、DB commitのrefsは保存済みjobとの順序込み一致が要求される。以下に各々を列挙する。`used_input_refs`は全入力を必須列挙するgateではなく部分集合検証。参照配列の完全さをプロンプトで要求することと、一覧差だけで機械拒否することは別である。

## 2. 入力前提・開始前の明示gate

| 位置 | 条件（不成立なら拒否） | code／例外 |
| --- | --- | --- |
| `copy_service.py:216` | run_id／offer_key／settings_version／generator_versionが前後空白なし非空文字列、run_idが安全ID。draft_versionはbool以外の正整数。pattern_version=settings_version | `RejectedContract`（各field／version理由） |
| `copy_service.py:235` | input_refsは非空tuple、evidence_refsはtuple。各要素string、重複なし、evidenceはinputの部分集合、全refは安全ID | `RejectedContract`（copy input/evidence references／available inputs／safe identifiers） |
| `copy_service.py:256` | input_payloadはMappingかつstrict JSON（NaN不可）、target_dateは正規ISO日、deadlineはtimezone付、monotonic deadlineはbool以外の有限数。offer Mappingに対象offer_key／draft_versionが一致 | `RejectedContract`（payload／date／deadline／offer理由） |
| `repository.py:1328`; `copy_service.py:1009` | runとofferが存在し、runのsettings_version・target_dateがspecに一致 | `RejectedContract("copy run or offer does not exist")`／settings・date differs |
| `ai_runner.py:277` | job_idは安全ID最大60字、run_id最大128字、attempt=1..1,000,000。AI-O taskはT02/T05/T06/T07/T08/T09/T10 | `request_invalid:<例外class>`、`rejected_contract` |
| `ai_runner.py:284` | scheduled_dailyはT02かつexplicit_user_request=False。user_requestはT05/T06/T07/T08/T09/T10かつexplicit=True。他trigger不可 | 同上 |
| `ai_runner.py:294` | source_kindはexternal_analysis_result/daily/metrics/generation、settings_versionは非空string、target_dateが日付parse可能、deadline_atはtimezone付ISO、monotonic有限、input_payload Mapping | 同上 |
| `ai_runner.py:315` | input/evidence refsは重複なし安全ID、copy_targetsはT02のみ、CopyTargetかつ非空offer_key・draft_version>=1・offer/version重複なし。payloadがstrict JSON | 同上 |
| `ai_runner.py:621` | cli argv非空、timeout/poll interval正有限、require_observed_metadata bool、data_rootが有効 | constructor `ValueError`／`PersistenceContractError`。応答拒否ではなく設定前提 |
| `ai_runner.py:812`／`:829`／`:889` | stagingがdata_root直下、jobsが安全directory、skillが所定rootの実dir＋SKILL.md（symlink不可）、bundleにsymlinkなし、許可role TOMLが所定rootの実file、job_dirがstaging内 | `AIJobInputError`。run中は`:2326`で`runner_io_or_contract_error`／pendingへ集約 |

安全IDの正規表現は`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`、root jobだけ末尾量指定`{0,59}`（`ai_runner.py:47`）。

### 日次永続rootのidentity/checkpoint

| `daily_ai.py`位置 | 条件／停止内容 | code |
| --- | --- | --- |
| `:63` | run欠落、mode!=daily、statusがrunning/partial以外 | `daily_run_missing` |
| `:69` | checkpoint JSONがdictでない（不正JSONもparse例外） | `saved_root_checkpoint_invalid`／JSON parse例外 |
| `:77` | 既存contextのrun_id/target_date/settings_version/seedを変更 | `root_cannot_change_daily_identity` |
| `:81` | saved rootがdictでない、schema_version!=1、identity不一致、非null root_thread_idが非空stringでない | `saved_root_identity_invalid` |
| `:96` | process_startsがbool／非int／負数 | `saved_root_process_count_invalid` |
| `:123`／`:132` | checkpoint同期時connectionがtransaction中、run欠落／settingsまたはseed不一致 | `root_checkpoint_requires_idle_connection`／`root_checkpoint_run_conflict` |
| `:140` | 既存root_thread_id変更。ただし未使用root差替えかつai_jobなしの許可例外あり | `root_thread_cannot_change` |
| `:168`／`:178` | contextなしでmetadata取得／root起動 | `root_not_bound` |
| `:215` | history欠落は未使用rootに限り新threadへ差替え。それ以外は再開失敗を伝播 | `thread_history_not_found`等 |
| `:232` | contextなし、request task!=T02/source_kind!=daily、run/date/settingsがbound root不一致 | `job_does_not_match_bound_daily_root` |
| `:242` | 同じ日次rootで同時job | `concurrent_daily_ai_job` |

## 3. 期限・取消・通信・保存不能（明示gateと実行失敗）

| 位置 | 条件 | 結果／code | 分類 |
| --- | --- | --- | --- |
| `ai_runner.py:1980`／`:1992` | 開始前cancel／deadline経過 | cancelled/expired `cancelled_before_start`、timed_out/expired `deadline_elapsed_before_start` | 明示期限gate |
| `ai_runner.py:2007` | 観測auth_modeがchatgpt以外（unobserved含む） | error/pending `auth_mode_not_permitted` | 明示認証gate |
| `ai_runner.py:1661`／`:1669`／`:1675`／`:1685` | 途中cancel、残期限非有限／<=0、呼出最大時間超過（共通既定120秒／日次480秒）、完了取得時に期限経過 | cancelled/timed_out/late_result、adoption=expired、code同名 | 明示期限gate |
| `ai_runner.py:1655`／`:1666`／`:2146` | Popen失敗／残期限計算失敗等でprocess_status!=completed | `cli_start_failed`／pending | 実行失敗 |
| `ai_runner.py:2157`／`:1694` | final.response欠落、symlink、実fileでない、親dir逸脱、raw保存失敗 | `final_response_not_saved`。transport正常時rejected_contract、それ以外pending | 安全path gate＋I/O失敗 |
| `ai_runner.py:2171`／`:1020`; `daily_ai.py:354` | CLI rc!=0。日次では`_turn_completed=True`かつrc=Noneが必要 | `cli_exit_nonzero`／pending | transport完了gate |
| `ai_runner.py:2183` | raw bytesが空 | empty/pending `empty_final_response` | 出力なし |
| `ai_runner.py:2326` | OSError/ValueError/PersistenceContractError（schemaロード・staging・raw読出等） | `runner_io_or_contract_error`／pending | 実行失敗または契約集約 |
| `copy_service.py:1162` | 新attempt前に期限経過 | expired `timed_out` | 明示期限gate |
| `copy_service.py:1195` | runner呼出が任意Exception | `runner_error_<class>`、3回まで再試行、最後generation_failed | 実行失敗 |
| `copy_service.py:1285`／`:1304` | rootがlate/timed_out/cancelled、またはadoptionがrejected_contract/conflict/expired | expired code同名／contract_error `copy_runner_contract_rejected`（原因codeは安全形式のみtrace） | 上流結果gate |
| `copy_service.py:1352`／`:1430`／`:1471` | rootまたはwriterのempty/error、ready相当bodyが空白だけ | `empty_response`／`ai_generation_error`、3回まで再試行、最後generation_failed | 結果／本文gate |
| `copy_service.py:1628` | 許可されたattempt=1..3を使い切った | attempts_exhausted `copy_attempt_limit_3` | 明示回数gate |
| `copy_service.py:922` | DB保存のsqlite.Error/OSError/InjectedFailure | pending_commit `draft_save_failed` | 保存失敗 |
| `copy_service.py:943` | commit時RejectedContract/PersistenceContractError | contract_error `draft_commit_contract_error` | 下層gateの集約 |

### app-server境界（日次は共通runnerの下で使用）

`daily_ai.py:451`はAppServerErrorをtimed_out/cancelled/その他errorへ写し、その他errorはrunnerで`cli_start_failed`へ集約する。詳細原因はapp-server transportログ側。

| `app_server.py`位置 | 条件／code | 区別 |
| --- | --- | --- |
| `:138`～`:190` | argv不正、sandboxがreadOnly/workspaceWrite以外、modelなしeffort、tierがNone/default/priority以外、fast_mode非bool、developer instructions空 | constructor ValueError、日次既定ではmodel/effort/tier指定なし |
| `:287`／`:318`～`:355` | log書込不能 `log_write_failed`、path検査不能`log_path_uninspectable`、不正`log_path_invalid`、root外`log_path_out_of_scope` | I/O失敗＋path gate。best_effort_logging=Trueなら書込失敗は継続。日次既定False |
| `:404`／`:411`／`:432`／`:435`／`:441` | `server_closed`／`rpc_write_failed`／`cancelled`／`timed_out`／`server_stdout_closed` | 通信・期限失敗 |
| `:495`／`:499`／`:502` | RPC error、history欠落、非dict RPC result | `thread_history_not_found`／`rpc_error`／`rpc_result_invalid` |
| `:552`／`:597` | account.type!=chatgpt、thread id欠落またはresume identity不一致 | `auth_mode_not_permitted`／`root_thread_identity_invalid` |
| `:675`～`:752` | model/effort/tier/fast指定時のcatalog不正または非対応 | `model_catalog_invalid`／`model_unavailable`／`effort_unavailable`／`service_tier_unavailable`／`feature_catalog_invalid`／`fast_mode_unavailable`。advisory_capability_probes=Trueなら`:289`で観測だけ。日次既定は指定なしなのでこれらprobeは走らない |
| `:761`／`:790`／`:816`／`:850`／`:897`／`:899` | 同時turn、turn id欠落、params非dict、final複数、status!=completed、final非string | `concurrent_root_turn`／`turn_identity_invalid`／`notification_params_invalid`／`multiple_final_messages`／`turn_not_completed`／`final_response_missing` |

別thread/turnの通知は`:818`／`:825`で無視（それ自体で即拒否しない）。model/effort/tier観測不一致は`:861`でobservation_status=blockedをログへ書くが、`:897`以降にその不一致を理由とするraiseはない。server requestは`:446`でunsupported_or_ungranted_requestとして拒否するが、AI最終応答そのものの拒否gateではない。

## 4. root出力形式・identityの明示gate

| `ai_runner.py`位置 | 条件 | code（adoption=rejected_contract） |
| --- | --- | --- |
| `:2194` | UTF-8 JSONではない | `final_response_not_json` |
| `:2207` | rootがdictでない／ai-job schema違反 | `root_not_object`／`root_schema` |
| `:556`／`:2213` | stdoutの非空行がJSON objectではない | `invalid_jsonl`（identityエラーがあればそちら優先） |
| `:580`～`:598` | protocol_version/schema_version=1、run/job/attempt/task/agent/skill/date/settings/input_refs/evidence_refs/input_hash/deadline_atがrequest期待値と一致しない（refsは配列順序も一致） | 不一致field名そのもの |
| `:599` | payload.referencesがlistでない、またはrequest input/evidenceの和集合にない参照を含む | `payload.references` |

root schema詳細は末尾のフィールド表。successとerror=nullの整合をschemaが要求するわけではない。通常runはschemaが許すsuccess＋error objectもそのままAIJobResultにする（`:2297`）。raw recoveryは後述のとおりsuccess＋error=nullを要求する。

## 5. native子観測・委譲・専門payloadの明示gate

| `ai_runner.py`位置 | 条件 | code（rejected_contract） |
| --- | --- | --- |
| `:795`／`:2224` | native spawn未完了、spawn role件数と実子rollout role件数不一致 | `native_spawn_child_mismatch` |
| `:2235` | require_observed_metadata=True（既定）でsession traceなし、root model/effort未観測、任意子model/effort未観測 | `session_rollout_unobserved`／`runtime_model_settings_unobserved`／`subagent_model_settings_unobserved` |
| `:1726`／`:1743` | declarationsがlistでない、要素がMappingでない | `delegations`／`delegations[i]` |
| `:1749`～`:1756` | 子roleが当taskの許可roleでない、agent/task/skill組が固定表と異なる | `delegations[i].agent_id`／`.task_id`／`.skill_name` |
| `:1757`／`:1770` | role aliasに対応する未消費native spawnなし、または子traceを対応付けできない（trace存在または観測必須時） | `.native_spawn_unobserved`／`.native_child_unobserved` |
| `:1786` | declaration input/evidenceがlistでない、または各々requestの対応集合にないrefを含む | `.refs` |
| `:1795` | statusがsuccess/empty/error以外、payload非Mapping | `.payload` |
| `:1801` | COPY／REVIEW declaration input/evidence集合がrequest集合と全一致しない | `.incomplete_source_refs` |
| `:1806` | REVIEW実子final JSON payload hashと親が申告したreview payload hash不一致／子hash未取得（trace存在または観測必須時） | `.native_review_result_mismatch` |
| `:1813`／`:1816` | role固有schema違反／下表の意味・identity検証不成立 | `.payload_schema`／`.payload_identity` |
| `:1838` | 生成したchild job idが安全IDでない | `.job_id` |
| `:1872` | 申告role件数とnative spawn件数不一致、あるいは未対応子traceが残る | `native_spawn_result_count` |

session traceは`:703`～`:783`で当thread唯一のroot rollout、妥当meta、親thread一致・source=subagent・/root/以下のagent_path等を読めた子だけを観測として採用する。欠落・不正でNone／子除外となるため、上表の未観測／件数gateに至る。COPY-REVIEWのfinal hash取得`:171`は認識するassistant final／task_completeからJSON objectをparseする。空・不正JSON・非objectならhash=None、正確な```json fenceのみ除去する。

| 専門役 | 固定task／skillと追加意味検証 | 根拠 |
| --- | --- | --- |
| AG-COPY | T02／rr-inago-copywriter。offer/versionがcopy_targetsのいずれか。payload.evidence_refsはrequest.evidence、used_input_refsはrequest.inputの部分集合。insufficient_informationはbody=""。それ以外はentities.copy_response検証 | `ai_runner.py:78`／`:1912`～`:1944` |
| AG-COPY-REVIEW | T02／rr-inago-copy-reviewer。offer/versionがcopy_targets、payload refsは上記部分集合。review本文照合・pass判定はcopy serviceで行う | 同`:1912`～`:1927` |
| AG-OBS | T02またはT04／rr-inago-observation。payload.refがrequest refs和集合内 | 同`:80`／`:1885` |
| AG-KEY | T02／rr-inago-keywords。input word_idsが非空string・unique、keywords.wordsがMapping配列でword_id順序がword_idsと同じ。decisionsがMapping配列・string word_id、snapshot/query一致、件数・unique件数・集合がknown IDsと一致 | 同`:1887`～`:1911` |
| AG-INSIGHT | T05／rr-inago-insight。全finding.evidence_refsがrequest.evidenceの部分集合 | 同`:1945` |
| AG-CONFIG | T06／rr-inago-config。requires_owner_decisionがTrue | 同`:1955` |
| AG-RECOVERY | T08／rr-inago-recovery。全cause.evidence_refsがrequest.evidenceの部分集合 | 同`:1950` |

root T02の許可子はOBS/KEY/COPY/REVIEW。ただしword_ids list＋keywords MappingがなければKEYを除外（`:259`）。T05はINSIGHT、T06はCONFIG、T08はRECOVERY、T07/T09/T10は子なし（`:88`）。root schemaのagent/task/skill enumだけでは組合せを限定せず、上の実装で組合せを限定する。`seen_copy_targets`は宣言されるがこの版では重複target拒否には使われていない。コードにないgateを追加解釈していない。

## 6. copy serviceのroot・本文・独立review受入gate

| 位置 | 条件 | 結果／code |
| --- | --- | --- |
| `copy_service.py:1248`／`:496` | rootがAIJobResultでない、job/run/attempt/task/agent/skill/date/settings/hash/input_refs/evidence_refs/deadline不一致、adoptionがpending/rejected_contract/expired/conflict以外。artifactがArtifactRecordでない、refが安全IDでない | contract_error `copy_runner_result_invalid`（artifact_ref原因を別記） |
| `:1377` | AG-COPY結果がちょうど1件でない | `copy_delegation_missing` |
| `:1397`／`:515` | writerのparent/run/attempt/task=T02/agent/skill/date/settings/deadline不一致、job id/hash/input refs空、refs重複、request refsの部分集合でない、payloadがMappingでない／offer/version不一致 | `copy_identity_mismatch` |
| `:1464` | writerがsuccess以外（empty/errorは先に再試行分岐） | `copy_payload_contract_invalid` |
| `:549` | writer payloadの9field過不足、selection_reason非空でない、used_input_refs/omitted_unknownsがstring配列でない／空白要素、used/evidenceがwriterに渡したrefsの部分集合でない、statusがready/insufficient_information以外 | `copy_payload_contract_invalid` |
| `:583` | insufficient_informationにbodyあり | `copy_payload_contract_invalid` |
| `:597` | readyのbusiness payloadがentities.copy_response違反（本文空／空白のみ、pattern enum等） | `copy_payload_contract_invalid` |
| `:606` | REVIEWがちょうど1件でない | `copy_review_contract_invalid`（one independent copy review is required） |
| `:610` | review job idがwriterと同じ、parent/run/attempt/task/skill/date/settings/deadline不一致、status!=success、job id/hash空、writer/reviewerのinput/evidence集合がroot要求と全一致しない | `copy_review_contract_invalid`（identity or source inputs differ） |
| `:629` | REVIEW payloadの8field過不足、offer/version/bodyが最終writerと不一致、reasonが非空でない、verdictがpass/revise/insufficient_information以外 | `copy_review_contract_invalid` |
| `:645` | review evidence/used/findingsが非空stringの配列でない（空配列自体は許可）、payload evidence/usedがroot提供refsの部分集合でない | `copy_review_contract_invalid` |
| `:1530` | ready writerにreview.pass以外、情報不足writerにreview.insufficient_information以外 | contract_error `copy_review_not_passed` |
| `:1550` | writerとreviewが共に情報不足 | **正常不採用** insufficient_information、DB draft保存なし。生成attemptはempty／insufficient_informationとして保存 |

上記以外の「タイトルの再掲」「水増し」「根拠の薄さ」などは専門skillの判断対象であり、このPython経路が直接文字列heuristicで判定するgateはない。本文長の数値上限、禁止語リスト、findingsが空であること、selection_reasonの内容品質を機械条件としては課していない。

## 7. 保存済み応答・履歴再開のgate

| `copy_service.py`位置 | 条件 | 結果／code |
| --- | --- | --- |
| `:1018` | 既存draft_versionがspecと異なる | existing_draft_version_mismatch。既存同versionは再生成せずalready_committed |
| `:1035` | 最新generation_attemptがin_progress | in_progress／in_progress_recovery_required、AI再呼出なし |
| `:1045` | 成功checkpointあり、committed_count>=15 | attempts_exhausted／daily_limit_reached |
| `:697` | 成功attempt/copy_job/artifactのどれかなし | PersistenceContractError→copy_checkpoint_contract_invalid |
| `:699` | root_jobなし | copy_checkpoint_inputs_unverified |
| `:703` | 保存rootのjob/run/attempt/task/skill/date/settings/status=success/adoption=pendingが不一致 | copy_checkpoint_root_invalid |
| `:715` | 保存rootのinput hashまたはinput/evidence refs集合がspecと不一致 | copy_checkpoint_inputs_changed |
| `:721` | 保存writerのrun/settings/date/attempt/task/agent/skill/status=success/adoption=pending不一致 | copy_checkpoint_contract_invalid（元文言saved copy checkpoint no longer matches this run） |
| `:733` | 保存writer input/evidence集合がspecと不一致 | copy_checkpoint_inputs_changed |
| `:737` | writer payloadがdictでない | copy_checkpoint_contract_invalid |
| `:740` | 保存reviewがちょうど1件でない | copy_checkpoint_review_missing |
| `:658` | 保存専門payload非dict、refs非list／string以外、job idがroot prefixなし。JSON decode失敗も失敗 | copy_checkpoint_specialist_invalid／copy_checkpoint_contract_invalid |
| `:752` | 復元writerが_subjob_matches不成立、保存deadline不一致 | copy_checkpoint_writer_invalid |
| `:758` | 復元reviewが通常の_validate_copy_reviewに違反（全参照集合一致を含む） | copy_checkpoint_review_invalid |
| `:763` | 保存review verdict!=pass | copy_checkpoint_review_not_passed |
| `:765` | 保存writer business payloadがentities.copy_response違反 | copy_checkpoint_contract_invalid |
| `:1056` | 上記契約／型／key／parseエラー | safeなcopy_checkpoint_*のみcode維持、それ以外copy_checkpoint_contract_invalid。saved_response_rejectedとして記録、AI再生成しない |
| `:1094` | 最新error_code=insufficient_information | 情報不足結果をそのまま再利用、新AIなし |
| `:1103`／`:47` | 最新error_codeが下記non-retryable集合 | expired/contract_errorを再返却、新AIなし |

non-retryable集合は`copy_identity_mismatch`, `copy_input_mismatch`, `copy_payload_contract_invalid`, `copy_delegation_missing`, `copy_review_contract_invalid`, `copy_review_not_passed`, `copy_runner_result_invalid`, `copy_runner_contract_rejected`, `late_result`, `timed_out`, `cancelled`, `in_progress_recovery_required`。この版の`copy_input_mismatch`は集合にはあるが、同ファイルに発火分岐はない。過去の契約拒否は製品のgateを変えただけではこの分岐から自動再試行されない。

### runner raw＋trace復旧

すべて`ai_runner.py:1034`。戻り値Noneは再採用証拠不足、例外は契約違反／I/Oとして呼出元へ返る。自動的に成功扱いしない。

| 位置 | 復旧を止める条件 | 例外／戻り値 |
| --- | --- | --- |
| `:1043` | request検証不成立 | 前記AIJobInputError等 |
| `:1050`／`:1100` | artifactまたはtrace欠落、空raw／空trace | None |
| `:1055` | data_root外、symlink/reparse point、途中消失、実fileでない | PersistenceContractError（recovery file/path理由） |
| `:1089`／`:1102`／`:1113` | 読出・UTF-8・JSON失敗、非object trace event、全trace eventのschema/run/job/attempt/task/settings identity不一致 | PersistenceContractError |
| `:1125` | invocationにjob_finishedが唯一でない、success/pending、artifact ref一致、rc=0の成功invocationがない | None。後の失敗より前の成功を使える |
| `:1152` | job_started/auth_observed/cli_finished/artifact_saved/session_trace_observed/job_finishedが各1個でない | None |
| `:1176`／`:1187` | 上記event順序不正。auth!=chatgpt、cli未completed/rc!=0、artifact不一致、terminal非success/pending/error_code非null/auth/ref/rc不一致 | 順序はPersistenceContractError、値不一致はNone |
| `:1201` | root model/effortが非空stringでない、observedと不一致 | recovery root runtime metadata is invalid |
| `:1213` | child_records／observed_children非list、spawn_countと件数不一致、両件数不一致 | recovery child-agent trace is incomplete |
| `:1222` | child非dict、agent/model/effort/auth/result_payload_hash不一致 | recovery child-agent metadata is invalid／does not match |
| `:1239` | raw非UTF-8 JSON object、root schema／identity違反、result_status!=success、error!=null | recovery response is not valid JSON／violates the AI job contract |
| `:1267`／`:1294` | child agent不明、auth!=chatgpt、session child metadata不一致 | recovery child-agent identity is invalid／native-child trace is inconsistent |
| `:1306` | _validate_delegations再検証違反（参照全一致、native review hash等を含む） | recovery delegation violates the AI job contract: <delegation_error> |
| `:1319` | started/finished/artifact created timestampがISOでない／timezoneなし | recovery trace timestamp is invalid／is not timezone-aware |

### 再開計画（AI応答受入そのものと別）

`recovery.py:166`～`:229`はsnapshot必須identity/budget不足、識別子不正、日付不正、search/count非負intでない、count>15、unfinished_generation_attempts非Mapping／件数>10000／offer id不正／attempt>3、run_status enum外、metrics_pending非boolをRecoveryPlanErrorにする。`:277`～`:283`はnow timezone、database_integrity enum、confirmation bool、export_only_failed boolを要求する。

`:297`以降の計画はDB整合okでないなら`database_integrity_not_confirmed`、本人未確認なら`owner_confirmation_required`、過去日非manualなら`manual_previous_day_target_required`、未来日なら`target_date_is_in_the_future`、完了runなら`completed_run_is_not_replayed`、非manualで13時前なら`daily_start_1300`、count>=15ならgeneration=False。`cause_resolved`は観測値として記録されるがFalseだから再開を拒否する分岐はない。これは実行しない計画APIであり、直接のAI最終応答validationとは区別する。

## 8. checkpoint／DB永続化のgate

`RejectedContract`は`PersistenceContractError`の子、`IdempotencyConflict`は`RejectedContract`の子（`contracts.py:10`～`:18`）。以下は最終的に`draft_commit_contract_error`へ集約され得るが、元の条件を残す。

| `repository.py`位置 | 条件／拒否理由 |
| --- | --- |
| `:88` | entity名が$defsにない、entity schema違反。schema違反は`<entity> schema violation at <field_path>` |
| `:1004` | DBがmemory/tempでroot確定不能（`:995`）、artifactがroot外／欠落／読出不能、bytes SHA256が記録と違う、非UTF-8 JSON／非object、copy_response schema違反 |
| `:1031` | request.response非strict JSON objectまたはcopy_response schema違反 |
| `:1049` | artifact schema違反、source_kind!=generation、status!=available |
| `:1068` | run/job/settings/draft/generator/pattern/input_hash空、attempt_noが1..3外、input_refs空／空白要素、generated_at/committed_atがtimezone付ISOでない |
| `:1136` | 同draft idまたはrun+offerで既存draftとdraft_id/offer/run/version/body/generated/committed/generator/pattern/artifact/pattern_id/evidence JSON不一致 |
| `:1145`／`:1162` | 既存draft再commitでrun settings不一致、保存jobのrun/date/settings/refs JSON/hash/attempt/artifact/success/accepted/accepted_at/task/agent/skill/payload不一致 |
| `:1183`／`:1195`／`:1212` | 再commit payload.evidenceがjob入力外、成功attemptとのartifact/draft結合なし、reservationまたはrun countがない |
| `:1234`／`:1238` | AI jobなし。run/date/settings/refs（順序＋JSON表現）/hash/attempt/artifact/status=success/adoption=pending/task=T02/agent=AG-COPY/skill/payload一致しない |
| `:1256` | evidence_refsにjob入力外のref |
| `:1263`／`:1299` | artifact entity違反、同artifact_refが異なるpath/hash/created/source/retention/statusを指す（IdempotencyConflict） |
| `:1450`／`:1468` | attempt予約のstarted_at不正、generation_attempt schema違反、clock timezoneなし |
| `:1475`～`:1515` | 予約時run不存在／非daily／非running、settings不一致、count>=15、既存draft、effective exclusion、active reservation、pending posted report |
| `:1525`／`:1527` | 最新attemptがin_progress、今回attempt_noが次番号でない／>3 |
| `:1569`／`:1581` | 完了outcomeがsuccess/empty/error外、ended_at不正、予約不存在／run/offer/attempt不一致、generation_attempt schema違反 |
| `:1603`／`:1607`／`:1627` | 既存完了outcomeが異なる、同outcome replayでartifact/error/ended値が異なる、in_progress更新件数!=1 |
| `:1666`／`:1675`／`:1699` | ai_job record全field過不足、entities.ai_job違反、payload/errorがstrict JSONにできない |
| `:1756` | 同job_idが異なる全実行データを指す（run/protocol/schema/task/agent/skill/attempt/date/settings/refs/hash/deadline/times/model/effort/auth/status/payload/error/artifact/accepted/adoption） |
| `:2123`／`:2127`／`:2133` | 非対応failpoint、artifact payload!=request payload、clock timezoneなし |
| `:2147`～`:2167` | commit時runなし／非daily／非running、settings不一致、count>=15、response offer_keyが保存offerを指さない |
| `:2179` | generation_attemptなし／非success／artifact不一致／既にdraftに結合済み |
| `:2204`～`:2209` | effective exclusion、active reservation、pending posted report |
| `:2258`／`:2269`／`:2278` | draft結合更新、pending success jobのaccepted化、running count<15への加算の各更新件数!=1 |
| `:1400`／`:1406`／`:1418` | 既存draft read-back時、run pattern version不一致、committed draftのartifact recordなし、artifact照合後のoffer/version/body/pattern/evidenceがDB draftと不一致 |

実行失敗も別に存在する。`BEGIN IMMEDIATE`、SQL INSERT/UPDATE／FK／UNIQUE／CHECK／commit、artifactファイル保存でsqlite.Error/OSErrorが発生すればtransaction rollback、応答checkpoint後なら同じ応答での再保存経路が残る。`:2281`／`:2289`のInjectedFailureは試験failpointであり通常の品質gateではない。`artifact_io.py:46`～`:76`はroot逸脱・symlink・非dir／非file、`:100`／`:122`は同artifact_refのbytes不一致、`:172`／`:216`は安全ref／source kind／時刻／JSON／bytes型を拒否する。書込不能`:95`／`:127`はI/O失敗。書込関数から出る例外はcopy checkpoint作成`:1565`等では伝播し得る。

### SQLが直接要求する保存条件

根拠は`automation/inago/sql/001_initial.sql`。後続migration 002～007はこの5テーブルの契約を再定義していない。

| テーブル・位置 | 明示DB制約（違反はSQLite例外。接続・ディスク故障とは別） |
| --- | --- |
| artifact `:127` | artifact_ref PK、path/hash/created/source/status NOT NULL、trim(hash)非空、source_kindとstatus enum |
| draft `:137` | draft_id PK、offer/run FK、version>=1、trim(body)非空、statusとpattern_id enum、artifact FK、evidence_refs_jsonはJSON array。unique(draft_id,version)、(offer_key,draft_id)、(offer_key,draft_id,version)、(offer_key,version)、(run_id,offer_key)。本文・時刻・generator/pattern version等のNOT NULL |
| generation_attempt `:158` | attempt id PK、run/offer/draft FK、attempt_no 1..3、outcome enum、unique(run,offer,attempt_no)、started_at NOT NULL |
| reservation `:172` | reservation id PK、offer FK、(offer_key,draft_id)複合FK、status active/released、release_reasonはnullまたはposted/skipped/valid_prior_post。activeはreleased_at/reason両null、releasedは両非null。`:185`でactive offerがunique |
| ai_job `:311` | job id PK、run/settings FK、task enum、attempt>=1、input/evidence JSON array、payload/errorはnullまたはJSON object/null、result/adoption enum、acceptedならaccepted_at非null。identity／model／effort／auth等のNOT NULL |

`entities.reservation:630`の条件付きschemaも、activeならreleased_at/release_reason=null、releasedならtimezone付released_atとposted/skipped/valid_prior_post理由を要求する。下のfield表だけではこのallOf条件は表れないためここへ記録した。

`orchestrator.py:4108`／`:4134`はspec欠落／run identity不一致を当商品失敗として記録。`:4180`はRejectedContractをcopy_spec_contract_rejectedへ集約して次商品へ進む。旧経路`:4537`はpending/in_progressをcopy_resume_required、`:4541`はcontract_errorを_CopyContractRejectedへ写す。これらは下層拒否の伝達であり新しい本文判定はない。

## 9. schemaフィールド表

以下は根拠版のschemaから機械抽出。各表の「必須」はそのobject内のrequired。schemaの型・enum・min/max・pattern・追加field制約は受入条件そのもの。root schemaと専門schemaの両方が評価される。`entities.draft/reservation`はDB保存形の契約参考であり、`commit_draft`はそれらを直接validate_entity呼出していない（SQL制約は別途作用）。


### ai-job.schema.json

根拠: `automation/inago/schemas/ai-job.schema.json:1`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `protocol_version` | 必須 | `{"type":"string","enum":["1"]}` | 27 |
| `schema_version` | 必須 | `{"type":"string","enum":["1"]}` | 33 |
| `run_id` | 必須 | `{"type":"string","minLength":1}` | 39 |
| `job_id` | 必須 | `{"type":"string","minLength":1}` | 43 |
| `attempt` | 必須 | `{"type":"integer","minimum":1}` | 47 |
| `task_id` | 必須 | `{"enum":["T02","T05","T06","T07","T08","T09","T10"]}` | 51 |
| `agent_id` | 必須 | `{"type":"string","enum":["AI-O"]}` | 62 |
| `skill_name` | 必須 | `{"type":"string","enum":["rakuten-room-inago"]}` | 68 |
| `target_date` | 必須 | `{"type":"string","minLength":10}` | 74 |
| `settings_version` | 必須 | `{"type":"string","minLength":1}` | 78 |
| `input_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 82 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 89 |
| `input_hash` | 必須 | `{"type":"string","pattern":"^[0-9a-f]{64}$"}` | 96 |
| `deadline_at` | 必須 | `{"type":"string","minLength":20}` | 100 |
| `result_status` | 必須 | `{"enum":["success","empty","error"]}` | 104 |
| `payload` | 必須 | `{"type":"object","object":"下表"}` | 111 |
| `error` | 必須 | `{"anyOf":[{"type":"null"},{"type":"object","required":["code","detail"],"properties":{"code":{"type":"string","minLength":1},"detail":{"type":"string","minLength":1}},"additionalProperties":false}]}` | 139 |
| `delegations` | 必須 | `{"type":"array","items":{"$ref":"#/$defs/delegation"}}` | 164 |

### ai-job.schema.json.payload

根拠: `automation/inago/schemas/ai-job.schema.json:111`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `summary` | 必須 | `{"type":"string","minLength":1}` | 119 |
| `details` | 必須 | `{"type":"array","items":{"type":"string"}}` | 123 |
| `references` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 129 |

### ai-job.schema.json.error.anyOf[1]

根拠: `automation/inago/schemas/ai-job.schema.json:144`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `code` | 必須 | `{"type":"string","minLength":1}` | 151 |
| `detail` | 必須 | `{"type":"string","minLength":1}` | 155 |

### ai-job.schema.json.$defs.delegation

根拠: `automation/inago/schemas/ai-job.schema.json:172`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `agent_id` | 必須 | `{"enum":["AG-OBS","AG-KEY","AG-COPY","AG-INSIGHT","AG-CONFIG","AG-RECOVERY","AG-COPY-REVIEW"]}` | 184 |
| `task_id` | 必須 | `{"enum":["T02","T04","T05","T06","T08"]}` | 195 |
| `skill_name` | 必須 | `{"enum":["rr-inago-observation","rr-inago-keywords","rr-inago-copywriter","rr-inago-insight","rr-inago-config","rr-inago-recovery","rr-inago-copy-reviewer"]}` | 204 |
| `input_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 215 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 222 |
| `result_status` | 必須 | `{"enum":["success","empty","error"]}` | 229 |
| `payload` | 必須 | `{"$ref":"#/$defs/delegation_payload"}` | 236 |

### ai-job.schema.json.$defs.delegation_payload.anyOf[0]

根拠: `automation/inago/schemas/ai-job.schema.json:244`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `field` | 必須 | `{"type":"string","minLength":1}` | 255 |
| `value` | 必須 | `{"anyOf":[{"type":"string"},{"type":"number"},{"type":"integer"},{"type":"boolean"},{"type":"null"}]}` | 259 |
| `position` | 必須 | `{"anyOf":[{"type":"string"},{"type":"integer"},{"type":"null"}]}` | 278 |
| `ref` | 必須 | `{"type":"string","minLength":1}` | 291 |
| `availability` | 必須 | `{"enum":["observed","unknown","unavailable","not_present"]}` | 295 |
| `unknown_reason` | 必須 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` | 303 |

### ai-job.schema.json.$defs.delegation_payload.anyOf[1]

根拠: `automation/inago/schemas/ai-job.schema.json:316`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `snapshot_id` | 必須 | `{"type":"string","minLength":1}` | 324 |
| `query_word` | 必須 | `{"type":"string","minLength":1}` | 328 |
| `decisions` | 必須 | `{"type":"array","maxItems":20,"items":"object（下表）"}` | 332 |

### ai-job.schema.json.$defs.delegation_payload.anyOf[1].decisions[]

根拠: `automation/inago/schemas/ai-job.schema.json:335`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `word_id` | 必須 | `{"type":"string","minLength":1}` | 344 |
| `decision` | 必須 | `{"enum":["query_same","duplicate_member","candidate","unknown"]}` | 348 |
| `group_ref` | 必須 | `{"anyOf":[{"type":"string","minLength":1},{"type":"null","minLength":1}]}` | 356 |
| `reason` | 必須 | `{"type":"string","minLength":1}` | 368 |

### ai-job.schema.json.$defs.delegation_payload.anyOf[2]

根拠: `automation/inago/schemas/ai-job.schema.json:379`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `offer_key` | 必須 | `{"type":"string","minLength":1}` | 393 |
| `draft_version` | 必須 | `{"type":"integer","minimum":1}` | 397 |
| `body` | 必須 | `{"type":"string"}` | 401 |
| `pattern_id` | 必須 | `{"enum":["P01","P02","P03","P04","P05"]}` | 404 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 413 |
| `selection_reason` | 必須 | `{"type":"string","minLength":1}` | 420 |
| `used_input_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 424 |
| `omitted_unknowns` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 431 |
| `status` | 必須 | `{"enum":["ready","insufficient_information"]}` | 438 |

### ai-job.schema.json.$defs.delegation_payload.anyOf[3]

根拠: `automation/inago/schemas/ai-job.schema.json:447`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `scope` | 必須 | `{"type":"string","minLength":1}` | 454 |
| `findings` | 必須 | `{"type":"array","items":"object（下表）"}` | 458 |

### ai-job.schema.json.$defs.delegation_payload.anyOf[3].findings[]

根拠: `automation/inago/schemas/ai-job.schema.json:460`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `statement` | 必須 | `{"type":"string","minLength":1}` | 469 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 473 |
| `comparison_basis` | 必須 | `{"type":"string","minLength":1}` | 480 |
| `limitations` | 必須 | `{"type":"string","minLength":1}` | 484 |

### ai-job.schema.json.$defs.delegation_payload.anyOf[4]

根拠: `automation/inago/schemas/ai-job.schema.json:495`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `proposal_id` | 必須 | `{"type":"string","minLength":1}` | 505 |
| `reason` | 必須 | `{"type":"string","minLength":1}` | 509 |
| `diff` | 必須 | `{"type":"array","items":"object（下表）"}` | 513 |
| `affected_settings` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 568 |
| `requires_owner_decision` | 必須 | `{"type":"boolean","enum":[true]}` | 575 |

### ai-job.schema.json.$defs.delegation_payload.anyOf[4].diff[]

根拠: `automation/inago/schemas/ai-job.schema.json:515`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `setting_key` | 必須 | `{"type":"string","minLength":1}` | 524 |
| `before` | 必須 | `{"anyOf":[{"type":"string"},{"type":"number"},{"type":"boolean"},{"type":"null"}]}` | 528 |
| `after` | 必須 | `{"anyOf":[{"type":"string"},{"type":"number"},{"type":"boolean"},{"type":"null"}]}` | 544 |
| `rationale` | 必須 | `{"type":"string","minLength":1}` | 560 |

### ai-job.schema.json.$defs.delegation_payload.anyOf[5]

根拠: `automation/inago/schemas/ai-job.schema.json:584`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `causes` | 必須 | `{"type":"array","items":"object（下表）"}` | 593 |
| `unfinished` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 617 |
| `resume_proposal` | 必須 | `{"type":"string","minLength":1}` | 624 |
| `owner_actions` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 628 |

### ai-job.schema.json.$defs.delegation_payload.anyOf[5].causes[]

根拠: `automation/inago/schemas/ai-job.schema.json:595`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `hypothesis` | 必須 | `{"type":"string","minLength":1}` | 602 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 606 |

### ai-job.schema.json.$defs.delegation_payload.anyOf[6]

根拠: `automation/inago/schemas/ai-job.schema.json:638`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `offer_key` | 必須 | `{"type":"string","minLength":1}` | 651 |
| `draft_version` | 必須 | `{"type":"integer","minimum":1}` | 655 |
| `verdict` | 必須 | `{"enum":["pass","revise","insufficient_information"]}` | 659 |
| `reviewed_body` | 必須 | `{"type":"string"}` | 666 |
| `reason` | 必須 | `{"type":"string","minLength":1,"pattern":"\\S"}` | 669 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 674 |
| `used_input_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 681 |
| `findings` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 688 |

### ai-copy.schema.json

根拠: `automation/inago/schemas/ai-copy.schema.json:1`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `offer_key` | 必須 | `{"type":"string","minLength":1}` | 18 |
| `draft_version` | 必須 | `{"type":"integer","minimum":1}` | 22 |
| `body` | 必須 | `{"type":"string"}` | 26 |
| `pattern_id` | 必須 | `{"enum":["P01","P02","P03","P04","P05"]}` | 29 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 38 |
| `selection_reason` | 必須 | `{"type":"string","minLength":1}` | 45 |
| `used_input_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 49 |
| `omitted_unknowns` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 56 |
| `status` | 必須 | `{"enum":["ready","insufficient_information"]}` | 63 |

### ai-copy-review.schema.json

根拠: `automation/inago/schemas/ai-copy-review.schema.json:1`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `offer_key` | 必須 | `{"type":"string","minLength":1}` | 17 |
| `draft_version` | 必須 | `{"type":"integer","minimum":1}` | 21 |
| `verdict` | 必須 | `{"enum":["pass","revise","insufficient_information"]}` | 25 |
| `reviewed_body` | 必須 | `{"type":"string"}` | 32 |
| `reason` | 必須 | `{"type":"string","minLength":1,"pattern":"\\S"}` | 35 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 40 |
| `used_input_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 47 |
| `findings` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 54 |

### ai-observation.schema.json

根拠: `automation/inago/schemas/ai-observation.schema.json:1`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `field` | 必須 | `{"type":"string","minLength":1}` | 8 |
| `value` | 必須 | `{"anyOf":[{"type":"string"},{"type":"number"},{"type":"integer"},{"type":"boolean"},{"type":"null"}]}` | 9 |
| `position` | 必須 | `{"anyOf":[{"type":"string"},{"type":"integer"},{"type":"null"}]}` | 18 |
| `ref` | 必須 | `{"type":"string","minLength":1}` | 25 |
| `availability` | 必須 | `{"enum":["observed","unknown","unavailable","not_present"]}` | 26 |
| `unknown_reason` | 必須 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` | 27 |

### ai-keywords.schema.json

根拠: `automation/inago/schemas/ai-keywords.schema.json:1`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `snapshot_id` | 必須 | `{"type":"string","minLength":1}` | 12 |
| `query_word` | 必須 | `{"type":"string","minLength":1}` | 16 |
| `decisions` | 必須 | `{"type":"array","maxItems":20,"items":"object（下表）"}` | 20 |

### ai-keywords.schema.json.decisions[]

根拠: `automation/inago/schemas/ai-keywords.schema.json:23`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `word_id` | 必須 | `{"type":"string","minLength":1}` | 32 |
| `decision` | 必須 | `{"enum":["query_same","duplicate_member","candidate","unknown"]}` | 36 |
| `group_ref` | 必須 | `{"anyOf":[{"type":"string","minLength":1},{"type":"null","minLength":1}]}` | 44 |
| `reason` | 必須 | `{"type":"string","minLength":1}` | 50 |

### ai-insight.schema.json

根拠: `automation/inago/schemas/ai-insight.schema.json:1`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `scope` | 必須 | `{"type":"string","minLength":1}` | 8 |
| `findings` | 必須 | `{"type":"array","items":"object（下表）"}` | 9 |

### ai-insight.schema.json.findings[]

根拠: `automation/inago/schemas/ai-insight.schema.json:11`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `statement` | 必須 | `{"type":"string","minLength":1}` | 15 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 16 |
| `comparison_basis` | 必須 | `{"type":"string","minLength":1}` | 17 |
| `limitations` | 必須 | `{"type":"string","minLength":1}` | 18 |

### ai-config.schema.json

根拠: `automation/inago/schemas/ai-config.schema.json:1`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `proposal_id` | 必須 | `{"type":"string","minLength":1}` | 8 |
| `reason` | 必須 | `{"type":"string","minLength":1}` | 9 |
| `diff` | 必須 | `{"type":"array","items":"object（下表）"}` | 10 |
| `affected_settings` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 38 |
| `requires_owner_decision` | 必須 | `{"type":"boolean","enum":[true]}` | 39 |

### ai-config.schema.json.diff[]

根拠: `automation/inago/schemas/ai-config.schema.json:12`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `setting_key` | 必須 | `{"type":"string","minLength":1}` | 16 |
| `before` | 必須 | `{"anyOf":[{"type":"string"},{"type":"number"},{"type":"boolean"},{"type":"null"}]}` | 17 |
| `after` | 必須 | `{"anyOf":[{"type":"string"},{"type":"number"},{"type":"boolean"},{"type":"null"}]}` | 25 |
| `rationale` | 必須 | `{"type":"string","minLength":1}` | 33 |

### ai-recovery.schema.json

根拠: `automation/inago/schemas/ai-recovery.schema.json:1`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `causes` | 必須 | `{"type":"array","items":"object（下表）"}` | 8 |
| `unfinished` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 20 |
| `resume_proposal` | 必須 | `{"type":"string","minLength":1}` | 21 |
| `owner_actions` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 22 |

### ai-recovery.schema.json.causes[]

根拠: `automation/inago/schemas/ai-recovery.schema.json:10`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `hypothesis` | 必須 | `{"type":"string","minLength":1}` | 14 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"type":"string","minLength":1}}` | 15 |

### entities共通型

| 定義 | 制約 |
| --- | --- |
| `id` | `{"type":"string","minLength":1}` |
| `date` | `{"type":"string","format":"date"}` |
| `timestamp` | `{"type":"string","format":"date-time"}` |
| `nullableTimestamp` | `{"anyOf":[{"$ref":"#/$defs/timestamp"},{"type":"null"}]}` |
| `nullableString` | `{"type":["string","null"]}` |
| `positiveInteger` | `{"type":"integer","minimum":1}` |

### entities.copy_response

根拠: `automation/inago/schemas/entities.schema.json:802`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `offer_key` | 必須 | `{"$ref":"#/$defs/id"}` | 806 |
| `draft_version` | 必須 | `{"$ref":"#/$defs/positiveInteger"}` | 806 |
| `body` | 必須 | `{"type":"string","minLength":1,"pattern":"\\S"}` | 807 |
| `pattern_id` | 必須 | `{"enum":["P01","P02","P03","P04","P05"]}` | 808 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"$ref":"#/$defs/id"}}` | 809 |

### entities.ai_job

根拠: `automation/inago/schemas/entities.schema.json:779`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `job_id` | 必須 | `{"$ref":"#/$defs/id"}` | 783 |
| `run_id` | 必須 | `{"$ref":"#/$defs/id"}` | 783 |
| `protocol_version` | 必須 | `{"$ref":"#/$defs/id"}` | 784 |
| `schema_version` | 必須 | `{"$ref":"#/$defs/id"}` | 784 |
| `task_id` | 必須 | `{"enum":["T02","T03","T04","T05","T06","T07","T08","T09","T10","T11"]}` | 785 |
| `agent_id` | 必須 | `{"$ref":"#/$defs/id"}` | 786 |
| `skill_name` | 必須 | `{"$ref":"#/$defs/id"}` | 786 |
| `attempt` | 必須 | `{"$ref":"#/$defs/positiveInteger"}` | 787 |
| `target_date` | 必須 | `{"$ref":"#/$defs/date"}` | 787 |
| `settings_version` | 必須 | `{"$ref":"#/$defs/id"}` | 788 |
| `input_refs` | 必須 | `{"type":"array","items":{"$ref":"#/$defs/id"}}` | 789 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"$ref":"#/$defs/id"}}` | 790 |
| `input_hash` | 必須 | `{"$ref":"#/$defs/id"}` | 791 |
| `deadline_at` | 必須 | `{"$ref":"#/$defs/timestamp"}` | 791 |
| `started_at` | 必須 | `{"$ref":"#/$defs/timestamp"}` | 792 |
| `finished_at` | 必須 | `{"$ref":"#/$defs/nullableTimestamp"}` | 792 |
| `resolved_model` | 必須 | `{"$ref":"#/$defs/id"}` | 793 |
| `reasoning_setting` | 必須 | `{"$ref":"#/$defs/id"}` | 793 |
| `auth_mode` | 必須 | `{"$ref":"#/$defs/id"}` | 794 |
| `result_status` | 必須 | `{"enum":["success","empty","error","timed_out","cancelled","late_result"]}` | 795 |
| `payload` | 必須 | `{"type":["object","null"]}` | 796 |
| `error` | 必須 | `{"type":["object","null"]}` | 796 |
| `artifact_ref` | 必須 | `{"$ref":"#/$defs/nullableString"}` | 797 |
| `accepted_at` | 必須 | `{"$ref":"#/$defs/nullableTimestamp"}` | 797 |
| `adoption_status` | 必須 | `{"enum":["pending","accepted","rejected_contract","expired","conflict"]}` | 798 |

### entities.generation_attempt

根拠: `automation/inago/schemas/entities.schema.json:608`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `generation_attempt_id` | 必須 | `{"$ref":"#/$defs/id"}` | 612 |
| `run_id` | 必須 | `{"$ref":"#/$defs/id"}` | 612 |
| `offer_key` | 必須 | `{"$ref":"#/$defs/id"}` | 613 |
| `draft_id` | 必須 | `{"$ref":"#/$defs/nullableString"}` | 613 |
| `attempt_no` | 必須 | `{"type":"integer","minimum":1,"maximum":3}` | 614 |
| `outcome` | 必須 | `{"enum":["success","empty","error","in_progress"]}` | 615 |
| `artifact_ref` | 必須 | `{"$ref":"#/$defs/nullableString"}` | 616 |
| `error_code` | 必須 | `{"$ref":"#/$defs/nullableString"}` | 616 |
| `started_at` | 必須 | `{"$ref":"#/$defs/timestamp"}` | 617 |
| `ended_at` | 必須 | `{"$ref":"#/$defs/nullableTimestamp"}` | 617 |

### entities.artifact

根拠: `automation/inago/schemas/entities.schema.json:716`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `artifact_ref` | 必須 | `{"$ref":"#/$defs/id"}` | 720 |
| `path` | 必須 | `{"$ref":"#/$defs/id"}` | 720 |
| `hash` | 必須 | `{"$ref":"#/$defs/id"}` | 721 |
| `created_at` | 必須 | `{"$ref":"#/$defs/timestamp"}` | 721 |
| `source_kind` | 必須 | `{"enum":["external_analysis_result","daily","metrics","generation"]}` | 722 |
| `retention_expires_at` | 必須 | `{"$ref":"#/$defs/nullableTimestamp"}` | 723 |
| `status` | 必須 | `{"enum":["available","expired","missing"]}` | 724 |

### entities.draft

根拠: `automation/inago/schemas/entities.schema.json:593`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `draft_id` | 必須 | `{"$ref":"#/$defs/id"}` | 597 |
| `offer_key` | 必須 | `{"$ref":"#/$defs/id"}` | 597 |
| `run_id` | 必須 | `{"$ref":"#/$defs/id"}` | 598 |
| `version` | 必須 | `{"$ref":"#/$defs/positiveInteger"}` | 598 |
| `body` | 必須 | `{"type":"string","minLength":1,"pattern":"\\S"}` | 599 |
| `generated_at` | 必須 | `{"$ref":"#/$defs/timestamp"}` | 600 |
| `committed_at` | 必須 | `{"$ref":"#/$defs/timestamp"}` | 600 |
| `generator_version` | 必須 | `{"$ref":"#/$defs/id"}` | 601 |
| `pattern_version` | 必須 | `{"$ref":"#/$defs/id"}` | 601 |
| `status` | 必須 | `{"enum":["committed","human_edited","posted","skipped","do_not_post_already_posted"]}` | 602 |
| `artifact_ref` | 必須 | `{"$ref":"#/$defs/nullableString"}` | 603 |
| `pattern_id` | 必須 | `{"enum":["P01","P02","P03","P04","P05"]}` | 603 |
| `evidence_refs` | 必須 | `{"type":"array","items":{"$ref":"#/$defs/id"}}` | 604 |

### entities.reservation

根拠: `automation/inago/schemas/entities.schema.json:621`。object追加field: `false`。

| field | 必須 | 型・制約 | 変更前行 |
| --- | --- | --- | --- |
| `reservation_id` | 必須 | `{"$ref":"#/$defs/id"}` | 625 |
| `offer_key` | 必須 | `{"$ref":"#/$defs/id"}` | 625 |
| `draft_id` | 必須 | `{"$ref":"#/$defs/id"}` | 626 |
| `status` | 必須 | `{"enum":["active","released"]}` | 626 |
| `created_at` | 必須 | `{"$ref":"#/$defs/timestamp"}` | 627 |
| `released_at` | 必須 | `{"$ref":"#/$defs/nullableTimestamp"}` | 627 |
| `release_reason` | 必須 | `{"type":["string","null"],"enum":["posted","skipped","valid_prior_post",null]}` | 628 |

## 10. 検証と限界

静的コード監査を実施。schema表はHEADのJSONをparseし、各property位置を元テキストから算出して抽出した。製品・試験コードを変更していないため、モデル実行やpytestは実施していない。本番本文、credentials、cookie、運用DBを読んでいない。画面に依存する不具合調査ではないためスクリーンショット・動画・traceは未取得。通常のI/O故障の具体的なOS例外は実行環境依存であり、本一覧では分岐で定義された受入停止と代表的な例外伝播を区別した。

## Review Summary

| Severity | Count | Status |
| --- | --- | --- |
| CRITICAL | 0 | pass |
| HIGH | 0 | pass |
| MEDIUM | 0 | info |
| LOW | 0 | note |

Verdict: 変更前のgate棚卸し。製品変更の承認を示すレビューではない。撤廃対象は親担当へ渡し、本記録は変更前版の監査結果として保持する。
