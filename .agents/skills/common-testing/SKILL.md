---
name: common-testing
description: 承認計画に指定された隔離試験とfixtureだけを実行し、期待・実結果・完全node・証拠を記録する。実装コードやDBを変更しない。
---

# 共通試験

## 使う場面

management stage=testingで、review approved plan、実装版、独立case、隔離環境、allowed_outputsが揃った場合に使用する。modelはgpt-6-luna、effortはmax。試験はU20の凍結全回帰を先取りしない。

## 作業

- case IDと完全nodeを先に特定し、PASS履歴があれば再実行しない。FAIL/未実施など計画が許したcaseだけ実行する。
- 入力、時計、コード/設定版、実行command、期待、実結果、終了値、初期/最終件数、証拠参照を記録する。必要なら通常の一時DBや専用test dataを使うが、検証済みOS sandboxやhost観測証拠は要求しない。
- Gitがhost-created managed worktreeの所有者を拒否する場合、controller-bound attemptで指定されたworktree rootを使い、該当Git commandだけ`git -c safe.directory=<exact worktree root>`で実行する。`--global`、`*`、Git設定ファイルの変更はしない。
- skip、xfail、error、未実施をPASSに読み替えず、実際に観測できない値は未確認とする。
- 本人専用ローカルシステムの開始・再開を、過剰な堅牢性証拠やテスト環境要件で止めない。ユーザー方針で除外されたOS sandbox、host receipt、production runner証拠を再び開始条件へ持ち込まず、計画された通常のローカル確認で記録する。

## 禁止と停止

- 製品コード、実稼働DB、Scheduler、認証、投稿、所有外fixtureを変更しない。
- planにないtest node、pytest collect、全回帰、外部サービス、本番remote、日次処理を実行しない。
- fixture出力を本番実績と表現しない。ケースや一時データの保存先は通常のローカル一時領域から選び、検証済みOS sandboxなどの堅牢性証拠がないことを理由に試験開始を止めない。

## 参照契約

実行許可、証拠、失敗、再実行規則は[contracts.md](references/contracts.md)を参照する。
