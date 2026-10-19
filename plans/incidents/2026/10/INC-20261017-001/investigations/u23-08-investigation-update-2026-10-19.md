# 日次不具合調査更新（2026-10-17、U23.08）

## 1. 対象runと結論

- incident_id: 352492da-ac58-4680-b1fd-fe5f24000229
- job_id: job-historical-9e7f0c175cc4c874c955a5dab29c4427
- source_launch_id: historical-9e7f0c175cc4c874c955a5dab29c4427
- source_run_id: null
- target_date: 2026-10-17（JST）。調査実施日2026-10-19へ置換しない。
- result_version: u23-08-update-v2。今回のformal attempt番号、controller指定base_commit、完全なinput_refs/evidence_refs配列は未提供。計画の作業開始HEAD d7241547d5e6111962177105c9149d6414a2ddeeを今回調査のbase_commitへ転用しない。担当stageはinvestigation。
- 結論: 前セッション供給の観測ではCLIがIntegrityErrorでfailedとなり、観測された当日run行は0だった。根本原因、未保存理由、実投稿件数、回復、再発防止はunknown。今回原証拠は再読不能で、指定計画のF001～F008の供給値として採用する。
- 今回読めたS01～S03記録は、独立した後続開始制限の除去と将来用の診断計装、隔離試験、独立製品レビューを示す。これを当日障害の原因修正や回復と扱わない。
- S04記録の20:36:40 JST時点では同じjobはrunning、resume_count=7、source_run_id=null。これは今回担当によるDB再観測ではない。以降のresume/Worker/公開結果は本版の範囲外で、親が最終報告に追記する。

## 2. 目標・実績・影響

| fact_id | 対象・期待 | 記録された実結果 | 影響・限界 | 根拠 |
|---|---|---|---|---|
| F001 | 日次完了と終了値の記録 | 13:00:04.062398 JST CLI failed、IntegrityError、error_count=1、committed_count=0、observation exit_code=4 | OSの実process終了値は独立確認なし | 計画§2、historical observation/source-evidence/production-cli/read-aggregate |
| F002 | 対象runの保存 | 14:14:35 JST当日run行0、total=2、両backupも当日0、source_run_id=null | 未保存理由unknown。runを後から生成しない | 計画§2、historical observation/source-evidence/production-cli/read-aggregate |
| F003 | 当日成果と既存分を区別 | CLI当日確定0、表示12、既存draft12、active予約12、有効publicationイベント2 | 集計範囲が異なる。実投稿ゼロを証明しない | 計画§2、historical source-evidence/production-cli/read-aggregate |
| F004 | 保全結果の記録 | 13:00:03.098658開始backup、03.888401終了backup、schema5、integrity ok、manifest一致、CLI保全/export ready | 日次回復・復元成功の証拠ではない | 計画§2、historical source-evidence/e0-manifest/production-cli/read-aggregate |
| F005 | trace保存範囲の区別 | CLI trace not_written、count0、refなし。別観測はretention begin/end計2件、partial | 全trace不存在とは断定しない | 計画§2、historical e0-manifest/production-cli/read-aggregate |
| F006 | metricsの取得状態 | unknown、当日metric観測行0 | 指標値0や取得成功へ読み替えない | 計画§2、historical production-cli/read-aggregate |
| F007 | 当日Scheduler観測 | 14:17:05観測でReady/enabled、13:00:01起動、result4 | 当時のScheduler値。現在状態やOS process終了値の直接証明ではない | 計画§2、historical observation/source-evidence/scheduler/read-aggregate |
| F008 | 捕捉ビュー範囲の記録 | 13:00:04ビュー records=[]、L02/L03各artifact1/record0、他24分類not_listed | 全ログ不存在を証明しない | 計画§2、evidence/2026-10-17/fff0d77048a7162c01da9f6a/manifest.json |

