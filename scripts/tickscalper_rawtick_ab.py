#!/usr/bin/env python3
"""TickScalper Raw Bid/Ask replay A0/B1/B2/B3.

Source-backed derived research:
- HFT direction/timing: nexobanks-prep/XAUUSD-
- persistence / velocity safety: n30dyn4m1c/gold-pro-scalper
Upstream sources remain READ ONLY.

Downloads Dukascopy hourly XAUUSD tick .bi5 and replays executable Bid/Ask.
This is a research replay, not live-performance certification.
"""
from __future__ import annotations
import csv, datetime as dt, json, lzma, math, os, statistics, struct, subprocess, time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "tickscalper-rawtick-v1"
CACHE = ROOT / "nautilus" / "cache" / "rawtick-xauusd"
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

HOST = "https://datafeed.dukascopy.com/datafeed"
REC = struct.Struct(">IIIff")  # ms from hour, ask_raw, bid_raw, ask_vol, bid_vol
SCALE = 1000.0
POINT = 0.001
START = dt.date(2026, 9, 21)
END   = dt.date(2026, 9, 25)

# Upstream HFT defaults
BUFFER_N = 30
MOM_N = 10
MOM_THR = 0.03
CONSEC_MIN = 3
WMOM_THR = 0.02
FAST_MA_N = 5
SLOW_MA_N = 20
MAX_SPREAD = 40 * POINT
MIN_BETWEEN_MS = 500
MAX_HOLD_MS = 900
EARLY_PROFIT_MS = 100
TP = 50 * POINT
SL = 70 * POINT

def curl(url: str, dest: Path, retries: int = 5) -> int:
    last = 0
    for i in range(retries):
        r = subprocess.run(
            ["curl","-sS","-L","--http1.1","-m","30","-A","Mozilla/5.0",
             "-o",str(dest),"-w","%{http_code}",url],
            capture_output=True,text=True,timeout=40
        )
        try: last = int((r.stdout or "0").strip() or 0)
        except: last = 0
        if last == 200 and dest.exists() and dest.stat().st_size > 20:
            return 200
        if last in (204,404):
            if dest.exists(): dest.unlink()
            return last
        time.sleep(1.5*(i+1))
    return last

def fetch_hour(day: dt.date, hour: int):
    p = CACHE / f"{day:%Y%m%d}_{hour:02d}h_ticks.bi5"
    if not p.exists() or p.stat().st_size <= 20:
        url = f"{HOST}/XAUUSD/{day.year}/{day.month-1:02d}/{day.day:02d}/{hour:02d}h_ticks.bi5"
        code = curl(url,p)
        if code != 200:
            return []
    try:
        raw = lzma.decompress(p.read_bytes())
    except Exception:
        return []
    origin = dt.datetime(day.year,day.month,day.day,hour,tzinfo=dt.timezone.utc)
    out=[]
    for i in range(0,len(raw)-REC.size+1,REC.size):
        ms, ar, br, av, bv = REC.unpack_from(raw,i)
        ask=ar/SCALE; bid=br/SCALE
        if not (500 < bid < 10000 and bid <= ask and ask-bid < 5):
            continue
        t = int(origin.timestamp()*1000)+int(ms)
        out.append((t,ask,bid,float(av),float(bv)))
    return out

def load_ticks():
    all_ticks=[]
    d=START
    hourly={}
    while d<=END:
        if d.weekday()<5:
            n=0
            for h in range(24):
                x=fetch_hour(d,h)
                n += len(x); all_ticks.extend(x)
            hourly[str(d)] = n
        d += dt.timedelta(days=1)
    all_ticks.sort(key=lambda x:x[0])
    return all_ticks,hourly

def minute_atr_map(ticks):
    # Mid-price OHLC by UTC minute, ATR14; value assigned causally from previous completed minute.
    bars={}
    for t,a,b,_,_ in ticks:
        m=t//60000
        p=(a+b)/2
        z=bars.get(m)
        if z is None: bars[m]=[p,p,p,p]
        else:
            z[1]=max(z[1],p); z[2]=min(z[2],p); z[3]=p
    mins=sorted(bars)
    tr_hist=deque(maxlen=14)
    atr={}
    prev_close=None
    prev_atr=None
    for m in mins:
        o,h,l,c=bars[m]
        tr=(h-l) if prev_close is None else max(h-l,abs(h-prev_close),abs(l-prev_close))
        # current minute gets previous completed ATR only
        atr[m]=prev_atr
        tr_hist.append(tr)
        if len(tr_hist)==14:
            prev_atr=sum(tr_hist)/14
        prev_close=c
    return atr

