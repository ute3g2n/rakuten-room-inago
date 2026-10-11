import json
import copy
import sys
from datetime import date
from pathlib import Path
from threading import Event

import pytest

from automation.codex_cli_runner import CodexCliRunner
from automation.config_loader import load_model_policy
from automation.model_policy import ModelPolicy
from automation.run_context import RunContext


FAKE = Path(__file__).parent / "fixtures" / "fake_codex.py"


def _run(tmp_path: Path, mode: str = "success", job: str = "triage", reason=None,
         special_path: bool = False, payload_override=None, allowed_ids=None,
         url_by_id=None, cancel_event=None):
    context = RunContext.create(tmp_path / "orchestrator", "research", date(2026, 9, 25), "manual")
    subskill = tmp_path / "skills with spaces" / job / "SKILL.md"
    subskill.parent.mkdir(parents=True)
    subskill.write_text("# skill", encoding="utf-8")
    input_path = tmp_path / ("a & b $.json" if special_path else "input.json")
    input_path.write_text("{}", encoding="utf-8")
    envelope = tmp_path / "job-envelope.json"
    envelope.write_text("{}", encoding="utf-8")
    record = tmp_path / "record.json"
    payload = ({"run_id": context.run_id, "target_date": "2026-09-25", "selected": [], "excluded": [], "uncertain": []}
               if job == "triage" else {"run_id": context.run_id, "target_date": "2026-09-25", "verified": [], "rejected": [], "unavailable": []})
    if payload_override is not None:
        payload.update(payload_override)
    env = {"FAKE_MODE": mode, "FAKE_RECORD": str(record), "FAKE_PAYLOAD": json.dumps(payload),
           "FAKE_COUNTER": str(tmp_path / "counter.txt")}
    runner = CodexCliRunner(tmp_path, executable=Path(sys.executable), executable_prefix=[str(FAKE)],
                            timeout_seconds=0.25 if mode == "timeout" else 5, extra_env=env)
    result = runner.run(context, job, subskill, input_path, envelope,
                        "triage-output" if job == "triage" else "product-verification",
                        escalation_reason=reason, allowed_ids=allowed_ids if allowed_ids is not None else set(),
                        url_by_id=url_by_id if url_by_id is not None else {}, cancel_event=cancel_event)
    return context, result, record


def test_success_uses_policy_flags_and_stdin(tmp_path: Path):
    context, result, record = _run(tmp_path, special_path=True)
    assert result.success
    data = json.loads(record.read_text(encoding="utf-8"))
    args = data["args"]
    assert args[args.index("--model") + 1] == "gpt-6-luna"
    assert "model_reasoning_effort=low" in args
    assert "--skip-git-repo-check" in args and "--ephemeral" in args and "--json" in args
    assert "a & b $.json" in data["prompt"]
    assert "auth-state.json" not in data["prompt"] and "rakuten-api.credentials.json" not in data["prompt"]
    assert args[-1] == "-"
    assert context.manifest["model_attempts"][0]["model"] == "gpt-6-luna"


def test_one_normal_retry_succeeds(tmp_path: Path):
    context, result, _ = _run(tmp_path, mode="fail_once")
    assert result.success and len(result.attempts) == 2
    assert all(attempt["model"] == "gpt-6-luna" for attempt in context.manifest["model_attempts"])


@pytest.mark.parametrize("mode,code", [("invalid_json", "SCHEMA_INVALID"), ("schema_invalid", "SCHEMA_INVALID"), ("timeout", "CLI_TIMEOUT"), ("missing", "OUTPUT_MISSING")])
def test_invalid_and_timeout_never_reach_canonical_output(tmp_path: Path, mode: str, code: str):
    context, result, _ = _run(tmp_path, mode=mode)
    assert not result.success and result.error_code == code
    assert not (context.logger.run_dir / "outputs/triage.json").exists()


def test_escalation_only_for_configured_reason(tmp_path: Path):
    context, result, _ = _run(tmp_path, mode="escalate", job="product_verify", reason="conflicting_product_information")
    assert result.success
    assert [a["model"] for a in result.attempts] == ["gpt-6-sol", "gpt-6-sol", "gpt-6-astra"]
    assert context.manifest["model_attempts"][-1]["escalated"]


def test_auth_blocks_escalation(tmp_path: Path):
    _context, result, _ = _run(tmp_path, mode="auth", job="product_verify", reason="conflicting_product_information")
    assert not result.success and result.error_code == "AUTH_REQUIRED"
    assert len(result.attempts) == 1


def test_logs_redact_dummy_secrets(tmp_path: Path):
    context, result, _ = _run(tmp_path, mode="secret")
    assert result.success
    logs = "".join(path.read_text(encoding="utf-8") for path in context.logger.run_dir.glob("*.log"))
    assert not any(value in logs for value in ("dummy-key", "dummy-password", "dummy-bearer"))


def test_prompt_rejects_credential_named_input(tmp_path: Path):
    context = RunContext.create(tmp_path / "orchestrator", "research", date(2026, 9, 25), "manual")
    subskill = tmp_path / "skill/SKILL.md"
    subskill.parent.mkdir()
    subskill.write_text("# skill", encoding="utf-8")
    secret_named = tmp_path / "auth-state.json"
    secret_named.write_text("{}", encoding="utf-8")
    runner = CodexCliRunner(tmp_path, executable=Path(sys.executable), executable_prefix=[str(FAKE)])
    result = runner.run(context, "triage", subskill, secret_named, tmp_path / "envelope.json", "triage-output")
    assert not result.success and result.error_code == "INPUT_INVALID"


def test_url_mismatch_and_unknown_id_are_rejected(tmp_path: Path):
    bad = {"verified": [{"product_id": "P03", "url": "https://item.rakuten.co.jp/wrong/other/",
                         "checked_at": "2026-09-25T13:00:00+09:00", "evidence": ["page"]}]}
    context, result, _ = _run(tmp_path, job="product_verify", payload_override=bad,
                              allowed_ids={"P03"}, url_by_id={"P03": "https://item.rakuten.co.jp/shop/right/"})
    assert not result.success and result.error_code == "DATA_MISMATCH"
    assert not (context.logger.run_dir / "outputs/product_verify.json").exists()


def test_cancel_is_reported_separately(tmp_path: Path):
    cancellation = Event()
    cancellation.set()
    _context, result, _ = _run(tmp_path, mode="timeout", cancel_event=cancellation)
    assert not result.success and result.error_code == "CLI_CANCELLED"
    assert len(result.attempts) == 1


def test_every_policy_job_has_valid_normal_model():
    policy = ModelPolicy()
    for name in load_model_policy()["jobs"]:
        assert policy.job(name)["normal_model"] in {"gpt-6-luna", "gpt-6-sol"}


def test_invalid_model_is_rejected_before_cli_launch(tmp_path: Path):
    document = copy.deepcopy(load_model_policy())
    document["jobs"]["triage"]["normal_model"] = "unapproved-model"
    context = RunContext.create(tmp_path / "orchestrator", "research", date(2026, 9, 25), "manual")
    runner = CodexCliRunner(tmp_path, executable=Path(sys.executable), executable_prefix=[str(FAKE)],
                            policy=ModelPolicy(document))
    result = runner.run(context, "triage", tmp_path / "unused-skill", tmp_path / "unused-input",
                        tmp_path / "unused-envelope", "triage-output")
    assert not result.success and result.error_code == "MODEL_INVALID"
    assert context.manifest["model_attempts"] == []
