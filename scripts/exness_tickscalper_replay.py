#!/usr/bin/env python3
"""Exness XAUUSD TickScalper replay — D_F_STRICT + 20s exit.

Data source:
- Exness public tick history archive format (CSV ZIP).
- Direct archive mirror endpoint documented by terrylica/exness-data-preprocess:
  https://ticks.ex2archive.com/ticks/{SYMBOL}/{YYYY}/{MM}/Exness_{SYMBOL}_{YYYY}_{MM}.zip

Research-only. This script does not claim broker fill parity beyond historical Bid/Ask.
"""
from __future__ import annotations
import csv, datetime as dt, io, json, math, statistics, subprocess, zipfile
from collections import deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"results"/"exness-tickscalper"
CACHE=ROOT/"nautilus"/"cache"/"exness-ticks"
OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)

YEAR=2026; MONTH=9
START=dt.datetime(2026,9,21,7,0,tzinfo=dt.timezone.utc)
END=dt.datetime(2026,9,25,18,0,tzinfo=dt.timezone.utc)

VARIANTS=["XAUUSD_Raw_Spread","XAUUSD_Zero_Spread"]
BASE="https://ticks.ex2archive.com/ticks"

# Frozen D_F_STRICT core
BUFFER_N=30; MOM_N=10; MOM_THR=0.03; CONSEC_MIN=3; WMOM_THR=0.02
FAST_MA_N=5; SLOW_MA_N=20
SEED_SPREAD_CAP=0.40
MIN_BETWEEN_MS=500
FOLLOW_MIN_MS=250; FOLLOW_MAX_MS=1000
TP=0.50; SL=0.70
MAX_HOLD_MS=20000
EARLY_PROFIT_MS=100

# 0.01 lot XAUUSD = 1 XAU if lot size=100, so price-unit PnL equals USD PnL.
COMMISSION_RT_SCENARIOS=[0.0,0.001,0.01,0.03,0.05,0.07]

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

def download(symbol):
    dest=CACHE/f"Exness_{symbol}_{YEAR}_{MONTH:02d}.zip"
    url=f"{BASE}/{symbol}/{YEAR}/{MONTH:02d}/Exness_{symbol}_{YEAR}_{MONTH:02d}.zip"
    if not dest.exists() or dest.stat().st_size<1000:
        r=subprocess.run(["curl","-fL","--retry","3","--connect-timeout","15","-o",str(dest),url],
                         capture_output=True,text=True,timeout=180)
        if r.returncode!=0:
            raise RuntimeError(f"download failed {symbol}: {r.stderr[-500:]}")
    return dest,url

def parse_ts(s):
    s=s.strip().replace("Z","+00:00")
    x=dt.datetime.fromisoformat(s)
    if x.tzinfo is None:x=x.replace(tzinfo=dt.timezone.utc)
    return x.astimezone(dt.timezone.utc)

def load_ticks(symbol):
    zp,url=download(symbol)
    ticks=[]
    with zipfile.ZipFile(zp) as z:
        names=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names: raise RuntimeError(f"no csv in {zp}")
        for name in names:
            with z.open(name) as raw:
                txt=io.TextIOWrapper(raw,encoding="utf-8-sig",newline="")
                reader=csv.DictReader(txt)
                # normalize possible columns
                for r in reader:
                    ts=r.get("Timestamp") or r.get("timestamp")
                    bid=r.get("Bid") or r.get("bid")
                    ask=r.get("Ask") or r.get("ask")
                    if not (ts and bid and ask): continue
                    try:
                        t=parse_ts(ts)
                        if t<START or t>=END: continue
                        # only 07:00-17:59 UTC each day
                        if not (7<=t.hour<18): continue
                        b=float(bid); a=float(ask)
                    except Exception: continue
                    if not (500<b<10000 and b<=a and a-b<5): continue
                    ticks.append((int(t.timestamp()*1000),a,b))
    ticks.sort(key=lambda x:x[0])
    return ticks,str(zp),url

def normalize_ticks(ticks, mode):
    if mode=="ALL": return ticks
    out=[]; prev_bid=None; prev_ask=None
    for x in ticks:
        t,ask,bid=x
        keep = (mode=="QUOTE_CHANGE" and (ask!=prev_ask or bid!=prev_bid)) or (mode=="BID_CHANGE" and bid!=prev_bid)
        if keep: out.append(x)
        prev_bid=bid; prev_ask=ask
    return out

