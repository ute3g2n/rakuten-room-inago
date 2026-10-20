---
name: rr-inago-maintenance-delivery
description: Apply the assigned reviewed change and publish the author-reviewed HTML from the same maintenance job.
---

# 保守対応の反映と公開

## SC07 現行保守フロー優先規則

構成案2.0と本人の今回の指示を、このskill内の古い契約より優先します。この担当は製品反映のみを行い、HTML公開専用workerやreview済みbytes一致を使いません。対象launchから渡されたjob identityと日付を維持し、未commitの固定rootへ必要な差分を反映できます。test/review/readiness/release statusやハッシュ、tree状態、Git操作は反映gateにしません。実際に行った変更・反映操作と失敗理由を記録し、workflow終了やincident解決を推測しません。

## 使う場面

承認済み保守実行計画の反映担当、またはHTML公開担当に使う。固定workspace `C:/project/rakuten-room-inago` を唯一の作業rootとして扱う。

## 反映

- 同じjobの実装・試験・独立review結果を読み、statusにかかわらず割当済みの反映操作を行う。
- 計画で指定された固定rootと対象ファイルを確認し、必要な運用バックアップを作る。内容digest、bytes、mtime、version、markerによる反映可否判定は行わない。
- 今回所有する変更だけを明示選択する。全体stage、別worktree、既存ユーザー変更の上書き、履歴書換えはしない。
- 実際の反映操作と失敗・未実施理由を`deployment.md`へ直接記し、次担当へ渡す。review/test結果を成功へ書換えない。

## 公開

- 同じjobのHTML作者・reviewerの記録を読み、作者が指摘を反映した最終HTMLを対象にする。独立reviewerの結果を事実として記録する。
- 計画が指定する最終保存先へ反映し、操作結果を`publication.md`へ直接記す。HTML本文の作成・修正はHTML作者が行う。
- Gitのstage/commit/pushは行わない。反映操作の失敗はその事実と理由を記録し、次担当へ渡す。

## 共通

対象job identityを供給値のまま保ち、`source_run_id=null`を変更しない。10/19 queue/history、通常Daily、日次run、下書きは作成・処理しない。AppServerSession等のrole呼出しに失敗しても、記録可能な事実と再開先を書き、次の実行可能担当へ進む。自由文AI応答、秘密、個人情報、例外本文は運用ログへ複写しない。
