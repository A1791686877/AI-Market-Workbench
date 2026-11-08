from datetime import timedelta
from app.fixtures import BARS
from app.models import SnapshotRequest, Interval, AiAnalysisTaskRequest, OutcomeStatus
from app.storage import Store
from app.service import MarketWorkbenchService
from app.outcome_worker import evaluate


def setup_case(tmp_path):
    svc=MarketWorkbenchService(Store(tmp_path/'outcome.db'))
    snap=svc.create_snapshot(SnapshotRequest(subject_id='crypto-btc',intervals=[Interval.MIN_15]))
    snap=snap.model_copy(update={'scenario_rules':[]})  # legacy unregistered analysis
    analysis=svc._generate_analysis(snap,AiAnalysisTaskRequest(snapshot_id=snap.snapshot_id))
    outcome=svc._pending_outcomes(analysis,snap)[0]
    template=snap.bars[0].bars[-1]
    bars=[template.model_copy(update={'open_at':snap.cutoff_at+timedelta(minutes=15*i),'close_at':snap.cutoff_at+timedelta(minutes=15*(i+1))}) for i in range(5)]
    return snap,outcome,bars


def test_outcomes_future_is_separate_and_missing_window_retries(tmp_path):
    snap,outcome,bars=setup_case(tmp_path);now=outcome.matures_at+timedelta(hours=1)
    assert evaluate(outcome,snap,bars,outcome.matures_at-timedelta(seconds=1)).status is OutcomeStatus.PENDING
    assert evaluate(outcome,snap,[],now).status is OutcomeStatus.AWAITING_DATA
    assert evaluate(outcome,snap,[bars[0],*bars[2:]],now).status is OutcomeStatus.AWAITING_DATA
    result=evaluate(outcome,snap,bars,now)
    assert result.status is OutcomeStatus.MATURED and result.return_fraction=='0'
    assert result.observed_end_at==outcome.matures_at
    assert result.scenario_scoring_status=='NOT_PRE_REGISTERED'
    assert outcome.end_price is None
