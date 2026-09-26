#!/usr/bin/env python3
"""Search small predefined overlays on first OOS, then freeze winner for second OOS.

No broad optimization: only spread and stronger-momentum overlays.
"""
from __future__ import annotations
import csv, datetime as dt, io, json, statistics, subprocess, zipfile
from collections import deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"results"/"exness-pf197-search"
CACHE=ROOT/"nautilus"/"cache"/"exness-ticks"
OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)

SYMBOL="XAUUSD_Zero_Spread"; YEAR=2026; MONTH=9
BASE="https://ticks.ex2archive.com/ticks"
M3=0.09899999999970532; M5=0.09174999999981992
SPREAD_CAP=.40; MIN_BETWEEN_MS=500; FOLLOW_MIN_MS=250; FOLLOW_MAX_MS=1000
TP=.50; SL=.70; MAX_HOLD_MS=120000
COMMS=(0.0,0.03)

class B:
    def __init__(self): self.q=deque(maxlen=60)
    def push(self,x): self.q.append(x)
    def last(self,n): return list(self.q)[-min(n,len(self.q)):]
    def mom(self,n):
        x=self.last(n); return x[-1]-x[0] if len(x)>=2 else 0
    def avg(self,n):
        x=self.last(n); return sum(x)/len(x) if x else 0
    def wm(self,n=10):
        x=self.last(n)
        if len(x)<2:return 0
        s=w=0
        for i in range(1,len(x)): s+=(x[i]-x[i-1])*i; w+=i
        return s/w if w else 0
    def cons(self):
        x=list(self.q)
        if len(x)<2:return 0
        d=0;c=0
        for i in range(len(x)-1,0,-1):
            z=x[i]-x[i-1]
            if z==0:continue
            nd=1 if z>0 else -1
            if d==0:d=nd;c=1
            elif nd==d:c+=1
            else:break
        return c*d

def sig(b):
    if len(b.q)<10:return 0
    m=b.mom(10)
    if abs(m)<.03:return 0
    d=1 if m>0 else -1
    c=b.cons()
    if abs(c)<3 or (1 if c>0 else -1)!=d:return 0
    w=b.wm()
    if abs(w)<.02 or (1 if w>0 else -1)!=d:return 0
    f=b.avg(5); s=b.avg(20)
    if f==0 or s==0 or (1 if f>s else -1)!=d:return 0
    return d

def parse_ts(s):
    x=dt.datetime.fromisoformat(s.strip().replace("Z","+00:00"))
    if x.tzinfo is None:x=x.replace(tzinfo=dt.timezone.utc)
    return x.astimezone(dt.timezone.utc)

def load(start,end):
    p=CACHE/f"Exness_{SYMBOL}_{YEAR}_{MONTH:02d}.zip"
    if not p.exists():
        u=f"{BASE}/{SYMBOL}/{YEAR}/{MONTH:02d}/Exness_{SYMBOL}_{YEAR}_{MONTH:02d}.zip"
        r=subprocess.run(["curl","-fL","--retry","3","-o",str(p),u],capture_output=True,text=True,timeout=180)
        if r.returncode: raise RuntimeError(r.stderr[-500:])
    out=[]
    with zipfile.ZipFile(p) as z:
        for n in [x for x in z.namelist() if x.lower().endswith(".csv")]:
            with z.open(n) as raw:
                rr=csv.DictReader(io.TextIOWrapper(raw,encoding="utf-8-sig",newline=""))
                for r in rr:
                    ts=r.get("Timestamp") or r.get("timestamp"); bid=r.get("Bid") or r.get("bid"); ask=r.get("Ask") or r.get("ask")
                    if not(ts and bid and ask):continue
                    try:x=parse_ts(ts); b=float(bid); a=float(ask)
                    except:continue
                    if x<start or x>=end or not(7<=x.hour<18):continue
                    if 500<b<10000 and b<=a and a-b<5:out.append((int(x.timestamp()*1000),a,b))
    out.sort();return out

