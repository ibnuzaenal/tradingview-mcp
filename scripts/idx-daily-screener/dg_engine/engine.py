"""Closed-bar Python research port of IDX_Analyzer_Dev_V2.4.pine.

NOT a Pine interpreter. Pivot ties assumed rightmost extreme; mintick=1
for historical adjusted prices (vendor's original instrument tick unavailable).
AI nearest-neighbor adaptation attribution: aibitcointrend, AI Forecast Pattern
Engine, CC BY-NC-SA 4.0, https://creativecommons.org/licenses/by-nc-sa/4.0/.
"""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MINTICK = 1.0

def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False, ignore_na=True).mean().to_numpy()

def rma(x, n):
    x=np.asarray(x,float); out=np.full(len(x),np.nan); buf=[]; prev=np.nan
    for i,v in enumerate(x):
        if np.isnan(v):
            out[i]=prev
            continue
        if np.isnan(prev):
            buf.append(v)
            if len(buf)==n: prev=float(np.mean(buf))
        else: prev=(prev*(n-1)+v)/n
        out[i]=prev
    return out

def lag(x,n=1):
    return pd.Series(x).shift(n).to_numpy()

def roll(x,n,how='mean'):
    return getattr(pd.Series(x).rolling(n),how)().to_numpy()

def rsi(c,n=14):
    d=c-lag(c); up=rma(np.maximum(d,0),n); dn=rma(np.maximum(-d,0),n)
    with np.errstate(divide='ignore',invalid='ignore'):
        return np.where(dn==0,100,np.where(up==0,0,100-100/(1+up/dn)))

def pivots(x,n):
    # Confirm at t; extreme belongs to t-n. Equal older extremes allowed.
    out=np.full(len(x),np.nan)
    for t in range(2*n,len(x)):
        j=t-n
        if x[j]>=np.max(x[j-n:j]) and x[j]>np.max(x[j+1:t+1]): out[t]=x[j]
    return out

def bars_since(x):
    last=None; out=np.full(len(x),np.nan)
    for i,v in enumerate(x):
        if v: last=i
        if last is not None: out[i]=i-last
    return out

def load(ticker, adjusted=False):
    d=pd.read_csv(ROOT/'idx_stocks_10y'/f'{ticker}.csv',parse_dates=['Date'])
    if adjusted:
        f=d['Adj Close']/d.Close
        for col in ['Open','High','Low','Close']: d[col]=d[col]*f
    return d

def base(d):
    o,h,l,c,v=[d[k].to_numpy(float) for k in ['Open','High','Low','Close','Volume']]
    a={k:z for k,z in zip(['o','h','l','c','v'],[o,h,l,c,v])}
    for n in [10,20,50]: a[f'e{n}']=ema(c,n)
    a['sma200']=roll(c,200); a['rsi']=rsi(c)
    tr=np.maximum(h-l,np.maximum(np.abs(h-lag(c)),np.abs(l-lag(c)))); tr[0]=h[0]-l[0]
    a['atr']=rma(tr,14); a['atr100']=rma(tr,100)
    up=h-lag(h); down=lag(l)-l
    plus=rma(np.where((up>down)&(up>0),up,0.),14)
    minus=rma(np.where((down>up)&(down>0),down,0.),14)
    with np.errstate(divide='ignore',invalid='ignore'):
        a['diPlus']=100*plus/a['atr']; a['diMinus']=100*minus/a['atr']
        a['adx']=rma(100*np.abs(plus-minus)/np.where(plus+minus==0,1,plus+minus),14)
    macd=ema(c,12)-ema(c,26); a['macdHist']=macd-ema(macd,9)
    a['vma']=roll(v,20); a['rvol']=np.divide(v,a['vma'],out=np.zeros(len(c)),where=a['vma']>0)
    a['avgValue']=roll(c*v,20)
    a['rng']=np.maximum(h-l,MINTICK); a['body']=np.abs(c-o)
    a['br']=a['body']/a['rng']; a['uw']=h-np.maximum(o,c); a['lw']=np.minimum(o,c)-l
    a['clv']=(c-l)/a['rng']
    periods=d.Date.dt.to_period('W-FRI')
    wc=d.groupby(periods,sort=True).Close.last(); we=ema(wc.to_numpy(),20); wr=rsi(wc.to_numpy())
    weekly=pd.DataFrame({'wc':wc.to_numpy(),'we':we,'wep':lag(we),'wr':wr},index=wc.index).shift(1)
    w=weekly.reindex(periods).reset_index(drop=True)
    a['wBull']=((w.wc>w.we)&(w.we>w.wep)&(w.wr>50)).to_numpy()
    a['wBear']=((w.wc<w.we)&(w.we<w.wep)&(w.wr<45)).to_numpy()
    a['wRegime']=a['wBull'].astype(float)-a['wBear'].astype(float)
    a['dates']=d.Date.to_numpy(); return a

