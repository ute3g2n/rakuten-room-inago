# Step 04 結果

- 状態: success
- `codex-cli 0.155.0-alpha.16`と`codex exec --help`で使用フラグを再確認。実モデルは実行していない。
- Pythonの引数配列と標準入力でCLIを起動し、`shell=False`。CLI実体は絶対パスへ解決する。
- model-policyでLuna/Solの通常モデル、1回までの通常再試行、条件付きAstra昇格を制御する。
- 出力Schema、対象日、run_id、商品ID・URL照合後だけ正規成果物へ保存する。CLIログは保存前にマスクする。

## 公開API

- `CodexCliRunner.run(context, job_name, subskill, input_path, envelope, schema_name, ...)`。fake CLI用に実体パス、前置引数、timeout、環境を注入可能。
- `ModelPolicy.job/can_escalate`、`build_job_prompt`。
- RunContextのmanifestにモデル、推論量、試行番号、昇格、終了コード、出力先を記録。

## 秘密を含まないコマンド形

```text
codex exec -C <project-root> --skip-git-repo-check --ephemeral --model gpt-6-luna --config model_reasoning_effort=low --output-schema <schema-path> --json --output-last-message <temporary-output-path> -
```

## 検証

- `codex --version` / `codex exec --help`: exit 0。
- fake CLI試験を含む`pytest -q tests_kai`: 30 passed、exit 0。通常成功、同モデル再試行、条件付きAstra、昇格禁止、認証待ち、timeout、cancel、Schema不正、JSON不正、出力欠落、ID・URL不一致、秘密マスク、スペース・特殊文字パス、モデル名不正を確認。
- 不正出力は正規成果物へ保存されない。

## 既知の制限

- 実CLIによるモデル応答・認証・usageイベントの確認はStep 15で行う。
- Windowsタスクと従来スキルは変更していない。
