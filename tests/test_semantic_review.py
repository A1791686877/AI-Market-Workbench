from app.models import AiModelOutput, AiAnalysisResult, AnalysisAudit
from app.semantic_review import review_analysis
from pathlib import Path
import json
from types import SimpleNamespace
from app.models import FvgResult


class SavedFixture:
    def get_analysis(self, analysis_id):
        return AiAnalysisResult.model_validate(self.payload()['analysis'])
    def payload(self):
        return json.loads((Path(__file__).resolve().parents[1]/'examples/saved-semantic-regression.json').read_text(encoding='utf-8'))
    def get_snapshot(self, snapshot_id):
        p=self.payload()
        return SimpleNamespace(bars=[SimpleNamespace(bars=[SimpleNamespace(close=p['last_close'])])],
             indicator_bundles=[SimpleNamespace(fvg=[FvgResult.model_validate(f) for f in p['fvg']])])


service=SavedFixture()


def test_saved_browser_answer_with_upside_poc_as_downside_is_rejected():
    # The immutable production mistake supplied by the user, not a fabricated model call.
    result=service.get_analysis('analysis-1398be9a43b34a66a4321a35aef3845d')
    snapshot=service.get_snapshot(result.snapshot.snapshot_id)
    review=review_analysis(result,snapshot)
    assert review.status=='REJECTED'
    assert any(i.code=='PRICE_DIRECTION' and '79706.659375' in i.detail for i in review.issues)
    assert result.scenarios[-1].summary.endswith('非当前有效性确认。')


def test_same_saved_answer_with_direction_fixed_passes_checked_scope():
    result=service.get_analysis('analysis-1398be9a43b34a66a4321a35aef3845d')
    snapshot=service.get_snapshot(result.snapshot.snapshot_id)
    bearish=result.scenarios[-1].model_copy(update={'summary':'若跌破79691.4666666667且不能收复，79577.2666666667为下方参考；79706.659375估算POC在触发价上方，仅作反弹回测参考。'})
    review=review_analysis(result.model_copy(update={'scenarios':[*result.scenarios[:-1],bearish]}),snapshot)
    assert not any(i.code=='PRICE_DIRECTION' for i in review.issues)


def test_rounded_wrong_direction_and_wrong_current_role_are_rejected():
    result=service.get_analysis('analysis-1398be9a43b34a66a4321a35aef3845d')
    snapshot=service.get_snapshot(result.snapshot.snapshot_id)
    bearish=result.scenarios[-1].model_copy(update={'summary':'若跌破79691.47，79706.66是下方参考。'})
    review=review_analysis(result.model_copy(update={'scenarios':[*result.scenarios[:-1],bearish]}),snapshot)
    assert any(i.code=='PRICE_DIRECTION' for i in review.issues)
