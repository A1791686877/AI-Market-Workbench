from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
import pytest
from app.fixtures import BARS, SOURCES
from app.models import Interval, SnapshotRequest, DataProvider, OutcomeStatus
from app.indicators import compute_fvg, compute_vpvr, compute_divergences
from app.service import MarketWorkbenchService
from app.storage import Store
from app.market_cache import MarketCache
from app.outcome_worker import evaluate


def series(rows):
    b=BARS['CRYPTO:BTCUSDT:SPOT'][0]
    return [b.model_copy(update={'bar_id':f'gold-{i}', 'open_at':b.open_at+timedelta(minutes=15*i),
        'close_at':b.close_at+timedelta(minutes=15*i),'open':str(c),'high':str(h),'low':str(l),'close':str(c),'volume':str(v)})
        for i,(h,l,c,v) in enumerate(rows)]


def test_fvg_hand_case_prefix_confirmation_frozen_atr_and_invalidation():
    bars=series([(102,100,101,10)]*14+[(105,103,104,10),(105,103,104,10),(104,102.5,103,10),(104,99,100,10)])
    gap=lambda seq:next(g for g in compute_fvg(seq) if g.fvg_id=='fvg-gold-14')
    assert not any(g.fvg_id=='fvg-gold-14' for g in compute_fvg(bars[:14]))
    g=gap(bars[:15]); assert (g.lower,g.upper,g.midpoint)==('102','103','102.5')
    assert Decimal(g.atr_at_creation)==pytest.approx(Decimal(15)/7,abs=Decimal('1e-9'))
    assert g.fill_status.value=='UNTOUCHED'
    assert gap(bars[:17]).fill_status.value=='FILLED_50'
    assert gap(bars).fill_status.value=='FULLY_FILLED'
    assert gap(bars).validity_status.value=='INVALIDATED'
    assert gap(bars).atr_at_creation==g.atr_at_creation


def test_vpvr_hand_case_volume_conservation_and_tie_break():
    result=compute_vpvr(series([(12,10,11,100),(12,11,11.5,100)]),bin_count=2)
    assert [Decimal(b.allocated_volume) for b in result.bins]==[50,150]
    assert (Decimal(result.poc),Decimal(result.val),Decimal(result.vah))==(Decimal('11.5'),11,12)


@pytest.mark.parametrize('component',['RSI_LINE','MACD_HISTOGRAM'])
@pytest.mark.parametrize('p1,p2,o1,o2,kind,field',[(100,90,30,40,'REGULAR_BULLISH','low'),(100,110,30,20,'HIDDEN_BULLISH','low'),
    (200,210,70,60,'REGULAR_BEARISH','high'),(200,190,70,80,'HIDDEN_BEARISH','high')])
def test_divergence_hand_case_with_injected_oscillator(monkeypatch,p1,p2,o1,o2,kind,field,component):
    # Separately validate pivot geometry; RSI/MACD numerical formula tested independently.
    import app.indicators as engine
    bars=series([(151+i*.001,130+i*.001,140,1) for i in range(40)])
    bars[20]=bars[20].model_copy(update={field:str(p1)})
    bars[30]=bars[30].model_copy(update={field:str(p2)})
    osc=[(60 if field=='low' else 40)+i*.001 for i in range(40)];osc[20]=o1;osc[30]=o2
    monkeypatch.setattr(engine,'rsi',lambda values,*args:osc[:len(values)] if component=='RSI_LINE' else [None]*len(values))
    monkeypatch.setattr(engine,'macd',lambda values:([None]*len(values),[None]*len(values),osc[:len(values)] if component=='MACD_HISTOGRAM' else [None]*len(values)))
    full=compute_divergences(bars)
    found=next(d for d in full if d.kind.value==kind)
    assert found.signal_at==bars[33].close_at
    assert [a.value for a in found.price_anchors]==[str(p1),str(p2)]
    assert found.oscillator_component.value==component
    assert compute_divergences(bars[:33])==[]


def test_multiframe_snapshot_separates_legs_and_never_includes_future(tmp_path):
    svc=MarketWorkbenchService(Store(tmp_path/'multi.db'))
    snap=svc.create_snapshot(SnapshotRequest(subject_id='crypto-btc',intervals=[Interval.MIN_15,Interval.HOUR_1],include_perpetual=True))
    assert len(snap.timeframe_coverage)==4
    assert len({p.instrument.instrument_id for p in snap.bars})==2
    assert all(b.close_at<=snap.cutoff_at for p in snap.bars for b in p.bars)


def test_cache_incremental_merge_preserves_older_bars(tmp_path):
    cache=MarketCache(tmp_path/'cache');bars=BARS['CRYPTO:BTCUSDT:SPOT'][:4];src=SOURCES[bars[0].source_ref]
    cache.save(DataProvider.OKX,bars[0].instrument_id,Interval.MIN_15,bars[:3],src)
    result=cache.save(DataProvider.OKX,bars[0].instrument_id,Interval.MIN_15,bars[2:],src)
    assert len(result.bars)==4


def test_historical_pivots_on_wrong_price_side_are_not_current_roles():
    from app.indicators import compute_support_resistance_and_trend
    result,_=compute_support_resistance_and_trend(BARS['CRYPTO:ETHUSDT:PERP'])
    assert result.levels
    for level in result.levels:
        broken=(level.kind.value=='SUPPORT' and Decimal(level.price)>Decimal(level.reference_close)) or (level.kind.value=='RESISTANCE' and Decimal(level.price)<Decimal(level.reference_close))
        assert (level.role_status=='BROKEN_HISTORICAL')==broken