def chart_patterns(a,max_keep=12):
    h,l,c,atr=[a[k] for k in ['h','l','c','atr']]; length=len(c)
    ph=pivots(h,3); pl=-pivots(-l,3)
    zz=[]; pats=[]; events=[]; dropped=[]
    bias=np.zeros(length,int); age=np.full(length,999999,int)
    bullconf=np.zeros(length,bool); bearconf=bullconf.copy()
    def neck(p,t): return p['neck']+p['slope']*(t-p['nx'])
    for t in range(length):
        tol=atr[t]*.5; buf=atr[t]*.15
        for val,di in [(ph[t],1),(pl[t],-1)]:
            if not np.isfinite(val): continue
            appended=False
            if not zz or zz[-1][2]!=di:
                zz.append((t-3,val,di)); appended=True
            elif (di==1 and val>zz[-1][1]) or (di==-1 and val<zz[-1][1]): zz[-1]=(t-3,val,di)
            zz=zz[-20:]
            if not appended: continue
            n=len(zz); candidates=[]
            if n>=5:
                ls,t1,hd,t2,rs=[z[1] for z in zz[-5:]]
                valid=(hd>ls+tol and hd>rs+tol and abs(ls-rs)<=2*tol and abs(t1-t2)<=.35*(hd-min(t1,t2))) if di==1 else (hd<ls-tol and hd<rs-tol and abs(ls-rs)<=2*tol and abs(t1-t2)<=.35*(max(t1,t2)-hd))
                candidates.append((5,valid,3 if di==1 else 4))
            if n>=3:
                x,y,z=[q[1] for q in zz[-3:]]
                valid=abs(x-z)<=tol and ((min(x,z)-y>1.5*tol) if di==1 else (y-max(x,z)>1.5*tol))
                candidates.append((3,valid,1 if di==1 else 2))
            for sz,valid,kind in candidates:
                if not valid: continue
                pts=zz[-sz:]; start=pts[0][0]; leg=max(3,int((pts[-1][0]-start)/(sz-1)))
                nx=pts[1][0]; nv=pts[1][1]
                sl=(pts[3][1]-nv)/(pts[3][0]-nx) if sz==5 else 0.
                before=zz[n-sz-1][0] if n>=sz+1 else start
                lim=max(1,min(2*leg,start-before)); cross=-1
                for k in range(1,lim+1):
                    b=start-k; lv=nv+sl*(b-nx)
                    if b>=0 and ((di==1 and l[b]<=lv) or (di==-1 and h[b]>=lv)):
                        cross=b; break
                sb=cross if cross>=0 else before; end=pts[-1][0]+leg
                if any(min(end,p['pts'][-1][0])-max(sb,p['pts'][0][0])>.15*min(end-sb,p['pts'][-1][0]-p['pts'][0][0]) for p in pats): continue
                points=[(z[0],z[1]) for z in pts]
                if cross>=0: points.insert(0,(cross,nv+sl*(cross-nx)))
                elif n>=sz+1: points.insert(0,(zz[n-sz-1][0],zz[n-sz-1][1]))
                ex=max(pts[0][1],pts[-1][1]) if di==1 else min(pts[0][1],pts[-1][1])
                apex=pts[2][1] if sz==5 else (pts[0][1]+pts[-1][1])/2
                pats.append(dict(kind=kind,dir=-di,neck=nv,slope=sl,nx=nx,apex=apex,
                    apexbar=pts[2][0] if sz==5 else pts[-1][0],invalid=pts[-1][1] if sz==5 else ex,
                    ext=apex if sz==5 else ex,born=t,conf=None,result=0,pts=points))
                break
        keep=[]
        for p in pats:
            gone=False
            if p['conf'] is None:
                invalid=(h[t]>p['ext']+tol) if p['dir']==-1 else (l[t]<p['ext']-tol)
                confirm=(c[t]<neck(p,t)-buf) if p['dir']==-1 else (c[t]>neck(p,t)+buf)
                if invalid: gone=True
                elif confirm:
                    p['conf']=t; height=(p['apex']-neck(p,p['apexbar'])) if p['dir']==-1 else (neck(p,p['apexbar'])-p['apex'])
                    p['target']=neck(p,t)+p['dir']*height
                    tb=t
                    for b in range(p['pts'][-1][0]+1,t+1):
                        if (p['dir']==-1 and l[b]<=neck(p,b)) or (p['dir']==1 and h[b]>=neck(p,b)): tb=b; break
                    p['pts'].append((tb,neck(p,tb)))
                    bullconf[t] |= p['dir']==1; bearconf[t] |= p['dir']==-1
                elif t-p['born']>30: gone=True
            elif p['result']==0:
                hit=(l[t]<=p['target']) if p['dir']==-1 else (h[t]>=p['target'])
                fail=(c[t]>=p['invalid']) if p['dir']==-1 else (c[t]<=p['invalid'])
                if hit: p['result']=1
                elif fail: p['result']=2
                elif t-p['conf']>60: p['result']=3
                if p['result']:
                    events.append({k:v for k,v in p.items() if k!='pts'}|{'end':t,'samebar_hit_fail':bool(hit and fail)})
            if not gone: keep.append(p)
        pats=keep
        while sum(p['conf'] is not None for p in pats)>max_keep:
            j=next(j for j,p in enumerate(pats) if p['conf'] is not None)
            p=pats.pop(j)
            if p['result']==0: dropped.append(p)
        active=[p for p in pats if p['conf'] is not None and p['result']==0]
        if active:
            p=max(active,key=lambda x:x['conf']); bias[t]=p['dir']; age[t]=t-p['conf']
    a.update(patBias=bias,patAge=age,bullConf=bullconf,bearConf=bearconf)
    return events,dropped,[p for p in pats if p['conf'] is not None and p['result']==0]