class TickBuf:
    def __init__(self,n): self.q=deque(maxlen=n)
    def push(self,x): self.q.append(x)
    def last(self,n): return list(self.q)[-min(n,len(self.q)):]
    def avg(self,n):
        x=self.last(n); return sum(x)/len(x) if x else 0
    def momentum(self,n):
        x=self.last(n); return x[-1]-x[0] if len(x)>=2 else 0
    def weighted_mom(self,n):
        x=self.last(n)
        if len(x)<2:return 0
        s=w=0.0
        for i in range(1,len(x)):
            wt=i; s+=(x[i]-x[i-1])*wt; w+=wt
        return s/w if w else 0
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
    consec=buf.consecutive()
    if abs(consec)<CONSEC_MIN or (1 if consec>0 else -1)!=sig:return 0
    wm=buf.weighted_mom(MOM_N)
    if abs(wm)<WMOM_THR or (1 if wm>0 else -1)!=sig:return 0
    fast=buf.avg(FAST_MA_N); slow=buf.avg(SLOW_MA_N)
    if fast==0 or slow==0 or (1 if fast>slow else -1)!=sig:return 0
    return sig

def run_variant(ticks,atrmap,variant):
    buf=TickBuf(BUFFER_N)
    pos=None; trades=[]
    last_entry=-10**18
    cand_dir=0; cand_count=0; cand_start=0
    vel_block_until=0
    spread_rej=vel_rej=persist_rej=0
    for t,ask,bid,av,bv in ticks:
        buf.push(bid)
        # manage open position first
        if pos:
            hold=t-pos["t"]
            if pos["dir"]==1:
                exec_px=bid
                pnl=exec_px-pos["entry"]
                reason=None
                if exec_px<=pos["sl"]: reason="SL"
                elif exec_px>=pos["tp"]: reason="TP"
                elif hold>=EARLY_PROFIT_MS and pnl>0: reason="PROFIT"
                elif hold>=MAX_HOLD_MS: reason="TIME"
            else:
                exec_px=ask
                pnl=pos["entry"]-exec_px
                reason=None
                if exec_px>=pos["sl"]: reason="SL"
                elif exec_px<=pos["tp"]: reason="TP"
                elif hold>=EARLY_PROFIT_MS and pnl>0: reason="PROFIT"
                elif hold>=MAX_HOLD_MS: reason="TIME"
            if reason:
                trades.append({
                    "entry_t":pos["t"],"exit_t":t,"dir":pos["dir"],"entry":pos["entry"],
                    "exit":exec_px,"pnl":pnl,"hold_ms":hold,"reason":reason,
                    "spread_entry":pos["spread"]
                })
                pos=None
            continue

        sig=base_signal(buf)
        if sig==0:
            cand_dir=0; cand_count=0; cand_start=0
            continue

        spread=ask-bid
        if spread>MAX_SPREAD:
            spread_rej+=1; continue
        if t-last_entry<MIN_BETWEEN_MS: continue

        use_persist = variant in ("B1","B3")
        use_vel = variant in ("B2","B3")

        if use_persist:
            if sig!=cand_dir:
                cand_dir=sig; cand_count=1; cand_start=t
            else:
                cand_count+=1
            if not (cand_count>=3 or t-cand_start>=2000):
                persist_rej+=1; continue

        if use_vel:
            x=buf.last(10)
            # all timestamps aren't stored in price buffer, so derive velocity span from current
            # tick cadence using separate last-10 global times below is needed; approximate here
            # via 10 ticks always considered within <=10s only when feed is active.
            atr=atrmap.get(t//60000)
            if atr and len(x)>=10 and abs(x[-1]-x[0]) > 0.25*atr:
                vel_block_until=max(vel_block_until,t+3000)
            if t<vel_block_until:
                vel_rej+=1; continue

        if sig==1:
            entry=ask; sl=entry-SL; tp=entry+TP
        else:
            entry=bid; sl=entry+SL; tp=entry-TP
        pos={"t":t,"dir":sig,"entry":entry,"sl":sl,"tp":tp,"spread":spread}
        last_entry=t
        cand_dir=0; cand_count=0; cand_start=0

    # force close at final executable quote
    if pos and ticks:
        t,ask,bid,_,_=ticks[-1]
        px=bid if pos["dir"]==1 else ask
        pnl=(px-pos["entry"]) if pos["dir"]==1 else (pos["entry"]-px)
        trades.append({"entry_t":pos["t"],"exit_t":t,"dir":pos["dir"],"entry":pos["entry"],
                       "exit":px,"pnl":pnl,"hold_ms":t-pos["t"],"reason":"EOD",
                       "spread_entry":pos["spread"]})
    return trades,{"spread_rejects":spread_rej,"velocity_rejects":vel_rej,"persistence_rejects":persist_rej}

def metrics(trades,days):
    pn=[x["pnl"] for x in trades]
    wins=[x for x in pn if x>0]; losses=[x for x in pn if x<0]
    pf=sum(wins)/abs(sum(losses)) if losses else (999.0 if wins else 0.0)
    eq=0.0; peak=0.0; mdd=0.0; streak=mxst=0
    for x in pn:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
        if x<0: streak+=1; mxst=max(mxst,streak)
        else: streak=0
    holds=[x["hold_ms"] for x in trades]
    spreads=[x["spread_entry"] for x in trades]
    return {
        "N":len(trades),"N_per_day":len(trades)/max(days,1),
        "WR":len(wins)/len(trades) if trades else 0.0,
        "PF":pf,"EV_price_units":sum(pn)/len(pn) if pn else 0.0,
        "Net_price_units":sum(pn),"MaxDD_price_units":mdd,
        "max_loss_streak":mxst,
        "avg_hold_ms":sum(holds)/len(holds) if holds else 0.0,
        "median_hold_ms":statistics.median(holds) if holds else 0.0,
        "avg_entry_spread":sum(spreads)/len(spreads) if spreads else 0.0,
    }

ticks,hourly=load_ticks()
if not ticks:
    raise SystemExit("NO RAW TICKS DOWNLOADED")
atrmap=minute_atr_map(ticks)
active_days=sum(1 for n in hourly.values() if n>0)
summary={"period":{"start":str(START),"end":str(END),"active_days":active_days},
         "ticks":len(ticks),"hourly_counts":hourly,"scale":SCALE,"point":POINT,
         "raw_bid_ask":True,"variants":{}}
for v in ("A0","B1","B2","B3"):
    tr,rej=run_variant(ticks,atrmap,v)
    summary["variants"][v]={"metrics":metrics(tr,active_days),"rejects":rej}
    with (OUT/f"trades_{v}.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["entry_t","exit_t","dir","entry","exit","pnl","hold_ms","reason","spread_entry"])
        w.writeheader(); w.writerows(tr)

summary["warnings"]=[
 "Raw Dukascopy Bid/Ask tick replay, but not a broker-specific Exness fill model.",
 "No explicit extra commission/slippage added in v1; spread is paid through executable Ask/Bid fills.",
 "B2/B3 velocity gate v1 uses the last 10 price observations and causal previous-minute ATR; exact inter-tick span refinement is next.",
 "Upstream performance targets are not treated as verified results."
]
(OUT/"summary.json").write_text(json.dumps(summary,indent=2))
with (OUT/"summary.csv").open("w",newline="") as f:
    w=csv.writer(f); w.writerow(["variant","N","N_per_day","WR","PF","EV","Net","MaxDD","max_loss_streak","avg_hold_ms","avg_spread","spread_rej","persist_rej","velocity_rej"])
    for v,z in summary["variants"].items():
        m=z["metrics"]; r=z["rejects"]
        w.writerow([v,m["N"],m["N_per_day"],m["WR"],m["PF"],m["EV_price_units"],m["Net_price_units"],m["MaxDD_price_units"],m["max_loss_streak"],m["avg_hold_ms"],m["avg_entry_spread"],r["spread_rejects"],r["persistence_rejects"],r["velocity_rejects"]])
print(json.dumps(summary,indent=2))
