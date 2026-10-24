"""Explicit independent-review fixtures; never used by production code."""

from dataclasses import replace

from automation.inago.contracts import AISubjobResult


def review_subjob(
    author: AISubjobResult, *, verdict: str = "pass", body: str | None = None
) -> AISubjobResult:
    return replace(
        author,
        job_id=f"{author.parent_job_id}-a{author.attempt}-review",
        agent_id="AG-COPY-REVIEW",
        skill_name="rr-inago-copy-reviewer",
        input_hash="fixture-independent-review-input",
        result_status="success",
        payload={
            "offer_key": author.payload["offer_key"],
            "draft_version": author.payload["draft_version"],
            "verdict": verdict,
            "reviewed_body": author.payload["body"] if body is None else body,
            "reason": "Fixture review result; not evidence of real AI quality.",
            "evidence_refs": list(author.evidence_refs),
            "used_input_refs": list(author.input_refs),
            "findings": [],
        },
    )
