from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import settings
from app.fixtures import SUBJECTS, BARS
from app.models import AiAnalysisTaskRequest, Interval, SnapshotRequest, KeyLevelKind
from app.model_provider import child_environment, public_input, strict_output_schema
from app.providers import OkxPublicProvider
from app.safe_http import JsonResponse
from app.service import MarketWorkbenchService, AnalysisValidationError
from app.storage import Store
from app.indicators import compute_divergences


def test_child_environment_never_inherits_market_or_api_secrets(monkeypatch):
    monkeypatch.setenv('ALPACA_MARKET_DATA_SECRET', 'test-secret-do-not-export')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret-do-not-export')
    monkeypatch.setenv('UNRELATED_LOCAL_DATA', 'private')
    monkeypatch.setenv('CODEX_HOME', r'E:\CodexData\.codex-main')
    env = child_environment()
    assert not {'ALPACA_MARKET_DATA_SECRET', 'OPENAI_API_KEY', 'UNRELATED_LOCAL_DATA'} & env.keys()
    assert env['TEMP'].startswith('E:')


def test_search_provides_subject_id_for_next_mcp_call(tmp_path):
    service = MarketWorkbenchService(Store(tmp_path / 'search.db'))
    result = service.search_instruments('BTC', 20, None)
    assert all(item.subject_id == 'crypto-btc' for item in result.items)
    view = service.get_indicator_evidence(result.items[0].subject_id, Interval.MIN_15)
    assert view.evidence and view.latest_bar.confirmed


@pytest.mark.parametrize('leg,expected', [('SPOT','2'), ('PERP','200')])
def test_okx_volume_is_base_currency_not_quote(monkeypatch, leg, expected):
    provider = OkxPublicProvider()
    instrument = next(i for i in SUBJECTS['crypto-btc'].instruments if i.instrument_id.endswith(leg))
    opened = datetime(2026, 1, 1, tzinfo=UTC)
    row = [str(int(opened.timestamp()*1000)), '100', '101', '99', '100', '2', '200', '200', '1']
    monkeypatch.setattr(provider.http, 'get_json', lambda *a, **k: JsonResponse({'code':'0','data':[row]}, '0'*64, 200))
    bars, source = provider.fetch_bars(instrument, Interval.MIN_15, opened, opened+timedelta(minutes=15))
    assert bars[0].volume == expected
    assert 'normalization v2' in source.limitations[1]


def test_public_projection_and_exact_price_validation(tmp_path):
    service = MarketWorkbenchService(Store(tmp_path / 'validation.db'))
    snapshot = service.create_snapshot(SnapshotRequest(subject_id='crypto-btc', intervals=[Interval.MIN_15]))
    payload = public_input(snapshot)
    assert 'outcomes' not in payload
    assert snapshot.market_context is None or snapshot.market_context.as_of <= snapshot.cutoff_at
    assert len(payload['bars'][0]['recent_confirmed_bars']) <= 8
    constrained = strict_output_schema(snapshot)
    assert set(constrained['$defs']['Scenario']['properties']['evidence_ids']['items']['enum']) == {e.evidence_id for e in snapshot.evidence}
    request = AiAnalysisTaskRequest(snapshot_id=snapshot.snapshot_id)
    analysis = service._generate_analysis(snapshot, request)
    level = analysis.key_levels[0].model_copy(update={'price':'99999999'})
    invalid = analysis.model_copy(update={'key_levels':[level, *analysis.key_levels[1:]]})
    with pytest.raises(AnalysisValidationError, match='not supported'):
        service._validate_analysis(invalid, snapshot)
    bad_kind = analysis.key_levels[0].model_copy(update={'kind':KeyLevelKind.FVG_BOUNDARY})
    with pytest.raises(AnalysisValidationError, match='boundary'):
        service._validate_analysis(analysis.model_copy(update={'key_levels':[bad_kind, *analysis.key_levels[1:]]}), snapshot)


def test_schema_requires_every_property_and_no_extra_fields():
    from jsonschema import Draft202012Validator
    schema = strict_output_schema()
    Draft202012Validator.check_schema(schema)
    assert set(schema['required']) == set(schema['properties'])
    assert schema['additionalProperties'] is False


def test_divergence_waits_for_both_price_and_oscillator_confirmation():
    bars = BARS['CRYPTO:BTCUSDT:SPOT']
    for divergence in compute_divergences(bars):
        latest_anchor = max(a.at for a in [*divergence.price_anchors, *divergence.oscillator_anchors])
        assert divergence.signal_at >= latest_anchor + timedelta(minutes=45)


def test_incomplete_crypto_week_and_intraday_buckets_are_not_confirmed():
    from app.bar_builder import aggregate_bars
    from app.models import SessionType
    template = BARS['CRYPTO:BTCUSDT:SPOT'][0]
    monday = datetime(2026, 8, 31, tzinfo=UTC)
    daily = [template.model_copy(update={'bar_id':f'day-{i}', 'interval':Interval.DAY_1,
             'open_at':monday+timedelta(days=i), 'close_at':monday+timedelta(days=i+1),
             'session':SessionType.CRYPTO_24X7}) for i in range(7)]
    assert aggregate_bars(daily[:6], Interval.WEEK_1) == []
    assert len(aggregate_bars(daily, Interval.WEEK_1)) == 1
    minute = BARS['CRYPTO:BTCUSDT:SPOT'][:2]
    assert aggregate_bars(minute, Interval.MIN_195) == []
