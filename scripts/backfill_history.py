import argparse
from datetime import datetime
from app.history import backfill
from app.models import Interval
from app.service import service

if __name__=='__main__':
    p=argparse.ArgumentParser(description='Bounded resumable public crypto history; no model calls')
    p.add_argument('--instrument',required=True);p.add_argument('--interval',required=True,choices=['15m','1h','4h','1d'])
    p.add_argument('--start',required=True,help='Timezone-aware ISO timestamp')
    p.add_argument('--max-pages',type=int,default=5)
    a=p.parse_args();start=datetime.fromisoformat(a.start.replace('Z','+00:00'))
    if start.tzinfo is None:p.error('--start must include a timezone')
    print(backfill(service.get_instrument(a.instrument),Interval(a.interval),start,a.max_pages).model_dump_json(indent=2))
