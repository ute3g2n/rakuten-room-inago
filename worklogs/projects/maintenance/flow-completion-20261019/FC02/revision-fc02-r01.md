# FC02-R01 作者修正

修正版 fc02-contract-v2、確認時刻 2026-10-19T14:05:06.612523+00:00。common-tester.tomlのdeveloper_instructions 11–12行とcommon-testing/SKILL.mdの作業先頭bullet/禁止項目を修正した。計画指定のfocused local pytestを実行し、新規計画内nodeの確認が必要な場合だけ最小collectionを行う。関連code/test版変更がある影響nodeは理由と版を記録して再実行し、変更のない既PASSの重複、無関係なcollection、全suiteは実行しない。テストコードの独立reviewを追加せず、結果を後続gateにしない。

元のindependent-review.md/jsonとv1結果・機械確認・role after SHA・handoffは上書きせず保存した。書込は2つの指示fileとFC02成果だけ。製品code/DB/試験/plan section14は変更していない。TOML/JSON、targeted rg、対象2file git diff --checkは上記JSONの実結果。follow-upは独立reviewerにこの2fileのexact版を依頼し、approvedを作者が捏造しない。