F001～F008は全て「前セッション supplied、今回原証拠再読不能」。今回確認できたのは計画にその値とrefsが記載されていること。個別の全evidence_refsは付属JSONに列挙した。各Fのevidence_refsには実際の供給元である計画v1.3の§2参照（SHA-256 BF5773E6FE9A42EBFA9DC05111E3357C05415654554A0DDCB7A95F241F8B2994）も含める。歴史refsは前セッション供給・原文未再読のまま保持する。既存12件、当日0件、publicationイベント2件を加減して別の当日成果件数を作らない。

## 3. 当日日次の失敗とrun未保存

発生区分は当日の供給観測。CLI failed/IntegrityErrorと当日run行0はF001/F002の別事実であり、同じ原因だと確定していない。制約、対象表、SQL、入力、callbackの例外発生位置、commit/rollback経路は原証拠から今回確認できない。error_count=1を原因の個数と扱わない。source_run_id=nullを提案IDや別日のrun IDで補わない。

計画§3の_create_runの処理説明は調査継続の候補で、10/17にそこで発生したとの証明ではない。今回の新しいfailure_module/failure_function/failure_line/code_sha256はpipelineの捕捉位置を表す。callback内部の例外発生位置や根本原因の表示ではない。error_name/constraint_labelもSQLite分類であり原因断定に使わない。

回復状態はunknown。今回の担当はコード、DB、Scheduler、Daily、queue/historyを変更・起動していない。

## 4. trace欠測・保全と回復

F004のbackup/integrity/manifest/readyとF005のCLI trace not_writtenを別々に保持する。retention begin/endの2件があっても、日次全体のtraceが完全だったとは言えない。13:00:03台のbackupイベントは13:00:04.062398のCLI失敗イベントより時刻上先にある。CLI保全/export readyを「失敗後に復元し回復した」という時系列に変えない。

F008のrecords=[]は指定捕捉ビューの範囲、not_listedは一覧未掲載の範囲である。ログ全件が存在しないという結論には使わない。回復操作・復元成功は未確認。元証拠不在により追加の当日検証は未実施とし、その操作の不足を後続工程のgateにしない。

## 5. 調査後の製品対応・契約結果

| update fact | 今回読んだ記録 | 確認範囲と限界 |
|---|---|---|
| U001 | S01-K01-review-retry-test-record.md | 5 focused nodesが5 passed、関連storeファイル15 passed。先行sequence行不足やchanges_requested/rejected後の再試行を扱う隔離結果。最新store SHAはU003と区別 |
| U002 | S03-R01R02-focused-retest-record.md | 20:30:10.461～20:30:17.425 JSTの最終6 nodeが6 passed/exit0。logger障害で日次結果を置換しない、rollback後のtransaction状態、捕捉位置/hashの一致を確認。rollback後の行数そのものは未検証 |
| U003 | reviews/s03-final-product-review.md | 製品4ファイルの独立review approved、追加findings0。テストコードはreview対象外。原因・回復・再発防止の確認ではない |
| U004 | S03-K08-report-stage-test-record.md | strict→partial fallbackからstub authorのrun_turnへ到達。最終整形後1 passed/exit0。結果はsynthetic_author_transport_unavailable。外部model実行成功やHTML作成の証明ではない |
| U005 | S04-production-readonly-observation.md | 20:36:40～42 JSTの親観測。maintenance schema13/quick_check ok、同job running、resume_count7、null保持。matching maintenance/Daily Python process0、関連lease released。10/19 queue1 queued/event0、watchdog queue0。Daily/Worker/Watchdog Ready+Enabled。次Daily10/20 13:00 |

U002の製品SHA-256はdaily_pipeline D010BE9965577358686EB9574AE11C59CFE85A273E9951316F1B83B7DAB7D202、trace 92A2C9D7F4395839E1590539EA5ADC57A48B267F5669FF19BC492ACC112DCAB9、orchestrator F615A5C3C8DF6A94412791F95273560CE45D023BEBA34932D80616C295D7F2A7。U003のstoreは7832451DC008144D8820D495964E083098F07C49792061319EE28FBCA08A253E。これらは対象コードの識別値で、証拠ファイルSHAでも10/17実行コードSHAでもない。

