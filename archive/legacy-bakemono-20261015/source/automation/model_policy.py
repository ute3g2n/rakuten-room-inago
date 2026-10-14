"""Model choice and bounded retry/escalation from the shared policy."""

from automation.config_loader import load_model_policy


ALLOWED_MODELS = {"gpt-6-luna", "gpt-6-sol", "gpt-6-astra"}
NO_RETRY_ERRORS = {"INPUT_INVALID", "AUTH_REQUIRED", "CAPTCHA_REQUIRED", "FETCH_DATA_INSUFFICIENT", "MODEL_INVALID"}


class ModelPolicy:
    def __init__(self, document: dict | None = None):
        self.document = document if document is not None else load_model_policy()

    def job(self, name: str) -> dict:
        try:
            policy = self.document["jobs"][name]
        except KeyError as error:
            raise ValueError(f"unknown model job: {name}") from error
        for key in ("normal_model", "escalation_model"):
            model = policy[key]
            if model is not None and model not in ALLOWED_MODELS:
                raise ValueError(f"invalid model in policy: {name}/{key}")
        if policy["normal_model"] == "gpt-6-astra":
            raise ValueError("Astra cannot be a normal model")
        if policy["normal_retries"] > 1:
            raise ValueError("normal retries must be at most one")
        return policy

    def can_escalate(self, name: str, reason: str | None, error_code: str | None) -> bool:
        policy = self.job(name)
        return bool(policy["escalation_model"] and reason in policy["escalation_conditions"]
                    and reason not in policy["escalation_forbidden_when"]
                    and error_code not in NO_RETRY_ERRORS
                    and error_code not in policy["escalation_forbidden_when"])
