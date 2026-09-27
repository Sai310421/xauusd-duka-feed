#!/usr/bin/env python3
from __future__ import annotations
import csv, datetime as dt, io, json, math, subprocess, zipfile
from collections import deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"results"/"amos-jev-ab-bt"
CACHE=ROOT/"nautilus"/"cache"/"exness-ticks"
OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)

SYMBOL="XAUUSD_Zero_Spread"; YEAR=2026; MONTH=9
BASE="https://ticks.ex2archive.com/ticks"
M3=.09899999999970532; M5=.09174999999981992
MAX_SPREAD=.40; MIN_BETWEEN=500; CONFIRM_MIN=250; CONFIRM_MAX=1000; SEED_EXP=10000
MOM10=.03; WMOM=.02; CONSEC=3
TP=.60; SL=.60; HOLD=120000; HOURS={13,14,15}
VOL_HIST=200; VOL_WARM=50
MFE_ARM=.20; MFE_GIVE=.12
# AE defaults
RHO=1.0; LAMBDA=1.0; K=.20; SIGMA_D=1.0; TAIL_B=20.0; PNR_MIN=.60
# G75 frozen
GTRIG=.12; GADD=.025; GMAX=10
COMMS=(0.0,.03)

class B:
    def __init__(self): self.q=deque(maxlen=60)
    def push(self,x): self.q.append(x)
    def last(self,n): return list(self.q)[-min(n,len(self.q)):]
    def mom(self,n):
        x=self.last(n); return x[-1]-x[0] if len(x)>=2 else 0.0
    def avg(self,n):
        x=self.last(n); return sum(x)/len(x) if x else 0.0
    def wm(self,n=10):
        x=self.last(n)
        if len(x)<2:return 0.0
        s=w=0.0
        for i in range(1,len(x)):
            s+=(x[i]-x[i-1])*i; w+=i
        return s/w if w else 0.0
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

def base_sig(b):
    if len(b.q)<20:return 0
    m=b.mom(10)
    if abs(m)<MOM10:return 0
    d=1 if m>0 else -1
    c=b.cons()
    if abs(c)<CONSEC or (1 if c>0 else -1)!=d:return 0
    w=b.wm(10)
    if abs(w)<WMOM or (1 if w>0 else -1)!=d:return 0
    f=b.avg(5); s=b.avg(20)
    if f==0 or s==0 or (1 if f>s else -1)!=d:return 0
    return d

def parse_ts(s):
    x=dt.datetime.fromisoformat(s.strip().replace("Z","+00:00"))
    if x.tzinfo is None:x=x.replace(tzinfo=dt.timezone.utc)
    return x.astimezone(dt.timezone.utc)

def archive():
    p=CACHE/f"Exness_{SYMBOL}_{YEAR}_{MONTH:02d}.zip"
    if not p.exists():
        u=f"{BASE}/{SYMBOL}/{YEAR}/{MONTH:02d}/Exness_{SYMBOL}_{YEAR}_{MONTH:02d}.zip"
        r=subprocess.run(["curl","-fL","--retry","3","-o",str(p),u],capture_output=True,text=True,timeout=180)
        if r.returncode: raise RuntimeError(r.stderr[-500:])
    return p

def load(start,end):
    out=[]
    with zipfile.ZipFile(archive()) as z:
        for n in [x for x in z.namelist() if x.lower().endswith(".csv")]:
            with z.open(n) as raw:
                rr=csv.DictReader(io.TextIOWrapper(raw,encoding="utf-8-sig",newline=""))
                for r in rr:
                    ts=r.get("Timestamp") or r.get("timestamp")
                    bid=r.get("Bid") or r.get("bid")
                    ask=r.get("Ask") or r.get("ask")
                    if not(ts and bid and ask): continue
                    try:x=parse_ts(ts); b=float(bid); a=float(ask)
                    except: continue
                    if x<start or x>=end or not(7<=x.hour<18): continue
                    if 500<b<10000 and b<=a and a-b<5:
                        out.append((int(x.timestamp()*1000),a,b))
    out.sort();return out

def dstar():
    kap=K*RHO*math.sqrt(2*RHO)/(2*LAMBDA*SIGMA_D)
    lo,hi=0.0,1.0
    while hi-math.tanh(hi)<kap: hi*=2
    for _ in range(80):
        m=(lo+hi)/2
        if m-math.tanh(m)<kap:lo=m
        else:hi=m
    return SIGMA_D/math.sqrt(2*RHO)*(lo+hi)/2
