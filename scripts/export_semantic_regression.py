"""Extract saved public mistake once; never edits canonical model audit or calls a model."""
import json
from app.service import service
from app.config import settings
from app.semantic_review import review_analysis


def main():
    result=service.get_analysis('analysis-1398be9a43b34a66a4321a35aef3845d')
    snapshot=service.get_snapshot(result.snapshot.snapshot_id)
    # Small derived fixture: only data used by semantic checks, not a canonical snapshot.
    payload={'analysis':result.model_dump(mode='json'), 'last_close':snapshot.bars[0].bars[-1].close,
             'fvg':[f.model_dump(mode='json') for b in snapshot.indicator_bundles for f in b.fvg],
             'provenance':{'snapshot_id':snapshot.snapshot_id,'snapshot_sha256':snapshot.snapshot_sha256,
                           'raw_response_sha256':result.audit.raw_response_sha256,'derived_fixture':True}}
    path=settings.root/'examples/saved-semantic-regression.json'
    if not path.exists():path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    report=review_analysis(result,snapshot)
    (settings.root/'reports/saved-answer-semantic-review.json').write_text(report.model_dump_json(indent=2),encoding='utf-8')
    print(report.status,[(i.code,i.detail) for i in report.issues])

if __name__=='__main__':main()
