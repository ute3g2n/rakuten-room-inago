# FC10 独立製品review r1

判定: changes requested（Medium 1件）。

対象manifest SHA-256: `adce6774960a15ff198518155ab9cc0e97cefd92b447b77e30fd2f9df5ae4915`。
対象製品SHA-256: `7186adc923d253c362b904f74e8cb016d1ad2adc37a800db508834788cb991e7`。

指摘: 通常の設定済みreviewerについてDBのreview記録が欠けても、review_record=Noneが通過して公開可能でした。fallback unavailable側だけがDB記録を必須にしていました。両経路で記録を必須にし、版・reviewer・statusをattempt/resultに照合する必要があります。

その他の確認: controller unavailableの組合せ、result review一致、通常reviewer ID、report/attempt/HTML/manifest/launch identityの検査は維持されていました。reviewerはtestを閲覧・実行せず、編集・本番操作をしていません。
