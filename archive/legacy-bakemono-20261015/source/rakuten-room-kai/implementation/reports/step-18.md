# Step 18 従来スキルコードの退避

実施日: 2026-09-25 JST。判定: **success**。Step 17成功、旧SKILL.md.disabledのサイズ・SHA-256一致、旧タスクDisabled、新2タスクEnabledを確認した。

`.agents/skills/` の旧固有6項目（`SKILL.md.disabled`、`assets/`、`references/`、`scripts/`、`templates/`、`webapp/`）だけを `archive/legacy-skills/rakuten-room-bakemono-20260924/source/` に1項目ずつ移動した。Step 00 baselineと16ファイルの相対パス・個別サイズが1対1で一致し、合計189,791バイト。移動前後の各SHA-256も一致。削除0。Step 16 staging ZIPをarchive package直下へ移し、サイズ・作成日時・SHA-256を `archive-manifest.json` に記録した。

`RESTORE.md` と `restore-legacy-skill.ps1` を作成。スクリプトは引数なしまたは `-WhatIf` でpreviewのみ、`-Apply` を明示した場合だけ、新タスク無効化→ファイル復元→旧タスク有効化を行う。`-WhatIf` は16ファイルの全移動元・復元先とタスク切替順を表示して成功した。

退避後のactive skill entrypointは新9件のみ。旧root SKILL.mdと旧root resourcesは存在せず、新9件quick_validate成功。Python全164件、Node全55件成功。旧参照への依存による破損は検出されなかった。Step 19へ進む。
