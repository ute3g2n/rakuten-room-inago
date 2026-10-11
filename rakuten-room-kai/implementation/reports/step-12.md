# Step 12 結果

- 状態: success
- 外部依存をfakeに置き換え、一時workspaceで必須10群の全経路を確認した。`path-coverage-offline.json` に61経路のテスト名、結果、実行ID、成果物を保存。
- 日次15件以上では観測、分析、research skip、計画、dashboard、success。0件ではresearch-plan、候補取得、triage、実ページ観測fake、文面、品質、正本反映、計画の順を確認。確認済み1件だけのpartialとなり、同日再実行で商品・文面の二重登録なし。
- 7フローをmanual/chat/scheduler引数で計21通り実行し、manifest、events、history、成果物とworkflow stage順を照合。
- affiliateの観測済み0、注文商品一致・不一致、unavailableを区別。ROOMの確認済み0、未照合、取得失敗を区別。JST30日境界と確認済み0件はNodeテスト結果を経路表へ対応付けた。
- Aから別商品3件の調査依頼、Bの単一変更、Cの条件低下、保留の非除外を確認。fake CLIは同一モデル再試行、許可時Astra昇格、認証エラーでの昇格禁止を確認。
- 投稿済み商品の計画再採用0、非帰属注文の投稿帰属0、正本二重書込み0。実楽天、実Codex、実Windowsタスクは変更していない。

## 機械検証

| コマンド | 結果 |
|---|---|
| `pytest -q tests_kai --basetemp=<一時ディレクトリ> --junitxml=offline-junit.xml` | 138 passed、exit 0 |
| `python -m automation.offline_path_coverage ...` | 10群61経路、未通過0、exit 0 |
| `npm test` | 51 passed、exit 0。`node-step12.tap`に保存 |
| `python -m compileall -q automation tests_kai` | exit 0 |

全体回帰でWindowsのfake CLIが日本語の標準入力を既定文字コードで読んだ失敗を検出し、fake側をUTF-8に固定した。修正後の全テストで再確認した。

## Step 13へ

投稿dashboardの実ブラウザ表示と操作はStep 13で検証する。Step 12はlocalhost画面のE2Eを含まない。
