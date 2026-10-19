# FC07 / FCT09 独立HTMLフォローアップレビュー r3

## 判定

**changes_requested（未承認）**。指定manifestのSHA-256は `50918af4703c85161358359b0b4b63eccc181291e90d08bded61d59887e82960` と一致し、記載された17ファイルすべてのSHA-256が一致しました。対象はFC06の合成HTML4件と、そのreport fallback・保存・再開実装、およびmanifest記載の関連仕様です。実際の10/17調査レポートへの承認ではありません。

対象identity: job `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、source_launch `historical-9e7f0c175cc4c874c955a5dab29c4427`、target date `2026-10-17`、source_run `null`、incident `352492da-ac58-4680-b1fd-fe5f24000229`。

## 前回指摘の確認

- 元の7指摘：**すべて修正済み**。合成例の明示、レビュー未実施・公開前の表示、期待/実際/影響/回復/変更と既知・未知の分離、変更しない理由、renderer失敗の説明、出来事区分と具体的根拠、workflow完了とincident解決の分離を4例で確認しました。
- F1〜F4：**すべて修正済み**。区分が本文にあり、根拠リンクは実在する資料を指しています。review指摘は実authorの1回の修正へ渡され、その後reviewerが再起動します。author失敗または再指摘時は未承認のfallbackを保存して公開へ進みます。欠けたidentityは本文とinputに残し、保存用contextだけをjob/launch由来で補います。
- r2 F1：**修正済み**。再review後に作る最終fallbackは自身の版/hashに結び付く `unavailable` review記録になり、以前のreview指摘は以前の版のhashとともに別response sidecarへ保存されます。
- r2 F2：**修正済み**。入力とHTML保存にstorage identityを使いつつ、生の欠損identityを報告に残します。共通context検証は引き続き厳格です。
- r2 F3：**修正済み**。再開処理は `result.report_input.path` とSHA-256を `_daily_report_attempt_input` へ渡し、参照先を直接読みます。同じjob/attempt配下の固定形式名、内容hash、fallback input schema、raw/storage identity、HTML path/hash、review bindingを検証します。別のversioned inputが存在しても名前順では選びません。

## 新しい指摘

### R3-F1 — rejected reviewを保存したfallback結果は、同一jobの再開時に復元できない

場所: `automation/inago/maintenance/production_workflow.py` 8687–8689行および9772–9779行。

fallback作成処理は `review_document["status"] != "unavailable"` なら `stage_status="reviewed"` とします。この条件には、schema上有効な `rejected` と、revision対象にならない空findingsの `changes_requested` も入ります。どちらも `report_status="pending"` のまま公開へ進むので、実行時にreportを確認できたとしても、同一jobの完了後再開では `stage_status="reviewed"` 分岐が `status="approved"` かつ `report_status="complete"` だけを許し、`report_fallback_review_status_invalid` で復元を失敗させます。レビュー拒否/指摘の状態を事実として保持したまま、未承認pending結果として安全に再開復元できる条件にしてください。review状態をapproveに偽装しないでください。

## 表示・内容の確認と未確認範囲

4 HTMLの本文だけで、何が起きたか、何を調べたか、変更、未知事項、次の行動を理解できます。合成fixtureと本番事実を混同せず、原因・業務影響・回復を断定していません。本文に内部ID・status・reason code・hashは出ておらず、参考付録に限定されています。秘密や個人情報は見つかりませんでした。

各例のfragment linkは重複しない既存IDへ解決し、付録のコード参照先は全て実在しました。viewport、折返し、印刷用CSSはソースで確認しました。**ブラウザー表示と印刷レンダリングは実施していません**。参照したのは合成HTMLであり、実reportのレビューでもありません。

| 合成HTML | SHA-256 |
|---|---|
| `通常.html` | `31ecb4e0d115b3fd82cfa789e5cf3862a5e126f1e0a663d91e3336eeeeb2c8d4` |
| `全項目不明.html` | `751bafa96778e878e3cea113b4d9e64c6758fa83f983ce4b6815af7bde881059` |
| `変更なし.html` | `514db4f1fd61b03201f231074c4a871cf669df0a30765d676ec49f235eb10ff8` |
| `通常renderer失敗.html` | `1171f80698567fe6ae19b041944d7d4f19b9f01846e15e9d6272cd68facdd66b` |
