#!/usr/bin/env python3
"""Profitability decomposition for Exness TickScalper.

Frozen trade generator:
- D_F_STRICT seed/confirmation
- Reverse execution
- early profit OFF
- max hold 120s
- TP 0.50 / SL 0.70
- no new entry logic in this script

Purpose: describe where PnL concentrates by hour, spread, and entry-time
3/5/10/20/30-tick momentum features. Exploratory only; no promotion from 5 days.
"""
from __future__ import annotations
import csv, datetime as dt, io, json, statistics, subprocess, zipfile
from collections import deque, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"results"/"exness-profitability-grid"
CACHE=ROOT/"nautilus"/"cache"/"exness-ticks"
OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)

YEAR=2026; MONTH=9
START=dt.datetime(2026,9,21,7,0,tzinfo=dt.timezone.utc)
END=dt.datetime(2026,9,25,18,0,tzinfo=dt.timezone.utc)
BASE="https://ticks.ex2archive.com/ticks"
SYMBOLS=["XAUUSD_Raw_Spread","XAUUSD_Zero_Spread"]

BUFFER_N=30; MOM_N=10; MOM_THR=0.03; CONSEC_MIN=3; WMOM_THR=0.02
FAST_MA_N=5; SLOW_MA_N=20; SPREAD_CAP=0.40
MIN_BETWEEN_MS=500; FOLLOW_MIN_MS=250; FOLLOW_MAX_MS=1000
TP=0.50; SL=0.70; MAX_HOLD_MS=120000
FEATURE_WINDOWS=(3,5,10,20,30)
COMMISSIONS=(0.0,0.01,0.03,0.05,0.07)

class TickBuf:
    def __init__(self,n): self.q=deque(maxlen=n)
    def push(self,x): self.q.append(x)
    def last(self,n): return list(self.q)[-min(n,len(self.q)):]
    def avg(self,n):
        x=self.last(n); return sum(x)/len(x) if x else 0.0
    def momentum(self,n):
        x=self.last(n); return x[-1]-x[0] if len(x)>=2 else 0.0
    def weighted_mom(self,n):
        x=self.last(n)
        if len(x)<2:return 0.0
        s=w=0.0
        for i in range(1,len(x)):
            wt=i; s+=(x[i]-x[i-1])*wt; w+=wt
        return s/w if w else 0.0
    def consecutive(self):
        x=list(self.q)
        if len(x)<2:return 0
        direction=0; cnt=0
        for i in range(len(x)-1,0,-1):
            diff=x[i]-x[i-1]
            if diff==0: continue
            d=1 if diff>0 else -1
            if direction==0: direction=d; cnt=1
            elif d==direction: cnt+=1
            else: break
        return cnt*direction

def base_signal(buf):
    if len(buf.q)<MOM_N:return 0
    mom=buf.momentum(MOM_N)
    if abs(mom)<MOM_THR:return 0
    sig=1 if mom>0 else -1
    c=buf.consecutive()
    if abs(c)<CONSEC_MIN or (1 if c>0 else -1)!=sig:return 0
    wm=buf.weighted_mom(MOM_N)
    if abs(wm)<WMOM_THR or (1 if wm>0 else -1)!=sig:return 0
    fast=buf.avg(FAST_MA_N); slow=buf.avg(SLOW_MA_N)
    if fast==0 or slow==0 or (1 if fast>slow else -1)!=sig:return 0
    return sig

def parse_ts(s):
    s=s.strip().replace("Z","+00:00")
    x=dt.datetime.fromisoformat(s)
    if x.tzinfo is None:x=x.replace(tzinfo=dt.timezone.utc)
    return x.astimezone(dt.timezone.utc)

def download(symbol):
    dest=CACHE/f"Exness_{symbol}_{YEAR}_{MONTH:02d}.zip"
    url=f"{BASE}/{symbol}/{YEAR}/{MONTH:02d}/Exness_{symbol}_{YEAR}_{MONTH:02d}.zip"
    if not dest.exists() or dest.stat().st_size<1000:
        r=subprocess.run(["curl","-fL","--retry","3","--connect-timeout","15","-o",str(dest),url],
                         capture_output=True,text=True,timeout=180)
        if r.returncode!=0: raise RuntimeError(r.stderr[-500:])
    return dest

