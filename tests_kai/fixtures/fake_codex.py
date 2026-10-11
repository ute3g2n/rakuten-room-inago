import json
import os
import sys
import time
from pathlib import Path


args = sys.argv[1:]
sys.stdin.reconfigure(encoding="utf-8")
prompt = sys.stdin.read()
model = args[args.index("--model") + 1]
output = Path(args[args.index("--output-last-message") + 1])
record = os.environ.get("FAKE_RECORD")
if record:
    Path(record).write_text(json.dumps({"args": args, "prompt": prompt}, ensure_ascii=False), encoding="utf-8")
mode = os.environ.get("FAKE_MODE", "success")
if mode == "timeout":
    time.sleep(2)
if mode == "auth":
    print("AUTH_REQUIRED", file=sys.stderr)
    raise SystemExit(1)
if mode == "fail_once":
    counter = Path(os.environ["FAKE_COUNTER"])
    number = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    counter.write_text(str(number + 1), encoding="utf-8")
    if number == 0:
        print("temporary model failure", file=sys.stderr)
        raise SystemExit(1)
if mode in {"escalate", "escalate_fail"} and (model != "gpt-6-astra" or mode == "escalate_fail"):
    print("model could not resolve conflict", file=sys.stderr)
    raise SystemExit(1)
payload = os.environ.get("FAKE_PAYLOAD", "{}")
if mode == "invalid_json":
    payload = "{broken"
elif mode == "schema_invalid":
    payload = "{}"
if mode != "missing":
    output.write_text(payload, encoding="utf-8")
if mode == "secret":
    print("accessKey=dummy-key password=dummy-password")
    print("Authorization: Bearer dummy-bearer", file=sys.stderr)
else:
    print(json.dumps({"type": "usage", "model": model}))
