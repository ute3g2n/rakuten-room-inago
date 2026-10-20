# Three-field handoff example

This example documents the runner's format only; it is not a claimed agent handoff or proof that a predecessor read the file.

## Assigned Markdown receipt

`implementation.md` should contain a predecessor receipt like:

```text
前担当Markdown実読証跡
- path: rakuten-room-inago/runtime/maintenance-flow/job-historical-9e7f0c175cc4c874c955a5dab29c4427/investigation.md
- sha256: <the hash read from the file on disk>
- read fact: <one specific fact taken from that Markdown>
```

The runner checks that the exact predecessor hash is present in the agent-owned output, but the final record must also retain the actual output Markdown and the call input/output references. A controller fallback is never counted as an agent read.

## Final response schema

```json
{
  "担当": "試験",
  "入力": "incident.md、investigation.md、implementation.mdと実差分",
  "依頼": "変更した日次境界を合成fixtureで失敗させ、実ログを確認する。"
}
```

Only these three non-empty strings are accepted by the structured handoff parser. A malformed response receives one same-role format-only retry; if that also fails, the runner records the safe failure and proceeds with the fixed next role using available files and a marked fallback handoff.
