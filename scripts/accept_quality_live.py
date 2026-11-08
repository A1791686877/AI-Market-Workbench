"""Bounded public data acceptance; never imports/invokes a model provider."""
import json
from datetime import UTC, datetime
from app.config import settings
from app.service import service
from app.history import backfill
from app.models import SnapshotRequest, Interval, QualityFlag
from app.outcome_worker import refill_once


def main():
    if settings.data_mode!='live':raise RuntimeError('Live mode required')
    runs=settings.root/'data/model-runs'
    before={p.name for p in runs.iterdir() if p.is_dir()}
    result={'created_at':datetime.now(UTC).isoformat(),'snapshots':[],'backfill':[]}
    import sys
    for _ in range(0 if '--skip-backfill' in sys.argv else 2):
        state=backfill(service.get_instrument('CRYPTO:BTCUSDT:SPOT'),Interval.MIN_15,datetime(2010,1,1,tzinfo=UTC),2)
        result['backfill'].append(state.model_dump(mode='json'))
        print('BACKFILL',state.status,state.cursor_end,flush=True)
    intervals=[Interval.MIN_15,Interval.HOUR_1,Interval.MIN_195,Interval.HOUR_4,Interval.DAY_1,Interval.WEEK_1,Interval.MONTH_1]
    for subject in ('crypto-btc','crypto-eth'):
        snap=service.create_snapshot(SnapshotRequest(subject_id=subject,intervals=intervals,include_perpetual=True))
        assert len(snap.timeframe_coverage)==14
        assert all(b.close_at<=snap.cutoff_at and QualityFlag.SYNTHETIC_FIXTURE not in b.quality_flags for p in snap.bars for b in p.bars)
        assert snap.market_context is None or snap.market_context.as_of<=snap.cutoff_at
        result['snapshots'].append({'snapshot_id':snap.snapshot_id,'subject_id':subject,'cutoff_at':snap.cutoff_at.isoformat(),
            'coverage':[c.model_dump(mode='json') for c in snap.timeframe_coverage],
            'context':snap.market_context.model_dump(mode='json') if snap.market_context else None,'missing_items':snap.missing_items})
        print('SNAPSHOT',subject,len(snap.timeframe_coverage),'context',snap.market_context is not None,flush=True)
    result['outcomes']=refill_once(service)
    after={p.name for p in runs.iterdir() if p.is_dir()}
    result['new_model_run_directories']=sorted(after-before)
    assert not result['new_model_run_directories']
    target=settings.root/'reports/quality-live-integration.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(target,flush=True)

if __name__=='__main__':main()