def tick_diagnostics(ticks):
    if not ticks: return {}
    same_quote=same_bid=0; dts=[]
    pb=pa=None; pt=None
    for t,a,b in ticks:
        if pb is not None:
            if b==pb: same_bid+=1
            if b==pb and a==pa: same_quote+=1
            dts.append(t-pt)
        pb=b; pa=a; pt=t
    dts.sort()
    q=lambda p: dts[min(len(dts)-1,int((len(dts)-1)*p))] if dts else 0
    n=max(len(ticks)-1,1)
    return {"same_bid_ratio":same_bid/n,"same_quote_ratio":same_quote/n,
            "dt_ms_p10":q(.10),"dt_ms_p50":q(.50),"dt_ms_p90":q(.90)}

def run(ticks, reverse=False, early_profit=True, max_hold_ms=MAX_HOLD_MS, entry_spread_cap=SEED_SPREAD_CAP):
    buf=TickBuf(BUFFER_N); signal=None; pos=None; trades=[]; last_entry=-10**18
    spread_rej=0
    for t,ask,bid in ticks:
        buf.push(bid)
        if pos:
            hold=t-pos["t"]
            if pos["dir"]==1:
                px=bid; pnl=px-pos["entry"]
                reason="SL" if px<=pos["sl"] else ("TP" if px>=pos["tp"] else None)
            else:
                px=ask; pnl=pos["entry"]-px
                reason="SL" if px>=pos["sl"] else ("TP" if px<=pos["tp"] else None)
            if early_profit and reason is None and hold>=EARLY_PROFIT_MS and pnl>0: reason="PROFIT"
            if reason is None and hold>=max_hold_ms: reason="TIME"
            if reason:
                mid=(ask+bid)/2
                gross=(mid-pos["entry_mid"]) if pos["dir"]==1 else (pos["entry_mid"]-mid)
                trades.append({"entry_t":pos["t"],"exit_t":t,"dir":pos["dir"],
                               "entry":pos["entry"],"exit":px,"pnl_no_comm":pnl,
                               "gross_mid":gross,"spread_drag":gross-pnl,
                               "hold_ms":hold,"reason":reason,"spread_entry":pos["spread"]})
                pos=None
            continue

        sig=base_signal(buf)
        if signal is None and sig:
            if ask-bid>entry_spread_cap:
                spread_rej+=1; continue
            if t-last_entry<MIN_BETWEEN_MS: continue
            signal={"t":t,"dir":sig,"seed_mom":buf.momentum(MOM_N)}
            continue
        if signal is None: continue
        age=t-signal["t"]
        if age>10000:
            signal=None; continue
        if ask-bid>entry_spread_cap:
            spread_rej+=1; continue
        if not (FOLLOW_MIN_MS<=age<=FOLLOW_MAX_MS): continue
        d=signal["dir"]; mom=buf.momentum(MOM_N); c=buf.consecutive()
        if (mom*d)>0 and abs(mom)>=max(abs(signal["seed_mom"]),MOM_THR) and abs(c)>=3 and (1 if c>0 else -1)==d:
            trade_d=(-d if reverse else d)
            entry=ask if trade_d==1 else bid
            pos={"t":t,"dir":trade_d,"entry":entry,"entry_mid":(ask+bid)/2,
                 "sl":entry-SL if trade_d==1 else entry+SL,
                 "tp":entry+TP if trade_d==1 else entry-TP,
                 "spread":ask-bid}
            last_entry=t; signal=None

    return trades,spread_rej

def metrics(trades,commission_rt):
    pn=[x["pnl_no_comm"]-commission_rt for x in trades]
    wins=[x for x in pn if x>0]; losses=[x for x in pn if x<0]
    pf=sum(wins)/abs(sum(losses)) if losses else (999 if wins else 0)
    eq=peak=mdd=0.0; streak=mxst=0
    for x in pn:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
        if x<0: streak+=1; mxst=max(mxst,streak)
        else: streak=0
    return {"N":len(pn),"N_per_day":len(pn)/5.0,
            "WR":len(wins)/len(pn) if pn else 0.0,
            "PF":pf,"EV_USD_per_0.01lot":sum(pn)/len(pn) if pn else 0.0,
            "Net_USD_per_0.01lot":sum(pn),"MaxDD_USD_per_0.01lot":mdd,
            "max_loss_streak":mxst}

