import json
from app.service import service
from app.outcome_worker import refill_once
from app.config import settings

if __name__=='__main__':
    result=refill_once(service)
    (settings.root/'reports/outcomes-refill.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(result)