def replay(ticks):
    b=B(); seed=None; pos=None; out=[]; last=-10**18; mids=deque(maxlen=120)
    for t,a,bd in ticks:
        mid=(a+bd)/2; b.push(bd); mids.append(mid)
        ret=0.0
        if len(mids)>=20:
            xs=list(mids)[-20:]; ret=sum(abs(xs[i]-xs[i-1]) for i in range(1,len(xs)))/19
        if pos:
            hold=t-pos["entry_t"]
            if pos["dir"]==1:
                px=bd; pnl=px-pos["entry"]; rsn="SL" if px<=pos["sl"] else ("TP" if px>=pos["tp"] else None)
            else:
                px=a; pnl=pos["entry"]-px; rsn="SL" if px>=pos["sl"] else ("TP" if px<=pos["tp"] else None)
            if rsn is None and hold>=MAX_HOLD_MS:rsn="TIME"
            if rsn:
                pos.update(pnl=pnl,reason=rsn);out.append(pos);pos=None
            continue
        s=sig(b)
        if seed is None and s:
            if a-bd>SPREAD_CAP or t-last<MIN_BETWEEN_MS:continue
            seed={"t":t,"d":s,"m":b.mom(10)};continue
        if seed is None:continue
        age=t-seed["t"]
        if age>10000:seed=None;continue
        if a-bd>SPREAD_CAP or not(FOLLOW_MIN_MS<=age<=FOLLOW_MAX_MS):continue
        d=seed["d"]; m=b.mom(10); c=b.cons()
        if (m*d)>0 and abs(m)>=max(abs(seed["m"]),.03) and abs(c)>=3 and (1 if c>0 else -1)==d:
            td=-d; e=a if td==1 else bd; x=dt.datetime.fromtimestamp(t/1000,dt.timezone.utc)
            pos={"entry_t":t,"dir":td,"entry":e,"sl":e-SL if td==1 else e+SL,"tp":e+TP if td==1 else e-TP,
                 "spread":a-bd,"hour":x.hour,"m3":abs(b.mom(3))/2,"m5":abs(b.mom(5))/4,"ret":ret}
            last=t;seed=None
    return out

def causal_vol(rows):
    hist=deque(maxlen=200); out=[]
    for r in rows:
        if len(hist)>=50:
            s=sorted(hist); q25=s[int((len(s)-1)*.25)]; q75=s[int((len(s)-1)*.75)]
            if q25<r["ret"]<=q75:out.append(r)
        hist.append(r["ret"])
    return out

def met(rows,c=0):
    pn=[r["pnl"]-c for r in rows];w=[x for x in pn if x>0];l=[x for x in pn if x<0]
    pf=sum(w)/abs(sum(l)) if l else (999 if w else 0)
    return {"N":len(rows),"Npd":len(rows)/5,"WR":len(w)/len(rows) if rows else 0,"PF":pf,"EV":sum(pn)/len(pn) if pn else 0}

def eval_period(start,end):
    tr=replay(load(start,end))
    main=[r for r in tr if r["m3"]>M3 and r["m5"]>M5]
    vol=causal_vol(main)
    base=[r for r in vol if r["hour"] in (13,14,15)]
    cands={
      "BASE":lambda r:True,
      "S05":lambda r:r["spread"]<=.05,
      "S04":lambda r:r["spread"]<=.04,
      "M5x1.10":lambda r:r["m5"]>M5*1.10,
      "M5x1.20":lambda r:r["m5"]>M5*1.20,
      "M3x1.10_M5x1.10":lambda r:r["m3"]>M3*1.10 and r["m5"]>M5*1.10,
      "M5x1.10_S05":lambda r:r["m5"]>M5*1.10 and r["spread"]<=.05,
      "M3x1.10_M5x1.10_S05":lambda r:r["m3"]>M3*1.10 and r["m5"]>M5*1.10 and r["spread"]<=.05,
    }
    return {k:{"0.00":met([r for r in base if fn(r)],0),"0.03":met([r for r in base if fn(r)],.03)} for k,fn in cands.items()}

res={
 "search_period":"2026-09-14..18",
 "validation_period":"2026-09-07..11",
 "search":eval_period(dt.datetime(2026,9,14,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,18,18,tzinfo=dt.timezone.utc)),
 "validation":eval_period(dt.datetime(2026,9,7,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,11,18,tzinfo=dt.timezone.utc))
}
(OUT/"summary.json").write_text(json.dumps(res,indent=2))
print(json.dumps(res,indent=2))
