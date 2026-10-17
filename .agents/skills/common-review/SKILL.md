---
name: common-review
description: 実装と試験証拠を読取専用で確認し、対象版に結び付く差戻し・承認・拒否を記録する。変更を作成・修正しない。
---

# 共通コードレビュー

## 使う場面

management stage=reviewで、別の実装担当と試験担当が提出したresult_version、差分、計画、試験記録が揃った場合に使用する。modelはgpt-6-luna、effortはmax。Fastは親Bから継承する。

## 確認事項

- 計画の許可範囲とプロダクトコードの差分、設計、危険な副作用、エラー境界、未確認事項を読む。テストコード自体は独立レビューしない。
- 計画に記録されたcaseの期待と実測結果、証拠、版、完全nodeを照合する。skip/error/未実施を成功に扱わない。テストコードのバグが実行やデバッグ中に判明した場合は、担当者へ修正と対象caseの再実行を求める。
- 具体的なfile/行、根拠、対応するplan item、severityを含むreview resultを作る。
- 審査対象に修正が必要ならchanges_requested、重大な契約違反ならrejected、情報不足はunavailable/blockedを返す。

## 禁止と停止

review対象のfileを編集しない。reviewer自身が作成した実装・試験を承認しない。別版の承認を流用しない。DBや稼働環境を変更しない。差分・証拠にアクセスできなければ推測せず停止する。

## 参照契約

review scopeとresult_versionの結合、許可出力、差戻し上限は[contracts.md](references/contracts.md)を参照する。