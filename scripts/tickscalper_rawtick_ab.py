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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "tickscalper-rawtick-v1"
CACHE = ROOT / "nautilus" / "cache" / "rawtick-xauusd"
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

HOST = "https://datafeed.dukascopy.com/datafeed"
REC = struct.Struct(">IIIff")  # ms from hour, ask_raw, bid_raw, ask_vol, bid_vol
SCALE = 1000.0
POINT = float(os.environ.get("HFT_POINT","0.01"))
START = dt.date.fromisoformat(os.environ.get("RAW_START","2026-09-21"))
END   = dt.date.fromisoformat(os.environ.get("RAW_END","2026-09-25"))
HOUR_START = int(os.environ.get("RAW_HOUR_START","0"))
HOUR_END = int(os.environ.get("RAW_HOUR_END","24"))

# Upstream HFT defaults
BUFFER_N = 30
MOM_N = 10
MOM_THR = 0.03
CONSEC_MIN = 3
WMOM_THR = 0.02
FAST_MA_N = 5
SLOW_MA_N = 20
MAX_SPREAD = float(os.environ.get("RAW_MAX_SPREAD","0.40"))
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
    jobs=[]
    d=START
    days=[]
    while d<=END:
        if d.weekday()<5:
            days.append(d)
            for h in range(HOUR_START,HOUR_END):
                jobs.append((d,h))
        d += dt.timedelta(days=1)
    hourly={str(d):0 for d in days}
    # Parallelize only transport/decode; replay ordering is restored below.
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(fetch_hour,d,h):(d,h) for d,h in jobs}
        for fut in as_completed(futs):
            d,h=futs[fut]
            try: x=fut.result()
            except Exception as e:
                print("fetch error",d,h,repr(e)); x=[]
            hourly[str(d)] += len(x)
            all_ticks.extend(x)
            print("hour",d,h,"ticks",len(x))
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

