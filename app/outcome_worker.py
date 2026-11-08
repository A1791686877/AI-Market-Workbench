"""Cached, deterministic forward PRICE performance. Never interprets scenarios as hits.

Does not fetch providers or call models. Only the separate outcomes table is updated.
Legacy natural-language scenarios have no pre-registered executable rule and remain unscored.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from app.models import AnalysisOutcome, OutcomeStatus, Interval, DataProvider, is_real_model_provider
from app.logging_config import audit_event


RULE='CRYPTO_CLOSED_15M_FIRST_CLOSE_AT_OR_AFTER_MATURITY_V1'


def evaluate(outcome, snapshot, bars, now):
    if outcome.status is OutcomeStatus.MATURED or now < outcome.matures_at:
        return outcome
    update={'evaluation_rule_version':RULE,'computed_at':now,'scenario_scoring_status':'PLATFORM_RULES_NOT_MODEL_HIT_RATE' if snapshot.scenario_rules else 'NOT_PRE_REGISTERED'}
    if snapshot.subject.subject_type.value != 'CRYPTO_ASSET' or snapshot.bars[0].interval is not Interval.MIN_15:
        return outcome.model_copy(update={**update,'status':OutcomeStatus.UNAVAILABLE,
            'unavailable_reason':'Only crypto 15m reference snapshots supported by this outcome rule; other reference rules not inferred.'})
    start=snapshot.cutoff_at
    future=sorted([b for b in bars if b.confirmed and b.open_at>=start and b.close_at<=now],key=lambda b:b.open_at)
    ends=[b for b in future if outcome.matures_at<=b.close_at<outcome.matures_at+timedelta(minutes=15)]
    if not ends:
        return outcome.model_copy(update={**update,'status':OutcomeStatus.AWAITING_DATA,'unavailable_reason':'Matured but exact permitted end bar is not cached; will retry without model calls.'})
    end=ends[0]
    window=[b for b in future if b.close_at<=end.close_at]
    first_expected=((int(start.timestamp())+899)//900)*900
    complete=bool(window) and int(window[0].open_at.timestamp())==first_expected and all(a.close_at==b.open_at for a,b in zip(window,window[1:]))
    if not complete:
        return outcome.model_copy(update={**update,'status':OutcomeStatus.AWAITING_DATA,'unavailable_reason':'Internal future-window gap; no interpolation or favorable path assumptions.'})
    price=Decimal(outcome.start_price)
    return outcome.model_copy(update={**update,'status':OutcomeStatus.MATURED,
        'end_price':end.close,'return_fraction':str(Decimal(end.close)/price-1),
        'maximum_favorable_excursion':str(max(Decimal(0),max(Decimal(b.high) for b in window)/price-1)),
        'maximum_adverse_excursion':str(min(Decimal(0),min(Decimal(b.low) for b in window)/price-1)),
        'observed_end_at':end.close_at,'source_refs':sorted({*outcome.source_refs,*[b.source_ref for b in window]}),
        'unavailable_reason':None})


def refill_once(service, now=None):
    now=now or datetime.now(UTC)
    # Separate post-analysis table. Never selects these rows when freezing an input.
    with service.store.connection() as conn:
        rows=conn.execute('SELECT payload_json FROM outcomes').fetchall()
    changed=[]
    for row in rows:
        outcome=AnalysisOutcome.model_validate_json(row['payload_json'])
        if outcome.status is OutcomeStatus.MATURED or outcome.matures_at>now:continue
        analysis=service.store.get_analysis(outcome.analysis_id)
        if not analysis or not is_real_model_provider(analysis.audit.provider):continue
        snapshot=service.store.get_snapshot(analysis.snapshot.snapshot_id)
        provider=snapshot.bars[0].sources[0].provider
        if provider is DataProvider.FIXTURE_SYNTHETIC:continue
        cached=service.cache.load(provider,snapshot.bars[0].instrument.instrument_id,Interval.MIN_15)
        updated=evaluate(outcome,snapshot,cached.bars if cached else [],now)
        # Avoid writing unchanged AWAITING_DATA every minute.
        if (updated.status,updated.unavailable_reason)!=(outcome.status,outcome.unavailable_reason) or updated.status is OutcomeStatus.MATURED:
            changed.append(updated)
    service.store.save_outcomes(changed)
    from app.scenario_rules import evaluate as evaluate_scenario
    from app.storage import canonical_json
    with service.store.connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS scenario_outcomes (analysis_id TEXT NOT NULL, rule_id TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(analysis_id,rule_id))')
        analyses=conn.execute('SELECT analysis_id FROM analyses').fetchall()
        for row in analyses:
            analysis=service.store.get_analysis(row['analysis_id'])
            if not is_real_model_provider(analysis.audit.provider):continue
            snapshot=service.store.get_snapshot(analysis.snapshot.snapshot_id)
            for rule in snapshot.scenario_rules:
                cached=service.cache.load(snapshot.bars[0].sources[0].provider,rule.instrument_id,Interval.MIN_15)
                state=evaluate_scenario(rule,cached.bars if cached else [],now)
                conn.execute('INSERT OR REPLACE INTO scenario_outcomes VALUES (?,?,?)',
                    (analysis.analysis_id,rule.rule_id,canonical_json(state.model_dump(mode='json'))))
    if changed:audit_event('outcomes_refilled',updated=len(changed),rule=RULE,model_calls=0)
    return {'updated':len(changed),'matured':sum(x.status is OutcomeStatus.MATURED for x in changed),'model_calls':0,'scenario_hit_rate':None}
