from types import SimpleNamespace
from dataclasses import replace
from app.fixtures import BARS,SOURCES,BTC_SPOT
from app.models import Interval,DataProvider
from app.market_cache import MarketCache
import app.history as history


def test_backfill_commits_before_failure_and_resumes_without_losing_cache(monkeypatch,tmp_path):
    bars=BARS[BTC_SPOT.instrument_id][:20];source=SOURCES[bars[0].source_ref]
    cache=MarketCache(tmp_path/'market');cache.save(DataProvider.OKX,BTC_SPOT.instrument_id,Interval.MIN_15,bars[10:],source)
    monkeypatch.setattr(history,'market_cache',cache)
    monkeypatch.setattr(history,'settings',replace(history.settings,root=tmp_path))
    calls=[]
    class Fake:
        http=SimpleNamespace(close=lambda:None)
        def fetch_bars(self,instrument,interval,start,end,max_pages):
            calls.append(end)
            if len(calls)==1:return bars[5:10],source
            if len(calls)==2:raise TimeoutError('test failure')
            return bars[:5],source
    monkeypatch.setattr(history,'OkxPublicProvider',Fake)
    a=history.backfill(BTC_SPOT,Interval.MIN_15,bars[0].open_at,2)
    assert a.status=='REQUEST_FAILED' and a.committed_pages==1 and a.cursor_end==bars[5].open_at
    b=history.backfill(BTC_SPOT,Interval.MIN_15,bars[0].open_at,1)
    assert b.status=='TARGET_REACHED' and b.committed_pages==2
    assert calls[1]==calls[2]==bars[5].open_at
    assert len(cache.load(DataProvider.OKX,BTC_SPOT.instrument_id,Interval.MIN_15).bars)==20


def test_provider_empty_page_is_distinct_and_end_cursor_is_exclusive(monkeypatch):
    from app.providers import OkxPublicProvider,ProviderHistoryEmpty
    from app.safe_http import JsonResponse
    import pytest
    provider=OkxPublicProvider();end=BARS[BTC_SPOT.instrument_id][0].open_at
    def response(*args,**kwargs):
        assert kwargs['params']['after']==int(end.timestamp()*1000)
        return JsonResponse({'code':'0','data':[]},'0'*64,200)
    monkeypatch.setattr(provider.http,'get_json',response)
    with pytest.raises(ProviderHistoryEmpty):
        provider.fetch_bars(BTC_SPOT,Interval.DAY_1,end.replace(year=2000),end,max_pages=1)
    provider.http.close()
