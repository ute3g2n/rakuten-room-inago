# 要件HTML生成ツール

`docs/specifications/inago/イナゴスキル要件定義書.html` が唯一の正本です。生成・編集スクリプトは、HTML内部の `requirements-source` snapshot を入力にします。削除済みの旧Markdownを読み込んだり、旧資料から正本を再構成したりしません。

```powershell
node tools/docs/inago-requirements/build.mjs
```

同梱資料のファイル名と文書IDは正本の `referenceDocumentIds` で対応し、生成時に `docs/document-index.json` の現在パスから本文を読み直します。図解は正本と同じフォルダーの `図解/` へ生成します。レビュー出力は `worklogs/projects/requirements/reviews/`、旧比較版は `archive/documents/requirements/` に保存します。
