# rakuten-room-bakemono-kai 実装実行計画

作成日：2026-09-24  
対象プロジェクト：`C:\project\rakuten-room-bakemono`  
設計の正本：`bakemonoスキル改変構成.md`

## 0. 本番運用開始日の前提

- **新スキルによる日次タスクと実投稿の開始日は、2026-09-25 JSTとする。**
- 2026-09-24は実装・試験・切替準備日として扱い、新スキルの本番投稿日には数えない。9月24日分の本番15件を後追い生成・投稿する運用にはしない。
- 最初の本番日次計画、投稿管理HTMLの本番セクション、投稿予定、日次manifestの対象日は **2026-09-25** とする。
- Windows日次タスク `RakutenRoomBakemonoKaiDaily` の開始境界も2026-09-25とし、開始前の日付を `missed` と判定しない。health checkも同じ `operation_start_date` を参照する。
- 9月24日に行うテストでは、実運用正本へ本番投稿済み状態を書き込まない。日付付きE2Eが必要な場合は隔離fixture workspaceを使い、テストデータであることを明示する。
- 9月25日の投稿そのものはユーザー本人が行う。新スキルは15件または実際に確認・準備できた件数の本文と投稿管理HTMLを用意するところまでを担当する。
- 2026-09-25の開始時刻までにStep 16の切替ゲートを通過できない場合、テストや安全確認を省略して新自動タスクを強行しない。その場合は旧スキルを有効なまま残し、9月25日分だけ新候補の確認済み成果物を使った手動運用へ切り替え、未検証の自動化は合格後に開始する。この代替判断と理由を日次状態へ記録する。

## 1. この計画の使い方

この計画は、**Step 00から順番に1 Stepずつ実行する**。各Stepの「AIエージェント投入用プロンプト」を、そのまま実装担当エージェントへ渡せる。後続Stepは、先行Stepの受け入れ条件と成果物がすべてそろうまで開始しない。

各Stepの担当エージェントは次を守る。

1. 作業開始時に、設計の正本である `bakemonoスキル改変構成.md` と、そのStepで指定した先行成果物を読む。
2. `rakuten-room-kai/implementation/execution-ledger.json` に開始状態を記録し、完了時に結果、検証コマンド、終了コード、変更ファイル、残課題を追記する。
3. `rakuten-room-kai/implementation/reports/step-XX.md` を作成し、実装内容、テスト結果、未検証範囲、次Stepへの引き継ぎを残す。
4. 失敗した検証を省略・成功扱いにしない。修正可能ならそのStep内で修正し、再実行する。
5. 次Stepの作業へ勝手に進まない。
6. 楽天ID、パスワード、Cookie、Access Key、Codex認証情報を出力・ログ・プロンプトへ含めない。`rakuten-api.credentials.json` や `auth-state.json` の本文を表示しない。
7. 楽天ROOMへの投稿、いいね、フォロー、商品購入などの外部状態変更を行わない。自動化対象は調査、分析、計画、HTML生成、状態保存までとする。
8. 現在の作業フォルダはGitリポジトリではない。Gitを勝手に初期化しない。各Stepでは変更ファイル一覧と検証ログで作業内容を追跡する。
9. **Step 17までは従来スキルを変更、移動、無効化しない。** 従来Windowsタスク `RakutenRoomBakemonoAffiliateDaily` もStep 17まで有効なまま残す。
10. 既存技術部品の再実装を避け、次を原則として再利用する。
    - `scripts/check-affiliate.mjs`
    - `scripts/collect-room-likes.mjs`
    - `scripts/research-brief.mjs`
    - `scripts/research-batch.mjs`
    - `scripts/rakuten-api.mjs`
    - 各 `*-lib.mjs` と既存テスト

## 2. 全体の完了条件

全Step完了時、次を満たすこと。

- `.agents/skills/rakuten-room-bakemono-kai/` と7つの判断系サブスキルが有効である。
- Pythonオーケストレータ、Codex CLIモデル切替、監査ログ、ロック、再開、状態確認、日次ヘルスチェックが動作する。
- 日次、リサーチ、成果分析、初期設定、構成変更、障害復旧、状態確認の全経路が、手動、チャット、スケジューラの該当入口から動作する。
- 1日15件、5ジャンル、再投稿禁止、120件在庫、投稿管理HTML、アフィリエイト、ROOMいいね、成果学習が統合される。
- 正常、部分完了、本人対応待ち、失敗、再開、二重起動、未起動、heartbeat停止の経路が検証される。
- 従来スキルはCodexの探索対象から外れ、従来Windowsタスクは無効になる。
- 従来スキルの全コードは、ファイル一覧付きで `archive/legacy-skills/` に退避され、復元手順がある。削除しない。
- 新スキル切替後の手動、チャット相当、Windowsタスク起動が再検証される。
- 2026-09-25を最初の本番対象日として日次成果物が作成され、9月24日以前を新運用の未実行日として警告しない。

## 3. Step一覧

| Step | 区分 | 内容 | 推奨モデル |
|---:|---|---|---|
| 00 | 実装準備 | 現状固定、ファイル一覧、実行台帳、受け入れ条件一覧 | `gpt-6-sol / high` |
| 01 | 実装 | 元スキル複製、新オーケストレータ／サブスキル雛形 | `gpt-6-sol / high` |
| 02 | 実装 | 設定、JSON Schema、データ正本、移行処理 | `gpt-6-sol / high` |
| 03 | 実装 | Python実行基盤、監査ログ、ロック、原子的保存 | `gpt-6-sol / high` |
| 04 | 実装 | Codex CLIランナー、モデル切替、再試行・昇格 | `gpt-6-sol / high` |
| 05 | 実装 | 既存JS技術部品のアダプタ、在庫・観測統合 | `gpt-6-sol / high` |
| 06 | 実装 | 投稿管理HTMLとローカル状態保存サーバー | `gpt-6-sol / high` |
| 07 | 実装 | 成果帰属、A/B/C/保留、翌日反映エンジン | `gpt-6-sol / high` |
| 08 | 実装 | 判断系サブスキルとLLMジョブ契約 | `gpt-6-astra / high` |
| 09 | 実装 | 全フローのオーケストレータ実装 | `gpt-6-astra / high` |
| 10 | 実装 | Python入口、チャット経路、Windowsタスク／ヘルスチェック | `gpt-6-sol / high` |
| 11 | テスト | 単体・Schema・スキル構造・回帰テスト | `gpt-6-sol / high` |
| 12 | テスト | オフライン結合テストと全分岐テスト | `gpt-6-sol / high` |
| 13 | テスト | 投稿管理HTMLのPlaywright E2E | `gpt-6-sol / high` |
| 14 | テスト | 障害注入、復旧、監査、セキュリティテスト | `gpt-6-astra / high` |
| 15 | 全Path | 実環境スモーク、手動・チャット・scheduler全経路 | `gpt-6-astra / xhigh` |
| 16 | 切替判定 | 独立レビュー、要件照合、リリース候補固定 | `gpt-6-astra / xhigh` |
| 17 | 無効化 | 従来スキルと従来Windowsタスクの無効化 | `gpt-6-sol / high` |
| 18 | 退避 | 従来スキル全コードのファイル一覧付き退避 | `gpt-6-sol / high` |
| 19 | 最終確認 | 切替後再検証と復元訓練 | `gpt-6-astra / high` |

---

## Step 00：現状固定と実装台帳の作成

### AIエージェント投入用プロンプト

~~~text
あなたは rakuten-room-bakemono-kai 実装のStep 00担当です。作業場所は C:\project\rakuten-room-bakemono です。これは実装前の現状固定と受け入れ条件整理です。後続実装には進まないでください。

最初に必ず読むもの:
- C:\project\rakuten-room-bakemono\bakemonoスキル改変構成.md
- C:\project\rakuten-room-bakemono\.agents\skills\SKILL.md
- package.json
- scripts/ と tests/ の全ファイル名

