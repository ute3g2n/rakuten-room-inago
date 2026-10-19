# FC02 stage成果契約

全行は共通handoffを持つ。stage progressと操作成否は別、後続は品質結果にかかわらず実起動する。

| 工程 | 実担当 | 通常必須内容 | 失敗時の内容あるfallback | 次担当 |
|---|---|---|---|---|
| 対象確認・証拠 | controller | 固定identity、観測時刻、取得refs/欠測 | 読めたrefsと読めなかった理由のbundle | investigator |
| 調査 | rr-inago-incident-investigator | 9章MD、facts、影響/不明/仮説、次の計装候補 | 取得事実と調べ切れなかった範囲を9章へ記す | investigation reviewer→planner |
| 計画 | common-execution-planner | 0～14節、STEP14項目/全文prompt、case12項目、所有/試験/適用/報告 | 今回の不足計装の最小具体計画、不能なら候補/理由と説明HTMLの検証・公開計画 | plan reviewer→implementer |
| 実装 | common-implementer | 実diff、捕捉operation、期待event、版、所有一覧 | 原因修正不能でも計装、双方不能だけ個別操作not_performed+理由 | tester |
| 試験 | common-tester | 完全node、clock/版、期待/実測/exit、件数、証拠、未実施理由 | 差分0でもschema/引継ぎ/報告データを実検査、対象なしは空PASSにしない | product reviewer |
| 製品review | common-reviewer（作者と別） | 製品diff/実測対象版、所見、修正要求 | 差分0を実確認して対象差分なし、approvedを捏造しない | 元作者修正→deployment |
| 反映 | controller/deployment | 候補/現用/反映SHA、schema、正式ledger、read-back | 候補なし/失敗操作の理由、既存版維持の実値と観測 | reporter |
| 報告 | rr-inago-incident-reporter | 平易なversion付きHTML、result、技術付録 | controller最小HTMLに出来たこと/未確認/次行動、原生成失敗を残す | independent report reviewer |
| 報告review/修正 | rr-inago-incident-report-reviewer→原reporter | exact版/hash、読解/根拠/リンク/狭幅/秘密の所見と作者対応 | fallback実在版にも独立review依頼し、実起動失敗と未確認を残す | publication |
| 公開・終端 | controller/publication | HTML版/hash/review/commit/remote/DB結合、終端/finished_at | push失敗でもHTML/原因/再開位置保存と終端整合操作、未達をcompleteにしない | same-job recovery or FC11照合 |

本表は実行契約であり全担当実行済みの記録ではない。FC02で実起動したのはplannerのみ。その他は各STEP/同一jobの正式attemptで記録する。
