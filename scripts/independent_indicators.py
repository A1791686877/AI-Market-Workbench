"""Independent Decimal(40 digits) reference; imports no production indicator code.

Pinned conventions: SMA seeded EMA; Wilder RSI; ATR includes first H-L;
ADX directional movement excludes first bar; BB population variance; OBV seed0;
HLC3 volume-weighted session VWAP. Not claimed to be TA-Lib's different seeds.
"""
from decimal import Decimal, localcontext
from datetime import UTC
from zoneinfo import ZoneInfo

D=Decimal
ABS_TOL=D('0.0000001')
REL_TOL=D('0.00000001')


def reference(bars):
    with localcontext() as ctx:
        ctx.prec=40
        n=len(bars); c=[D(b.close) for b in bars]; h=[D(b.high) for b in bars]; l=[D(b.low) for b in bars]; v=[D(b.volume) for b in bars]
        def average(values,k):
            return [None if i+1<k else sum(values[i+1-k:i+1])/k for i in range(n)]
        def smooth(values,k,alpha):
            result=[None]*len(values)
            start=next((i for i,x in enumerate(values) if x is not None),len(values))
            end=start+k
            if end>len(values) or any(x is None for x in values[start:end]):return result
            acc=sum(values[start:end])/k;result[end-1]=acc
            for i in range(end,len(values)):
                if values[i] is not None:acc+=(values[i]-acc)*alpha;result[i]=acc
            return result
        sm=average(c,20); em=smooth(c,20,D(2)/21)
        ef=smooth(c,12,D(2)/13);es=smooth(c,26,D(2)/27)
        mac=[None if a is None or b is None else a-b for a,b in zip(ef,es)]
        signal=smooth(mac,9,D(2)/10); hist=[None if b is None else a-b for a,b in zip(mac,signal)]
        delta=[None]+[b-a for a,b in zip(c,c[1:])]
        gain=smooth([None if x is None else max(x,D(0)) for x in delta],14,D(1)/14)
        loss=smooth([None if x is None else max(-x,D(0)) for x in delta],14,D(1)/14)
        rsi=[None if g is None else D(50) if g==s==0 else D(100) if s==0 else D(100)*g/(g+s) for g,s in zip(gain,loss)]
        tr=[h[0]-l[0]]+[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,n)]
        atr=smooth(tr,14,D(1)/14)
        plus=[None];minus=[None]
        for i in range(1,n):
            up=h[i]-h[i-1];down=l[i-1]-l[i]
            plus.append(up if up>down and up>0 else D(0));minus.append(down if down>up and down>0 else D(0))
        tw=smooth([None]+tr[1:],14,D(1)/14);pw=smooth(plus,14,D(1)/14);mw=smooth(minus,14,D(1)/14)
        pdi=[None if t is None or t==0 else D(100)*p/t for p,t in zip(pw,tw)]
        mdi=[None if t is None or t==0 else D(100)*m/t for m,t in zip(mw,tw)]
        dx=[None if p is None else D(0) if p+m==0 else D(100)*abs(p-m)/(p+m) for p,m in zip(pdi,mdi)]
        adx=smooth(dx,14,D(1)/14)
        upper=[None]*n;lower=[None]*n
        for i in range(19,n):
            std=(sum((x-sm[i])**2 for x in c[i-19:i+1])/20).sqrt();upper[i]=sm[i]+2*std;lower[i]=sm[i]-2*std
        obv=[D(0)]
        for i in range(1,n):obv.append(obv[-1]+(v[i] if c[i]>c[i-1] else -v[i] if c[i]<c[i-1] else D(0)))
        sums={};vwap=[]
        for i,b in enumerate(bars):
            key=b.open_at.astimezone(ZoneInfo('America/New_York') if b.session.value=='US_RTH' else UTC).date()
            pv,vol=sums.get(key,(D(0),D(0)));pv+=(h[i]+l[i]+c[i])*v[i]/3;vol+=v[i];sums[key]=(pv,vol)
            vwap.append(pv/vol if vol else None)
        vm=average(v,20);ratio=[None if mean is None or mean==0 else value/mean for value,mean in zip(v,vm)]
        change=[None]+[None if a==0 else b/a-1 for a,b in zip(c,c[1:])]
        return {'SMA':{'primary':sm},'EMA':{'primary':em},'RSI':{'primary':rsi},
            'MACD':{'primary':mac,'signal':signal,'histogram':hist},'ATR':{'primary':atr},
            'BOLLINGER_BANDS':{'primary':sm,'middle':sm,'upper':upper,'lower':lower},
            'ADX':{'primary':adx,'plus_di':pdi,'minus_di':mdi},'OBV':{'primary':obv},
            'VWAP':{'primary':vwap},'VOLUME_PRICE_RELATION':{'primary':ratio,'signal':change}}


def compare(bars, indicators):
    expected=reference(bars);checks=[]
    for indicator in indicators:
        components=[]
        for field,values in expected[indicator.name.value].items():
            errors=[];maximum=D(0);compared=0
            for i,(point,target) in enumerate(zip(indicator.series,values,strict=True)):
                actual=getattr(point.values,field)
                if (actual is None)!=(target is None):errors.append({'index':i,'reason':'WARMUP_OR_MISSING_MASK'});continue
                if target is None:continue
                diff=abs(D(actual)-target);maximum=max(maximum,diff);compared+=1
                if diff>ABS_TOL+REL_TOL*abs(target):errors.append({'index':i,'error':str(diff)})
            components.append({'field':field,'compared_points':compared,'maximum_absolute_error':str(maximum),
                               'failed_points':len(errors),'first_errors':errors[:3]})
        checks.append({'indicator':indicator.name.value,'parameters':indicator.parameters.model_dump(mode='json'),
                       'warmup_bars':indicator.warmup_bars,'absolute_tolerance':str(ABS_TOL),'relative_tolerance':str(REL_TOL),
                       'passed':all(c['failed_points']==0 for c in components),'components':components})
    return checks
