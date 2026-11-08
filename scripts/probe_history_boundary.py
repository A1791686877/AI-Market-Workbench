from datetime import UTC,datetime
import json
from app.history import backfill
from app.service import service
from app.models import Interval
from app.config import settings

if __name__=='__main__':
    results=[]
    for asset in ('BTC','ETH'):
        for leg in ('SPOT','PERP'):
            result=backfill(service.get_instrument(f'CRYPTO:{asset}USDT:{leg}'),Interval.DAY_1,datetime(2000,1,1,tzinfo=UTC),1)
            results.append(result.model_dump(mode='json'));print(asset,leg,result.status,result.cursor_end,flush=True)
    (settings.root/'reports/history-boundary-probes.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