def elite(a,without_pattern=False):
    o,h,l,c,atr,rv,e20,e50=[a[k] for k in ['o','h','l','c','atr','rvol','e20','e50']]
    b,br,lw,uw,clv=[a[k] for k in ['body','br','lw','uw','clv']]
    ph=pivots(h,5); pl=-pivots(-l,5)
    levels={k:np.full(len(c),np.nan) for k in ['lastPH','prevPH','lastPL','prevPL']}
    lastph=prevph=lastpl=prevpl=np.nan
    for i in range(len(c)):
        if np.isfinite(ph[i]): prevph,lastph=lastph,ph[i]
        if np.isfinite(pl[i]): prevpl,lastpl=lastpl,pl[i]
        for k,v in zip(levels,[lastph,prevph,lastpl,prevpl]): levels[k][i]=v
    a.update(levels); r1,r2,s1,s2=[levels[k] for k in levels]
    hh=r1>r2; hl=s1>s2; lh=r1<r2; ll=s1<s2
    bosb=(c>r1)&(lag(c)<=r1); boss=(c<s1)&(lag(c)>=s1)
    stbull=(hh&hl)|(hl&(c>e20)); stbear=(lh&ll)|boss
    strong=(c>e20)&(e20>e50)&(e20>lag(e20))&(e50>lag(e50))&(c>a['sma200'])&(a['rsi']>50)&(a['adx']>20)
    early=(c>e20)&(e20>lag(e20))&(bosb|hl)
    bear=(c<e20)&(e20<e50)&~(e20>lag(e20))&(a['rsi']<45)
    sideways=(a['adx']<20)&(np.abs(e20-e50)<=atr*.5)
    nearS=np.abs(c-s1)<=atr*.75; nearR=np.abs(c-r1)<=atr*.75
    center=np.where(np.abs(s1-e20)<=atr*.5,(s1+e20)/2,np.where(np.isfinite(s1),s1,e20))
    inzone=(l<=center+atr*.25)&(h>=center-atr*.25)
    ext=np.divide(c-e20,atr,out=np.zeros(len(c)),where=atr>0); over=(ext>2)|((c>e20)&((c-e20)/e20*100>10))
    liquid=(a['avgValue']>=1e10)&(a['vma']>0)&(atr>0)
    bulleng=(c>o)&(lag(c)<lag(o))&(c>=lag(o))&(o<=lag(c))&(b>lag(b))
    beareng=(c<o)&(lag(c)>lag(o))&(c<=lag(o))&(o>=lag(c))&(b>lag(b))
    hammer=(lw>=b*2)&(uw<=b*.5)&(br<=.4)
    shooting=(uw>=b*2)&(lw<=b*.5)&(br<=.4)
    small=lag(b)<=lag(b,2)*.35
    morning=(lag(c,2)<lag(o,2))&(lag(b,2)>0)&small&(c>o)&(c>(lag(o,2)+lag(c,2))/2)
    evening=(lag(c,2)>lag(o,2))&(lag(b,2)>0)&small&(c<o)&(c<(lag(o,2)+lag(c,2))/2)
    piercing=(lag(c)<lag(o))&(c>o)&(o<lag(c))&(c>(lag(o)+lag(c))/2)&(c<lag(o))
    dark=(lag(c)>lag(o))&(c<o)&(o>lag(c))&(c<(lag(o)+lag(c))/2)&(c>lag(o))
    cb=2*morning.astype(int)+2*bulleng.astype(int)+piercing.astype(int)+hammer.astype(int)
    cs=2*evening.astype(int)+2*beareng.astype(int)+dark.astype(int)+shooting.astype(int)
    pb=np.zeros(len(c),int) if without_pattern else a['patBias']; pa=a['patAge']
    bearblock=(pb==-1)&(pa<=5); bullboost=(pb==1)&(pa<=10)
    I=lambda x:x.astype(int)
    acc=I(clv>=.55)+I(clv>=.7)+I(rv>=1.2)+I(rv>=1.5)+I(rv>=2)+I(lw>b)+I((c>o)&(br>=.55))+2*I(nearS|inzone)+I(stbull)+2*I(bosb)+cb+2*I(bullboost)-2*I(over)-I(nearR&~bosb)
    dist=I(clv<=.45)+I(clv<=.3)+I(rv>=1.2)+I(rv>=1.5)+I(rv>=2)+I(uw>b)+I((c<o)&(br>=.55))+2*I(nearR)+I(over)+I(lh)+2*I(boss)+cs+2*I((pb==-1)&(pa<=10))-I(nearS&~boss)
    acl=np.select([(acc>=8)&(rv>=2),(acc>=5)&(rv>=1.5),(acc>=3)&(rv>=1.1)],[3,2,1],0)
    dcl=np.select([(dist>=8)&(rv>=2),(dist>=5)&(rv>=1.5),(dist>=3)&(rv>=1.1)],[3,2,1],0)
    accluster=(roll(I(acl>0),5,'sum')>=2)&~boss
    dccluster=(roll(I(dcl>0),5,'sum')>=2)&~bosb
    bo=(c>r1+atr*.1)&(lag(c)<=r1)&(rv>=1.5)&(c>o)&(clv>=.6)&(uw<=np.maximum(b,MINTICK))
    bbo=bars_since(bo); rt=(bbo>=1)&(bbo<=5)&(l<=r1+atr*.25)&(c>r1)&(c>o)
    pull=(strong|early)&stbull&inzone&(c>o)&(clv>=.6)&(rv>=1)
    rev=nearS&hl&bosb&(rv>=1.5)
    bsb=bars_since(bosb); bulltrap=(bsb>=1)&(bsb<=3)&(c<r1)&((rv>=1.2)|(uw>b))
    upthrust=(h>r1)&(c<r1)&(uw>b)&(clv<=.35)&(rv>=1.5)
    beartrap=(l<s1)&(c>s1)&(lw>b)&(rv>=1.2)
    gaptrap=(o>r1)&(c<r1)&(clv<=.35)&(rv>=1.5)
    rev2=liquid&(nearS|(pb==1))&(cb>=2)&(rv>=1.5)&~bulltrap&~over
    stopbase=np.where(rt|bo,np.minimum(l,np.where(np.isfinite(r1),r1,l)),np.where(np.isfinite(s1),s1,l))
    stop=stopbase-.25*atr; risk=np.maximum(c-stop,MINTICK); stoppct=risk/c*100
    rr1=np.where(r1>c,(r1-c)/risk,np.nan); rr2=np.where(r2>c,(r2-c)/risk,np.nan)
    rr=np.fmin(rr1,rr2); rr=np.where(np.isnan(rr),99,rr); rrok=rr>=2; stopok=(stop<c)&(stoppct<=8)
    mom=I(a['rsi']>50)+I(a['rsi']>lag(a['rsi']))+I(a['macdHist']>lag(a['macdHist']))+I(a['diPlus']>a['diMinus'])
    sv=bo|rt|pull|rev
    hard=liquid&~a['wBear']&(strong|early)&(stbull|bosb)&sv&((acl>=2)|accluster|bo)&(mom>=2)&~over&~bulltrap&~upthrust&~gaptrap&rrok&stopok&~bearblock
    bs=I(a['wBull'])+np.where(strong,2,np.where(early,1,0))+2*I(stbull)+I(inzone|rt)+I(sv)+I(rv>=1.2)+I(mom>=2)+I(rrok)+I(cb>=2)
    core=hard&(bs>=8); rv2=~core&rev2&rrok&stopok&~upthrust&~gaptrap&~bearblock; buy=core|rv2
    # Panel's earlier clauses take priority over buySignal, reproducing contradiction.
    panel=buy&liquid&~(a['wBear']|bear)&~(bulltrap|upthrust|gaptrap)&~over&(stbull|rev2)&(sv|rev2)&rrok&stopok
    setup=np.select([rt,pull,bo,rev,rev2],['RT','PB','BO','RV','RV2'],'NONE')
    values=dict(bosBull=bosb,bosBear=boss,structureBull=stbull,structureBear=stbear,nearSupport=nearS,nearResistance=nearR,
        strong=strong,early=early,bear=bear,sideways=sideways,liquid=liquid,extension=ext,overextended=over,
        bullEngulf=bulleng,bearEngulf=beareng,hammer=hammer,shootingStar=shooting,morningStar=morning,eveningStar=evening,
        candleBullScore=cb,candleBearScore=cs,accScore=acc,distScore=dist,accClass=acl,distClass=dcl,
        breakout=bo,retest=rt,pullback=pull,reversal=rev,reversalConfluence=rev2,bullTrap=bulltrap,bearTrap=beartrap,
        upthrust=upthrust,gapTrap=gaptrap,autoStop=stop,risk=risk,rrAvailable=rr,stopPct=stoppct,
        momentumScore=mom,buyScore=bs,buyCore=core,buyReversal=rv2,buySignal=buy,panelBuy=panel,setup=setup,
        lots=np.floor(np.floor(750000/risk)/100),distCluster=dccluster)
    if not without_pattern: a.update(values)
    return values

