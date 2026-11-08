from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.logging_config import _redact
import app.safe_http as safe_http
from app.safe_http import NetworkDisabledError, ReadOnlyHttpClient, UnsafeEndpointError
from app.trading_calendar import is_complete_xnys_15m_bar


ROOT = Path(r"E:\AI-Market-Workbench")


def test_network_is_disabled_by_default() -> None:
    client = ReadOnlyHttpClient({"data.alpaca.markets"})
    with pytest.raises(NetworkDisabledError):
        client.get_json("https://data.alpaca.markets/v2/stocks/bars")


def test_http_and_non_allowlisted_hosts_are_rejected_before_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_http, "settings", replace(safe_http.settings, network_enabled=True))
    client = ReadOnlyHttpClient({"data.alpaca.markets"})
    with pytest.raises(UnsafeEndpointError):
        client.get_json("http://data.alpaca.markets/v2/stocks/bars")
    with pytest.raises(UnsafeEndpointError):
        client.get_json("https://api.alpaca.markets/v2/orders")
    assert is_complete_xnys_15m_bar(datetime(2026, 9, 4, 13, 30, tzinfo=UTC))
    assert is_complete_xnys_15m_bar(datetime(2026, 9, 4, 19, 45, tzinfo=UTC))
    assert not is_complete_xnys_15m_bar(datetime(2026, 9, 4, 13, 15, tzinfo=UTC))
    assert not is_complete_xnys_15m_bar(datetime(2026, 9, 4, 20, 0, tzinfo=UTC))


def test_secrets_are_redacted() -> None:
    payload = _redact({"Authorization": "Bearer secret", "nested": {"token": "abc"}, "safe": "value"})
    assert payload["Authorization"] == "[REDACTED]"
    assert payload["nested"]["token"] == "[REDACTED]"
    assert payload["safe"] == "value"


def test_source_has_no_trading_domain_or_order_route() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app").glob("*.py"))
    assert "https://api.alpaca.markets" not in source
    assert "/v2/orders" not in source
    assert "/api/v3/order" not in source
    assert "/fapi/v1/order" not in source
    openapi = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
    mcp = json.loads((ROOT / "contracts" / "mcp-tools.json").read_text(encoding="utf-8"))
    capability_names = " ".join([*openapi["paths"], *(tool["name"] for tool in mcp["tools"])]).lower()
    assert all(word not in capability_names for word in ("account", "order", "withdraw", "transfer"))