def load_ticks(symbol):
    zp=download(symbol); out=[]
    with zipfile.ZipFile(zp) as z:
        for name in [n for n in z.namelist() if n.lower().endswith(".csv")]:
            with z.open(name) as raw:
                rr=csv.DictReader(io.TextIOWrapper(raw,encoding="utf-8-sig",newline=""))
                for r in rr:
                    ts=r.get("Timestamp") or r.get("timestamp")
                    bid=r.get("Bid") or r.get("bid"); ask=r.get("Ask") or r.get("ask")
                    if not(ts and bid and ask):continue
                    try:
                        x=parse_ts(ts)
                        if x<START or x>=END or not(7<=x.hour<18):continue
                        b=float(bid); a=float(ask)
                    except:continue
                    if 500<b<10000 and b<=a and a-b<5:
                        out.append((int(x.timestamp()*1000),a,b))
    out.sort()
    return out

def replay(ticks):
    buf=TickBuf(BUFFER_N); seed=None; pos=None; trades=[]; last_entry=-10**18
    for t,ask,bid in ticks:
        buf.push(bid)
        if pos is not None:
            hold=t-pos["entry_t"]
            if pos["dir"]==1:
                px=bid; pnl=px-pos["entry"]
                reason="SL" if px<=pos["sl"] else ("TP" if px>=pos["tp"] else None)
            else:
                px=ask; pnl=pos["entry"]-px
                reason="SL" if px>=pos["sl"] else ("TP" if px<=pos["tp"] else None)
            if reason is None and hold>=MAX_HOLD_MS: reason="TIME"
            if reason:
                row=dict(pos)
                row.update(exit_t=t,exit=px,pnl_no_comm=pnl,hold_ms=hold,reason=reason)
                trades.append(row); pos=None
            continue

        sig=base_signal(buf)
        if seed is None and sig:
            if ask-bid>SPREAD_CAP or t-last_entry<MIN_BETWEEN_MS:continue
            seed={"t":t,"dir":sig,"seed_mom":buf.momentum(10)}
            continue
        if seed is None:continue

        age=t-seed["t"]
        if age>10000: seed=None; continue
        if ask-bid>SPREAD_CAP:continue
        if not(FOLLOW_MIN_MS<=age<=FOLLOW_MAX_MS):continue
        d=seed["dir"]; m10=buf.momentum(10); c=buf.consecutive()
        if (m10*d)>0 and abs(m10)>=max(abs(seed["seed_mom"]),MOM_THR) and abs(c)>=3 and (1 if c>0 else -1)==d:
            trade_d=-d
            entry=ask if trade_d==1 else bid
            entry_dt=dt.datetime.fromtimestamp(t/1000,dt.timezone.utc)
            feats={}
            for n in FEATURE_WINDOWS:
                m=buf.momentum(n)
                feats[f"mom{n}"]=m
                feats[f"mom{n}_abs"]=abs(m)
                feats[f"mom{n}_aligned_seed"]=m*d
                feats[f"mom{n}_per_tick"]=abs(m)/max(n-1,1)
            pos={"entry_t":t,"dir":trade_d,"seed_dir":d,"entry":entry,
                 "sl":entry-SL if trade_d==1 else entry+SL,
                 "tp":entry+TP if trade_d==1 else entry-TP,
                 "spread_entry":ask-bid,
                 "hour_utc":entry_dt.hour,
                 "hour_jst":(entry_dt+dt.timedelta(hours=9)).hour,
                 **feats}
            last_entry=t; seed=None
    return trades