最初のS02 reviewのchanges_requested/R01・R02、試験のTypeError、K08 fixture不備によるFAILは元記録に残る。後のPASS/approvedでそれらを成功へ書き換えない。strict helperの不足だけから作者起動gateとした旧計画主張はplan review PR01で訂正済み。別件の先行sequence全行要求の除去と混同しない。

## 6. 日次が最終状態に至った経路

1. 前セッション供給のScheduler値は10/17 13:00:01起動を示す（F007）。
2. 13:00:03.098658と03.888401にbackupの開始・終了が記録される（F004）。
3. 13:00:04.062398にCLI failed/IntegrityError、error_count1、committed_count0、observation exit_code4（F001）。CLI traceはnot_written/count0（F005）。
4. 14:14:35の集計は当日run行0、metrics unknown等を示す（F002/F003/F006）。14:17:05にScheduler Ready/enabled/result4の観測（F007）。これらを日次再実行や回復と扱わない。
5. 後日のmaintenanceでは調査attempt8 completeの供給記録があり、既存独立reviewはchanges_requested。20:36:40のS04記録ではplanning attempt 3df719f6-ef87-4734-8279-9ed14b23eeaaはrunning、finished_at=null。計画は同attemptのtransport failed/成果物なしを供給しており、DB statusとtransport observationは別記録として保持する。自動planning成功へ補完しない。

10/17 CLI observation exit_code=4、Scheduler LastResult=4、OSの実process終了確認は別項目。今回時点のmatching process count=0も当日processの終了値を証明しない。実OS終了値はunknown。

## 7. 障害でなかった工程・監視/記録上の課題

| 項目 | 確認範囲 | 判定・限界 |
|---|---|---|
| backup integrity/manifest | F004の供給値 | 保全チェックのokであり、日次正常や復元成功は未確認 |
| metrics | F006 | unknown。ゼロ値/成功扱いは不可 |
| traceと捕捉ビュー | F005/F008 | 複数の観測範囲あり。全ログ不存在の断定不可 |
| 日次診断計装 | U002/U003 | 隔離検証と製品review実施。10/17原因の追加証拠ではない |
| 10/19除外・次回自然日次 | U005/計画§0 | 親観測ではqueueは未処理。10/20自然runは本版でnot_observed。再発なしとしない |
| formal deployment ledger | 計画§3/§12 | active_commit不一致の供給記載。今回本番反映/一致は確認していない |
| 入力と実行情報 | 本割当 | formal attempt/base_commit/完全refs、実model/effort/tier観測は未提供。high指定で実施し観測不足を開始条件にしない |

## 8. 原因確度と次の検証候補

確認できたのは当日供給観測F001～F008と後日の記録U001～U005。根本原因はunknown。仮説H01「run作成transaction中の制約失敗」、H02「別callback/工程でのIntegrityError」、H03「診断保存/相関不足で発生工程を復元できない」は候補で、いずれも当日の因果として確定しない。H03は観測限界を説明する候補で、業務失敗の原因とは限らない。

| 候補・未確認 | 次に必要なtrace/log | 区別できること・担当への引継ぎ |
|---|---|---|
| H01とH02の切り分け | 次の実在launchのphase begin/error/end、許可済みoperation label、SQLite error_code/error_name/constraint_label、transaction begin/commit/rollback/rollback_failed | transaction内か他工程かを絞る。分類だけで制約/入力/原因を確定しない |
| run保存有無 | proposed_run_id、persisted_run_id、persistedフラグを別欄に保存し、実在launchに結び付ける | 提案ID付きretention traceを永続run成功と誤認しない。欠測/hash取得失敗はnull/unknownのまま |
| 捕捉位置の一貫性 | failure_module/function/line/code_sha256を同一pipeline捕捉位置としてlog/JSONLへ保存 | callback例外の起点と混ぜない。必要なら別の許可済みphase labelでcallback境界を識別 |
| H03とdiagnostic I/O障害 | trace request/result/status、失敗分類と後処理の完了結果 | 元のdaily resultと診断保存失敗を別に追う。SQL/params/例外自由文/traceback/商品/認証情報は保存しない |
| 実終了値・回復 | CLI観測値、Scheduler結果、外部で確認したprocess exit、回復操作の有無を別欄へ | 同じ整数や現在process不在からOS終了値/回復を補完しない |

