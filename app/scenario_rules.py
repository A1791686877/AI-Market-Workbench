"""Pre-analysis freeze and deterministic post-analysis state, without hit-rate claims."""
from datetime import timedelta
from decimal import Decimal
from typing import Literal
from app.models import FrozenScenarioRule, StrictModel, Interval, PageMeta


class ScenarioState(StrictModel):
    rule_id: str
    status: Literal['WAITING_TRIGGER','TRIGGERED','INVALIDATED','EXPIRED_UNTRIGGERED','EXPIRED_AFTER_TRIGGER','DATA_GAP']
    triggered_at: str | None
    invalidated_at: str | None
    evaluated_at: str
    prediction_hit: None = None


class ScenarioOutcomePage(StrictModel):
    registration_status: Literal['NOT_PRE_REGISTERED','PLATFORM_RULES_NOT_MODEL_HIT_RATE']
    items: list[ScenarioState]
    page: PageMeta


def freeze(cutoff, bundles, instrument_id):
    bundle=next((b for b in bundles if b.instrument.instrument_id==instrument_id and b.interval is Interval.MIN_15),None)
    if bundle is None or Decimal(bundle.vpvr.val)>=Decimal(bundle.vpvr.vah):return []
    return [FrozenScenarioRule(rule_id=f'rule-{case.lower()}-{int(cutoff.timestamp()*1000000)}',scenario=case,
        instrument_id=instrument_id,lower=bundle.vpvr.val,upper=bundle.vpvr.vah,frozen_at=cutoff,
        expires_at=cutoff+timedelta(days=7),trigger=trigger,invalidation=invalidation,
        evidence_ids=[f'ev_vpvr_{bundle.vpvr.vpvr_id}'])
        for case,trigger,invalidation in [('BULLISH','CLOSE_ABOVE_UPPER','CLOSE_BELOW_LOWER'),
            ('NEUTRAL','CLOSE_INSIDE_INCLUSIVE','CLOSE_OUTSIDE'),('BEARISH','CLOSE_BELOW_LOWER','CLOSE_ABOVE_UPPER')]]


def evaluate(rule, bars, now):
    low,high=Decimal(rule.lower),Decimal(rule.upper)
    end=min(now,rule.expires_at)
    expected=((int(rule.frozen_at.timestamp())+899)//900)*900
    rows=sorted([b for b in bars if b.instrument_id==rule.instrument_id and b.interval is Interval.MIN_15 and b.confirmed and b.open_at>=rule.frozen_at and b.close_at<=end],key=lambda b:b.open_at)
    triggered=invalidated=None;status='WAITING_TRIGGER'
    for b in rows:
        if int(b.open_at.timestamp())!=expected:
            status='DATA_GAP';break
        expected=int(b.close_at.timestamp());price=Decimal(b.close)
        match={'CLOSE_ABOVE_UPPER':price>high,'CLOSE_BELOW_LOWER':price<low,
            'CLOSE_INSIDE_INCLUSIVE':low<=price<=high,'CLOSE_OUTSIDE':price<low or price>high}
        if triggered is None and match[rule.trigger]:triggered=b.close_at;status='TRIGGERED'
        elif triggered is not None and match[rule.invalidation]:invalidated=b.close_at;status='INVALIDATED';break
    if status not in ('INVALIDATED','DATA_GAP') and expected+900<=int(end.timestamp()):status='DATA_GAP'
    if status not in ('INVALIDATED','DATA_GAP') and now>=rule.expires_at:
        status='EXPIRED_AFTER_TRIGGER' if triggered else 'EXPIRED_UNTRIGGERED'
    return ScenarioState(rule_id=rule.rule_id,status=status,triggered_at=triggered.isoformat() if triggered else None,
        invalidated_at=invalidated.isoformat() if invalidated else None,evaluated_at=now.isoformat())
