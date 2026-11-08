from datetime import UTC,datetime,timedelta
from app.models import FrozenScenarioRule
from app.fixtures import BARS
from app.scenario_rules import evaluate


def test_preregistered_close_only_trigger_invalidation_expiry_and_gap():
    start=datetime(2026,9,1,tzinfo=UTC)
    rule=FrozenScenarioRule(rule_id='rule-test',scenario='BULLISH',instrument_id='CRYPTO:BTCUSDT:SPOT',lower='90',upper='110',
        frozen_at=start,expires_at=start+timedelta(hours=1),trigger='CLOSE_ABOVE_UPPER',invalidation='CLOSE_BELOW_LOWER',evidence_ids=['ev_test'])
    template=BARS[rule.instrument_id][0]
    bars=[template.model_copy(update={'open_at':start+timedelta(minutes=15*i),'close_at':start+timedelta(minutes=15*(i+1)),
        'close':str(price)}) for i,price in enumerate([100,111,89,100])]
    assert evaluate(rule,bars[:1],bars[0].close_at).status=='WAITING_TRIGGER'
    assert evaluate(rule,bars[:2],bars[1].close_at).status=='TRIGGERED'
    assert evaluate(rule,bars,bars[-1].close_at).status=='INVALIDATED'
    assert evaluate(rule,[bars[0],bars[2]],bars[2].close_at).status=='DATA_GAP'
    flat=[b.model_copy(update={'close':'100'}) for b in bars]
    assert evaluate(rule,flat,rule.expires_at).status=='EXPIRED_UNTRIGGERED'
    assert evaluate(rule,bars,rule.expires_at).prediction_hit is None