実施内容:
1. `rakuten-room-kai/implementation/` と、その下の `reports/`、`baselines/`、`fixtures/` を作る。
2. `execution-ledger.json` を原子的に作成する。最低限 `plan_version`、`current_step`、各Stepの `status`、`started_at`、`finished_at`、`report_path`、`verification_commands`、`exit_codes`、`changed_files`、`blockers` を持たせる。Step 00だけを running にして開始し、完了時に success へ更新する。
3. 従来スキルを構成する `.agents/skills/SKILL.md`、`.agents/skills/assets/`、`references/`、`scripts/`、`templates/`、`webapp/` の相対ファイル一覧とサイズを `baselines/legacy-skill-files.json` に保存する。認証情報の本文は読まない。
4. 現在のプロジェクト技術部品、Node/Python/Codex CLIのバージョン、既存Windowsタスク `RakutenRoomBakemonoAffiliateDaily` の状態とアクションを `baselines/environment.json` に保存する。Windowsタスクは変更しない。タスク定義XMLは `baselines/RakutenRoomBakemonoAffiliateDaily.xml` にエクスポートする。
4a. `environment.json` と `acceptance-criteria.md` に、本番運用開始日 `2026-09-25`、準備日 `2026-09-24`、最初の本番投稿日 `2026-09-25` を記録する。
5. `npm test`、`npm audit --audit-level=high`、`node --check` 対象スクリプトの構文確認を実行し、結果を `baselines/pre-implementation-tests.txt` に保存する。
6. `bakemonoスキル改変構成.md` の各「受け入れ条件」と必須方針を抽出し、重複を整理して `acceptance-criteria.md` を作る。最低限、運用件数、5ジャンル、重複禁止、HTML、アフィリエイト、ROOM巡回、オーケストレータ、モデル切替、監査ログ、成果学習、全実行入口、旧スキル退避を含める。各条件に `AC-001` 形式の一意IDを付ける。
7. 現在がGitリポジトリでないことを確認する。Git初期化はしない。
8. `reports/step-00.md` に実行結果、ベースラインのファイル数と合計サイズ、既存テスト結果、未解決事項を記録する。

禁止事項:
- 従来スキル、既存scripts、既存Windowsタスクを変更しない。
- `rakuten-api.credentials.json`、Playwrightのauth-state、Cookie、パスワードを表示・複製しない。
- 新スキル本体の実装を開始しない。

受け入れ条件:
- execution-ledger.json が有効なJSONである。
- 従来スキルの全ファイルが相対パスとサイズ付きで一覧化されている。
- 既存テスト結果とWindowsタスク定義が保存されている。
- acceptance-criteria.md の各条件が設計書の節番号へ逆参照できる。
- 従来スキルをこのStepで変更していない。

最終報告は、作成ファイル、実行コマンドと終了コード、受け入れ条件の件数、ベースライン異常の有無、Step 01へ渡す注意点を簡潔に示してください。
~~~

---

## Step 01：新スキルの複製とスキル群の雛形作成

### AIエージェント投入用プロンプト

~~~text
あなたはStep 01担当です。作業場所は C:\project\rakuten-room-bakemono です。Step 00がsuccessであることを execution-ledger.json で確認してください。

目的:
従来スキルを変更せずに複製し、`rakuten-room-bakemono-kai` オーケストレータスキルと判断系サブスキル群の発見可能な雛形を作る。まだ業務ロジックやPythonオーケストレータは実装しない。

最初に読むもの:
- bakemonoスキル改変構成.md の第1〜10節、第15〜17節
- .agents/skills/SKILL.md と配下の既存resources
- C:\Users\ute3g\.codex\skills\.system\skill-creator\SKILL.md
- Step 00の acceptance-criteria.md と legacy-skill-files.json

所有範囲:
- `.agents/skills/rakuten-room-bakemono-kai/**`
- `.agents/skills/rr-kai-research-plan/**`
- `.agents/skills/rr-kai-triage/**`
- `.agents/skills/rr-kai-product-verify/**`
- `.agents/skills/rr-kai-copywriter/**`
- `.agents/skills/rr-kai-quality-gate/**`
- `.agents/skills/rr-kai-daily-plan/**`
- `.agents/skills/rr-kai-performance/**`
- `.agents/skills/rr-kai-recovery/**`

実施内容:
1. 従来スキルの `SKILL.md`、`assets`、`references`、`scripts`、`templates`、`webapp` を新しい `.agents/skills/rakuten-room-bakemono-kai/` 内へ明示的にコピーする。コピー元を再帰探索してコピー先自身を取り込まない。コピー直後のコピー元・コピー先の相対パス、ファイル数、サイズを `rakuten-room-kai/implementation/baselines/kai-initial-copy.json` に保存する。
2. 新スキルのYAML nameを `rakuten-room-bakemono-kai` に変更し、descriptionへ「化け物・改を召喚して」「今日の日次フロー」「商品補充」「成果分析」等の明確な起動条件を記す。従来スキルのnameや起動文は新スキルのトリガーとして残さない。
3. 新SKILL.mdを短いオーケストレータ入口へ整理する。詳細は `references/routing.md`、`daily-flow.md`、`output-contracts.md`、`data-policy.md` へ分離する。元スキルから必要な方法論、テンプレート、確度ラベル、コンプライアンス規則は消さず、参照先を明確にする。
4. 8つの `rr-kai-*` サブスキルを作る。各SKILL.mdは、担当範囲、入力JSON、出力JSON、読むべき参照、成功条件、推測禁止事項、正本を直接書換えないこと、担当モデルは外部model-policyが決めることを明記する。
5. `rr-kai-observe`、`rr-kai-inventory`、`rr-kai-api-collect`、`rr-kai-dashboard` はLLMスキルではなくローカル工程なので、独立SKILL.mdを作らず、オーケストレータ参照文書にローカル工程として定義する。
6. 各スキルに必要なら `agents/openai.yaml` を作る。自動発見を維持し、display_name、short_description、default_promptをSKILL.mdと一致させる。余計な依存や明示起動限定は付けない。
7. skill-creatorの `quick_validate.py` を新オーケストレータと全サブスキルに実行する。
8. 従来スキル側を編集対象にしていないことを変更ファイル一覧で確認する。
9. `reports/step-01.md` とexecution-ledgerを更新する。

品質条件:
- SKILL.mdへ実装詳細を詰め込みすぎず、条件付き詳細はreferencesへ分ける。
- サブスキルが台帳、HTML、post-statusを直接更新する指示を持たない。
- 投稿自動化や認証情報入力を指示しない。
- 1日15件、5ジャンル、再投稿禁止、ID付き報告を新オーケストレータから追跡できる。

受け入れ条件:
- 9スキルすべてquick_validate成功。
- 新スキル名、説明、参照パスに旧名由来の誤った相対パスがない。
- 従来スキルのファイルを変更していない。
- 未完成のTODO、サンプルプレースホルダー、空SKILL.mdがない。

最終報告では、作成スキル一覧、検証結果、コピー後に整理したファイル、Step 02で必要なデータ契約を示してください。次Stepへ進まないでください。
~~~

---

## Step 02：設定、Schema、データ正本、移行処理の実装

### AIエージェント投入用プロンプト

~~~text
あなたはStep 02担当です。Step 00と01がsuccessであることを確認してください。設計の正本は bakemonoスキル改変構成.md です。

目的:
オーケストレータと全工程が共有する設定、JSON Schema、データ正本、旧データからの非破壊移行を実装する。LLMやブラウザはまだ起動しない。

所有範囲:
- `rakuten-room-kai/orchestrator/**` のconfigとschemas
- `rakuten-room-kai/` の新しい台帳雛形
- `automation/config_loader.py`
- `automation/schema_validator.py`
- `automation/migrate_legacy_data.py`
- `automation/requirements.txt` または同等の依存定義
- 対応するPythonテスト