def ai_forecast(a,H=20,N=20,D=40,K=3,spacing=3,smooth=3,norm='maturity'):
    c,o,h,l,an=[a[k] for k in ['c','o','h','l','atr100']]; n=len(c)
    raw=np.stack([c-o,h-l,np.minimum(c,o)-l,h-np.maximum(c,o)],axis=1)
    windows=np.full((n,N*4),np.nan)
    for t in range(N-1,n): windows[t]=raw[t-N+1:t+1].reshape(-1)
    pred=np.full(n,np.nan); conf=pred.copy(); dispersion=pred.copy(); effn=pred.copy()
    minimum_start=H+N+1
    for t in range(max(99,minimum_start),n):
        maturity=np.arange(max(minimum_start,t-D+1),t+1)
        endpoints=maturity-H
        denom=an[maturity] if norm=='maturity' else an[endpoints]
        ok=np.isfinite(denom)&(denom>0); maturity=maturity[ok]; endpoints=endpoints[ok]; denom=denom[ok]
        if not len(endpoints) or not np.isfinite(an[t]) or an[t]<=0: continue
        x=windows[t]/an[t]; hist=windows[endpoints]/denom[:,None]
        distances=np.sqrt(np.sum((hist-x)**2,axis=1)*.25)
        selected=[]
        for j in np.argsort(distances,kind='stable'):
            if all(abs(int(endpoints[j])-int(endpoints[k]))>=spacing for k in selected): selected.append(j)
            if len(selected)==K: break
        ids=np.array(selected); labels=np.clip(np.log(c[maturity[ids]]/c[endpoints[ids]]),np.log(.75),np.log(1.25))
        w=1/np.maximum(distances[ids],1e-6)**2; w=w/w.sum()
        mean=w@labels; sd=np.sqrt(w@((labels-mean)**2)); vote=w@(labels>0)
        pred[t]=mean; dispersion[t]=sd; effn[t]=1/(w@w)
        conf[t]=.5*max(vote,1-vote)+.3*np.exp(-distances[ids[0]]/np.sqrt(N))+.2*np.exp(-sd*8)
    # Early NaN dictionary handling differs from Pine sorting; evaluation starts after 250.
    return np.expm1(ema(pred,smooth)),conf,dispersion,effn