def run_variant(ticks,atrmap,variant,reverse=False,max_hold_ms=MAX_HOLD_MS):
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
                elif hold>=max_hold_ms: reason="TIME"
            else:
                exec_px=ask
                pnl=pos["entry"]-exec_px
                reason=None
                if exec_px>=pos["sl"]: reason="SL"
                elif exec_px<=pos["tp"]: reason="TP"
                elif hold>=EARLY_PROFIT_MS and pnl>0: reason="PROFIT"
                elif hold>=max_hold_ms: reason="TIME"
            if reason:
                trades.append({
                    "entry_t":pos["t"],"exit_t":t,"dir":pos["dir"],"entry":pos["entry"],
                    "exit":exec_px,"pnl":pnl,
                    "gross_pnl":(((ask+bid)/2-pos["entry_mid"]) if pos["dir"]==1 else (pos["entry_mid"]-(ask+bid)/2)),
                    "cost_drag":((((ask+bid)/2-pos["entry_mid"]) if pos["dir"]==1 else (pos["entry_mid"]-(ask+bid)/2))-pnl),
                    "hold_ms":hold,"reason":reason,
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

        trade_sig = -sig if reverse else sig
        if trade_sig==1:
            entry=ask; sl=entry-SL; tp=entry+TP
        else:
            entry=bid; sl=entry+SL; tp=entry-TP
        pos={"t":t,"dir":trade_sig,"entry":entry,"entry_mid":(ask+bid)/2,"sl":sl,"tp":tp,"spread":spread}
        last_entry=t
        cand_dir=0; cand_count=0; cand_start=0

    # force close at final executable quote
    if pos and ticks:
        t,ask,bid,_,_=ticks[-1]
        px=bid if pos["dir"]==1 else ask
        pnl=(px-pos["entry"]) if pos["dir"]==1 else (pos["entry"]-px)
        trades.append({"entry_t":pos["t"],"exit_t":t,"dir":pos["dir"],"entry":pos["entry"],
                       "exit":px,"pnl":pnl,
                       "gross_pnl":((((ask+bid)/2)-pos["entry_mid"]) if pos["dir"]==1 else (pos["entry_mid"]-((ask+bid)/2))),
                       "cost_drag":(((((ask+bid)/2)-pos["entry_mid"]) if pos["dir"]==1 else (pos["entry_mid"]-((ask+bid)/2)))-pnl),
                       "hold_ms":t-pos["t"],"reason":"EOD",
                       "spread_entry":pos["spread"]})
    return trades,{"spread_rejects":spread_rej,"velocity_rejects":vel_rej,"persistence_rejects":persist_rej}


def run_direction_state_machine(ticks, mode="HYBRID", follow_min_ms=250, follow_max_ms=1000, follow_ratio=1.0, follow_consec=3):
    """Derived timing hypothesis:
    seed momentum -> 0.25-1.0s follow if acceleration persists;
    1-3s dead zone; 3-10s reverse only after opposite micro-tick confirmation.
    """
    buf=TickBuf(BUFFER_N)
    pos=None; pending=None; trades=[]
    last_entry=-10**18
    spread_rej=follow_rej=reverse_rej=expired=0

    def open_pos(t,ask,bid,trade_dir,spread,phase):
        if trade_dir==1:
            entry=ask; sl=entry-SL; tp=entry+TP
        else:
            entry=bid; sl=entry+SL; tp=entry-TP
        max_hold = 2000 if phase=="FOLLOW" else 10000
        return {"t":t,"dir":trade_dir,"entry":entry,"entry_mid":(ask+bid)/2,"sl":sl,"tp":tp,
                "spread":spread,"phase":phase,"max_hold":max_hold}

    for t,ask,bid,av,bv in ticks:
        buf.push(bid)

        if pos:
            hold=t-pos["t"]
            if pos["dir"]==1:
                exec_px=bid; pnl=exec_px-pos["entry"]
                reason = "SL" if exec_px<=pos["sl"] else ("TP" if exec_px>=pos["tp"] else None)
            else:
                exec_px=ask; pnl=pos["entry"]-exec_px
                reason = "SL" if exec_px>=pos["sl"] else ("TP" if exec_px<=pos["tp"] else None)
            if reason is None and hold>=EARLY_PROFIT_MS and pnl>0:
                reason="PROFIT"
            if reason is None and hold>=pos["max_hold"]:
                reason="TIME"
            if reason:
                trades.append({
                    "entry_t":pos["t"],"exit_t":t,"dir":pos["dir"],"entry":pos["entry"],
                    "exit":exec_px,"pnl":pnl,
                    "gross_pnl":(((ask+bid)/2-pos["entry_mid"]) if pos["dir"]==1 else (pos["entry_mid"]-(ask+bid)/2)),
                    "cost_drag":((((ask+bid)/2-pos["entry_mid"]) if pos["dir"]==1 else (pos["entry_mid"]-(ask+bid)/2))-pnl),
                    "hold_ms":hold,"reason":reason,
                    "spread_entry":pos["spread"],"phase":pos["phase"]
                })
                pos=None
            continue

        spread=ask-bid
        sig=base_signal(buf)

        # Create a seed event only when flat and no active event.
        if pending is None and sig!=0:
            if spread>MAX_SPREAD:
                spread_rej+=1
                continue
            if t-last_entry<MIN_BETWEEN_MS:
                continue
            seed_mom=buf.momentum(MOM_N)
            pending={"t":t,"dir":sig,"seed_px":bid,"seed_mom":seed_mom}
            continue

        if pending is None:
            continue

        age=t-pending["t"]
        seed_dir=pending["dir"]
        if age>10000:
            expired+=1; pending=None
            continue

        if spread>MAX_SPREAD:
            spread_rej+=1
            continue

        mom10=buf.momentum(MOM_N)
        consec=buf.consecutive()
        xs=buf.last(4)
        mom3=(xs[-1]-xs[0]) if len(xs)>=4 else 0.0

        # FOLLOW phase: require time persistence plus same-direction momentum
        # that is at least as strong as the seed. This explicitly tests
        # whether momentum has a short useful life instead of entering at t=0.
        if follow_min_ms <= age <= follow_max_ms and mode in ("FOLLOW","HYBRID"):
            same_mom=(mom10*seed_dir)>0
            accel=abs(mom10)>=max(abs(pending["seed_mom"])*follow_ratio,MOM_THR)
            same_consec=(abs(consec)>=follow_consec and (1 if consec>0 else -1)==seed_dir)
            if same_mom and accel and same_consec:
                pos=open_pos(t,ask,bid,seed_dir,spread,"FOLLOW")
                last_entry=t; pending=None
                continue
            follow_rej+=1

        # 1-3 seconds is deliberately a no-trade dead zone.
        if age < 3000:
            continue

        # REVERSE phase: seed momentum must have actually turned.
        # Require a short opposite move and >=2 opposite consecutive ticks.
        if 3000 <= age <= 10000 and mode in ("REVERSE","HYBRID"):
            opp3=(mom3*seed_dir) < -0.01
            opp_consec=(abs(consec)>=2 and (1 if consec>0 else -1)==-seed_dir)
            retraced=((bid-pending["seed_px"])*seed_dir) < 0
            if opp3 and opp_consec and retraced:
                pos=open_pos(t,ask,bid,-seed_dir,spread,"REVERSE")
                last_entry=t; pending=None
                continue
            reverse_rej+=1

    if pos and ticks:
        t,ask,bid,_,_=ticks[-1]
        px=bid if pos["dir"]==1 else ask
        pnl=(px-pos["entry"]) if pos["dir"]==1 else (pos["entry"]-px)
        trades.append({"entry_t":pos["t"],"exit_t":t,"dir":pos["dir"],"entry":pos["entry"],
                       "exit":px,"pnl":pnl,
                       "gross_pnl":((((ask+bid)/2)-pos["entry_mid"]) if pos["dir"]==1 else (pos["entry_mid"]-((ask+bid)/2))),
                       "cost_drag":(((((ask+bid)/2)-pos["entry_mid"]) if pos["dir"]==1 else (pos["entry_mid"]-((ask+bid)/2)))-pnl),
                       "hold_ms":t-pos["t"],"reason":"EOD",
                       "spread_entry":pos["spread"],"phase":pos["phase"]})
    return trades,{"spread_rejects":spread_rej,"follow_rejects":follow_rej,
                   "reverse_rejects":reverse_rej,"expired_events":expired}


def run_strict_execution(ticks, mode="MARKET", pullback=0.0, entry_spread_cap=MAX_SPREAD, pending_ms=5000):
    """Freeze D_F_STRICT direction/timing and vary execution only.
    Signal confirmation is identical to D_F_STRICT:
    250-1000 ms, same-direction momentum >= seed, >=3 consecutive ticks.
    """
    buf=TickBuf(BUFFER_N)
    signal=None
    order=None
    pos=None
    trades=[]
    last_entry=-10**18
    signal_count=fill_count=expired_count=spread_rej=0

    def execute(t,ask,bid,direction):
        if direction==1:
            entry=ask; sl=entry-SL; tp=entry+TP
        else:
            entry=bid; sl=entry+SL; tp=entry-TP
        return {"t":t,"dir":direction,"entry":entry,"entry_mid":(ask+bid)/2,
                "sl":sl,"tp":tp,"spread":ask-bid,"max_hold":2000}

    for t,ask,bid,av,bv in ticks:
        buf.push(bid)

        if pos is not None:
            hold=t-pos["t"]
            if pos["dir"]==1:
                px=bid; pnl=px-pos["entry"]
                reason="SL" if px<=pos["sl"] else ("TP" if px>=pos["tp"] else None)
            else:
                px=ask; pnl=pos["entry"]-px
                reason="SL" if px>=pos["sl"] else ("TP" if px<=pos["tp"] else None)
            if reason is None and hold>=EARLY_PROFIT_MS and pnl>0:
                reason="PROFIT"
            if reason is None and hold>=pos["max_hold"]:
                reason="TIME"
            if reason:
                mid=(ask+bid)/2
                gross=(mid-pos["entry_mid"]) if pos["dir"]==1 else (pos["entry_mid"]-mid)
                trades.append({
                    "entry_t":pos["t"],"exit_t":t,"dir":pos["dir"],
                    "entry":pos["entry"],"exit":px,"pnl":pnl,
                    "gross_pnl":gross,"cost_drag":gross-pnl,
                    "hold_ms":hold,"reason":reason,"spread_entry":pos["spread"],
                    "exec_mode":mode
                })
                pos=None
            continue

        # Pending virtual/pullback order created only after strict confirmation.
        if order is not None:
            age=t-order["t"]
            if age>pending_ms:
                expired_count+=1
                order=None
            else:
                spread=ask-bid
                if spread>entry_spread_cap:
                    spread_rej+=1
                    continue
                if order["dir"]==1:
                    improved = ask <= order["ref_ask"] - pullback
                else:
                    improved = bid >= order["ref_bid"] + pullback
                if improved:
                    pos=execute(t,ask,bid,order["dir"])
                    fill_count+=1
                    last_entry=t
                    order=None
            continue

        sig=base_signal(buf)

        if signal is None and sig!=0:
            if t-last_entry<MIN_BETWEEN_MS:
                continue
            signal={"t":t,"dir":sig,"seed_mom":buf.momentum(MOM_N)}
            continue

        if signal is None:
            continue

        age=t-signal["t"]
        if age>10000:
            signal=None
            continue
        if age<250:
            continue
        # D_F_STRICT keeps an unfilled seed locked through 10s;
        # strict follow confirmation is only eligible through 1000ms.
        if age>1000:
            continue
        if (ask-bid)>MAX_SPREAD:
            spread_rej+=1
            continue

        direction=signal["dir"]
        mom10=buf.momentum(MOM_N)
        consec=buf.consecutive()
        same_mom=(mom10*direction)>0
        accel=abs(mom10)>=max(abs(signal["seed_mom"]),MOM_THR)
        same_consec=(abs(consec)>=3 and (1 if consec>0 else -1)==direction)
        if not (same_mom and accel and same_consec):
            continue

        # Strict signal confirmed. Only execution changes below.
        signal_count+=1
        spread=ask-bid
        if mode=="MARKET":
            if spread<=entry_spread_cap:
                pos=execute(t,ask,bid,direction)
                fill_count+=1
                last_entry=t
            else:
                spread_rej+=1
        else:
            order={"t":t,"dir":direction,"ref_ask":ask,"ref_bid":bid}
        signal=None

    if pos is not None and ticks:
        t,ask,bid,_,_=ticks[-1]
        px=bid if pos["dir"]==1 else ask
        pnl=(px-pos["entry"]) if pos["dir"]==1 else (pos["entry"]-px)
        mid=(ask+bid)/2
        gross=(mid-pos["entry_mid"]) if pos["dir"]==1 else (pos["entry_mid"]-mid)
        trades.append({"entry_t":pos["t"],"exit_t":t,"dir":pos["dir"],
                       "entry":pos["entry"],"exit":px,"pnl":pnl,
                       "gross_pnl":gross,"cost_drag":gross-pnl,
                       "hold_ms":t-pos["t"],"reason":"EOD",
                       "spread_entry":pos["spread"],"exec_mode":mode})
    return trades,{"signals":signal_count,"fills":fill_count,"expired":expired_count,
                   "spread_rejects":spread_rej}

def metrics(trades,days):
    pn=[x["pnl"] for x in trades]
    gross=[x.get("gross_pnl",x["pnl"]) for x in trades]
    wins=[x for x in pn if x>0]; losses=[x for x in pn if x<0]
    gw=[x for x in gross if x>0]; gl=[x for x in gross if x<0]
    pf=sum(wins)/abs(sum(losses)) if losses else (999.0 if wins else 0.0)
    gross_pf=sum(gw)/abs(sum(gl)) if gl else (999.0 if gw else 0.0)
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
        "PF":pf,"Gross_PF_mid":gross_pf,
        "EV_price_units":sum(pn)/len(pn) if pn else 0.0,
        "Gross_EV_mid":sum(gross)/len(gross) if gross else 0.0,
        "Avg_Cost_Drag":(sum(gross)-sum(pn))/len(pn) if pn else 0.0,
        "Net_price_units":sum(pn),"Gross_Net_mid":sum(gross),
        "MaxDD_price_units":mdd,
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
spreads_all=sorted(a-b for _,a,b,_,_ in ticks)
def qtile(x,q):
    if not x:return 0.0
    i=min(len(x)-1,max(0,int((len(x)-1)*q)))
    return x[i]
summary={"period":{"start":str(START),"end":str(END),"active_days":active_days},
         "ticks":len(ticks),"hourly_counts":hourly,"scale":SCALE,"point":POINT,
         "max_spread_abs":MAX_SPREAD,
         "spread_quantiles":{"p10":qtile(spreads_all,.10),"p25":qtile(spreads_all,.25),
                             "p50":qtile(spreads_all,.50),"p75":qtile(spreads_all,.75),
                             "p90":qtile(spreads_all,.90),"p95":qtile(spreads_all,.95),
                             "p99":qtile(spreads_all,.99)},
         "raw_bid_ask":True,"variants":{}}
configs=[
    ("A0",False,900),
    ("B1",False,900),
    ("B2",False,900),
    ("B3",False,900),
    ("R09",True,900),
    ("R2",True,2000),
    ("R5",True,5000),
    ("R10",True,10000),
]
for v,rev,hold_ms in configs:
    gate = v if v in ("A0","B1","B2","B3") else "A0"
    tr,rej=run_variant(ticks,atrmap,gate,reverse=rev,max_hold_ms=hold_ms)
    summary["variants"][v]={"reverse":rev,"max_hold_ms":hold_ms,
                            "metrics":metrics(tr,active_days),"rejects":rej}
    with (OUT/f"trades_{v}.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["entry_t","exit_t","dir","entry","exit","pnl","gross_pnl","cost_drag","hold_ms","reason","spread_entry"])
        w.writeheader(); w.writerows(tr)

# Direction State Machine A/B: small pre-specified timing grid.
dconfigs=[
    # id, mode, min_ms, max_ms, ratio, consec
    ("D_F_STRICT","FOLLOW",250,1000,1.00,3),
    ("D_F_MID","FOLLOW",150,1250,0.90,2),
    ("D_F_WIDE","FOLLOW",100,1500,0.75,2),
    ("D_H_MID","HYBRID",150,1250,0.90,2),
]
strict_baseline_trades=None
for v,mode,fmin,fmax,ratio,consec_n in dconfigs:
    tr,rej=run_direction_state_machine(
        ticks,mode=mode,follow_min_ms=fmin,follow_max_ms=fmax,
        follow_ratio=ratio,follow_consec=consec_n
    )
    if v=="D_F_STRICT":
        strict_baseline_trades=[dict(x) for x in tr]
    summary["variants"][v]={"direction_state_machine":mode,
                            "follow_min_ms":fmin,"follow_max_ms":fmax,
                            "follow_ratio":ratio,"follow_consec":consec_n,
                            "metrics":metrics(tr,active_days),"rejects":rej}
    fields=["entry_t","exit_t","dir","entry","exit","pnl","gross_pnl","cost_drag","hold_ms","reason","spread_entry","phase"]
    with (OUT/f"trades_{v}.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader(); w.writerows(tr)

# Execution-only A/B with D_F_STRICT frozen.
exec_configs=[
    # id, mode, pullback, spread_cap, pending_ms
    ("E_MKT","MARKET",0.00,0.40,0),
    ("E_S25","MARKET",0.00,0.25,0),
    ("E_P10","PULLBACK",0.10,0.40,5000),
    ("E_P20","PULLBACK",0.20,0.40,5000),
    ("E_P10S25","PULLBACK",0.10,0.25,5000),
]
for v,mode,pb,scap,pms in exec_configs:
    tr,rej=run_strict_execution(ticks,mode=mode,pullback=pb,
                                entry_spread_cap=scap,pending_ms=pms)
    summary["variants"][v]={"execution_mode":mode,"pullback":pb,
                            "entry_spread_cap":scap,"pending_ms":pms,
                            "metrics":metrics(tr,active_days),"rejects":rej}
    fields=["entry_t","exit_t","dir","entry","exit","pnl","gross_pnl","cost_drag",
            "hold_ms","reason","spread_entry","exec_mode"]
    with (OUT/f"trades_{v}.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader(); w.writerows(tr)

# Parity gate: E_MKT must reproduce the frozen D_F_STRICT signal path.
e_mkt_path=OUT/"trades_E_MKT.csv"
if strict_baseline_trades is None:
    raise RuntimeError("D_F_STRICT baseline missing")
with e_mkt_path.open() as f:
    e_mkt_rows=list(csv.DictReader(f))
parity_ok=(len(e_mkt_rows)==len(strict_baseline_trades))
if parity_ok:
    for a,b in zip(e_mkt_rows,strict_baseline_trades):
        if int(a["entry_t"])!=int(b["entry_t"]) or int(a["dir"])!=int(b["dir"]):
            parity_ok=False
            break
summary["execution_parity"]={"E_MKT_vs_D_F_STRICT":parity_ok,
                             "E_MKT_N":len(e_mkt_rows),
                             "D_F_STRICT_N":len(strict_baseline_trades)}
if not parity_ok:
    raise RuntimeError("Execution parity failed: E_MKT != D_F_STRICT")

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
        w.writerow([v,m["N"],m["N_per_day"],m["WR"],m["PF"],m["EV_price_units"],m["Net_price_units"],m["MaxDD_price_units"],m["max_loss_streak"],m["avg_hold_ms"],m["avg_entry_spread"],r.get("spread_rejects",0),r.get("persistence_rejects",0),r.get("velocity_rejects",0)])
print(json.dumps(summary,indent=2))