S02は上記の一部を計装しU002/U003の範囲で確認済み。次は親が反映/同一job再開/HTML作成・独立review・指摘反映・公開の実値を記録する。原因不明、review status、missing evidence、retry回数は後続gateにしない。一人のローカルシステムで堅牢性を品質目標とせず、別名の承認/上限/receipt/lease条件を追加しない。計装を原因修復・再発防止と呼ばない。

## 9. 根拠・確認限界

以下のhistorical refsとSHA/時刻は計画§2の供給値で、原証拠を今回再読・再ハッシュしていない。各行を別証拠として保持する。

| evidence_ref | SHA-256（供給） | 観測・採取時刻（供給） | 対応 |
|---|---|---|---|
| historical-input/2026-10-17/observation.json | 48b96385ba6b0d90859f7b53e0b66acc654e4a1ec4cfbaaa47beb945ce11e8f6 | observed_at=2026-10-17T13:00:04.062398+09:00 | F001/F002/F007 |
| historical-input/2026-10-17/source-evidence.json | 0ece63496cf9423eca7e7cff4fa8b720ad06048cf962a1fa49ccb4f8c1d9e8c5 | 独立採取時刻unknown。内包CLIイベント13:00:04.062398 JST | F001～F004/F007 |
| historical-input/2026-10-17/e0-manifest.json | 2b457d5a85a8b0859487ed5fddab81dec52cb96e57bf2a0e6e301ced0dca35e8 | captured_at_jst=2026-10-17T14:17:06+09:00、manifest_version1.0 | F004/F005 |
| historical-input/2026-10-17/production-cli.filtered.json | bcb6ae7f881d4806b7234646393987a3d814536f529853d9d5f70f0d8acd70cf | at_jst=2026-10-17T13:00:04.062398+09:00、独立採取unknown | F001～F006 |
| historical-input/2026-10-17/scheduler.filtered.json | a638cdc1a72974c510501c0907448b73214529b736bb0d6d7c21d529374771e8 | observed_at_jst=2026-10-17T14:17:05+09:00 | F007 |
| historical-input/2026-10-17/read-aggregate.json | e97a935bacdfc9b3dc79c9262567fb9a2a8cca2bf71e7f53ed845d747e6e5c27 | observed_at_jst=2026-10-17T14:14:35+09:00 | F001～F007 |
| evidence/2026-10-17/fff0d77048a7162c01da9f6a/manifest.json | unknown（未提供） | ビューcaptured_at=2026-10-17T13:00:04+09:00 | F008 |

今回読んだlocal記録のSHA-256（原表は20:37:52 JST取得。S04の包装時追記版と旧版の対応は下記で明示する。収集/実行時刻は各記録内の値で、hash時刻と混同しない）：