DSTAR=dstar()

def ae_allow(debt):
    if debt<DSTAR:return True,"ALLOW"
    pnr=max(0,min(1,1-debt/TAIL_B))
    if pnr>=PNR_MIN:return False,"WAIT"
    return False,"REDUCE"

def adaptive_pass(hist,x):
    if len(hist)<VOL_WARM:return False
    s=sorted(hist)
    q25=s[int((len(s)-1)*.25)]; q75=s[int((len(s)-1)*.75)]
    return q25<x<=q75

def replay(ticks, export_states=False):
    b=B(); mids=deque(maxlen=120); volhist=deque(maxlen=VOL_HIST)
    seed=None; last_entry=-10**18
    positions=[] # dict dir,entry,sl,tp,is_base,entry_t
    pos_start=0; pos_mfe=0.0
    g_anchor=0.0; g_last=0.0; g_peak=0.0; g_armed=False; g_layers=0
    realized=[]; states=[]
    stats={"signals":0,"main":0,"vol":0,"time":0,"entries":0,"adds":0,"ae_reject_add":0,
           "base_tp":0,"base_sl":0,"mfe_exit":0,"time_exit":0}

    def basket(t,a,bd):
        if not positions:return None
        d=positions[0]["dir"]
        vol=len(positions)
        avg=sum(p["entry"] for p in positions)/vol
        px=bd if d>0 else a
        fl=sum((px-p["entry"]) if d>0 else (p["entry"]-px) for p in positions)
        return d,vol,avg,fl

    def close_all(t,a,bd,reason):
        nonlocal positions,pos_start,pos_mfe,g_anchor,g_last,g_peak,g_armed,g_layers
        if not positions:return
        d=positions[0]["dir"]; px=bd if d>0 else a
        pnl=sum((px-p["entry"]) if d>0 else (p["entry"]-px) for p in positions)
        realized.append({"t":t,"pnl":pnl,"legs":len(positions),"reason":reason})
        positions=[]
        pos_start=0;pos_mfe=0.0;g_anchor=g_last=g_peak=0.0;g_armed=False;g_layers=0

    for t,a,bd in ticks:
        b.push(bd); mids.append((a+bd)/2)
        ret20=0.0
        if len(mids)>=20:
            xs=list(mids)[-20:]
            ret20=sum(abs(xs[i]-xs[i-1]) for i in range(1,len(xs)))/19

        # Broker-side base SL/TP execution before EA OnTick
        if positions:
            keep=[]
            for p in positions:
                if not p["is_base"]:
                    keep.append(p);continue
                hit=None
                if p["dir"]>0:
                    if bd<=p["sl"]: hit=("SL",bd)
                    elif bd>=p["tp"]: hit=("TP",bd)
                else:
                    if a>=p["sl"]: hit=("SL",a)
                    elif a<=p["tp"]: hit=("TP",a)
                if hit:
                    pnl=(hit[1]-p["entry"]) if p["dir"]>0 else (p["entry"]-hit[1])
                    realized.append({"t":t,"pnl":pnl,"legs":1,"reason":"BASE_"+hit[0]})
                    stats["base_"+hit[0].lower()]+=1
                else: keep.append(p)
            positions=keep
            if not positions:
                pos_start=0;pos_mfe=0.0;g_anchor=g_last=g_peak=0.0;g_armed=False;g_layers=0

        if positions:
            snap=basket(t,a,bd)
            d,vol,avg,fl=snap
            px=bd if d>0 else a
            pnl_price=(px-avg) if d>0 else (avg-px)
            pos_mfe=max(pos_mfe,pnl_price)
            favorable=(px-g_anchor) if d>0 else (g_anchor-px)
            g_peak=max(g_peak,favorable)
            if not g_armed and favorable>=GTRIG:
                g_armed=True;g_last=px
            if g_armed and g_layers<GMAX:
                since=(px-g_last) if d>0 else (g_last-px)
                if since>=GADD:
                    debt=max(0.0,-fl)
                    ok,_=ae_allow(debt)
                    if ok:
                        entry=a if d>0 else bd
                        positions.append({"dir":d,"entry":entry,"sl":None,"tp":None,"is_base":False,"entry_t":t})
                        g_layers+=1;g_last=entry;stats["adds"]+=1
                    else:stats["ae_reject_add"]+=1
            # recompute after possible add
            snap=basket(t,a,bd);d,vol,avg,fl=snap
            px=bd if d>0 else a
            pnl_price=(px-avg) if d>0 else (avg-px)
            pos_mfe=max(pos_mfe,pnl_price)
            if pos_mfe>=MFE_ARM and pnl_price<=pos_mfe-MFE_GIVE:
                close_all(t,a,bd,"MFE_TRAIL");stats["mfe_exit"]+=1;continue
            if positions and t-pos_start>=HOLD:
                close_all(t,a,bd,"TIME");stats["time_exit"]+=1;continue
            continue

        bs=base_sig(b)
        spread=a-bd
        if seed is None and bs:
            if spread>MAX_SPREAD or t-last_entry<MIN_BETWEEN:continue
            seed={"t":t,"d":bs,"m":b.mom(10)}
            continue
        if seed is None:continue
        age=t-seed["t"]
        if age>SEED_EXP:seed=None;continue
        if spread>MAX_SPREAD or age<CONFIRM_MIN or age>CONFIRM_MAX:continue
        m=b.mom(10);c=b.cons();d=seed["d"]
        if not ((m*d)>0 and abs(m)>=max(abs(seed["m"]),MOM10) and abs(c)>=CONSEC and (1 if c>0 else -1)==d):
            continue
        stats["signals"]+=1
        m3=abs(b.mom(3))/2;m5=abs(b.mom(5))/4
        if not(m3>M3 and m5>M5):seed=None;continue
        stats["main"]+=1
        vp=adaptive_pass(volhist,ret20);volhist.append(ret20)
        if not vp:seed=None;continue
        stats["vol"]+=1
        hr=dt.datetime.fromtimestamp(t/1000,dt.timezone.utc).hour
        if hr not in HOURS:seed=None;continue
        stats["time"]+=1
        td=-d
        if export_states:
            states.append({"time_msc":t,"trade_dir":"long" if td>0 else "short","mid":(a+bd)/2,
                           "spread":spread,"m3pt":m3,"m5pt":m5,"mom10":m,"ret_abs20":ret20,
                           "hour_utc":hr,"position":"flat","g75_layers":0,"ae_debt":0.0})
        entry=a if td>0 else bd
        positions=[{"dir":td,"entry":entry,
                    "sl":entry-SL if td>0 else entry+SL,
                    "tp":entry+TP if td>0 else entry-TP,
                    "is_base":True,"entry_t":t}]
        pos_start=t;pos_mfe=0.0
        g_anchor=entry;g_last=entry;g_peak=0.0;g_armed=False;g_layers=1
        last_entry=t;stats["entries"]+=1;seed=None

    return realized,stats,states