summary={"period":"2026-09-21..2026-09-25 07:00-18:00 UTC",
         "strategy":"D_F_STRICT + 20s max hold + early positive exit",
         "variants":{}}
for symbol in VARIANTS:
    try:
        ticks,path,url=load_ticks(symbol)
        if not ticks: raise RuntimeError("no ticks in selected window")
        spreads=sorted(a-b for _,a,b in ticks)
        q=lambda p: spreads[min(len(spreads)-1,int((len(spreads)-1)*p))]
        z={"tick_count":len(ticks),"source_file":path,"source_url":url,
           "diagnostics":tick_diagnostics(ticks),
           "spread":{"p10":q(.10),"p50":q(.50),"p90":q(.90),"p99":q(.99),
                     "mean":sum(spreads)/len(spreads)},
           "modes":{}}
        fields=["entry_t","exit_t","dir","entry","exit","pnl_no_comm","gross_mid","spread_drag","hold_ms","reason","spread_entry"]
        for mode in ("ALL","QUOTE_CHANGE","BID_CHANGE"):
            nt=normalize_ticks(ticks,mode)
            mz={"tick_count":len(nt),"diagnostics":tick_diagnostics(nt)}
            for direction in ("FOLLOW","REVERSE"):
                trades,spread_rej=run(nt,reverse=(direction=="REVERSE"))
                dz={"spread_rejects":spread_rej,"commission_scenarios":{}}
                for comm in COMMISSION_RT_SCENARIOS:
                    dz["commission_scenarios"][f"{comm:.3f}"]=metrics(trades,comm)
                if trades:
                    gross=[x["gross_mid"] for x in trades]; raw=[x["pnl_no_comm"] for x in trades]
                    dz["gross_mid_EV"]=sum(gross)/len(gross)
                    dz["bidask_EV_before_commission"]=sum(raw)/len(raw)
                    dz["avg_spread_drag"]=sum(x["spread_drag"] for x in trades)/len(trades)
                    dz["break_even_commission_rt"]=sum(raw)/len(raw)
                else:
                    dz["gross_mid_EV"]=dz["bidask_EV_before_commission"]=dz["avg_spread_drag"]=dz["break_even_commission_rt"]=0
                mz[direction]=dz
                with (OUT/f"trades_{symbol}_{mode}_{direction}.csv").open("w",newline="") as f:
                    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(trades)
            z["modes"][mode]=mz

        # Exness-specific reverse exit/cost A/B after feed normalization proved irrelevant.
        rz={}
        reverse_configs=[
            # id, early_profit, max_hold_ms, spread_cap
            ("R_EP20",True,20000,0.40),
            ("R_H20",False,20000,0.40),
            ("R_H60",False,60000,0.40),
            ("R_H120",False,120000,0.40),
            ("R_H60_S05",False,60000,0.05),
            ("R_H60_S09",False,60000,0.09),
        ]
        for rid,ep,hms,scap in reverse_configs:
            trades,rej=run(ticks,reverse=True,early_profit=ep,max_hold_ms=hms,entry_spread_cap=scap)
            dz={"early_profit":ep,"max_hold_ms":hms,"entry_spread_cap":scap,
                "spread_rejects":rej,"commission_scenarios":{}}
            for comm in COMMISSION_RT_SCENARIOS:
                dz["commission_scenarios"][f"{comm:.3f}"]=metrics(trades,comm)
            if trades:
                gross=[x["gross_mid"] for x in trades]; raw=[x["pnl_no_comm"] for x in trades]
                dz["gross_mid_EV"]=sum(gross)/len(gross)
                dz["bidask_EV_before_commission"]=sum(raw)/len(raw)
                dz["avg_spread_drag"]=sum(x["spread_drag"] for x in trades)/len(trades)
            else:
                dz["gross_mid_EV"]=dz["bidask_EV_before_commission"]=dz["avg_spread_drag"]=0
            rz[rid]=dz
        z["reverse_exit_ab"]=rz
        summary["variants"][symbol]=z
    except Exception as e:
        summary["variants"][symbol]={"error":repr(e)}

(OUT/"summary.json").write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