| local reference（incident folderからの相対path） | SHA-256 | 内容確認範囲 |
|---|---|---|
| 日次不具合修正計画_2026-10-17.md | BF5773E6FE9A42EBFA9DC05111E3357C05415654554A0DDCB7A95F241F8B2994 | v1.3、F001～F008/旧調査・review供給値/S01～S03記録 |
| test-results/S04-production-readonly-observation.md（20:37:52時点の観測本文版、現行ファイル先頭3750 bytes） | 49602DAFEE1821B4EAC268075E85F29FDF4B9922C65D1CF20226BBCD844D36CE | capture 20:36:40.7904901～20:36:42.2412393 JST、U005の対象 |
| test-results/S04-production-readonly-observation.md（JSON包装時・独立review時・今回再確認の追記版全体） | 26417D003BB1AA9B6502C00ED240332232E2D38798E757827CBFBCB45D5B7A79 | 上記本文に20:38 pre-resume/resume結果を追記。U005の観測区間は変更しない |
| test-results/S01-K01-review-retry-test-record.md | 893BC255B09A3C9271C2E88C7BEE49C5BD96F2858829E717D4D0D8D0F41E4806 | 20:01:38～49 JST、U001 |
| test-results/S03-R01R02-focused-retest-record.md | C32D6929595FF7BD840F26B3751ECC92E62429D62E8617C0B7A17A5DD2D8AC6C | 最新run 20:30:10～17 JST、U002と旧FAIL |
| test-results/S03-K08-report-stage-test-record.md | 41D7473C4273E214C0252AB1A917FEA7BFF681F76883710F679FF6C4DC71902B | 最終20:31:24～25 JST、U004と旧fixture FAIL |
| reviews/s03-final-product-review.md | 1F62AE65E01B38607B749B66A1D6B0FF335E17F0D62C58874B2C525B8C3DC822 | 2026-10-19 JST、U003 |
| reviews/s02-product-review.md | E429E8B3E08BA2983C80325E354D6348C1079FDE754AFE5AEC2B39072AAEF27A | R01/R02の過去changes_requested、対象版は別 |
| reviews/plan-v1.1-review.md | 7CFA4BC364901E472B738D0AA51CE11357D258572992CEBA161199283CDACC5E | PR01～PR04、strict helperだけの断定を訂正 |

IR-U23-01への版対応確認: 現行S04ファイルは20:38の追記を含み、全体SHAは26417D003BB1AA9B6502C00ED240332232E2D38798E757827CBFBCB45D5B7A79。旧49602…は20:37:52に取得した、20:36の観測本文だけを含む版である。今回、現行ファイルの先頭3750 bytes（`## Post-push, immediate pre-resume check`見出しの直前に追加された区切りCRLFを除いたprefix）をメモリ上で再ハッシュし、49602DAFEE1821B4EAC268075E85F29FDF4B9922C65D1CF20226BBCD844D36CEとの一致を確認した。独立した旧snapshotファイルは未提供だが、旧本文のbytes対応は現行prefixで照合できた。facts JSONのlocal_evidenceは包装時の追記版全体26417…を指し、U005は引き続き20:36:40～42の観測だけを使う。20:38のqueued/resume_count8をU005へ混入しない。

独立reviewの範囲制限: 歴史原snapshot7件はreviewerにも今回供給されず、計画供給値との照合に限られる（IR-U23-03）。F008 SHAはunknownを維持する。U001～U004の元試験・製品review・旧review記録は調査担当が指定入力として読んだが、独立調査reviewerの指定入力外だった（IR-U23-04）。同reviewerが原記録の詳細時刻・code SHA・旧FAIL・local証拠SHAまで再検証したとは表現しない。記録が不存在だという所見でもない。

独立review記録はreviews/u23-08-investigation-review.md/.json、2026-10-19T20:41:19+09:00、対象u23-08-update-v1、status=changes_requested。IR-U23-01の版対応とIR-U23-02の実供給元evidence_refs追加を本版v2へ反映した。v2の再reviewは未実施であり、旧判定をapprovedに置換しない。review statusは後続gateではない。

job-input結合SHA e97b191fdde1eb2f178c53bf51a5ad61979b69b80868a9a31b8accf31ab48d30はidentity用。旧review入力facts SHA 29bdb2f2736b4a93de67f78003f792a57520cbd7147d4bde3fefa1498da2fd59、report SHA 20881918f8d27ca2e863a569a4b9b3967fac08cf1104a57155f9300b5e68b397は供給値のみで原文未再読。証拠SHA、base_commit、現行コードSHAを置換しない。

本書は2026-10-19に/root/u23_08_investigation_updateが新しい調査更新として作成した。既存attemptの成果物を変更していない。本版v2の独立再reviewは親へ依頼する段階で未実施。v1の独立調査reviewは上記のとおり実施済み。DB/Scheduler/外部資料の追加探索、製品変更、日次起動、commit/pushは担当範囲外として実施せず、親が後続操作を継続する。
