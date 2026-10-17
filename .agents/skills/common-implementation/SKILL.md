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
- 必須入力、Git基準版、worktree、テスト担当への引継ぎがない場合は編集を始めない。

## 禁止と停止

- テスト担当が所有するtests/fixtures、他担当の成果、未知のユーザー変更、認証・設定・DB・Scheduler・本番データを変更しない。
- 稼働checkoutへ書かない。commit/push/merge/deployを行わない。本人投稿を行わない。
- 未承認範囲へ変更が必要と判明したら、追加差分を作らず必要な範囲・理由・影響を返してblockedにする。

## 参照契約

正確な読取・書込scope、受け渡し、禁止事項は[contracts.md](references/contracts.md)を参照する。