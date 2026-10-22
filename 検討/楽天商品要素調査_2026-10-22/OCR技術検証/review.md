# OCR技術検証 — STEP 05 実行review記録

## 初回独立review

- reviewer: `/root/ocr_tech_review`（read-only）
- 状態: 初回指摘を修正し、独立再reviewを依頼中
- 初回review時刻: 2026-10-22 JST（分単位の記録なし）
- 指摘severity: reviewer出力に明示なし。severityを推測しない。

### 初回指摘と対応

1. **index pathの制限不足** — runnerが任意の `--index` を受け取り得る。`index.json` の解決先を収集フォルダーに固定し、さらにrunnerの実フォルダーをstudy rootとして固定した。`OCR_STUDY_ROOT` が別の場所を指す場合は、cache/temp作成前に終了コード2で拒否する。
2. **一時 `R:` aliasの解除不足** — wrapperが新規作成したaliasを終了時に解除しなかった。新規作成時だけ `finally` で解除し、既存aliasは解除せず、変更したprocess環境変数も復元する。
3. **画像単位の照合証拠不足** — 集計数だけで各画像のyes/noと確認者・時刻を再現できなかった。59件の `OCR画像照合一覧.md` を作成し、record ID、元画像path、OCR状態、判定、短い理由を記録した。確認者はCodex (assistant)、照合記録確定時刻は2026-10-22 23:29:17 JST。

### 修正後の確認

- runnerの1画像smoke: `success 1 / empty 0 / error 0`、終了コード0。
- wrapper実行後の一時 `R:` alias: 解除済み。
- 収集index以外のindex path: 終了コード2、結果ファイルなし。
- `OCR_STUDY_ROOT=C:\Windows` の上書き: 終了コード2、結果ファイルなし。
- 収集index内で商品pathが収集ルート外へ解決するprobe: 終了コード1、結果ファイルなし。
- per-image照合一覧: 59行、raw JSONLの全record ID/path/statusと一致。判定は商品説明情報あり27件、なし32件、商品ごとのあり件数は `OCR検証結果.md` と一致。

## 独立再review 1

- 状態: 実施済み（read-only）。初回の3指摘が修正されたことを確認。
- reviewer所見: 59件すべてのID/path/商品フォルダー/OCR状態がraw JSONLと一致。59件すべてを再度独立に目視したわけではないが、各商品の代表的な肯定/否定画像を確認し、理由と出力の対応を確認した。入力59件のSHA256とraw JSONLのSHA256はmanifestと一致。
- 残る所見 `[LOW]`: 既存 `R:` のrunner hashだけで同一性を判断すると、runnerの同一コピーがある別フォルダーを再利用し得る。

### LOW所見への対応

- wrapperが既存aliasを再利用する前に、studyフォルダー内のvenv Pythonから `R:\` の実解決先と固定study rootを比較するよう変更した。異なる場合はOCRを始めず拒否する。
- 同じrunner hashを持つdecoyフォルダーに `R:` を割り当てるprobeで、wrapperが開始前に拒否し、OCR出力なしを確認。probe harnessがaliasを解除し、最終状態で `R:` が残らないことも確認。
- 正常系の最終smokeは1件success、終了コード0。wrapper作成aliasは実行後に解除された。

## 独立再review

- 状態: 実施済み。コード所見は解消、実行証拠に `[LOW]` の追記依頼あり。
- reviewerは更新後の `run_ocr.ps1` を確認し、異なる実pathを指す同一runner hashの既存 `R:` をOCR前に拒否すること、runner hashのmanifest一致、positive smokeの成功とwrapper作成aliasの解除、現在の `R:` 不在を確認した。
- reviewerの残る所見: decoy負例の記録に再現コマンド、wrapper終了コード、出力確認先、decoy hashが不足。

### LOW証拠記録所見への対応

- 再現時の準備: study folderで `asset-manifest.json` の `existing_alias_path_probe.setup_command` を実行し、同一hashのrunnerを含む `tmp/alias-decoy` に `R:` を割り当てた。コマンド全文はmanifestに保存した。
- wrapper command: `powershell.exe -NoLogo -NoProfile -NonInteractive -File .\run_ocr.ps1 -Limit 1 -OutputName 'ocr-alias-mismatch-probe-final.jsonl'`。
- wrapper process exit code: `1`。出力確認先 `results/ocr-alias-mismatch-probe-final.jsonl` は作成されなかった。
- study runnerとdecoy runnerのSHA256は両方 `59D0C784DBB86F6FA82EB9B428486D32BEFD4AE3B81BDCA83103B6B49F366170`。wrapper実行前のpath mismatchをハッシュ一致だけで通さないことを確認した。
- probe harnessは `subst R: /d` を終了コード0で実行し、その後 `R:` aliasが残らないことを確認した。`tmp/alias-decoy` は検討フォルダー内。
- 詳細値は `asset-manifest.json` の `existing_alias_path_probe` に保存した。独立reviewerが再現情報・wrapper/manifest間の一致・出力未作成・alias不在をread-only確認し、残る所見なしと報告した。

## 最終read-only再確認

- 状態: 実施済み。
- reviewerはmanifestとこの記録で、wrapper command、process exit code 1、出力確認先と未作成、study/decoy runnerの一致hash、`subst R: /d` の終了コード0を照合した。wrapperのpath不一致時の拒否順序と、既存aliasをwrapperが削除しない動作も記録と一致。
- STEP-05、計画第14節、review記録の状態・集計に矛盾なし。残る所見なし。承認・合格判定とは扱わない。

## 対象と限界

- 対象: `ocr_runner.py`、`run_ocr.ps1`、`smoke.py`、lock/hash/manifest、59件のOCR結果と元画像、`OCR検証結果.md`、`OCR画像照合一覧.md`。
- 正解文字列に対するCER/WER等の精度測定、商品ページDOMテキストや別要素画像の網羅確認は行っていない。
- 59画像に文字が含まれていたため、文字なし画像のempty経路を入力で確認していない。
- 本記録は独立reviewと作業者の目視照合を区別する。作業者の目視照合を独立reviewと呼ばない。
