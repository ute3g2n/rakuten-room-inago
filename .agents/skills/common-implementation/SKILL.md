---
name: common-implementation
description: 利用可能な計画の割当ファイルを実装し、差分と再現性のある変更記録を返す。計画レビュー承認を待たずに進み、テストコードの独立レビューは行わない。
---

# 共通実装

## 使う場面

management stage=implementationで、利用可能な計画版、base_commit、作業場所、allowed_outputsを受け取ったら開始する。plan review approvedやtest handoff statusは開始条件にしない。

対象jobと担当工程が特定できれば、利用可能な入力でimplementerを直ちに起動する。任意の証拠、review/test/readiness status、runtime観測、receipt、role設定との一致、全入力の充足を開始条件にしない。不足情報はunknownとして扱い、作成できる変更と記録を進める。

## 作業

- 計画と割当を照合し、review指摘・未確認・test FAILがあっても利用可能な実装を進める。解決できない点は成果に記録し、次の試験/reviewへ渡す。
- 自動不具合対応の後続工程をreview/test/readiness/release status、証拠不足、unknown原因、retry/review回数で止めない。状態は正確に記録して次工程へ渡す。
- 原因がunknownでも実装を止めない。計画に沿って、原因の切分けに役立つtrace/log計装など実行可能な変更を行い、試験・review・反映・最終reportへ引き継ぐ。計装のみの場合はその事実と限界を記録し、根本修正済みとは表現しない。
- デバッグ中にテストコードの不具合を確認した場合は最小修正して影響caseを再実行する。テストコードの独立レビューはしない。
- 各変更を計画上のSTEP/fact/要件に結び、差分要約と実行した確認だけを返す。
- 本人専用ローカルシステムでは、本人が指示した開始・再開を妨げる自作制限を条件付きで迂回せず、制限そのものを削除する。必須IDなど実行対象を特定する最小入力は維持する。割当範囲に制限の実装元がない場合は、対象fileと削除内容を親へ返して計画/allowed_outputsを更新させ、追加承認を求めるだけで作業を止めない。
- 変更先を選べる情報が不足していても、利用可能な対象を先に実装し、不足したfile/IDは結果へ記録する。
- Gitがmanaged worktreeの所有者を拒否する場合、controller-bound attemptで指定されたworktree rootを使い、該当Git commandだけ`git -c safe.directory=<exact worktree root>`で実行する。`--global`、`*`、Git設定ファイルの変更はしない。

## 禁止と停止

- 計画外のtests/fixtures、他担当の成果、未知のユーザー変更、認証・設定・DB・Scheduler・本番データを変更しない。デバッグで確認したテスト不具合は例外として該当箇所だけ修正し、影響caseを再実行する。
- 稼働checkoutへ書かない。commit/push/merge/deployを行わない。本人投稿を行わない。
- 自作の起動阻害制限を除くためにallowed_outputsの追加が必要なら、対象file・差分・理由を親へ直ちに返し、親の計画更新後に続行する。ユーザーへ同じ許可を再確認しない。実際の外部権限や外部環境障害のみ、その事実を明記して扱う。

## 参照契約

正確な読取・書込scope、受け渡し、禁止事項は[contracts.md](references/contracts.md)を参照する。
