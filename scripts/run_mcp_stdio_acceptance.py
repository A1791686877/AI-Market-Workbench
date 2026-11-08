from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime

from mcp import Client, StdioServerParameters

from app.config import settings
from app.logging_config import audit_event


async def run() -> dict:
    child_env = os.environ.copy()
    child_env.update(
        {
            "AMW_ROOT": str(settings.root),
            "AMW_DATA_MODE": "live",
            "AMW_NETWORK_ENABLED": "true",
            "AMW_CRYPTO_PROVIDER": "okx",
            "TEMP": str(settings.root / "tmp"),
            "TMP": str(settings.root / "tmp"),
        }
    )
    server = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-m", "app.mcp_server"],
        env=child_env,
        cwd=settings.root,
    )
    async with Client(server, read_timeout_seconds=120) as client:
        listed = await client.list_tools()
        search = await client.call_tool("market_search_instruments", {"q": "BTC"})
        bars = await client.call_tool(
            "market_get_bars",
            {"instrument_id": "CRYPTO:BTCUSDT:SPOT", "interval": "15m", "limit": 5000},
        )
        bundle = await client.call_tool(
            "market_get_indicator_bundle",
            {
                "subject_id": "crypto-btc",
                "instrument_id": "CRYPTO:BTCUSDT:SPOT",
                "interval": "15m",
            },
        )
        context = await client.call_tool("market_get_crypto_context", {"subject_id": "crypto-btc"})
        if any(result.is_error for result in (search, bars, bundle, context)):
            raise RuntimeError("one or more MCP live tool calls returned is_error=true")
        bar_data = bars.structured_content
        bundle_data = bundle.structured_content
        context_data = context.structured_content
        assert bar_data is not None and bundle_data is not None and context_data is not None
        assert bar_data["sources"][0]["provider"] == "OKX"
        assert bar_data["quality"]["request_status"] in {"SUCCESS", "STALE_CACHE"}
        assert all("SYNTHETIC_FIXTURE" not in row["quality_flags"] for row in bar_data["bars"])
        return {
            "server_name": client.server_info.name,
            "server_version": client.server_info.version,
            "protocol_version": client.protocol_version,
            "tool_count": len(listed.tools),
            "tool_names": [tool.name for tool in listed.tools],
            "search_first_instrument": search.structured_content["items"][0]["instrument_id"],
            "bars": {
                "provider": bar_data["sources"][0]["provider"],
                "source_at": bar_data["sources"][0]["source_at"],
                "received_at": bar_data["sources"][0]["received_at"],
                "delay_class": bar_data["sources"][0]["delay_class"],
                "request_status": bar_data["quality"]["request_status"],
                "bar_count": len(bar_data["bars"]),
                "missing_bar_count": bar_data["quality"]["missing_bar_count"],
                "history_complete": bar_data["quality"]["history_complete"],
                "synthetic_fallback": False,
            },
            "indicator_evidence_ids": [
                evidence["evidence_id"]
                for indicator in bundle_data["indicators"]
                for evidence in indicator["evidence"]
            ],
            "vpvr_calculation_mode": bundle_data["vpvr"]["calculation_mode"],
            "trend_state": bundle_data["trend_structure"]["state"],
            "market_context_provider": context_data["sources"][-1]["provider"],
            "account_or_trading_tools_present": any(
                any(word in tool.name.lower() for word in ("account", "order", "withdraw", "transfer"))
                for tool in listed.tools
            ),
        }


def main() -> None:
    result = asyncio.run(run())
    report = {
        "phase": "phase-1-mcp-live-stdio-acceptance",
        "created_at": datetime.now(UTC).isoformat(),
        "actual_stdio_subprocess": True,
        "external_llm_used": False,
        "result": result,
    }
    target = settings.root / "reports" / "mcp-live-stdio-acceptance.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    audit_event("mcp_live_stdio_acceptance_completed", report_path=str(target))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
