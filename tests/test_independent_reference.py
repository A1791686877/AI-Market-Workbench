from app.fixtures import BARS
from app.indicators import compute_indicators
from scripts.independent_indicators import compare


def test_all_standard_components_pointwise_against_decimal_reference():
    bars=BARS['CRYPTO:BTCUSDT:SPOT']
    checks=compare(bars,compute_indicators(bars))
    assert len(checks)==10
    assert all(c['passed'] for c in checks), checks


def test_macd_is_not_ready_before_signal_warmup():
    indicator=next(i for i in compute_indicators(BARS['CRYPTO:BTCUSDT:SPOT'][:27]) if i.name.value=='MACD')
    assert indicator.status.value!='READY'
    assert indicator.current.signal is None
