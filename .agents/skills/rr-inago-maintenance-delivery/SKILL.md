---
name: rr-inago-maintenance-delivery
description: Apply the assigned reviewed change and publish the author-reviewed HTML from the same maintenance job.
---

# 保守対応の反映と公開

## 使う場面

承認済み保守実行計画の反映担当、またはHTML公開担当に使う。固定workspace `C:/project/rakuten-room-inago` を唯一の作業rootとして扱う。

## 反映

- 同じjobの実装・試験・独立review成果を実際に読む。statusで操作を止めず、対象コードと選択されたファイルだけを確認する。
- 現在branchとtracking targetを読み、対象path/hash、migration条件、backupとreadback要件を確認する。
- 今回所有する変更だけを明示選択する。全体stage、別worktree、既存ユーザー変更の上書き、履歴書換えはしない。
- 反映した版と実行後の実物を読み戻し、失敗・未実施は理由と次の再開位置を`deployment.md`へ直接記す。review/test結果を成功へ書換えない。

## 公開

- 同じjobのreportとHTML reviewを読み、作者が指摘反映した実HTMLをhash付きで対象にする。独立reviewerの承認を公開の前提にせず、使える版を公開して実結果を記録する。
- 公開先からbytesを読み戻し、review対象hashとの一致を確認する。公開担当はHTML本文を作成・修正しない。結果を`publication.md`へ直接記す。
- Gitのcommit/pushは現在branch/tracking先と所有diffを確認し、今回のtask-owned paths/hunksだけに限定する。失敗時は変更を保持して理由を書く。

## 共通

対象job identityを供給値のまま保ち、`source_run_id=null`を変更しない。10/19 queue/history、通常Daily、日次run、下書きは作成・処理しない。AppServerSession等のrole呼出しに失敗しても、記録可能な事実と再開先を書き、次の実行可能担当へ進む。自由文AI応答、秘密、個人情報、例外本文は運用ログへ複写しない。
