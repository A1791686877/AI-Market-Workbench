"""Read saved acceptance artifacts only; no network or extra model calls."""
import hashlib
import json
import re
from datetime import UTC, datetime

from app.config import settings
from app.service import service


def main():
    root = settings.root
    real = json.loads((root/'reports/real-ai-acceptance.json').read_text(encoding='utf-8'))
    checks = []
    for row in real['results']:
        analysis = service.get_analysis(row['task']['analysis_id'])
        snapshot = service.get_snapshot(analysis.snapshot.snapshot_id)
        service._validate_analysis(analysis, snapshot)
        runs = [p for p in (root/'data/model-runs').glob('run-*/call.json')
                if json.loads(p.read_text(encoding='utf-8')).get('snapshot_id') == snapshot.snapshot_id]
        assert len(runs) == 1
        response = (runs[0].parent/'output.json').read_bytes()
        assert hashlib.sha256(response).hexdigest() == analysis.audit.raw_response_sha256
        assert all(any('normalization v2' in s for s in p.sources[0].limitations) for p in snapshot.bars)
        checks.append({'subject':row['subject'], 'analysis_id':analysis.analysis_id,
                       'snapshot_sha256':snapshot.snapshot_sha256, 'raw_response_hash_match':True,
                       'schema_and_referenced_price_validation':'PASS', 'volume_normalization':'base currency v2',
                       'history':snapshot.bars[0].quality.model_dump(mode='json'),
                       'audit':analysis.audit.model_dump(mode='json')})
    mcp = json.loads((root/'reports/real-ai-mcp-acceptance.json').read_text(encoding='utf-8'))
    answer = (__import__('pathlib').Path(mcp['run_directory'])/'answer.md').read_text(encoding='utf-8')
    citations = set(re.findall(r'ev_[A-Za-z0-9_-]+', answer))
    per_subject = {}
    for call in mcp['tool_calls']:
        if call['tool'] != 'market_get_indicator_evidence' or call['status'] != 'completed':
            continue
        data = call['result']['structured_content']
        known = {e['evidence_id'] for e in data['evidence']}
        per_subject[data['subject_id']] = {'cited_ids':sorted(citations & known), 'known_ids':sorted(known)}
    all_known = {eid for row in per_subject.values() for eid in row['known_ids']}
    assert citations <= all_known and len(per_subject) == 2
    assert all(len(row['cited_ids']) >= 3 for row in per_subject.values())
    totals = {'input_tokens':0, 'output_tokens':0, 'cached_input_tokens':0, 'completed_turns':0}
    runs = []
    for event_file in (root/'data/model-runs').glob('*/events.jsonl'):
        events = [json.loads(line) for line in event_file.read_text(encoding='utf-8').splitlines() if line.startswith('{')]
        usage = next((e.get('usage', {}) for e in reversed(events) if e.get('type') == 'turn.completed'), None)
        if usage:
            runs.append({'run':event_file.parent.name, 'usage':usage})
            totals['completed_turns'] += 1
            for key in ('input_tokens','output_tokens','cached_input_tokens'):
                totals[key] += usage.get(key, 0)
    report = {'checked_at':datetime.now(UTC).isoformat(), 'real_model_checks':checks,
              'mcp_passed':mcp['passed'], 'mcp_exact_citations':per_subject, 'unknown_citations':sorted(citations-all_known),
              'manual_review':'Reviewed BTC/ETH prices, RSI, MACD, ADX, trend and VPVR statements against frozen observations. Conditional scenarios are interpretations, not validated forecasts.',
              'superseded_runs':['run-b860007625a049daaed5c4bac414a3c6','run-e5e2a15cb9de43669be1e28fe70eda9f'],
              'superseded_reason':'Old OKX spot quote-volume normalization and old FVG midpoint type. Retain as call proof, exclude from indicator/performance acceptance.',
              'usage_all_saved_runs_including_failed_acceptance':totals, 'runs':runs,
              'model_cost_usd':None, 'data_subscription_usd':0,
              'limitations':['Token totals are provider-reported; cached input is a subset, not additional input.',
                             'Shared ChatGPT account quota percentages cannot be attributed to these tests.',
                             'No realized prediction accuracy or profitability has been validated.']}
    (root/'reports/ai-evidence-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'real_model_checks':len(checks),'mcp_subjects':len(per_subject),'unknown_citations':len(citations-all_known),'usage':totals}))


if __name__ == '__main__':
    main()