実施内容:
1. `rakuten-room-kai/orchestrator/automation-config.json` を作る。`operation_start_date: "2026-09-25"`、JST、日次予定13:00、health check猶予60分、日次15件、在庫120件、最大3並列、保持期間、ポート、各タイムアウト、dry-run既定値を持たせる。時刻、開始日、件数をPythonへ直書きしない。
2. `model-policy.json` を作る。設計書第15節の全ジョブについて通常モデル、reasoning effort、1回の通常再試行、昇格先、昇格条件、昇格禁止条件を表現する。Astraは例外昇格専用にする。
3. `workflow.json` を作る。daily、research、performance、setup、config-change、recovery、status、health-checkの工程DAG、依存、必須成果物、並列可否、失敗時の終了状態を定義する。
4. 設計書記載の6 Schemaに加え、manifest、event、inventory-brief、research-plan、daily-plan、post-status、product-ledger、room-observation、affiliate-observation、job-envelope、health-statusのSchemaを作る。`additionalProperties`の扱いを意図的に決め、必須項目、enum、ID形式、JST日付、URLを検証する。
5. Python 3.12用依存を定義する。JSON Schema検証には保守されたライブラリを使用し、バージョン範囲を記録する。テスト依存も分離または明記する。`.venv-kai/` を `.gitignore` へ追加するが、Git初期化はしない。
6. `rakuten-room-kai/products.json`、`drafts.json`、`posts-log.jsonl`、`post-status.json`、`dashboard-days.json` の初期形式を定義する。商品IDは全ジャンル横断で一意、投稿済みIDは再利用不能、同一商品コード・正規化URL・型番候補を検出できる形にする。
7. `migrate_legacy_data.py` をTDDで作る。`rakuten-room/*.md` を読取専用入力にし、新正本が空の場合だけ移行する。P03を投稿済みとして取り込む。既存新正本がある再実行では重複を作らない。旧ファイルは変更しない。
8. 移行前後の件数、ID対応、未解釈行、衝突をmigration report JSONへ出す。曖昧なデータを推測で補わず `needs_review` にする。
9. Schema検証と移行処理のテストを先に書き、正常、必須欠落、追加不正項目、重複ID、再実行、壊れた旧行を検証する。
10. reports/step-02.mdとledgerを更新する。

固定業務値:
- ジャンル: 時短・料理・調理・家電=6、家事ラク用品=2、住まい・収納・インテリア=2、子育て・キッズ用品=3、親の美容・セルフケア=2。
- 1日15件、在庫120件、完成下書き120件、次の2日分30件。
- 同一商品再投稿禁止。

受け入れ条件:
- 全JSONがSchema自身と整合する。
- 移行の2回実行で結果が増殖しない。
- 旧rakuten-room配下と従来スキルが無変更。
- 秘密情報を新設定へコピーしていない。
- Pythonテストが成功し、異常ケースが0や空の正常値へ変換されない。

最終報告では、Schema一覧、正本一覧、移行件数、needs_review、テスト結果、Step 03が利用するAPIを示してください。
~~~

---

## Step 03：Python実行基盤、監査ログ、ロックの実装

### AIエージェント投入用プロンプト

~~~text
あなたはStep 03担当です。Step 02のSchemaと設定を契約として使い、Pythonの共通実行基盤をTDDで実装してください。業務フロー本体やCodex CLI起動はまだ実装しません。

所有範囲:
- automation/execution_logger.py
- automation/run_lock.py
- automation/atomic_io.py
- automation/redaction.py
- automation/run_context.py
- automation/kai_orchestrator.py の骨格
- rakuten-room-kai/orchestrator/status、history、runsの生成処理
- 対応テスト

必須仕様:
1. run_idは `<JST日付>-<時刻>-<flow>-<6桁乱数>`。再試行は新run_id、parent_run_id、attemptを持つ。
2. Python入口の最初にrun_startedを記録する。manifestは running で作り、step_started、heartbeat、step_completed/failed、run_completedをevents.jsonlへ追記する。
3. manifestとstatus JSONは同一ディレクトリの一時ファイルへ書いてfsync後に置換する。events/historyは追記専用で、event_id重複を防ぐ。
4. 終了状態は success、partial、action_required、failed。終了コードは0、2、3、1へ対応させる。
5. エラーコードは設計書第18節をすべて定義し、未分類例外用コードも設ける。例外でも可能な限り終了時刻、最終工程、エラー要約をmanifestへ残す。
6. stdout/stderr、CLIイベント、例外、設定の保存前にAccess Key、Cookie、Authorization、password形式、楽天ID候補をマスクする。秘密値そのものをテストfixtureへ入れず、明確なダミー値を使う。
7. Windows対応のプロセスロックと対象日・flowロックを実装する。同じ日の日次を二重起動させず、古いロックはheartbeatとPIDを検査して自動削除ではなくstale候補として扱う。
8. `--resume`用に完了工程、失敗工程、必須成果物を復元できるRunContext APIを作る。完了済み正本更新を重複実行しない。
9. 月次history、latest、latest-daily、latest-daily-status.txtを生成する。ログ保存失敗を黙らせない。
10. 単体テストで正常完了、各終了状態、例外、原子的置換、同時ロック、stale、再開、redaction、イベント重複を検証する。
11. `operation_start_date` より前の日は日次未起動の `missed` にせず、明示的に `before_operation_start` として正常に除外するテストを追加する。最初の本番対象日が2026-09-25になることを確認する。

禁止事項:
- subprocessでCodexや既存JSをまだ起動しない。
- dashboardや商品台帳を直接実装しない。
- Windowsタスクを変更しない。

受け入れ条件:
- 強制例外を起こした試験でもmanifestがfailedで閉じる。
- 同時2プロセスの片方だけがロック取得する。
- ログの秘密パターン検査が0件。
- resume時に完了工程を二重記録しない。
- 全Pythonテスト成功。

reports/step-03.mdとexecution-ledgerを更新し、公開API、テスト結果、既知の制限を報告してください。
~~~

---

## Step 04：Codex CLIランナーとモデル切替の実装

### AIエージェント投入用プロンプト

~~~text
あなたはStep 04担当です。既存Codex CLIは0.155.0-alpha.16で、`codex exec`、`-m/--model`、`-c model_reasoning_effort=...`、`--output-schema`、`--json`、`--output-last-message`、`--ephemeral`、`-C`、`--skip-git-repo-check`が利用可能です。実装時にもhelpを再確認し、存在しないフラグを使わないでください。

所有範囲:
- automation/codex_cli_runner.py
- automation/job_prompt_builder.py
- automation/model_policy.py
- CLI runnerテストとfake Codex executable

実施内容:
1. shell文字列を組み立てず、subprocessへ引数配列を渡す。プロンプトは標準入力だけで渡す。cwdはプロジェクトルート、`--skip-git-repo-check`、`--ephemeral`、モデル、reasoning effort、output schema、output-last-message、JSON eventsを明示する。
2. サブエージェントへ渡す内容を、担当サブスキル絶対パス、job-envelope JSON、入力成果物パス、出力Schema、禁止事項、完了条件に限定する。親会話履歴や認証ファイルを渡さない。
3. CLI stdout JSONL、stderr、最終JSONをrun_id配下へ保存し、Step 03のredactionを通す。出力はJSONとして読み、Schema検証し、入力にない商品ID、URL不一致、必須成果物欠落を成功にしない。
4. model-policy.jsonから通常モデルと推論量を選ぶ。通常再試行は同モデルで1回まで。その後、定義された条件だけ上位モデルへ昇格する。INPUT_INVALID、AUTH_REQUIRED、CAPTCHA、取得データ不足では昇格しない。
5. timeout、キャンセル、非0終了、壊れたJSON、Schema不適合、出力ファイル欠落、モデル名不正を個別エラーコードへ変換する。
6. テスト用にCodex実体パスを依存注入できるようにする。本番既定は実体を解決して絶対パスを固定し、schedulerではPATHだけに依存しない。
7. fake CLIで、成功、初回失敗後成功、Schema不正、timeout、昇格、昇格禁止、stdout/stderrの秘密マスク、スペースを含むパス、プロンプト中の特殊文字を検証する。
8. 本Stepでは実モデルの高コスト実行を行わない。`codex --version` とhelp、fake CLIの試験までに留める。実モデルはStep 15で検証する。

受け入れ条件:
- コマンドインジェクション可能なshell=Trueや文字列連結がない。
- fake CLIで全モデルポリシー分岐が通る。
- 不正出力が正本へ到達しない。
- 認証情報名とダミー秘密値がprompt/logに残らない。
- Step 03のmanifestにモデル、推論量、再試行、昇格履歴が残る。

reports/step-04.mdとledgerを更新し、生成した実コマンド例は秘密を含まない形で示してください。
~~~

---

## Step 05：既存技術部品のアダプタと在庫・観測統合

