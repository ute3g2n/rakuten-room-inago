---
name: common-testing
description: 承認計画に指定された隔離試験とfixtureだけを実行し、期待・実結果・完全node・証拠を記録する。実装コードやDBを変更しない。
---

# 共通試験

## 使う場面

maintenance stage=testingの依頼を受けたら、利用できる計画・実装版・caseから試験を始める。review approved、完全なcase一覧、専用隔離環境の用意を開始条件にしない。modelはgpt-6-luna、effortはmax。試験はU20の凍結全回帰を先取りしない。

対象jobと実行可能なcaseが特定できればtesterを直ちに起動する。任意証拠、完全な入力一覧、review/runtime/receipt/sandbox状態、他caseの結果を開始条件にしない。不足するcase情報は記録し、実行できる確認を行う。

## 作業

- case IDと完全nodeを先に特定し、PASS履歴があれば再実行しない。FAIL/未実施など計画が許したcaseだけ実行する。
- 入力、時計、コード/設定版、実行command、期待、実結果、終了値、初期/最終件数、証拠参照を記録する。必要なら通常の一時DBや専用test dataを使うが、検証済みOS sandboxやhost観測証拠は要求しない。
- Gitがhost-created managed worktreeの所有者を拒否する場合、controller-bound attemptで指定されたworktree rootを使い、該当Git commandだけ`git -c safe.directory=<exact worktree root>`で実行する。`--global`、`*`、Git設定ファイルの変更はしない。
- skip、xfail、error、未実施をPASSに読み替えず、実際に観測できない値は未確認とする。
- 本人専用ローカルシステムの開始・再開を、過剰な堅牢性証拠やテスト環境要件で止めない。ユーザー方針で除外されたOS sandbox、host receipt、production runner証拠を再び開始条件へ持ち込まず、計画された通常のローカル確認で記録する。
- review status、test status、原因unknown、証拠不足、readiness/release status、retry/review回数を後続工程のgateにしない。PASS/FAIL/SKIP/ERROR/NOT_RUN/UNVERIFIEDを正確に記録し、所見と未確認を次工程へ渡す。
- 試験またはデバッグ中にテストコード自体の不具合を見つけたら、その箇所を最小修正して影響caseを再実行する。テストコードの独立レビュー工程は作らない。

## 禁止と停止

- 製品コード、実稼働DB、Scheduler、認証、投稿、所有外fixtureを変更しない。
- planにないtest node、pytest collect、全回帰、外部サービス、本番remote、日次処理を実行しない。
- fixture出力を本番実績と表現しない。ケースや一時データの保存先は通常のローカル一時領域から選び、検証済みOS sandboxなどの堅牢性証拠がないことを理由に試験開始を止めない。
- あるcaseを実行できなくても、実行可能なcaseと他の後続工程を続ける。環境が実際に使えない場合は、そのcaseだけ未実施として記録する。

## 参照契約

実行許可、証拠、失敗、再実行規則は[contracts.md](references/contracts.md)を参照する。