def pf_ev(rows,comm=0.0):
    pn=[r["pnl_no_comm"]-comm for r in rows]
    wins=[x for x in pn if x>0]; losses=[x for x in pn if x<0]
    pf=sum(wins)/abs(sum(losses)) if losses else (999.0 if wins else 0.0)
    return {"N":len(rows),"N_per_day":len(rows)/5.0,
            "WR":len(wins)/len(rows) if rows else 0.0,
            "PF":pf,"EV":sum(pn)/len(pn) if pn else 0.0,
            "Net":sum(pn)}

def spread_bucket(x):
    if x<=.03:return "<=0.03"
    if x<=.05:return "0.03-0.05"
    if x<=.07:return "0.05-0.07"
    if x<=.10:return "0.07-0.10"
    return ">0.10"

def quantile_edges(vals):
    s=sorted(vals)
    if not s:return []
    def q(p):return s[min(len(s)-1,int((len(s)-1)*p))]
    return [q(.25),q(.50),q(.75)]

def qbucket(x,e):
    if x<=e[0]:return "Q1"
    if x<=e[1]:return "Q2"
    if x<=e[2]:return "Q3"
    return "Q4"

summary={"period":"2026-09-21..25, 07:00-18:00 UTC","frozen":"Reverse + D_F_STRICT + 120s","symbols":{}}
all_bucket_rows=[]
for sym in SYMBOLS:
    ticks=load_ticks(sym); trades=replay(ticks)
    fields=["entry_t","exit_t","dir","seed_dir","entry","exit","pnl_no_comm","hold_ms","reason","spread_entry","hour_utc","hour_jst","sl","tp"]
    for n in FEATURE_WINDOWS:
        fields += [f"mom{n}",f"mom{n}_abs",f"mom{n}_aligned_seed",f"mom{n}_per_tick"]
    with (OUT/f"trades_{sym}.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(trades)

    z={"ticks":len(ticks),"trades":len(trades),"commission":{},"hour_utc":{},"spread":{},"momentum":{}}
    for comm in COMMISSIONS:z["commission"][f"{comm:.2f}"]=pf_ev(trades,comm)

    for h in range(7,18):
        rr=[r for r in trades if r["hour_utc"]==h]
        z["hour_utc"][str(h)]=pf_ev(rr,0.0)
        all_bucket_rows.append([sym,"hour",str(h),*pf_ev(rr,0.0).values()])

    for b in ("<=0.03","0.03-0.05","0.05-0.07","0.07-0.10",">0.10"):
        rr=[r for r in trades if spread_bucket(r["spread_entry"])==b]
        z["spread"][b]=pf_ev(rr,0.0)
        all_bucket_rows.append([sym,"spread",b,*pf_ev(rr,0.0).values()])

    for n in FEATURE_WINDOWS:
        key=f"mom{n}_per_tick"; edges=quantile_edges([r[key] for r in trades])
        nz={"edges":edges,"quartiles":{}}
        if edges:
            for b in ("Q1","Q2","Q3","Q4"):
                rr=[r for r in trades if qbucket(r[key],edges)==b]
                nz["quartiles"][b]=pf_ev(rr,0.0)
                all_bucket_rows.append([sym,f"mom{n}_per_tick",b,*pf_ev(rr,0.0).values()])
        z["momentum"][str(n)]=nz

    # exploratory combined filters: only dimensions defined before reading PnL.
    combos={}
    for hset_name,hset in [("EU_07_12",set(range(7,13))),("US_13_17",set(range(13,18)))]:
        for scap in (.03,.05,.07,.10):
            rr=[r for r in trades if r["hour_utc"] in hset and r["spread_entry"]<=scap]
            combos[f"{hset_name}_S{scap:.2f}"]=pf_ev(rr,0.0)
    z["predefined_combined_filters"]=combos
    summary["symbols"][sym]=z

(OUT/"summary.json").write_text(json.dumps(summary,indent=2))
with (OUT/"bucket_metrics.csv").open("w",newline="") as f:
    w=csv.writer(f);w.writerow(["symbol","dimension","bucket","N","N_per_day","WR","PF","EV","Net"]);w.writerows(all_bucket_rows)
print(json.dumps(summary,indent=2))
