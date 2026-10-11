# Step 16 独立レビュー所見

対象: 設計§0–19、60 AC、Python/Node実装、新スキル9件、Windowsタスク定義、Step 15の実Path証跡。実装結果を前提にせず、Schema・成果物・実行履歴・テストを再照合した。

## 所見と処置

| ID | 重要度 | 所見 | 処置・再確認 |
|---|---|---|---|
| R16-01 | MEDIUM（解消） | 登録スクリプトがUTF-8宣言を含むPowerShell文字列を`Register-ScheduledTask -Xml`へ渡すと、タスクスケジューラが文字列の符号化と宣言の不一致を拒否する。Step 15候補登録で再現。 | 登録時だけ宣言を除き、保存するXMLはUTF-8を維持。PowerShell parser error 0、候補登録の同方式で動作確認、ExportOnlyで本番タスク未登録を確認。 |
| R16-02 | LOW（解消） | 広域secret scanが旧文書の`$ANON`、`Bearer $SEC`等の変数参照を実値と誤検知。 | 変数参照だけを除外し、実値風のdummyは引き続き検出する回帰試験を追加。新スキル・run・実装成果物の再走査0件。 |
| R16-03 | LOW（運用条件） | 外側Codex CLIの既定サンドボックスでは内側CLIのモデル呼出しが待機する。 | 実行サンドボックスを本タスクと同じ`danger-full-access`へ合わせたCLIスモークで、chat経由の全9工程を完走。Windows Task Schedulerからの実行も完走。 |
| R16-04 | LOW（観測待ち） | 物理的なスリープ復帰は本監査では実施していない。 | `WakeToRun=true`、`StartWhenAvailable=true`、missed/stale試験、候補タスクのオンデマンド実行を確認。切替後の初回予定実行で運用観測する。 |

**未解決のCRITICAL/HIGH/MEDIUM: 0件。**

## 分野別レビュー

- **Python/code:** `subprocess`は引数配列と`shell=False`。ジョブは入力・出力Schema、商品ID/URL、run_id/dateを検証し、正本更新はオーケストレータで直列化。dry-runのaffiliate/ROOM/API/page結果はrun内へ隔離。core10モジュール行coverage 91.5%（865/945）。
- **Node/browser:** affiliateの注文XHR、CSV列構成、公開ROOM30日巡回、商品ページの未取得状態を区別。`npm test` 55件、dashboard E2E 13件、Node syntax 13ファイル通過。ページ取得失敗や在庫未確認を0・適格と扱わない。
- **Security:** 資格情報本文は読まず、既存ACL保護を確認。localhost APIのHost/Origin/CSRF、入力長、HTML escape、競合検知、失敗ログのredactionを既存の障害注入・E2Eで再確認。広域secret scan 0、npm/pip既知脆弱性0。
- **Skill structure:** 新9スキルquick_validate全件0。旧packageの補助15ファイルは新package内でもbyte一致。方法論、確度ラベル、実ページ確認、加熱スコア、表現ルール、保存データを保持し、新入口と4 referenceを追加。旧packageは16ファイル・189,791バイトでStep 00 baseline一致。
- **Windows tasks:** 開始境界はDaily 2026-09-25 13:00、Health 14:00。固定Python、作業ディレクトリ、`WakeToRun`、`StartWhenAvailable`、同一ユーザーSIDを確認。候補2件はオンデマンド実行後Disabled。旧affiliate taskはReady、旧SKILL.mdは有効のまま。
- **Path/artifacts:** `full-path-coverage.json` 42行中未合格0。9実manifestのstage artifact、開始/終了events、Schemaを独立照合し欠落0。成果分析→research-plan/daily-planの受け渡し、注文商品一致帰属と日別クリック非帰属、再投稿禁止はStep 07/09/12/14試験とStep 15実入力で確認。

## 再検証

| 項目 | 結果 | 証拠 |
|---|---|---|
| Python full suite + branch coverage | 164 passed、全automation 76%、core10行91.5% | `pytest-step16.txt`、`coverage-step16.json` |
| Node full suite | 55 passed | `npm-test-step16.txt` |
| Playwright dashboard E2E | 13 passed | `dashboard-e2e-step16.txt` |
| Schema valid/invalid | 32 passed | `schema-tests-step16.txt` |
| failure matrix | 31 case、未解決0、対象試験19 passed | `failure-injection-matrix.json`、`failure-tests-step16.txt` |
| skill quick_validate | 9/9 valid | `skills-validate-step16.json` |
| secret scan | 0 findings | `secret-scan-step16.txt` |
| dependency audit | npm/pip 0 known vulnerabilities | `npm-audit-step16.json`、`pip-audit-step16.json` |
| staging rollback archive | ZIP 16件、一覧/各sha256の読取照合成功 | `archive/legacy-skills/.staging/legacy-package-manifest.json` |

