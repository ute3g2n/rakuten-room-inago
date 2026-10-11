# 従来スキルの復元

退避元ファイルは `source/`、16ファイルの相対パス、サイズ、SHA-256は `archive-manifest.json` に記録している。旧タスクの最新XMLは `../.staging/RakutenRoomBakemonoAffiliateDaily-precutover.xml` に残してある。元のタスクは登録解除せずDisabledのまま保持している。

まず `./restore-legacy-skill.ps1 -WhatIf` を実行し、全ファイルの元パス、復元先、タスク切替順を確認する。引数なしもWhatIf。実復元時だけ `./restore-legacy-skill.ps1 -Apply` を実行する。スクリプトは全ファイルのサイズとSHA-256を検査し、新Daily/HealthCheckを無効化し、`.agents/skills/` へファイルを復元して旧root `SKILL.md` を有効化し、最後に旧AffiliateDailyタスクを有効化する。archiveの原本はコピー元として残る。既存の復元先ファイルを上書きしない。

復元後は新タスク2件がDisabled、旧タスクがReady、`.agents/skills/SKILL.md` が存在することを確認する。必要なら保存済みXMLとタスク定義を照合する。
