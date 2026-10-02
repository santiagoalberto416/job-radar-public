import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from job_radar.scorer import CreditExhaustedError, Scorer, ScorerFatalError, format_job

JOB = {
    "id": 1,
    "title": "Senior Angular Developer",
    "company": "Acme",
    "location": "Remote (LatAm)",
    "source": "greenhouse",
    "remote": 1,
    "description": "Angular 18, RxJS, NgRx. " * 10,
}
GOOD = {"score": 88, "fits_location": True, "seniority": "senior", "reason": "Angular senior remoto LatAm", "red_flags": []}


def response(text=None, stop_reason="end_turn", stop_details=None):
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=900)
    return SimpleNamespace(content=content, stop_reason=stop_reason, stop_details=stop_details, usage=usage)


class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeClient:
    def __init__(self, result):
        self.messages = FakeMessages(result)
        self.beta = SimpleNamespace(messages=FakeMessages(result))


def scorer(result, **cfg):
    return Scorer(llm_cfg={"model": "claude-opus-5", "effort": "low", **cfg}, profile="Senior FE", client=FakeClient(result))


def api_error(cls, status, message="boom"):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message=message, response=httpx2.Response(status, request=request), body=None)


def test_scores_job_with_structured_output_and_fallback():
    s = scorer(response(json.dumps(GOOD)))
    outcome = s.score(JOB)
    assert outcome.result == {**GOOD, "salary_usd_month": 0} and outcome.error is None
    call = s.client.beta.messages.calls[0]  # opus-5 uses the beta endpoint with server-side fallback
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "low"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Senior FE" in call["system"][0]["text"]
    assert s.usage.calls == 1 and s.usage.cost_usd("claude-opus-5") > 0


def test_haiku_has_no_effort_and_no_fallback():
    s = scorer(response(json.dumps(GOOD)), model="claude-haiku-4-5")
    s.score(JOB)
    call = s.client.messages.calls[0]
    assert "effort" not in call["output_config"] and "fallbacks" not in call
    assert s.client.beta.messages.calls == []


def test_clamps_score_and_normalizes():
    outcome = scorer(response(json.dumps({**GOOD, "score": 140, "reason": "  dos\nlineas "}))).score(JOB)
    assert outcome.result["score"] == 100 and outcome.result["reason"] == "dos lineas"


def test_refusal_is_skipped_not_raised():
    outcome = scorer(response(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))).score(JOB)
    assert outcome.result is None and "refusal" in outcome.error


def test_invalid_json_and_max_tokens():
    assert "invalid JSON" in scorer(response("not json")).score(JOB).error
    assert "max_tokens" in scorer(response("{", stop_reason="max_tokens")).score(JOB).error


def test_bad_request_skips_job():
    outcome = scorer(api_error(anthropic.BadRequestError, 400)).score(JOB)
    assert outcome.error.startswith("bad request")


@pytest.mark.parametrize(
    "cls, status",
    [
        (anthropic.AuthenticationError, 401),
        (anthropic.NotFoundError, 404),
        (anthropic.RateLimitError, 429),
        (anthropic.InternalServerError, 500),
    ],
)
def test_run_level_errors_stop_scoring(cls, status):
    with pytest.raises(ScorerFatalError):
        scorer(api_error(cls, status)).score(JOB)


def test_format_job_truncates_long_descriptions():
    text = format_job({**JOB, "description": "a" * 500}, max_chars=100)
    assert "[...description truncated...]" in text and "a" * 101 not in text
    assert text.startswith("<job>") and "Remote flag from source: yes" in text


def test_exhausted_credit_is_detected():
    msg = "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing."
    with pytest.raises(CreditExhaustedError):
        scorer(api_error(anthropic.BadRequestError, 400, msg)).score(JOB)


def test_location_rule_comes_from_config():
    from job_radar.scorer import location_rule

    rule = location_rule({"home": "Guadalajara, Mexico", "remote_ok": ["Mexico", "LatAm"], "onsite_ok": ["Guadalajara"]})
    assert "living in Guadalajara, Mexico" in rule
    assert "remote open to Mexico or LatAm" in rule and "on-site/hybrid in Guadalajara" in rule
    assert rule.count("living in") == 1  # only the configured home, no hardcoded city
    # Without a location block, Claude is told to use the profile instead.
    assert "described in the profile" in location_rule({})


def test_system_prompt_uses_location_and_profile():
    s = Scorer(llm_cfg={"model": "claude-sonnet-5"}, profile="Perfil X", client=FakeClient(None),
               location={"home": "Bogotá, Colombia", "remote_ok": ["LatAm"]})
    assert "Bogotá, Colombia" in s.system and "Perfil X" in s.system and "{location_rule}" not in s.system
