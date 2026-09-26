#!/usr/bin/env python3
"""Second frozen OOS validation for Exness TickScalper main candidate."""
from __future__ import annotations
import csv, datetime as dt, io, json, statistics, subprocess, zipfile
from collections import deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"results"/"exness-second-oos"
CACHE=ROOT/"nautilus"/"cache"/"exness-ticks"
OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)

SYMBOL="XAUUSD_Zero_Spread"; YEAR=2026; MONTH=9
START=dt.datetime(2026,9,7,7,0,tzinfo=dt.timezone.utc)
END=dt.datetime(2026,9,11,18,0,tzinfo=dt.timezone.utc)
BASE="https://ticks.ex2archive.com/ticks"

M3_Q4=0.09899999999970532
M5_Q4=0.09174999999981992
# Frozen Vol Q2-Q3 thresholds from prior OOS (2026-09-14..18)
VOL_Q25=0.02947368421044143
VOL_Q75=0.05736842105266438

BUFFER_N=30; MOM_THR=.03; WMOM_THR=.02
SPREAD_CAP=.40; MIN_BETWEEN_MS=500; FOLLOW_MIN_MS=250; FOLLOW_MAX_MS=1000
TP=.50; SL=.70; MAX_HOLD_MS=120000
HOURS={13,14,15}
COMMS=(0.0,0.01,0.03,0.05)

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
        for i in range(1,len(x)):
            s+=(x[i]-x[i-1])*i; w+=i
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

def sig(buf):
    if len(buf.q)<10:return 0
    m=buf.mom(10)
    if abs(m)<MOM_THR:return 0
    d=1 if m>0 else -1
    c=buf.cons()
    if abs(c)<3 or (1 if c>0 else -1)!=d:return 0
    w=buf.wm()
    if abs(w)<WMOM_THR or (1 if w>0 else -1)!=d:return 0
    f=buf.avg(5); s=buf.avg(20)
    if f==0 or s==0 or (1 if f>s else -1)!=d:return 0
    return d

def parse_ts(s):
    x=dt.datetime.fromisoformat(s.strip().replace("Z","+00:00"))
    if x.tzinfo is None:x=x.replace(tzinfo=dt.timezone.utc)
    return x.astimezone(dt.timezone.utc)

def load():
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
                    if x<START or x>=END or not(7<=x.hour<18):continue
                    if 500<b<10000 and b<=a and a-b<5:out.append((int(x.timestamp()*1000),a,b))
    out.sort();return out

def replay(ticks):
    buf=B(); seed=None; pos=None; out=[]; last=-10**18; mids=deque(maxlen=120)
    for t,a,b in ticks:
        mid=(a+b)/2; buf.push(b); mids.append(mid)
        ret_abs=0.0
        if len(mids)>=20:
            xs=list(mids)[-20:]
            ret_abs=sum(abs(xs[i]-xs[i-1]) for i in range(1,len(xs)))/19
        if pos:
            hold=t-pos["entry_t"]
            if pos["dir"]==1:
                px=b; pnl=px-pos["entry"]; reason="SL" if px<=pos["sl"] else ("TP" if px>=pos["tp"] else None)
            else:
                px=a; pnl=pos["entry"]-px; reason="SL" if px>=pos["sl"] else ("TP" if px<=pos["tp"] else None)
            if reason is None and hold>=MAX_HOLD_MS:reason="TIME"
            if reason:
                pos.update(exit_t=t,exit=px,pnl=pnl,hold_ms=hold,reason=reason);out.append(pos);pos=None
            continue
        s=sig(buf)
        if seed is None and s:
            if a-b>SPREAD_CAP or t-last<MIN_BETWEEN_MS:continue
            seed={"t":t,"d":s,"m":buf.mom(10)};continue
        if seed is None:continue
        age=t-seed["t"]
        if age>10000:seed=None;continue
        if a-b>SPREAD_CAP or not(FOLLOW_MIN_MS<=age<=FOLLOW_MAX_MS):continue
        d=seed["d"]; m=buf.mom(10); c=buf.cons()
        if (m*d)>0 and abs(m)>=max(abs(seed["m"]),MOM_THR) and abs(c)>=3 and (1 if c>0 else -1)==d:
            m3=abs(buf.mom(3))/2; m5=abs(buf.mom(5))/4
            td=-d; e=a if td==1 else b; x=dt.datetime.fromtimestamp(t/1000,dt.timezone.utc)
            pos={"entry_t":t,"dir":td,"entry":e,"sl":e-SL if td==1 else e+SL,"tp":e+TP if td==1 else e-TP,
                 "spread":a-b,"hour":x.hour,"m3pt":m3,"m5pt":m5,"ret_abs20":ret_abs}
            last=t;seed=None
    return out

def met(rows,c):
    pn=[r["pnl"]-c for r in rows];w=[x for x in pn if x>0];l=[x for x in pn if x<0]
    pf=sum(w)/abs(sum(l)) if l else (999 if w else 0)
    eq=peak=mdd=0;st=mx=0
    for x in pn:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
        if x<0:st+=1;mx=max(mx,st)
        else:st=0
    return {"N":len(rows),"N_per_day":len(rows)/5,"WR":len(w)/len(rows) if rows else 0,"PF":pf,
            "EV":sum(pn)/len(pn) if pn else 0,"Net":sum(pn),"MaxDD":mdd,"max_loss_streak":mx}

ticks=load();tr=replay(ticks)
main=[r for r in tr if r["m3pt"]>M3_Q4 and r["m5pt"]>M5_Q4]
vol=[r for r in main if VOL_Q25<r["ret_abs20"]<=VOL_Q75]
final=[r for r in vol if r["hour"] in HOURS]

res={"period":"2026-09-07..11 second OOS",
     "frozen":{"m3":M3_Q4,"m5":M5_Q4,"vol_q25":VOL_Q25,"vol_q75":VOL_Q75,"hours":sorted(HOURS)},
     "counts":{"all":len(tr),"main":len(main),"vol":len(vol),"final":len(final)},
     "metrics":{f"{c:.2f}":met(final,c) for c in COMMS}}
(OUT/"summary.json").write_text(json.dumps(res,indent=2))
print(json.dumps(res,indent=2))
