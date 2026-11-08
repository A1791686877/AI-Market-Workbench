from dataclasses import replace
import json

import pytest
import app.model_provider as provider


def test_zero_local_budget_fails_before_model_or_input_export(monkeypatch, tmp_path):
    monkeypatch.setattr(provider, 'settings', replace(provider.settings, root=tmp_path))
    monkeypatch.setenv('AMW_MODEL_MAX_ATTEMPTS_24H', '0')
    monkeypatch.setattr(provider.subprocess, 'run', lambda *a, **kw: pytest.fail('Model process must never start'))
    with pytest.raises(provider.ProviderError, match='budget exhausted'):
        provider._invoke(None, 'test', 'test', 'codex-stub', [])
    assert not list(tmp_path.rglob('input.json'))


# --- accounting: only attempts that reached the model consume the 24h slot -------------------
# The local cap exists to stop the project from spending model quota by accident. An upstream
# rejection (account usage limit, auth failure) or a missing CLI spends nothing, so it must not
# block the next *user-requested* call for a day. Nothing here retries automatically.

USAGE_LIMIT_EVENTS = [
    {"type": "error", "message": "Reconnecting... 5/5 (request timed out)"},
    {"type": "error", "message": "You've hit your usage limit. Upgrade to Pro or try again at 5:52 PM."},
    {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}},
]


def test_account_usage_limit_does_not_consume_the_local_budget() -> None:
    assert provider.counts_against_budget(usage={}, events=USAGE_LIMIT_EVENTS) is False


def test_a_completed_turn_consumes_the_budget() -> None:
    assert provider.counts_against_budget(usage={"input_tokens": 10, "output_tokens": 2}, events=[]) is True


def test_timeout_counts_because_the_spend_is_unknown() -> None:
    assert provider.counts_against_budget(usage={}, events=[], timed_out=True) is True


def test_unknown_failure_counts_conservatively() -> None:
    assert provider.counts_against_budget(usage={}, events=[{"type": "error", "message": "bad JSON"}]) is True
    assert provider.counts_against_budget(usage={}, events=[]) is True


def test_run_dir_without_a_decision_counts(tmp_path) -> None:
    run = tmp_path / "run-abc"
    run.mkdir()
    assert provider.run_counts_against_budget(run) is True                 # no call.json at all
    (run / "call.json").write_text(json.dumps({"status": "FAILED"}), encoding="utf-8")
    assert provider.run_counts_against_budget(run) is True                 # legacy record: no flag
    (run / "call.json").write_text(json.dumps({"counts_against_budget": False}), encoding="utf-8")
    assert provider.run_counts_against_budget(run) is False
    (run / "call.json").write_text("{not json", encoding="utf-8")
    assert provider.run_counts_against_budget(run) is True
