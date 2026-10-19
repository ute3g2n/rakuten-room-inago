---
name: common-implementation
description: 合格済み計画の割当ファイルを専用worktreeで実装し、差分と再現性のある変更記録を返す。テスト作成・試験実行・レビュー承認は行わない。
---

# 共通実装

## 使う場面

management stage=implementationで、親Bが承認済み計画版、base_commit、managed worktree、allowed_outputsを渡した場合にだけ使用する。modelはgpt-6-luna、effortはmax。Fastは親Bから継承される前提で、子設定だけではFast適用済みと報告しない。

## 作業

- 計画と割当を照合し、allowed_outputsに列挙された実装対象だけ変更する。
- 各変更を計画上のSTEP/fact/要件に結び、差分要約と実行した確認だけを返す。
- 本人専用ローカルシステムでは、本人が指示した開始・再開を妨げる自作制限を条件付きで迂回せず、制限そのものを削除する。必須IDなど実行対象を特定する最小入力は維持する。割当範囲に制限の実装元がない場合は、対象fileと削除内容を親へ返して計画/allowed_outputsを更新させ、追加承認を求めるだけで作業を止めない。
- 対象ID、Git基準版、worktree、割当範囲が欠けていて変更先を特定できない場合だけ、親へその値を返す。自作の開始制限を維持したままblockedにしない。
- Gitがmanaged worktreeの所有者を拒否する場合、controller-bound attemptで指定されたworktree rootを使い、該当Git commandだけ`git -c safe.directory=<exact worktree root>`で実行する。`--global`、`*`、Git設定ファイルの変更はしない。

## 禁止と停止

- テスト担当が所有するtests/fixtures、他担当の成果、未知のユーザー変更、認証・設定・DB・Scheduler・本番データを変更しない。
- 稼働checkoutへ書かない。commit/push/merge/deployを行わない。本人投稿を行わない。
- 自作の起動阻害制限を除くためにallowed_outputsの追加が必要なら、対象file・差分・理由を親へ直ちに返し、親の計画更新後に続行する。ユーザーへ同じ許可を再確認しない。実際の外部権限や外部環境障害のみ、その事実を明記して扱う。

## 参照契約

正確な読取・書込scope、受け渡し、禁止事項は[contracts.md](references/contracts.md)を参照する。
