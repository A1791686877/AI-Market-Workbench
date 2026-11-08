from __future__ import annotations

import json
from datetime import UTC, datetime

from app.config import settings
from app.logging_config import audit_event
from app.safe_http import ReadOnlyHttpClient


def main() -> None:
    if settings.data_mode != "live" or not settings.network_enabled:
        raise RuntimeError("probe requires live mode with network enabled")
    client = ReadOnlyHttpClient({"www.okx.com"})
    checks = []
    requests = (
        ("BTC-USDT spot candles", "/api/v5/market/history-candles", {"instId": "BTC-USDT", "bar": "15m", "limit": 8}),
        ("ETH-USDT spot candles", "/api/v5/market/history-candles", {"instId": "ETH-USDT", "bar": "15m", "limit": 8}),
        ("BTC-USDT-SWAP candles", "/api/v5/market/history-candles", {"instId": "BTC-USDT-SWAP", "bar": "15m", "limit": 8}),
        ("ETH-USDT-SWAP candles", "/api/v5/market/history-candles", {"instId": "ETH-USDT-SWAP", "bar": "15m", "limit": 8}),
        ("BTC funding", "/api/v5/public/funding-rate", {"instId": "BTC-USDT-SWAP"}),
        ("BTC open interest", "/api/v5/public/open-interest", {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}),
    )
    for label, path, params in requests:
        try:
            response = client.get_json(f"https://www.okx.com{path}", params=params)
            payload = response.payload
            checks.append(
                {
                    "label": label,
                    "status": "SUCCESS" if payload.get("code") == "0" else "FAILED",
                    "http_status": response.status_code,
                    "provider_code": payload.get("code"),
                    "row_count": len(payload.get("data", [])),
                    "sha256": response.sha256,
                }
            )
        except Exception as exc:
            audit_event("provider_probe_failed", provider="OKX", check=label, error_type=type(exc).__name__)
            checks.append({"label": label, "status": "FAILED", "error_type": type(exc).__name__, "detail": str(exc)[:500]})
    report = {
        "probed_at": datetime.now(UTC).isoformat(),
        "account_or_api_key_used": False,
        "checks": checks,
    }
    target = settings.root / "reports" / "okx-public-probe.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if any(item["status"] != "SUCCESS" for item in checks):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
