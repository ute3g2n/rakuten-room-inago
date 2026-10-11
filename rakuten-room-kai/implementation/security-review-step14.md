# Step 14 セキュリティレビュー

## 対象と方法

localhost投稿状態API、外部process起動、モデル入出力、実行ログ、資格情報ファイルの配置とACL、依存関係を確認した。資格情報・認証状態ファイルの本文は開いていない。実サイトにも接続していない。

## 修正済み所見

| ID | 当初重要度 | 所見と処置 | 再確認 |
|---|---|---|---|
| S14-01 | High | `rakuten-api.credentials.json` がWindows継承ACLで広い読み取り権限を持っていた。本人・SYSTEM・Administrators以外の継承・明示的な広い許可を除去。`rakuten-credentials.mjs`が今後も読取前に`icacls.exe`を引数配列で実行し、失敗時は読取を止める。 | 現ファイル、別置き`auth-state.json`、`affiliate-report`はいずれもACL保護済み、広い読み取り権限なし。dummy引数で自動処理をテスト。 |
| S14-02 | Medium | dashboard書込み失敗でplanだけが残り得た。HTML・plan・目次の書込み失敗時に既存内容へ戻し、日次runを`OUTPUT_WRITE_FAILED`/exit 1にした。 | 障害注入で計画とHTMLの部分更新なし。 |
| S14-03 | Medium | dashboard生成エラーの詳細に資格情報風の文字列が混入し得た。画面用エラーと日次失敗報告へ保存する前にredactionを適用。 | dummy値が成果物に残らないテスト、secret scan 0。 |
| S14-04 | Medium | 投稿状態を複数画面から更新すると古い状態を上書きし得た。訂正履歴件数によるrevisionをAPIが照合し、競合を409で拒否。 | stale revisionの更新拒否と正本不変をテスト。 |

## 確認結果

- サーバーは`127.0.0.1`だけにbindし、HostとOriginの完全一致、CSRF token、Content-Type、4096バイト上限、許可パスだけを検査。CSPを送信し、動的本文をHTML escapeで表示する。
- Python subprocessは引数配列・`shell=False`で起動。Windows ACL処理はNode `execFile`を使用。URL、商品ID、日付、JSON Schemaの入力制限と出力検証をテスト済み。
- prompt、manifest、events、stdout/stderr、status、dashboardを含む`rakuten-room-kai`とジョブpromptを走査し、credential-shaped値0。認証ファイル名はスキャン時に検知し、本文を読まない。
- `npm audit`は計0件。Python requirementsおよび実インストール版を`pip-audit`で確認し、既知脆弱性0件。
- CRITICAL/HIGH/MEDIUMの未解決所見: **0件**。

## 運用上の残り

物理的なディスク満杯ではrollback書込み自体も失敗し得る。この場合はrunが失敗し、手動で空き容量と正本を確認して再開する。実サイトのログイン・DOM・API応答に対する検証はStep 15で行う。
