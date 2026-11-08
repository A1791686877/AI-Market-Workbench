from __future__ import annotations

import asyncio
import os
import sys

from mcp import Client, StdioServerParameters

from app.mcp_contract import TOOLS
from app.mcp_server import mcp


def test_official_mcp_client_negotiates_lists_and_calls_tool() -> None:
    async def scenario() -> None:
        async with Client(mcp) as client:
            listed = await client.list_tools()
            actual_names = {item.name for item in listed.tools}
            assert actual_names == {item.name for item in TOOLS}
            expected = {item.name: item for item in TOOLS}
            for runtime_tool in listed.tools:
                spec = expected[runtime_tool.name]
                assert set(runtime_tool.input_schema.get("properties", {})) == set(
                    spec.input_model.model_json_schema(mode="validation").get("properties", {})
                )
                assert runtime_tool.output_schema is not None
                assert set(runtime_tool.output_schema.get("properties", {})) == set(
                    spec.output_model.model_json_schema(mode="serialization").get("properties", {})
                )
            result = await client.call_tool("market_search_instruments", {"q": "BTC"})
            assert result.is_error is False
            assert result.structured_content is not None
            assert result.structured_content["items"][0]["symbol"] == "BTCUSDT"
            assert result.structured_content["items"][0]["instrument_id"].startswith("CRYPTO:BTC")

    asyncio.run(scenario())


def test_real_stdio_transport_launches_and_returns_indicator_evidence() -> None:
    async def scenario() -> None:
        child_env = os.environ.copy()
        child_env.update(
            {
                "AMW_ROOT": "E:\\AI-Market-Workbench",
                "AMW_DATA_MODE": "fixture",
                "AMW_NETWORK_ENABLED": "false",
                "TEMP": "E:\\AI-Market-Workbench\\tmp",
                "TMP": "E:\\AI-Market-Workbench\\tmp",
            }
        )
        server = StdioServerParameters(
            command=sys.executable,
            args=["-B", "-m", "app.mcp_server"],
            env=child_env,
            cwd="E:\\AI-Market-Workbench",
        )
        async with Client(server, read_timeout_seconds=30) as client:
            result = await client.call_tool(
                "market_get_indicator_bundle",
                {
                    "subject_id": "crypto-btc",
                    "instrument_id": "CRYPTO:BTCUSDT:SPOT",
                    "interval": "15m",
                },
            )
            assert result.is_error is False
            assert result.structured_content is not None
            assert result.structured_content["instrument"]["instrument_id"] == "CRYPTO:BTCUSDT:SPOT"
            assert result.structured_content["vpvr"]["is_estimate"] is True
            assert result.structured_content["indicators"][0]["evidence"]

    asyncio.run(scenario())
