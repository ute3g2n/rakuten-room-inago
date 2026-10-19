# FC07 / FCT09 HTML・fallback独立フォローアップレビュー

## 判定

**changes_requested（未承認）**。合成HTML例4件とfallback生成処理を確認した。これは実本番レポートのレビューではない。

- job_id: `job-historical-9e7f0c175cc4c874c955a5dab29c4427`
- source_launch_id: `historical-9e7f0c175cc4c874c955a5dab29c4427`
- target_date: `2026-10-17`
- source_run_id: `null`
- incident_id: `352492da-ac58-4680-b1fd-fe5f24000229`
- 確認した対象manifest SHA-256: `f6d60c4f77244f677ad2d82aa97ea7ed2e036f78082f42eedbb9a3a6302dfc53`（一致）
- manifest記載の対象ファイルSHA-256はすべて実ファイルと一致した。

## 前回7指摘の追跡

| # | 判定 | 確認内容 |
|---|---|---|
| 1 合成例の表示 | 修正済み | 全4HTMLの冒頭（7行目）に、合成例であり実際の10/17調査結果ではないと表示。 |
| 2 未実施reviewの断定 | 修正済み | 全4HTMLの14行目は合成シナリオの想定状態として、別担当の確認がまだであると明記。指摘なし・承認済みとはしていない。 |
| 3 通常例の事実・影響・変更 | 修正済み | `通常.html` 9〜13行目に期待3件・記録2件、影響・回復の不明、変更有無、試験用記録である範囲を記載。 |
| 4 全項目不明の既知/未知 | 修正済み | `全項目不明.html` 9〜13行目で開始記録・終了記録欠如・原因候補の記録を既知情報として分け、進行範囲・原因確定・影響・回復を未確認と記載。 |
| 5 変更なしと計装 | 修正済み | `変更なし.html` 11〜13行目で、接続点未特定のため単独ログでは原因に結び付かないという理由と、次に接続点を探す計画を記載。 |
| 6 通常renderer失敗 | 修正済み | `通常renderer失敗.html` 9〜16行目で失敗、短い報告に載せた内容、review/publication保留、次の確認を分けて記載。 |
| 7 発生区分・証拠・工程とincident | **一部未解消** | 全例で期待・実際・影響・回復、合成記録への本文リンク、workflow終了と元の不具合解決の別記載は追加された。ただし下記F1・F2が残る。 |

## 残る指摘

### F1 — 事象の発生区分が定義済みの分類になっていない

対象: 4HTMLすべての8行目、および `report_fallback.py` 35〜94行目の `event_classification`。

現状は「日次処理の一部が記録されていない例」「原因未確定」など事象の説明だけで、ルールが求める「当日発生」「調査で判明」「試験で確認」の区分を明示していない。合成例だと分かる表示と試験範囲の説明はあるが、各事象の区分欄自体で発生区分を示していない。たとえば合成シナリオの分類を「試験で確認（合成シナリオ）」とし、実運用の事実ではないことを続けて明記する。

### F2 — 付録にある具体的証拠パスが存在しない

対象: 各HTML 17行目の `samples/...` 参照。例: `通常.html` は `samples/normal/expected.json` と `samples/normal/actual.json`。

本文リンクのアンカーは各1つのIDに解決することを静的確認した。しかし、付録に記載された9つの `samples/...` パスはいずれもこのworkspaceに存在しない。合成例と明示されていても、これらは確認可能な証拠ではなく、読者が実際の参照先と誤解しうる。合成fixtureを同梱して参照先を実在させるか、「架空の参照名を使った説明例で、証拠ファイルは付属しない」と見える形で明示し、根拠が実在するという表現を避ける。

### F3 — fallbackでreview指摘を本文へ反映せず、指摘があるとreview loopが抜けない

対象: `report_fallback.py` 148〜155行目、`production_workflow.py` 8414〜8431行目および8500〜8507行目。

fallback builderは `revision_findings` の本文を使わず、リストが空でない場合に「指摘を本文へ反映しました」とだけ出す。fallback workflowはreviewerが `changes_requested` とfindingを返すたびにfallbackを再生成して同じreview loopへ戻るが、具体的な指摘をHTMLの該当箇所へ適用しない。よって修正を確認できず、同じ指摘が続けば最終成果の保存と公開へ到達しない。指摘内容をfallback本文へ実際に適用し、適用不能ならその事実と指摘を付録へ記録してreview結果を保持したままpublicationへ進む必要がある。review結果を公開の条件として扱わないこと。

### F4 — identity欠測時にfallback生成を拒否する

対象: `report_fallback.py` 134〜136行目。

`job_id`、`source_launch_id`、`target_date`、`incident_id` のいずれかが文字列でない／空なら `ValueError` を投げ、fallbackを生成しない。skill・contracts・controller/reporter指示は、利用できるidentityを保ち、欠測を記録して続けるよう要求している。欠けた値を推測せず「不明／未提供」と表示し、欠測理由を残す形にする必要がある。今回のcontroller指定identityでは `source_run_id=null` を含め他の必要値が揃っており、この経路が今回の完全identityを阻害したという意味ではない。

## 他の確認と未確認範囲

- report bodyは平易な日本語で、期待・実際・影響・回復・変更・未知事項・次の行動を含む。全例でworkflowの終了登録と元の不具合解決は分けている。
- report fallback builderは可変内容をHTML escapeしている。ページはviewport、折返し、印刷時幅のCSSを含む。`href="#evidence-*"` は全て一意な `id` に解決した。
- 通常のHTML、レビュー用prompt、標準、template、skill、contracts、report schemas、role prompts、およびfallback builder/production workflowのmanifest hashを確認した。prompt/契約は具体的な証拠リンク、発生区分、見える合成表示、review/publication/workflow/incident状態の分離を指示しているが、F1〜F4の実装上のずれが残る。
- private/secret値は確認しなかった。HTMLソース確認のみで、ブラウザー幅狭表示と印刷レンダリングは未実施。

## 対象HTML版

| ファイル | reviewed SHA-256 |
|---|---|
| `通常.html` | `394e4e324fe5a56da9b3d0f1f79e8ac48aba854932b1c86ab83d89cd0487680f` |
| `全項目不明.html` | `a02e44b2bf52201b6ef00d57f71b68f5b443808ca828007a2977b86f7e761287` |
| `変更なし.html` | `199b551dbb91c17fa8ad495ee4ee18e50107af3825508bb3fe89bddf591a1db6` |
| `通常renderer失敗.html` | `1df6b61a0ccdfd3f5f5b66e8d785f700795512858e30bbf9ff3c3b8f0758c9c4` |

以上は合成例の独立follow-up結果であり、実本番HTMLのreview結果や承認ではない。