def metrics(rows,c):
    pn=[r["pnl"]-c*r["legs"] for r in rows]
    w=[x for x in pn if x>0];l=[x for x in pn if x<0]
    pf=sum(w)/abs(sum(l)) if l else (999 if w else 0)
    eq=peak=mdd=0;st=mx=0
    for x in pn:
        eq+=x;peak=max(peak,eq);mdd=min(mdd,eq-peak)
        if x<0:st+=1;mx=max(mx,st)
        else:st=0
    return {"N_exit_events":len(rows),"WR":len(w)/len(rows) if rows else 0,"PF":pf,
            "EV_per_exit":sum(pn)/len(pn) if pn else 0,"Net":sum(pn),"MaxDD":mdd,
            "max_loss_streak":mx,"closed_legs":sum(r["legs"] for r in rows)}

periods={
 "OOS2_2026-09-07_11":(dt.datetime(2026,9,7,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,11,18,tzinfo=dt.timezone.utc)),
 "OOS1_2026-09-14_18":(dt.datetime(2026,9,14,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,18,18,tzinfo=dt.timezone.utc)),
 "DISC_2026-09-21_25":(dt.datetime(2026,9,21,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,25,18,tzinfo=dt.timezone.utc)),
}
res={"variant":"JEV_OFF_v1.20 Python parity","DSTAR":DSTAR,"periods":{}}
allstates=[]
for name,(s,e) in periods.items():
    tr,st,states=replay(load(s,e),export_states=True)
    res["periods"][name]={"stats":st,"metrics":{f"{c:.2f}":metrics(tr,c) for c in COMMS}}
    for x in states:x["period"]=name
    allstates+=states
(OUT/"jev_off_summary.json").write_text(json.dumps(res,indent=2))
with (OUT/"jev_candidate_states.jsonl").open("w") as f:
    for x in allstates:f.write(json.dumps(x)+"\n")
print(json.dumps(res,indent=2))
