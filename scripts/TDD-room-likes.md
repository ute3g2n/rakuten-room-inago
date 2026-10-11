# ROOMいいね数巡回のTDD実行記録

実施日: 2026-09-23

## RED

- `node --test tests/room-likes.test.mjs`: `room-likes-lib.mjs` が存在せず `ERR_MODULE_NOT_FOUND`。
- URL検証、INITIAL_STATE抽出、投稿値変換、保存、失敗時の非上書きテストを先に作成。
- 一覧カード照合追加時は `matchRecentCollects` 未exportで失敗。
- アカウント文書対応時は `findRoomUrlInText` 未exportで失敗。
- 反応領域抽出時は `listCardFromRaw` 未exportで失敗。
- 画像配信URL差への対応テストは `0 !== 1` で失敗。
- 公開一覧応答の検証テストは `publicCollectsPayloadToPage` 未exportで失敗。
- 30日巡回追加時は `deduplicateObservations`、`jstCalendarRangeStart`、`selectRecentUnpinnedPage` の未exportで失敗。
- 確認済み0件の期間スナップショットは、従来の0件保護で拒否されることを確認してから期間巡回に限って許可。
- 最初の実ページ30日巡回で、古い通常投稿が先頭ページへ混在し、9月9日以降を取りこぼす停止条件の欠陥を発見。混在ページでは停止しないテストを追加すると `true !== false` で失敗した後、期間内通常投稿が0件のページまで進む条件へ修正。
- 境界監査用の直近期間外投稿日テストを追加し、未実装の `undefined` で失敗してから保存処理を追加。

## GREEN

- 各REDの後に最小実装を追加し、対象テストを再実行。
- `node --test tests/room-likes.test.mjs`: 20件成功、失敗0件。
- `node --experimental-test-coverage --test tests/room-likes.test.mjs`: line 94.27%、function 100.00%、branch 76.24%。
- `npm test`: プロジェクト全43件成功、失敗0件。
- `npm audit --omit=dev`: 既知の脆弱性0件。

## 実ページ検証

- 指定された他ユーザーのROOMで1件、6件、21件を段階的に取得。
- 6件の直後に同条件を再実行し、変更0件と観測履歴の非重複を確認。
- 21件で2ページ目の取得を確認。
- `--days 30` の最終検証では5ページを巡回し、通常投稿52件、固定投稿除外3件、重複0件、取得失敗0件。
- JST開始境界は2026-08-25 00:00。対象内の最古は同日11:36:42、直近の期間外投稿は前日20:49:08で、境界をまたいで取得できた。
- 再実行では同じ51件を取得し、その間に実際に増えた2投稿のいいね数だけを変更として保存。最終実行では新規投稿1件を検出して52件へ更新。
- 初期のDOM抽出失敗は `unavailable` として残り、`latest.json` を0件で作成しなかった。