def trend_path(base,drift,atr,H=20,steps=None):
    value=np.asarray(base,float).copy()
    for step in range(1,(H if steps is None else steps)+1):
        value=np.maximum(MINTICK,value+drift*(1-.35*(step-1)/max(H-1,1))+np.sin(step*1.618)*atr*.1)
    return value

def forecast(a,H=20,ai_options=None):
    c,atr,e20,rv,rsi_,clv=[a[k] for k in ['c','atr','e20','rvol','rsi','clv']]
    x=np.arange(20)-9.5; slope=pd.Series(c).rolling(20).apply(lambda y:np.dot(y,x)/np.dot(x,x),raw=True).to_numpy()
    tn=np.divide(slope,atr,out=np.zeros(len(c)),where=atr>0)
    et=np.divide(e20-lag(e20,5),5*atr,out=np.zeros(len(c)),where=atr>0)
    tc=(np.clip(tn,-.35,.35)*.65+np.clip(et,-.35,.35)*.35+a['wRegime']*.08)*atr
    mc=np.clip((rsi_-50)/50,-.5,.5)*atr*.35
    bp=np.where((clv>=.65)&(rv>=1),1.,np.where((a['lw']>a['body'])&(clv>=.55),.5,0.))
    sp=np.where((clv<=.35)&(rv>=1),1.,np.where((a['uw']>a['body'])&(clv<=.45),.5,0.))
    flow=np.clip(bp-sp,-1,1); vc=flow*atr*np.clip(rv,.5,2.5)*.16; mr=(e20-c)/20
    comps=np.stack([tc,mc,vc,mr],axis=1); w=np.array([.45,.2,.2,.15]); cap=c*.25/H
    drift=np.clip(comps@w,-cap,cap); trbase=trend_path(c,drift,atr,H)
    air,ac,ad,effn=ai_forecast(a,H=H,**(ai_options or {})); ai=c*(1+air)
    aw=.35*np.clip(.25+.75*np.nan_to_num(ac),0,1); aw=np.where(np.isfinite(air),aw,0)
    tr=trbase*(1-aw)+np.where(np.isfinite(ai),ai,trbase)*aw
    cyc=sum(np.sin(i*1.618)*.1 for i in range(1,H+1))*atr
    ds=sum(1-.35*(i-1)/max(H-1,1) for i in range(1,H+1))
    dd=(tr-c-cyc)/ds
    t_ag=np.sign(slope)==np.sign(e20-a['e50']); w_ag=(np.sign(dd)==a['wRegime'])|(a['wRegime']==0)
    v_ag=(rv>=1)&(np.sign(flow)==np.sign(dd))
    sq=np.where(((c>a['sma200'])&(e20>a['e50']))|((c<a['sma200'])&(e20<a['e50'])),1.,.4)
    ai_ag=np.sign(air)==np.sign(drift)
    conf=np.clip(38+13*t_ag+10*w_ag+10*v_ag+8*sq+11*ai_ag*np.nan_to_num(ac,nan=.5)-np.clip(atr/c*100-5,0,10),35,90)/100
    out=dict(AI=air,TR=tr/c-1,TR_BASE=trbase/c-1,AI_CONF=ac,TR_CONF=conf,AI_DISP=ad,AI_EFFN=effn,
        ZERO=np.zeros(len(c)),MOM20=np.clip(c/lag(c,20)-1,-.25,.25),EMA_SIGN=np.sign(e20-a['e50'])*atr*np.sqrt(H)/c,
        ALWAYS_UP=np.full(len(c),.000001),TR_BAND=atr*(.45+.055*H)*(1.15-conf/2)/c,AI_BAND=atr*1.5/c,
        TR1_BUG=trend_path(c,dd,atr,min(10,H))/c-1,TR1_FIXED=trend_path(c,dd,atr,H,steps=min(10,H))/c-1,
        NEXT=(np.maximum(MINTICK,c+dd+np.sin(1.618)*atr*.1)/c-1),flow=flow,slope=slope,comps=comps)
    for j,k in enumerate(['TREND','MOMENTUM','VOLUME','MEANREV']):
        weights=w.copy(); weights[j]=0; weights/=weights.sum()
        out['DROP_'+k]=trend_path(c,np.clip(comps@weights,-cap,cap),atr,H)/c-1
    return out
