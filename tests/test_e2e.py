from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import httpx

from app.config import settings
from app.main import app, get_service
from app.service import MarketWorkbenchService
from app.storage import Store


def _configure_service(tmp_path: Path) -> None:
    test_service = MarketWorkbenchService(Store(tmp_path / "e2e.db"))
    app.dependency_overrides[get_service] = lambda: test_service


def test_aapl_btc_eth_data_indicator_page_analysis_and_records(tmp_path: Path) -> None:
    async def scenario() -> None:
        _configure_service(tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            assert (await client.get("/")).status_code == 200
            health = (await client.get("/v1/health")).json()
            assert health == {
                "status": "ok",
                "checked_at": health["checked_at"],
                "data_mode": "fixture",
                "network_enabled": False,
                "crypto_provider": "okx",
                # The field must reflect this machine, not an assumed install.
                "codex_cli_available": shutil.which(settings.codex_command) is not None,
                "real_ai_cost_boundary": "Codex/ChatGPT usage quota; USD amount unavailable",
                "real_ai_model": "gpt-5.6-terra",
                "monthly_data_budget_usd": 0,
            }
            cases = [
                ("us-aapl", "US:AAPL", "195m"),
                ("crypto-btc", "CRYPTO:BTCUSDT:SPOT", "15m"),
                ("crypto-eth", "CRYPTO:ETHUSDT:SPOT", "15m"),
            ]
            for subject_id, instrument_id, interval in cases:
                bars = await client.get(
                    f"/v1/instruments/{instrument_id}/bars",
                    params={"interval": interval, "limit": 5000},
                )
                assert bars.status_code == 200, bars.text
                assert bars.json()["bars"]
                assert bars.json()["sources"][0]["provider"] == "FIXTURE_SYNTHETIC"
                bundle = await client.get(
                    f"/v1/subjects/{subject_id}/indicator-bundle",
                    params={"interval": interval},
                )
                assert bundle.status_code == 200, bundle.text
                assert bundle.json()["vpvr"]["is_estimate"] is True
                assert bundle.json()["support_resistance"]["algorithm_version"] == "pivot-cluster-3x3-atr025-v1"
                assert bundle.json()["trend_structure"]["confirmation"]["can_repaint"] is False

            analysis_cases = [
                ("us-aapl", ["15m", "195m", "4h"]),
                ("crypto-btc", ["15m", "4h"]),
                ("crypto-eth", ["15m", "4h"]),
            ]
            for subject_id, intervals in analysis_cases:
                snapshot_response = await client.post(
                    "/v1/analysis-snapshots",
                    json={"subject_id": subject_id, "intervals": intervals},
                )
                assert snapshot_response.status_code == 201, snapshot_response.text
                snapshot_id = snapshot_response.json()["snapshot_id"]
                task_response = await client.post(
                    "/v1/ai-analysis-tasks",
                    headers={"Idempotency-Key": f"test-e2e-{subject_id}-analysis-0001"},
                    json={
                        "snapshot_id": snapshot_id,
                        "provider": "DETERMINISTIC_DEMO",
                        "model": "evidence-rules-demo-v1",
                        "prompt_version": "scenario-v2",
                    },
                )
                assert task_response.status_code == 202, task_response.text
                task_id = task_response.json()["task_id"]
                task = (await client.get(f"/v1/ai-analysis-tasks/{task_id}")).json()
                assert task["status"] == "SUCCEEDED", task
                analysis = await client.get(f"/v1/ai-analyses/{task['analysis_id']}")
                assert analysis.status_code == 200, analysis.text
                result = analysis.json()
                assert [item["type"] for item in result["scenarios"]] == [
                    "BULLISH",
                    "NEUTRAL",
                    "BEARISH",
                ]
                assert result["subject"]["analysis_scope"] == "SINGLE_UNDERLYING"
                assert result["subject"]["portfolio_analysis"] is False
                assert result["audit"]["cost"] == "0"
                outcomes = await client.get(
                    f"/v1/ai-analyses/{task['analysis_id']}/outcomes", params={"limit": 2}
                )
                assert outcomes.status_code == 200
                assert len(outcomes.json()["items"]) == 2
                assert outcomes.json()["page"]["has_more"] is True
                assert all(item["status"] == "PENDING" for item in outcomes.json()["items"])

                frozen = (await client.get(f"/v1/analysis-snapshots/{snapshot_id}")).json()
                assert "outcomes" not in frozen
                assert "analysis_outcomes" not in str(frozen)
        app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_idempotency_conflict_and_typed_errors(tmp_path: Path) -> None:
    async def scenario() -> None:
        _configure_service(tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            first = (
                await client.post(
                    "/v1/analysis-snapshots", json={"subject_id": "us-aapl", "intervals": ["15m"]}
                )
            ).json()
            second = (
                await client.post(
                    "/v1/analysis-snapshots", json={"subject_id": "crypto-eth", "intervals": ["15m"]}
                )
            ).json()
            key = "same-key-different-body-0001"
            body = {
                "snapshot_id": first["snapshot_id"],
                "provider": "DETERMINISTIC_DEMO",
                "model": "evidence-rules-demo-v1",
                "prompt_version": "scenario-v2",
            }
            assert (
                await client.post("/v1/ai-analysis-tasks", headers={"Idempotency-Key": key}, json=body)
            ).status_code == 202
            body["snapshot_id"] = second["snapshot_id"]
            conflict = await client.post(
                "/v1/ai-analysis-tasks", headers={"Idempotency-Key": key}, json=body
            )
            assert conflict.status_code == 409
            assert conflict.headers["content-type"].startswith("application/problem+json")
            assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
            missing = await client.get("/v1/ai-analyses/not-found")
            assert missing.status_code == 404
            assert missing.json()["code"] == "NOT_FOUND"
            malformed_cursor = await client.get(
                "/v1/ai-analyses", params={"cursor": "***not-base64***"}
            )
            assert malformed_cursor.status_code == 400
            assert malformed_cursor.json()["code"] == "VALIDATION_ERROR"
        app.dependency_overrides.clear()

    asyncio.run(scenario())
