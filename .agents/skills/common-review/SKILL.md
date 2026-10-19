---
name: common-review
description: 実装と試験証拠を読取専用で確認し、対象版に結び付く差戻し・承認・拒否を記録する。変更を作成・修正しない。
---

# 共通コードレビュー

## 使う場面

management stage=reviewで、利用可能な実装差分、計画、試験結果を受け取ったら開始する。前段のapproved statusは必要条件にしない。

対象jobとreview対象versionまたは成果物が特定できればreviewerを直ちに起動する。reviewに不可欠な対象そのものが存在しない場合だけ、その不足を記録する。任意証拠、test/readiness/release status、runtime観測、host receipt、model/effort観測、完全な入力一覧を理由に起動を見送らない。reviewer未起動を合成review結果で置き換えず、必ず担当reviewerを呼び出す。

## 確認事項

- 計画の許可範囲とプロダクトコードの差分、設計、危険な副作用、エラー境界、未確認事項を読む。テストコード自体は独立レビューしない。
- 計画に記録されたcaseの期待と実測結果、証拠、版、完全nodeを照合する。skip/error/未実施を成功に扱わない。テストコードのバグが実行やデバッグ中に判明した場合は、担当者へ修正と対象caseの再実行を求める。
- 本人専用ローカルシステムでは堅牢性のための自作制限を追加要求しない。本人が許可した開始・再開を止める自作上限、理由照合、review回数、receipt、sandbox証拠、release gate等があれば、review findingとしてその制限の除去を求める。計画/実装/試験/レビューsubagentへ同じ方針が渡っていることも確認する。
- Gitがhost-created managed worktreeの所有者を拒否する場合、controller-bound attemptで指定されたworktree rootを使い、該当Git commandだけ`git -c safe.directory=<exact worktree root>`で実行する。`--global`、`*`、Git設定ファイルの変更はしない。
- 具体的なfile/行、根拠、対応するplan item、severityを含むreview resultを作る。
- review resultは改善所見として保存し、approved以外でも次stageへ進める。changes_requested/rejected/unavailableや未解決findingを理由に修正・反映・報告を止めない。
- 審査対象に修正が必要なら

## 禁止と停止

review対象のfileを編集しない。差分や証拠の一部が読めない場合は欠測を記録し、読める範囲のreviewを返して次stageへ渡す。

## 参照契約

review scopeとresult_versionの結合、許可出力、差戻し上限は[contracts.md](references/contracts.md)を参照する。
