# U23.08 調査更新v2の独立再レビュー

判定：`approved`。対象は`u23-08-update-v2`。v1のIR-U23-01/02は解消した。歴史原証拠と指定範囲による制限2件、および後続S04追記の観測1件を保持する。修正要求は残っていない。

- レビュー時刻：2026-10-19 20:49:37 JST。reasoning high指定を維持。実model/effort/tierの独立観測は未提供。
- incident_id：`352492da-ac58-4680-b1fd-fe5f24000229`。job_id：`job-historical-9e7f0c175cc4c874c955a5dab29c4427`。
- source_launch_id：`historical-9e7f0c175cc4c874c955a5dab29c4427`。source_run_id：`null`。target_date：`2026-10-17`。
- formal review attempt、controller base_commit、完全なcontroller refs配列は未提供。別attemptやHEADから補完していない。

## 対象版と結合

| 対象 | SHA-256 |
|---|---|
| investigation MD v2 | `b221b23c23cb20b8e8dea4ff418fdcf319d47c2756bbe742c65bbca6fcb1dda2` |
| facts JSON v2 | `0dc60c0d83868ace7484f47928b328ee12407e147146e83645a4bc0774e239d0` |
| result JSON v2 | `27f333a87f477f57dd6ebd935a40e92455b58386bdee6a830a1c3f8d9536930d` |
| 供給計画 v1.3 | `bf5773e6fe9a42ebfa9dc05111e3357c05415654554a0ddcb7a95f241f8b2994` |
| S04現行追記版（再review時） | `0f01b39fbebbca234337b119eb33cf7996ff19cfc1ab64d44d51b522123ba754` |

MD/facts/resultの`result_version`は一致し、`result.json`のfacts/investigation SHAは上記bytesと一致する。v1レビューのMD SHA `dc540e12c253dde106d7fa93aa2deb33d049a51fdf699363957fc10e7f1545a3`、JSON SHA `38cce545c1dc5a38b15672d63d0af5904013cc6c06fba20334e11504618e88f8`も一致し、旧`changes_requested`判定は保存されている。

## v1指摘の再確認

| ID | v2の位置 | 独立確認結果 |
|---|---|---|
| IR-U23-01 | MD109～122行、JSON `s04_version_mapping`/`local_evidence` | S04先頭3750 bytesのSHAは`49602dafee1821b4eac268075e85f29fdf4b9922c65d1cf20226bbcd844d36ce`。先頭5826 bytesのSHAは`26417d003bb1aa9b6502c00ed240332232e2d38798e757827cbfbcb45d5b7a79`。両方をメモリ上で再計算し一致した。旧観測本文と20:38追記版の対応、およびU005が20:36観測だけを使うことは明確になった。解消。 |
| IR-U23-02 | MD28行、JSON各Fの`evidence_refs` | F001～F008全8件に、実供給元の`plans/incidents/2026/10/INC-20261017-001/日次不具合修正計画_2026-10-17.md#2`が追加された。歴史refsと原文未再読の属性は残り、計画SHAも一致した。解消。 |

9章・13 factは維持されている。期待・実結果・影響・回復、当日事象と後日試験・reviewが分けられ、SQLite分類は原因断定に使われていない。pipelineのmodule/function/line/hashは捕捉位置であり、callback起点を示すものではない。提案runと永続runを区別し、`source_run_id=null`、原因・実投稿件数・回復・再発防止のunknownが保持されている。識別子の表示を変えるHTML entity、根拠のないゼロ・成功、secret/私的本文は指定資料で見当たらなかった。

## 保持する制限・観測

| ID | 位置 | 内容 |
|---|---|---|
| IR-U23-03 / low | MD96～106、124行 | 歴史原snapshot7件は未提供。第9章の各SHAと観測/収集時刻は供給計画との照合に限る。F008 manifest自身のSHAはunknown。v2はこの制限を保持している。 |
| IR-U23-04 / low | MD48～56、115～124行 | U001～U004の元試験・製品review・旧review記録は今回reviewer指定入力外。詳細時刻・個別code SHA・旧FAIL・各local証拠SHAの独立原文照合は未実施。調査担当の読取範囲とreviewer範囲をv2が分けている。 |
| IR-U23-V2-01 / low | MD13、114、122行、S04後続節 | S04は20:46以降の追記で現行全体SHAが`0f01…`へ変わった。v2更新20:44:16時の`26417…`版は現行先頭5826 bytesから再現できる。後続追記をU005へ混入した形跡はない。最終HTMLでは20:36のresume_count7/runningを最新値とせず、20:46のWorker/DB観測を別時点の事実として記す。 |

歴史7件それぞれの供給SHA・時刻はv1レビューで計画と照合済みで、今回v2の第9章でも同じ値を保持している。コードbase commitを証拠IDへ代用していない。独立した旧S04 snapshotファイルは未提供だが、本文bytesの対応は上記prefixの再計算で確認した。

本人1名のlocal systemで堅牢性は品質目標にしない。reviewの承認・指摘、原因unknown、証拠不足は後続gateではない。HTML作成・独立review・指摘反映・公開とU24/U25を継続する。調査・fact ledger・DB・製品・計画は変更していない。今回の変更はこの再レビュー2ファイルのみで、Gitは親controllerの所有監査と選択commit/pushへ渡す。