### AIエージェント投入用プロンプト

~~~text
あなたはStep 05担当です。既存のNode/Playwright/API処理を再利用し、Pythonオーケストレータから安定して呼べるアダプタを実装してください。既存スクリプトの振る舞いを不要に書き直さないでください。

対象の既存部品:
- npm run affiliate:check
- npm run room:likes:collect -- --days 30
- npm run research:brief
- npm run research:collect
- npm run rakuten:search / ranking / verify

所有範囲:
- automation/adapters/**
- automation/inventory_service.py
- automation/observation_service.py
- automation/research_collect_service.py
- 必要な既存Nodeスクリプトへの後方互換なJSON出力オプション
- テストfixtureとテスト

実施内容:
1. 各既存コマンドを引数配列で起動し、timeout、終了コード、stdout/stderr、成果物の更新時刻を検査するPythonアダプタを作る。
2. 人向けstdoutを解析の正本にしない。既存JSON成果物または新設する `--json-output <path>` を正本にする。既存手動コマンドは壊さない。
3. affiliateは日別と注文詳細、observed/0/unavailableを保持する。ROOM巡回は30日、固定投稿除外、未照合、取得失敗を保持する。APIリサーチは生件数、重複除外、already-posted、planned、in-ledger、newを保持する。
4. inventory_serviceで、post-status、products、drafts、posts-logをID結合し、確認済み・未投稿・完成下書きのジャンル別件数、15件不足数、120件在庫との差を出す。投稿済みID、同一itemCode、正規化URL、同一型番疑いを候補から除外またはreviewへ回す。
5. 既存P03を投稿済みとして扱い、投稿済み商品を日数経過や文章変更で復帰させない。
6. 商品ページ確認用に、選別済み候補だけをPlaywrightで開き、URL、商品名、型番、価格、販売状態、ポイント/クーポンの表示と期限、レビュー根拠、取得時刻、取得失敗をJSON snapshotへ保存する薄いNode処理を追加する。最大15件/バッチ、読取専用、推測禁止とする。
7. fixtureを使ったテストで正常、0件、取得失敗、古い成果物、部分失敗、重複、15件不足なし/あり、商品ページ取得失敗を検証する。
8. 実サイトへの不要な反復アクセスは避け、本Stepの実通信は既存のread-only smokeを各1回までとする。

受け入れ条件:
- 既存 `npm test` が全件成功。
- Pythonアダプタテスト成功。
- LLMを使わずinventory briefとobservation snapshotを作れる。
- 取得失敗が0件へ変換されない。
- 認証ファイル本文がrun outputsへ複製されない。

reports/step-05.mdとledgerを更新し、再利用した処理、追加したJSON契約、実通信結果を報告してください。
~~~

---

## Step 06：単一投稿管理HTMLと状態保存サーバー

### AIエージェント投入用プロンプト

~~~text
あなたはStep 06担当です。bakemonoスキル改変構成.md第11節を完全に満たす投稿管理画面とローカル保存処理を実装してください。TDDで進め、ブラウザE2E本番はStep 13で行います。

所有範囲:
- automation/dashboard_service.py
- automation/dashboard_server.py
- automation/run_dashboard.py
- rakuten-room-kai/posting-dashboard.html
- dashboard用のassetsが必要ならrakuten-room-kai/dashboard-assets/**
- 関連テスト

機能要件:
1. HTMLは常に `rakuten-room-kai/posting-dashboard.html` 1ファイル。同じ日を再生成しても日付セクションを増殖させず、その日だけ更新する。
2. 最新日が本文と左目次の先頭。最新日を初期展開し、過去日は折り畳む。各日見出しに投稿済み数/予定数を表示する。
3. 各カードにID、商品名、ジャンル、予定時刻、楽天商品リンク、コピー用本文、コピーボタン、投稿済みcheckboxを表示する。本文コピーにIDや管理メモを混ぜない。
4. IDでproducts、drafts、daily-planを結合する。欠落、重複ID、URL不一致はカードを作らずDATA_MISMATCHとして返す。
5. post-status.jsonを唯一の投稿済み正本とする。localStorageは折り畳み等の画面設定に限る。checkbox更新はサーバー成功後だけ画面へ確定する。
6. サーバーは127.0.0.1だけで待受ける。POSTはOrigin/Host、Content-Type、起動ごとのCSRF tokenを検証し、任意パスを読書きさせない。外部送信しない。
7. checkboxをtrueにしたら商品ID、予定日、JST更新日時、履歴を原子的に保存する。falseへの訂正は明示的な確認フラグと訂正理由を要求し、商品を自動で候補在庫へ戻さない。
8. サーバー停止中またはfile://ではcheckboxを無効化し、「保存サーバー未接続」を表示する。
9. 商品名、本文、URLをHTMLへ埋め込む際は適切にエスケープし、script閉じタグやイベント属性を注入できないようにする。
10. 単体/結合テストで日付順、同日更新、コピー対象、状態永続化、訂正、サーバー停止、重複ID、XSS文字列、同時更新を検証する。

受け入れ条件:
- 設計書第11節の6受け入れ条件をテストIDへ対応付ける。
- post-statusの再読込後もcheckedが復元される。
- checkbox以外の操作では投稿済みにならない。
- 投稿済みIDがinventory候補へ戻らない。
- dashboard生成とAPIテストが成功。

reports/step-06.mdとledgerを更新し、Step 13で使うE2E起動コマンドを明記してください。
~~~

---

## Step 07：成果帰属・A/B/C/保留・翌日反映エンジン

### AIエージェント投入用プロンプト

~~~text
あなたはStep 07担当です。bakemonoスキル改変構成.md第19節を正本として、成果学習の決定的な前処理、帰属、台帳更新を実装してください。LLMが行う意味判断用payloadも作りますが、本StepでCodex CLIは呼びません。

所有範囲:
- automation/performance_service.py
- automation/attribution.py
- automation/pattern_ledger.py
- rakuten-room-kai/performance/**
- 関連Schema調整とテスト

実施内容:
1. post-status/products/posts-log、ROOMいいねlatest、affiliate日別・注文詳細を読み、投稿別データ、日別aggregate、shop aggregate、購入商品データを分離する。
2. 商品一致はitemCode/正規化URLが得られる場合を最優先し、次にNFKC、前後空白除去、連続空白圧縮、大文字小文字統一をした商品名完全一致、利用可能ならショップ名一致で判定する。部分一致、意味類似、LLM推測は禁止する。
3. 注文商品が投稿済み商品に一致したら `post_confirmed_by_order_product_match` として確定帰属する。一致しない購入だけを `purchased_item_unattributed`、「購入商品は確認済み・入口投稿は未特定」にする。同じ正規化キーが複数IDならDATA_MISMATCHで確定しない。
4. 日別クリック合計を特定投稿へ配らない。いいね0をクリック0、購入0へ変換しない。未取得、未照合、期間未成熟を0へ変換しない。
5. LLMへ渡すperformance inputを作り、投稿別反応、aggregate、観測期間、attribution level、比較可能性、過去pattern ledgerを明示する。
6. performance出力の適用処理を作る。Aは同条件の別商品3件のresearch request、Bは1変数だけのexperiment、Cは条件の優先度低下、保留は非除外。投稿済み商品自体の再投稿要求を拒否する。
7. winning_conditions、single_change_experiments、deprioritized_conditions、research_requests、daily_plan_constraintsをpattern-ledgerとresearch-queueへ追記・更新し、出所run_idと根拠を保持する。
8. 同じ観測の再実行で履歴を重複させない。注文ステータス変化は新観測として残す。
9. テストfixtureで、商品一致購入、未一致購入、複数一致、日別クリックのみ、いいね強/弱/0、未取得、A/B/C/保留、少数観測、再実行、注文状態変化を検証する。

受け入れ条件:
- 設計書第19節の9受け入れ条件を自動テストへ対応付ける。
- 一致購入だけが投稿別確定成果になる。
- 非帰属データからC判定が作られない。
- performance出力がresearch-planとdaily-plan用に必須項目を持つ。
- 全テスト成功。

reports/step-07.mdとledgerを更新し、判定表とfixture対応を報告してください。
~~~

---

## Step 08：判断系サブスキルとLLMジョブ契約の完成

### AIエージェント投入用プロンプト

~~~text
あなたはStep 08担当です。Step 01のサブスキル雛形を、実運用できる詳細指示と入出力契約へ完成させ、Step 04のCLIランナーから呼べるjob定義を実装してください。

対象サブスキル:
- rr-kai-research-plan
- rr-kai-triage
- rr-kai-product-verify
- rr-kai-copywriter
- rr-kai-quality-gate
- rr-kai-daily-plan
- rr-kai-performance
- rr-kai-recovery

実施内容:
1. 各SKILL.mdに目的、適用範囲、必須入力、参照ファイル、判断規則、禁止事項、出力Schema、成功条件を記す。一般論や重複説明はreferencesへ分離する。
2. research-planは5ジャンルの不足、勝ち条件、優先度低下、research queueから検索要求を作る。保留を除外しない。
3. triageはAPI候補だけで実ページ確認対象を最大15件へ絞る。API候補を適格確定しない。
4. product-verifyはStep 05の実ページsnapshotを根拠に、商品名、型番、販売状態、価格条件、レビュー根拠、広告加熱項目、確度を判定する。取得失敗を補完しない。
5. copywriterは確認済み商品だけに、ID外部表示とコピー本文を分離し、誇大表現、薬機法違反、根拠なきNo.1、購入強要を避けた日本語本文を作る。
6. quality-gateはcopywriterと別jobとして、ID・商品名・URL・本文、根拠、NG表現、指定された単一変更を検査する。不合格理由と再作成対象を構造化する。
7. daily-planはローカル制約結果を使い、6/2/2/3/2の15件、重複なし、投稿済みなし、本文あり、成果条件反映を満たす。15件未満なら実数と不足理由を返す。
8. performanceはStep 07入力からA/B/C/保留と9配列を出力する。非帰属aggregateを投稿別成果にしない。
9. recoveryは失敗run/stepだけを診断し、安全な局所再実行案を返す。認証・CAPTCHA・入力不足をモデル昇格で解決しようとしない。
10. `automation/jobs/` に各jobのprompt templateとjob-envelope builderを作る。モデル名は埋め込まずmodel-policyを使う。
11. fake CLIで全jobについてSchema準拠成功、不足入力拒否、禁止出力拒否、ID/URL不一致拒否を試す。
12. quick_validateを全スキルへ再実行する。

受け入れ条件:
- 各サブスキルが正本を直接更新しない。
- 全jobが明示的Schemaを持つ。
- copywriterとquality-gateが別job/別実行になる。
- 低コストモデルで通常完了し、昇格条件だけがmodel-policyにある。
- fake CLI contract testsとskill validationが全成功。

従来スキルは変更しない。reports/step-08.mdとledgerを更新して終了してください。
~~~

---

## Step 09：オーケストレータ全フローの実装

### AIエージェント投入用プロンプト

~~~text
あなたはStep 09担当です。これまでのサービスとjobを結合し、`kai_orchestrator.py` に全業務フローを実装してください。薄いCLI入口やWindowsタスク登録はStep 10です。

実装対象フロー:
- daily
- research
- performance
- setup
- config-change
- recovery
- status/read-only

dailyの必須順序:
1. run_id、ロック、manifest running。
2. post-status、商品、下書き、model policy、最新状態をsnapshot。
3. affiliate、ROOM30日巡回、inventory briefを、競合しない範囲で最大3並列。
4. rr-kai-performanceを必ず実行し、Schema検証。
5. その出力をresearch-planとdaily-planの必須入力へ渡す。空でも有効JSONを渡す。
6. 15件分の確認済み未投稿下書きがあるか判定。不足時だけresearchを実行。ただしresearch_requestsは常にqueueへ保存。
7. research時は API collect→triage→product capture/verify→copywriter→別quality-gate→適格在庫反映。
8. daily-plan→ローカル制約再検査。
9. dashboard、台帳、performance ledger、当日成果物を直列更新。
10. `ID｜商品名｜商品URL｜ジャンル｜コピー用本文` を含むdaily reportを保存。
11. 必須成果物を確認してsuccess/partial/action_required/failedで終了。

運用開始日の条件:
- 本番modeでは `operation_start_date` より前の日付の日次計画、投稿管理HTML本番セクション、投稿予定を新規作成しない。
- 最初の本番対象日は2026-09-25。9月24日の実装試験はdry-runまたは隔離fixtureだけで行う。
- 9月25日の同日再実行は同じ日付セクションを更新し、重複作成しない。

共通要件:
- dry-runではrun成果物だけ作り、正本、dashboard、schedulerを変更しない。
- resumeは未完了工程だけ実行し、正本反映を二重化しない。
- setupは必要ファイルと移行を検査し、勝手にプロフィールを確定しない。
- config-changeは差分、影響、検証後に設定を更新し、壊れた配分を拒否する。
- recoveryは対象run_id/stepが必須で、無関係工程を再実行しない。
- statusは保存済みログを読むだけでLLMを呼ばない。
- 同一ファイルへの書込みは直列、独立読取だけ並列。
- 必須成果物欠落時に終了コード0を返さない。

テスト:
- fake adapters/fake CLIで各フローのhappy pathを実行。
- daily在庫十分でresearchをskipする経路。
- daily在庫不足でresearch全工程を通る経路。
- 15件未満のpartial。
- auth_required、取得失敗、Schema invalid、quality再作成、再開、二重起動。
- performance出力が後続2工程へ渡ったことをmanifest/input snapshotで確認。

受け入れ条件:
- workflow.jsonと実装工程が一致。
- 全フローが同じlogger、lock、Schema validator、model policyを使う。
- daily reportとdashboard入力が同じdaily-plan JSON由来。
- 投稿済み商品を選ばない。
- fake環境の全フローtest成功。

reports/step-09.mdとledgerを更新し、各フローの成果物表を付けて終了してください。
~~~

---

## Step 10：実行入口、チャット経路、Windowsタスク、ヘルスチェック

### AIエージェント投入用プロンプト

~~~text
あなたはStep 10担当です。Step 09の処理本体を複製せず、全入口と運用部品を実装してください。本Stepでは本番Windowsタスクを登録・変更しません。登録スクリプトの生成とテストだけです。

作成対象:
- automation/run_daily.py
- run_research.py
- run_performance.py
- run_setup.py
- run_config_change.py
- run_recovery.py
- run_orchestrator.py
- show_status.py
- check_daily_health.py
- run_dashboard.py
- register_kai_daily_task.ps1
- 必要なら unregister/disable/restore補助スクリプト

要件:
1. run_*.pyは薄い入口とし、flowとsourceをkai_orchestratorへ渡すだけにする。処理本体を重複させない。
2. 共通引数 `--date`、`--dry-run`、`--resume`、`--source scheduler|chat|manual`、対象run/step、`--json` を一貫させる。不正組合せは開始ログを残したうえでINPUT_INVALIDにする。
3. show_statusは `--latest`、`--flow --date`、`--failed-only --days`、`--run-id` を実装し、LLMを呼ばず終了状態、時間、工程、準備件数、本人対応事項、ログパスを表示する。
4. health checkは予定時刻+猶予、run_started、最終manifest、heartbeatを照合し、missed、stale、healthy、action_requiredをhealth.jsonと月次履歴へ保存する。実行中プロセスを勝手に終了しない。
5. register scriptは `.venv-kai\Scripts\python.exe` または設定済み絶対Pythonを使う。Dailyは2026-09-25の13:00 JSTから、HealthCheckは同日の14:00 JSTから有効になるよう開始境界を設定し、WakeToRun、StartWhenAvailable、多重起動禁止、同一Windowsユーザーを設定する。タスク名は `RakutenRoomBakemonoKaiDaily` と `RakutenRoomBakemonoKaiHealthCheck`。登録日がすでに開始日を過ぎている場合は、初回実行を黙って過去扱いせず、StartWhenAvailableまたは明示的オンデマンド実行のどちらを使ったか記録する。
6. register scriptに `-WhatIf` または `-ExportOnly` を設け、本Stepでは実登録しない。生成したXML/設定をfixtureで検証する。
7. 新オーケストレータSKILL.mdのroutingを、ユーザー依頼→対応Python入口 `--source chat` へ結びつける。チャットとschedulerが同じ実装を使う。
8. READMEではなく、スキルreferencesまたは既存設計文書へ実行方法を記す。
9. CLI parser、status、health、task XML、chat routingのテストを作る。

受け入れ条件:
- 全入口で同じrun形式が残る。
- `--source`がmanifestへ保存される。
- task XMLにWakeToRun、StartWhenAvailable、固定python path、working directoryがある。
- task XMLとautomation-configの開始日が2026-09-25で一致し、health checkが9月24日以前をmissedにしない。
- 本番Windowsタスク一覧が作業前後で変わっていない。
- 全テスト成功。

reports/step-10.mdとledgerを更新し、ここで「実装フェーズ完了」判定を出してください。旧スキルはまだ変更しないでください。
~~~

---

## Step 11：単体・Schema・スキル構造・回帰テスト

### AIエージェント投入用プロンプト

~~~text
あなたはテストフェーズStep 11担当です。実装済みコードに対して単体・契約・回帰テストを網羅し、失敗は最小修正して再検証してください。新機能追加を目的にしないでください。

実施内容:
1. acceptance-criteria.mdの各ACを、unit、contract、integration、E2E、liveのどこで検証するか `test-traceability-matrix.md` に割当てる。未割当を0にする。
2. 全Python moduleのunit testsを実行し、core（config、schema、logger、lock、redaction、CLI runner、inventory、attribution、dashboard、orchestrator）の行カバレッジ85%以上、分岐80%以上を目標にする。低い場合は意味ある境界テストを追加する。
3. `npm test`を実行し、既存48件以上が回帰なく成功することを確認する。
4. 全JSON Schemaについてvalid/invalid fixtureを検証する。必須欠落、enum外、追加不正項目、日付/URL/ID不正を含める。
5. 全9スキルへquick_validateを実行し、参照リンクの存在、YAML nameとdirectory名、未完TODO、空ファイル、旧相対パスを検査する。
6. `python -m compileall automation`、PowerShell parser、Node `--check` を実行する。
7. migration、同日dashboard、同一run resume、同値observationのidempotency testを再確認する。
8. secret scanningを実施し、credentials/auth-state本文、Access Key形式、Cookieが新成果物とログにないことを確認する。
9. すべてのコマンド、終了コード、カバレッジをreports/step-11.mdへ記録する。

合格条件:
- test-traceability-matrixの未割当ACが0。
- Python/Node/skill/schema/構文テストが全成功。
- 既存テストを削除・skipして合格させていない。
- 重大なcoverage欠落がない。達成不能なら具体的理由と対象行をblockerにする。
- 従来スキルとWindowsタスクが無変更。

execution-ledgerを更新し、Step 12へ渡す未検証経路を一覧化してください。
~~~

---

## Step 12：オフライン結合テストと全分岐テスト

### AIエージェント投入用プロンプト

~~~text
あなたはStep 12担当です。外部サイトと実LLMをmock/fakeに置き換え、全フローと主要分岐を決定的に結合テストしてください。

必須シナリオ:
1. daily + 在庫15件以上: observe→performance→research skip→daily-plan→dashboard→success。
2. daily + 在庫不足: research-plan→API collect→triage→verify→copy→quality→在庫反映→daily-plan。
3. daily + 適格商品不足: 実数でpartial、見ていない商品を追加しない。
4. research単独、performance単独、setup、config-change、recovery、statusの正常経路。
5. setup後の再実行、daily同日再実行、resumeで重複なし。
6. affiliate observed 0、注文あり、注文商品一致、注文商品不一致、unavailable。
7. ROOM likes observed、確認済み0、未照合、取得失敗、30日境界。
8. performance A/B/C/保留、Aから別商品3件、B単一変更、C条件低下、保留非除外。
9. model同一再試行、許可された昇格、昇格禁止。
10. source manual/chat/schedulerを引数レベルで全フローに通し、manifest sourceを確認。

方法:
- 時刻、乱数、外部process、Codex CLI、Playwright/API成果物を依存注入する。
- fixtureは `rakuten-room-kai/implementation/fixtures/integration/` に置き、実運用正本を変更しない一時workspaceで実行する。
- path coverageを `path-coverage-offline.json` に保存し、各pathにtest名、結果、run_id、成果物を紐付ける。
- failureを修正したら、そのfailureを再現する回帰テストを先に追加する。

合格条件:
- 必須10群の全ケースpass。
- 正本への二重書込み、投稿済み再採用、非帰属売上の投稿帰属が0。
- fake外部依存の呼出し順・入力・モデルがworkflow/model-policyと一致。
- manifest、events、status、historyの整合性エラー0。
- 実楽天、実Codex、実Windowsタスクを変更していない。

reports/step-12.md、path coverage、ledgerを更新して終了してください。
~~~

---

## Step 13：投稿管理HTMLのPlaywright E2E

### AIエージェント投入用プロンプト

~~~text
あなたはStep 13担当です。ローカルfixture workspaceとPlaywrightを使い、投稿管理HTMLと状態サーバーをブラウザでE2E検証してください。実楽天ROOMへの操作は禁止です。

必須E2E:
1. 3日分を生成し、最新日が左目次と本文の先頭、最新だけ展開、過去日が折り畳み。
2. 目次クリックで対象日を展開しスクロール。
3. 同じ日の再生成で日付セクションが増えず、本文だけ正しく更新。
4. 各商品のリンクhref、表示商品名、ID、ジャンル、予定時刻、本文がfixtureと一致。
5. 各copyボタンがそのIDの本文だけをclipboardへ入れ、別IDや管理IDを混ぜない。
6. checkboxを入れるとサーバー保存後にcheckedになり、再読込・サーバー再起動後も復元。
7. リンクを開く、本文コピー、折り畳みだけではcheckedにならない。
8. checkbox解除は確認と理由なしでは拒否。訂正後も商品が自動で未投稿候補へ戻らない。
9. サーバー停止、500、競合更新でcheckboxが元に戻り、エラー表示。
10. file://表示では保存不能を示しcheckbox無効。
11. 商品名/本文へHTML、script、イベント属性、改行、絵文字を入れてもXSSせず表示・コピーできる。
12. ID重複、draft欠落、URL不一致を画面・日次結果へエラー表示し、誤カードを作らない。

成果物:
- Playwright test、必要なpage object、fixture。
- 失敗時screenshot/trace。成功時は代表画面1枚。
- `path-coverage-dashboard.json`。

合格条件:
- Chromium/Edgeで全ケースpass。
- console error、未処理promise、外部network requestが0。
- post-statusと画面カウントが一致。
- 設計書第11節の受け入れ条件6件すべてにE2E test IDがある。

reports/step-13.mdとledgerを更新し、実行コマンドとartifact pathを報告してください。
~~~

---

## Step 14：障害注入、復旧、監査、セキュリティテスト

### AIエージェント投入用プロンプト

~~~text
あなたはStep 14担当です。失敗時に誤って成功・0件・投稿済みへ変換されないことと、復旧・監査・秘密保護を検証してください。

障害注入ケース:
- affiliateログイン切れ、注文XHR失敗、CSV列変更、CSV0件。
- ROOMページ構造変更、巡回上限、未照合、ネットワークtimeout。
- 楽天API 429/5xx/不正JSON/資格情報なし。
- Codex CLI未ログイン、timeout、非0終了、壊れたJSON、Schema不正、出力欠落。
- quality-gate連続不合格、許可昇格、昇格後失敗。
- dashboard書込失敗、壊れたpost-status、同時更新。
- ロック競合、stale heartbeat、強制終了、resume。
- 予定時刻未起動によるmissed、health check自体の遅延。
- ログ書込失敗、ディスク容量不足を模した例外。

検証要件:
1. 各ケースが設計されたerror code、終了状態、終了コードになる。
2. unavailableを0件、failedをsuccessへ変換しない。
3. recoveryは失敗stepだけを再実行し、完了stepと正本更新を重複しない。
4. action_requiredには本人が行う具体的1手を残すが、ID/パスワードを求めない。
5. secret scanでprompt、manifest、events、stdout/stderr、status、dashboard、test artifactに秘密がない。
6. localhost serverは外部interfaceへbindせず、Origin/CSRF/path traversal/JSON size/content-typeを検査する。
7. subprocessにshell injectionがなく、URL/ID/日付入力が検証される。
8. Windows ACL設定対象と認証ファイルの扱いをsecurity reviewする。
9. `show_status --failed-only` とhealth.jsonが障害を正確に表示する。
10. npm auditとPython依存監査を実行する。利用可能な標準監査ツールを使い、導入のために秘密を送らない。

成果物:
- `failure-injection-matrix.json`
- security review report
- 追加回帰テスト
- reports/step-14.md

合格条件:
- CRITICAL/HIGH/MEDIUMの未解決所見0。
- 全障害注入ケースに期待結果と実結果がある。
- 復旧後の正本が破損・重複していない。
- 従来スキルと既存本番タスクはまだ変更していない。

ledgerを更新し、残るlive-only検証をStep 15へ明記してください。
~~~

---

## Step 15：実環境スモークと全Path受け入れテスト

### AIエージェント投入用プロンプト

~~~text
あなたはStep 15担当です。オフライン試験済みのリリース候補を、実環境でread-onlyまたはローカル生成までの範囲で検証し、全実行経路のpath coverageを完成させてください。楽天ROOMへの自動投稿・いいね・購入は禁止です。従来スキルと旧Windowsタスクはまだ無効化しません。

事前条件:
- Step 11〜14がsuccess。
- failure/security matrixに重大未解決がない。
- 既存認証状態を本文表示せず利用できる。
- 13:00前後の旧affiliate taskとブラウザプロファイルが競合しない時間に実施する。

実施する実Path:
1. `run_setup.py --source manual --dry-run`。
2. `run_performance.py --source manual --dry-run`。実affiliate/ROOM保存値を読み、正本非更新で成果物生成。
3. `run_research.py --source manual --dry-run`。実楽天APIを最低限利用し、API候補→絞込み→最大少数ページのread-only snapshot→LLM判断まで確認。
4. `run_daily.py --source manual --dry-run`。observe、performance、在庫、必要時research、daily plan、dashboard preview、manifest完了まで。
5. chat相当経路として、Codex CLIへ新オーケストレータスキルの絶対パスと「今日の日次フローをdry-runで実行」を渡し、正しく `run_daily.py --source chat --dry-run` を選ぶことを確認する。同様にresearch、performance、status、recoveryのroutingを検査する。
6. model-policyの `gpt-6-luna`、`gpt-6-sol` を各1件、最小Schema jobで実呼出しする。Astraは昇格経路の最小jobを1件だけ実行し、通常経路で呼ばれていないことも確認する。モデル、effort、Schema、usage eventをmanifestへ保存する。
7. 新Windowsタスクは本番名でまだ登録せず、候補名 `RakutenRoomBakemonoKaiDailyCandidate` と `...HealthCheckCandidate` で一時登録する。スケジュール競合を避け、`Start-ScheduledTask`によるオンデマンド実行で `source=scheduler`、working directory、固定Python、終了コード、ログを確認後、候補タスクを無効化する。削除する場合は先にXMLを保存する。
8. show_statusの全オプションを実データで確認する。
9. dailyの在庫十分pathと不足pathのうち実状態で通らない方は、同じリリース候補を隔離fixture workspaceでE2E実行し、実行バイナリと設定が同じことを記録する。
10. `full-path-coverage.json` を作り、全flow、source、主要branch、status、model、artifactを行列で管理する。未実行、失敗、根拠なしを0にする。
11. 本番対象日2026-09-25でdaily dry-runを行い、最初の本番日付が9月25日、9月24日分の本番投稿計画が生成されないことを確認する。投稿管理HTMLの本番更新は切替ゲート通過後に限る。

実環境で変更してよいもの:
- rakuten-room-kai/orchestrator/runs以下のtest run成果物。
- dry-run previewとimplementation test artifacts。
- 候補Windowsタスク。旧タスクは変更禁止。

合格条件:
- manual/chat/schedulerの該当入口が同じorchestratorへ到達。
- 7 flowとstatus/healthの全pathがpass。
- 全3モデルの選択がpolicyどおり。
- dry-runでproducts、post-status、dashboard本番正本が変わらない。
- 外部状態変更0、秘密漏えい0。
- full-path-coverageの未合格行0。

reports/step-15.md、full-path-coverage.json、ledgerを更新してください。失敗が1件でもあればStep 16へ進めずblockedにしてください。
~~~

---

## Step 16：独立レビューとリリース候補固定

### AIエージェント投入用プロンプト

~~~text
あなたはStep 16のリリース判定担当です。実装者の主張を鵜呑みにせず、設計、コード、テスト、実Path証跡を独立に監査してください。旧スキルの無効化はまだ行いません。

実施内容:
1. bakemonoスキル改変構成.mdの全節とacceptance-criteria.mdを、test-traceability、full-path-coverage、実成果物へ照合する。
2. code review、Python review、security review、skill structure review、Windows task reviewを行う。CRITICAL/HIGH/MEDIUMを分類する。
3. `npm test`、Python full suite、coverage、skill quick_validate、Schema tests、Playwright E2E、failure matrix、secret scanをクリーン状態から再実行する。
4. 新スキルが旧スキルの必要な方法論、確度ラベル、コンプラ、保存データを失っていないか確認する。
5. 日次performance→research-plan/daily-planの必須受け渡し、商品一致購入の確定帰属、非帰属クリック、再投稿禁止を実成果物で確認する。
6. 全pathの必須成果物とmanifestが一致するか確認する。
7. 旧スキルがStep 17以前に変更・無効化されていないことを、作業記録とファイル一覧で確認する。
8. 従来スキルを退避する前の二重バックアップとして、現在のlegacy packageを `archive/legacy-skills/.staging/` にZIP化し、ファイル一覧、件数、合計サイズを保存する。ZIPを開いて一覧を読み取れることを確認し、コピー元は変更しない。
9. `release-readiness.md` を作り、各ACをPASS/FAIL/BLOCKED、証拠path、残リスクで示す。

切替ゲート:
- CRITICAL/HIGH/MEDIUM未解決0。
- 全AC PASS。
- full-path-coverage未合格0。
- rollback用staging archiveが作成され、ZIP内のファイル一覧を読み取れる。
- 旧スキルがまだ有効で、意図しない変更が記録されていない。

1つでも満たさない場合、execution-ledgerをblockedにし、Step 17を実行禁止と明記してください。満たす場合だけ `cutover_approved: true` をledgerへ記録してください。

最終報告には、レビュー所見、全検証結果、cutover可否、staging archive pathを記載してください。
~~~

---

## Step 17：従来スキルと従来Windowsタスクの無効化

### AIエージェント投入用プロンプト

~~~text
あなたはStep 17の切替担当です。これは従来スキルを無効化するStepです。コード退避はStep 18で行います。

絶対前提:
- execution-ledgerのStep 16がsuccessかつ `cutover_approved: true`。
- release-readiness.mdにFAIL/BLOCKEDがない。
- staging archiveが存在し、ZIP内のファイル一覧を正常に読み取れる。
前提を満たさない場合は何も変更せず停止してください。

実施順序:
1. 変更前に `.agents/skills/SKILL.md` のパス、サイズ、更新日時と旧タスク状態を再記録する。
2. 新タスク `RakutenRoomBakemonoKaiDaily` と `RakutenRoomBakemonoKaiHealthCheck` をStep 10の登録スクリプトで登録する。最初は無効状態または予定実行を開始しない状態で定義を検査する。
2a. 新タスクの開始境界が2026-09-25であることを確認する。2026-09-24以前のトリガーやcatch-up実行が設定されている場合は切替を中止する。
3. 新Dailyをオンデマンドで1回実行し、source=scheduler、manifest success/許容されたpartial、必須成果物、status表示を確認する。失敗したら新タスクを無効化し、旧側は変更せず停止する。
4. 従来タスク `RakutenRoomBakemonoAffiliateDaily` の最新XMLと状態をarchive stagingへ保存し、`Disable-ScheduledTask` で無効化する。削除・登録解除しない。
5. `.agents/skills/SKILL.md` を同じディレクトリの `SKILL.md.disabled` へ原子的にrenameする。内容を変更・削除しない。これでroot legacy skillの発見入口を無効化する。
6. 新Daily/HealthCheckタスクを有効化する。次回実行時刻、WakeToRun、StartWhenAvailable、実行ユーザー、action pathを確認する。
6a. 切替が2026-09-25 13:00 JSTより前に完了した場合は、その時刻を初回自動実行とする。完了が13:00を過ぎた場合は、同日分をオンデマンドで1回だけ実行し、その後の次回予定を確認する。二重実行しない。
7. active skill scanで `rakuten-room-bakemono` が発見されず、`rakuten-room-bakemono-kai` とrr-kai群が発見されることを確認する。
8. `cutover-state.json` に旧/新タスク状態、旧SKILL rename、新skill paths、時刻、担当run_id、rollback手順を保存する。

ロールバック条件:
- 新タスクの登録/実行失敗。
- 新スキルが発見されない。
- 旧スキルだけが無効で新入口がない。
この場合、新タスクを無効化し、SKILL.md.disabledをSKILL.mdへ戻し、旧タスクを再有効化する。ロールバック結果を必ず記録する。

受け入れ条件:
- 旧taskは存在するがDisabled。
- 旧SKILL.mdは削除されず、`SKILL.md.disabled` として存在する。
- 新task 2件が正しい設定でEnabled。
- 新スキルだけがactive discovery対象。
- コード削除0。

reports/step-17.mdとledgerを更新し、旧・新の状態を明示してください。Step 18以外へ進まないでください。
~~~

---

## Step 18：従来スキルコードのファイル一覧付き退避

### AIエージェント投入用プロンプト

~~~text
あなたはStep 18担当です。Step 17で無効化済みの従来スキルコードを、削除せず、Codexのskill discovery対象外へ退避してください。

前提:
- Step 17 success。
- `.agents/skills/SKILL.md.disabled` が存在し、Step 17の記録とサイズが一致する。
- 旧WindowsタスクがDisabled、新タスクがEnabled。

退避先:
`archive/legacy-skills/rakuten-room-bakemono-20260924/source/`
既に存在する場合は上書きせず、JST時刻付きの新しい一意ディレクトリ名にする。

移動対象は従来スキル固有の次だけ:
- `.agents/skills/SKILL.md.disabled`
- `.agents/skills/assets/`
- `.agents/skills/references/`
- `.agents/skills/scripts/`
- `.agents/skills/templates/`
- `.agents/skills/webapp/`

移動してはいけないもの:
- `.agents/skills/rakuten-room-bakemono-kai/`
- `.agents/skills/rr-kai-*/`
- プロジェクト直下の `scripts/`、`tests/`、`rakuten-room/`、`rakuten-room-kai/`
- 認証情報やprivate auth-state

実施内容:
1. 移動前manifestとbaselineを照合する。
2. PowerShellのLiteralPathまたはPython pathlibで対象を1つずつMoveする。ワイルドカードで新スキルまで移動しない。
3. 退避先の全ファイルの相対pathとsizeを `archive-manifest.json` に保存し、Step 00 baselineのファイル一覧と1対1で対応させる。件数、相対パス、各サイズの差異があれば報告して停止する。
4. `RESTORE.md` と `restore-legacy-skill.ps1` を作る。復元先、ファイル一覧確認、旧タスク再有効化、新タスク無効化の順序を記載する。restore scriptは既定でWhatIfにし、明示フラグなしで復元しない。
5. Step 16のstaging ZIPもarchive配下へ移し、ファイル名、サイズ、作成日時を記録する。削除しない。
6. active `.agents/skills` 直下に旧root SKILL.md、旧root resourcesが残っていないこと、新スキル群が残っていることを確認する。
7. 全新スキルquick_validate、Python/Node testを再実行する。旧参照パスに依存していれば退避後に壊れるため必ず検出する。

受け入れ条件:
- baselineの従来ファイル数、相対path、各サイズがarchiveと一致。
- 削除ファイル0。移動または事前ZIPのみ。
- active skill treeに旧skill entrypointがない。
- 新スキルの参照切れ0、全テスト成功。
- restore scriptのWhatIfが成功。

reports/step-18.mdとledgerを更新し、archive path、ファイル件数と合計サイズ、移動対象、残存active skillsを報告してください。
~~~

---

## Step 19：切替後の最終確認と復元訓練

### AIエージェント投入用プロンプト

~~~text
あなたは最終Step 19担当です。旧スキル退避後の状態で、新スキル全体が単独で動くことと、旧スキルを必要時に復元できることを確認してください。実際の旧スキル再有効化はしないでください。

実施内容:
1. active skill treeを列挙し、新オーケストレータと全rr-kaiサブスキルだけが期待どおり有効であることを確認する。
2. 全skill quick_validate、Python full tests、Node tests、Schema tests、Playwright dashboard E2E、secret scanを再実行する。
3. `run_daily.py --source manual --dry-run`、`run_performance.py --source manual --dry-run`、`show_status.py --latest` を退避後の実pathで実行する。
4. 新Daily Windowsタスクをオンデマンドで1回実行し、manifest、終了コード、latest-daily-status、health checkを確認する。実行時刻の競合を避ける。
4a. 2026-09-25の初回本番manifestと投稿管理HTMLに、対象日2026-09-25が1セクションだけ存在することを確認する。15件未満なら実数と不足理由があり、未確認商品で穴埋めされていないことを確認する。
5. chat相当で「化け物・改を召喚して、今日の状態を確認して」を実行し、旧skill参照なしで新status pathへ到達することを確認する。
6. `restore-legacy-skill.ps1 -WhatIf` を実行し、全移動元、ファイル一覧、タスク切替順が正しく表示されることを確認する。隔離tempディレクトリへの復元テストが可能なら、ファイル数、相対パス、各サイズがarchive manifestと一致することを確認する。本番active treeへは戻さない。
7. Windowsタスク状態を確認し、旧 `RakutenRoomBakemonoAffiliateDaily` はDisabledかつ存在、新2taskはEnabledであることを記録する。
8. `final-acceptance-report.md` を作り、全AC、全path、テスト、レビュー、active skill、archive、task、既知の制限をまとめる。
9. execution-ledgerの全Stepを検査し、未完了、失敗、report欠落がなければplanをcompleteにする。

最終合格条件:
- 旧skill resourcesがactive pathになくても新スキル全テストが成功。
- manual/chat/scheduler相当が新orchestratorだけで完了。
- 旧コードと旧task定義がarchive/disabled状態で完全に残る。
- restore WhatIfと隔離復元のファイル一覧検査が成功。
- 全AC PASS、全path PASS、重大所見0。

最終報告では、運用開始可能か、現在のactive skill/task、archive場所、主要コマンド、残る現実的な制限を明確に示してください。
~~~

## 4. 途中停止と再開の規則

- Stepが失敗したら、`execution-ledger.json` の当該Stepを `failed` または `blocked` とし、次Stepを開始しない。
- 修正再実行は同じStep番号で別run_idを発行し、`attempt` と前回reportへのリンクを残す。
- 外部認証切れは `action_required` とし、ユーザー本人のログイン後に同じStepを再開する。ID・パスワードを受け取らない。
- Step 17以降で切替に失敗した場合は、Step 17記載のロールバックを優先し、「新旧どちらも無効」の状態を残さない。
- 実注文が0件など実データが存在しない経路は、実画面の0件取得に加えて、同じリリース候補を使ったfixture E2Eで非0件を検証し、未検証とテスト済みを区別して報告する。

## 5. 実行後に日常利用する入口

計画完了後の標準入口は次になる。

```text
Windows日次:
  .venv-kai\Scripts\python.exe automation\run_daily.py --source scheduler

手動日次:
  .venv-kai\Scripts\python.exe automation\run_daily.py --source manual

手動リサーチ:
  .venv-kai\Scripts\python.exe automation\run_research.py --source manual

成果分析:
  .venv-kai\Scripts\python.exe automation\run_performance.py --source manual

状態確認:
  .venv-kai\Scripts\python.exe automation\show_status.py --latest

チャット:
  「化け物・改を召喚して、今日の日次フローを実行して」
```

実際のROOM投稿と投稿済みcheckbox操作は、引き続きユーザー本人が行う。
