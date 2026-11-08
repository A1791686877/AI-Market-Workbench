"""Real Codex AI client, isolated to two read-only AMW MCP tools (not a mock client)."""
import json
import os
import subprocess
import uuid
import asyncio
from datetime import UTC, datetime

from app.config import settings
from app.model_provider import child_environment, codex_command
from mcp import Client, StdioServerParameters


def main():
    root = settings.root
    run = root / "data" / "model-runs" / f"mcp-client-{uuid.uuid4().hex}"
    run.mkdir(parents=True)
    command = codex_command(run, settings.codex_default_model)
    config = {
        "command": str(root / ".venv" / "Scripts" / "python.exe"),
        "args": ["-B", "-m", "app.mcp_server"], "cwd": str(root),
        "enabled_tools": ["market_search_instruments", "market_get_indicator_evidence"],
        "startup_timeout_sec": 45, "tool_timeout_sec": 120,
        "env": {"AMW_ROOT": str(root), "AMW_DATA_MODE": "live", "AMW_NETWORK_ENABLED": "true",
                "AMW_CRYPTO_PROVIDER": settings.crypto_provider, "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1", "TEMP": str(root / "tmp"), "TMP": str(root / "tmp")},
    }
    async def preflight():
        env = child_environment() | config['env']
        params = StdioServerParameters(command=config['command'], args=config['args'], cwd=config['cwd'], env=env)
        async with Client(params, read_timeout_seconds=120) as client:
            listed = await client.list_tools()
            assert set(config['enabled_tools']) <= {t.name for t in listed.tools}
            for query in ('BTC', 'ETH'):
                search = await client.call_tool('market_search_instruments', {'q': query})
                item = next(i for i in search.structured_content['items'] if i['contract_type'] == 'SPOT')
                result = await client.call_tool('market_get_indicator_evidence',
                    {'subject_id':item['subject_id'], 'instrument_id':item['instrument_id'], 'interval':'15m'})
                assert not result.is_error, str(result)
                assert result.structured_content['evidence']
        print('MCP transport/data preflight passed; now starting independent AI client.', flush=True)
    asyncio.run(preflight())
    config['required'] = True  # Startup errors must fail, not silently omit the server.
    def toml(value):
        if isinstance(value, dict):
            return "{" + ",".join(f"{key}={toml(item)}" for key, item in value.items()) + "}"
        return json.dumps(value)
    command += ["-c", "mcp_servers.amw=" + toml(config),
                "--output-last-message", str(run / "answer.md"), "-"]
    prompt = ("请通过 amw MCP 工具自主查找 BTC 和 ETH 的标的 ID，再分别获取其现货 15m 指标证据，"
              "回答：两个标的目前动量和趋势是否一致？关键价位与数据局限是什么？"
              "你没有预先提供的行情，必须实际调用搜索工具及证据工具。回答必须分别给出数据时间、"
              "实际历史起点、来源、至少三个精确 evidence_id、看多/中性/看空条件，说明 VPVR 是估算。"
              "不要读取本地文件、运行命令、使用其他服务或声称历史完整，不是投资建议，不保证盈利。")
    (run / "prompt.txt").write_text(prompt, encoding="utf-8")
    started = datetime.now(UTC)
    result = subprocess.run(command, input=prompt, text=True, encoding="utf-8", errors="replace",
                            capture_output=True, timeout=360, cwd=run, env=child_environment(),
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    (run / "events.jsonl").write_text(result.stdout, encoding="utf-8")
    (run / "stderr.log").write_text(result.stderr, encoding="utf-8")
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    calls = [e["item"] for e in events if e.get("type") == "item.completed"
             and e.get("item", {}).get("type") == "mcp_tool_call"]
    names = {c.get("tool") for c in calls if c.get("status") == "completed"}
    subjects = {c.get('result', {}).get('structured_content', {}).get('subject_id') for c in calls
                if c.get('tool') == 'market_get_indicator_evidence' and c.get('status') == 'completed'
                and c.get('result', {}).get('structured_content')}
    usage = next((e.get("usage") for e in reversed(events) if e.get("type") == "turn.completed"), None)
    report = {"started_at": started.isoformat(), "finished_at": datetime.now(UTC).isoformat(),
              "client": "codex exec (real model autonomously selecting MCP calls)",
              "model": settings.codex_default_model, "returncode": result.returncode,
              "usage": usage, "run_directory": str(run), "tool_calls": calls,
              "passed": result.returncode == 0 and {"market_search_instruments", "market_get_indicator_evidence"} <= names and subjects == {'crypto-btc', 'crypto-eth'},
              "semantic_answer_validation": "manual review required", "data_subscription_usd": 0,
              "model_cost_usd": None}
    (root / "reports" / "real-ai-mcp-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (run / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != "tool_calls"}, ensure_ascii=False), flush=True)
    print("tools:", [c.get("tool") for c in calls], flush=True)


if __name__ == "__main__":
    main()
