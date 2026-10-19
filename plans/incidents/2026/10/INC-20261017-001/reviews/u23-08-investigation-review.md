# U23.08 調査更新の独立レビュー

判定：`changes_requested`。対象は `result_version=u23-08-update-v1`。9章・13 factを確認した。原因や回復を断定する記述は見当たらず、修正指摘は証拠の版識別と参照の完全性である。レビューは実施済みで、証拠不足を理由とする合成の未実施結果ではない。

- 実施時刻：2026-10-19 20:41:19 JST。指定 reasoning effort は high、実model/effort/tierの独立観測は未提供。
- identity：incident_id `352492da-ac58-4680-b1fd-fe5f24000229`、job_id `job-historical-9e7f0c175cc4c874c955a5dab29c4427`、source_launch_id `historical-9e7f0c175cc4c874c955a5dab29c4427`、source_run_id `null`、target_date `2026-10-17`。
- formal review attempt、controller base_commit、完全なcontroller refs配列は未提供。既存attemptやHEADから補完していない。
- 固定workspaceで指定入力だけを読んだ。歴史原snapshot探索、DB/Scheduler照会、コード変更、試験実行は行っていない。調査・fact ledgerは変更していない。

## 対象版

| 対象 | SHA-256 |
|---|---|
| investigation MD | `2ed633f25031da9ac510ef9d82629940ecf78ee572e2aa8c7aea598ad93258d4` |
| facts JSON | `789d549e53cfb0db8632a59fb5eeb6b429c166e17adc40c3957efd9f0621416e` |
| result JSON | `3ce48623f87c06863925acbfcf00f7ba4ee35a09c0375dfb38c7118e2ed846f6` |
| 供給計画 v1.3 | `bf5773e6fe9a42ebfa9dc05111e3357c05415654554a0ddcb7a95f241f8b2994` |
| S04記録（今回読んだ版） | `26417d003bb1aa9b6502c00ed240332232e2d38798e757827cbfbcb45d5b7a79` |

`result.json`のfacts/investigation SHAは実際の対象bytesと一致した。コードbase commitを証拠SHAの代用にしていない。

## 具体的な指摘

| ID・区分 | 位置 | 所見と必要な対応 |
|---|---|---|
| IR-U23-01 / medium / 証拠版識別 | 調査MD 109～113行、facts JSON 520～525行 | MD第9章のS04 SHAは`49602DAFEE1821B4EAC268075E85F29FDF4B9922C65D1CF20226BBCD844D36CE`（20:37:52取得）。JSON包装SHAと今回の現物は`26417D003BB1AA9B6502C00ED240332232E2D38798E757827CBFBCB45D5B7A79`。S04には20:38の追記があるため異なる時点の版である可能性があるが、旧snapshotと両版の対応は未提供。両成果物で版を一致させるか、別時点の版として各行を識別し、旧本文が再検証不能なら明記する。U005の20:36観測値の誤りは確認していない。 |
| IR-U23-02 / medium / fact参照不足 | facts JSON 58～251行のF001～F008 | 実際に値を供給した計画は`readable_support_refs`だけに記され、各`evidence_refs`に含まれない。計画v1.3参照も`evidence_refs`へ加える。歴史証拠参照とsupplied/未再読の属性を維持する。歴史refsの集合は計画のF行と一致する。 |
| IR-U23-03 / low / 元証拠不足 | 調査MD 96～106行 | 歴史証拠7件は今回原文未提供。SHA/時刻は供給計画との照合に限る。F008 manifest自身のSHAはunknown。調査内の制限表記は適切で、最終HTMLでも保持する。 |
| IR-U23-04 / low / reviewer範囲 | 調査MD 48～56、114～119行、U001～U004 | 元試験・製品review・旧review記録は今回reviewer指定入力外。計画の集計値とは整合するが、詳細時刻・個別code SHA・旧FAIL・local証拠SHAを原記録から再検証していない。原記録が存在しないという所見ではない。後続では確認範囲を過大に表現しない。 |

## 歴史証拠ごとの照合

各行とも、調査第9章と指定計画§2の供給値は一致した。原文の再読・再ハッシュは未実施である。

| evidence_ref | SHA-256（供給） | 観測/収集時刻（供給） |
|---|---|---|
| `historical-input/2026-10-17/observation.json` | `48b96385ba6b0d90859f7b53e0b66acc654e4a1ec4cfbaaa47beb945ce11e8f6` | observed_at `2026-10-17T13:00:04.062398+09:00` |
| `historical-input/2026-10-17/source-evidence.json` | `0ece63496cf9423eca7e7cff4fa8b720ad06048cf962a1fa49ccb4f8c1d9e8c5` | 独立採取unknown、内包CLI event 13:00:04.062398 JST |
| `historical-input/2026-10-17/e0-manifest.json` | `2b457d5a85a8b0859487ed5fddab81dec52cb96e57bf2a0e6e301ced0dca35e8` | captured_at_jst `2026-10-17T14:17:06+09:00`、manifest_version 1.0 |
| `historical-input/2026-10-17/production-cli.filtered.json` | `bcb6ae7f881d4806b7234646393987a3d814536f529853d9d5f70f0d8acd70cf` | event at_jst `2026-10-17T13:00:04.062398+09:00`、独立採取unknown |
| `historical-input/2026-10-17/scheduler.filtered.json` | `a638cdc1a72974c510501c0907448b73214529b736bb0d6d7c21d529374771e8` | observed_at_jst `2026-10-17T14:17:05+09:00` |
| `historical-input/2026-10-17/read-aggregate.json` | `e97a935bacdfc9b3dc79c9262567fb9a2a8cca2bf71e7f53ed845d747e6e5c27` | observed_at_jst `2026-10-17T14:14:35+09:00` |
| `evidence/2026-10-17/fff0d77048a7162c01da9f6a/manifest.json` | unknown・未提供 | view captured_at `2026-10-17T13:00:04+09:00` |

## 確認できた内容

- 1～9章が各1回あり、期待・実結果・影響・回復は区別されている。F001～F008は当日の供給観測、U001～U005は後日のmaintenance記録として分類されている。
- F001～F008の数値、範囲、時刻、歴史証拠refsは計画v1.3と一致する。既存12件・当日確定0件・publicationイベント2件の加減や、実投稿ゼロへの読み替えはない。
- `IntegrityError`、`error_name`、`constraint_label`を根本原因へ昇格していない。`failure_module`/`failure_function`/`failure_line`/`code_sha256`はpipelineの捕捉位置と明記され、callback起点と混同されていない。
- proposed/persisted runを分け、`source_run_id=null`を維持している。run未保存理由・実OS終了値・根本原因・回復・再発防止はunknown。CLI観測終了値とScheduler結果も別に扱う。
- U005はS04の20:36:40.7904901～20:36:42.2412393 JST観測と一致する。20:38のresume追記はU005の観測区間外で、後続操作の結果として扱う必要がある。
- Markdownに識別子・配列・リンクの表示を変えるHTML entityは見当たらない。指定資料にsecretや私的本文の転記、根拠のない成功・ゼロ・原因断定は見当たらない。

本人1名のlocal systemで堅牢性は品質目標にしない。`changes_requested`、原因unknown、証拠不足は観測であり、後続gateではない。修正指摘を引き継ぎ、計画・実装・試験・製品review・反映・最終HTML作成・指摘反映・公開を継続する。Gitは親controllerが所有監査し、今回所有分だけ選択commit/pushする。
