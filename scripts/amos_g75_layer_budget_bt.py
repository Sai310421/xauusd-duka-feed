#!/usr/bin/env python3
"""AE supervisor layer-budget A/B on frozen Main + G75.
G75 frozen trigger/add rules are unchanged; supervisor only caps allowed add layers.
"""
from __future__ import annotations
import csv, datetime as dt, io, json, math, subprocess, zipfile
from collections import deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"results"/"amos-g75-layer-budget"
CACHE=ROOT/"nautilus"/"cache"/"exness-ticks"
OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)

SYMBOL="XAUUSD_Zero_Spread"; YEAR=2026; MONTH=9; BASE="https://ticks.ex2archive.com/ticks"
M3=.09899999999970532; M5=.09174999999981992
MAX_SPREAD=.40; MIN_BETWEEN=500; CONFIRM_MIN=250; CONFIRM_MAX=1000; SEED_EXP=10000
MOM10=.03; WMOM=.02; CONSEC=3
TP=.60; SL=.60; HOLD=120000; HOURS={13,14,15}
VOL_HIST=200; VOL_WARM=50
MFE_ARM=.20; MFE_GIVE=.12
GTRIG=.12; GADD=.025
COMMS=(0.0,.03)
CAPS=(0,1,2,3,5,10)  # number of ADD legs allowed, base leg excluded

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

def sig(b):
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

def arc():
    p=CACHE/f"Exness_{SYMBOL}_{YEAR}_{MONTH:02d}.zip"
    if not p.exists():
        u=f"{BASE}/{SYMBOL}/{YEAR}/{MONTH:02d}/Exness_{SYMBOL}_{YEAR}_{MONTH:02d}.zip"
        r=subprocess.run(["curl","-fL","--retry","3","-o",str(p),u],capture_output=True,text=True,timeout=180)
        if r.returncode:raise RuntimeError(r.stderr[-500:])
    return p

def load(start,end):
    out=[]
    with zipfile.ZipFile(arc()) as z:
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

def adaptive(hist,x):
    if len(hist)<VOL_WARM:return False
    s=sorted(hist); q25=s[int((len(s)-1)*.25)]; q75=s[int((len(s)-1)*.75)]
    return q25<x<=q75

def replay(ticks,add_cap):
    b=B(); mids=deque(maxlen=120); vh=deque(maxlen=VOL_HIST)
    seed=None; last_entry=-10**18; pos=[]; realized=[]
    start=0;mfe=0.0;anchor=last_add=0.0;armed=False;adds=0
    st={"entries":0,"adds":0,"add_blocked_cap":0,"mfe_exit":0,"time_exit":0,"base_tp":0,"base_sl":0}

    def snap(a,bd):
        d=pos[0]["d"]; px=bd if d>0 else a
        avg=sum(p["e"] for p in pos)/len(pos)
        fl=sum((px-p["e"]) if d>0 else (p["e"]-px) for p in pos)
        return d,px,avg,fl

    def close_all(t,a,bd,reason):
        nonlocal pos,start,mfe,anchor,last_add,armed,adds
        if not pos:return
        d,px,avg,fl=snap(a,bd)
        realized.append({"t":t,"pnl":fl,"legs":len(pos),"reason":reason})
        pos=[];start=0;mfe=0;anchor=last_add=0;armed=False;adds=0

    for t,a,bd in ticks:
        b.push(bd);mids.append((a+bd)/2)
        ret=0.0
        if len(mids)>=20:
            xs=list(mids)[-20:];ret=sum(abs(xs[i]-xs[i-1]) for i in range(1,len(xs)))/19

        if pos:
            # base server-side TP/SL
            keep=[]
            for p in pos:
                if not p["base"]:
                    keep.append(p);continue
                hit=None
                if p["d"]>0:
                    if bd<=p["sl"]:hit=("SL",bd)
                    elif bd>=p["tp"]:hit=("TP",bd)
                else:
                    if a>=p["sl"]:hit=("SL",a)
                    elif a<=p["tp"]:hit=("TP",a)
                if hit:
                    pnl=(hit[1]-p["e"]) if p["d"]>0 else (p["e"]-hit[1])
                    realized.append({"t":t,"pnl":pnl,"legs":1,"reason":"BASE_"+hit[0]})
                    st["base_"+hit[0].lower()]+=1
                else:keep.append(p)
            pos=keep
            if not pos:
                start=0;mfe=0;anchor=last_add=0;armed=False;adds=0
                continue

            d,px,avg,fl=snap(a,bd)
            pp=(px-avg) if d>0 else (avg-px)
            mfe=max(mfe,pp)
            fav=(px-anchor) if d>0 else (anchor-px)
            if not armed and fav>=GTRIG:
                armed=True;last_add=px
            if armed:
                since=(px-last_add) if d>0 else (last_add-px)
                if since>=GADD:
                    if adds<add_cap:
                        e=a if d>0 else bd
                        pos.append({"d":d,"e":e,"base":False,"sl":None,"tp":None})
                        adds+=1;last_add=e;st["adds"]+=1
                    else:
                        st["add_blocked_cap"]+=1
                        last_add=px  # consume rung even when blocked to avoid recount spam
            d,px,avg,fl=snap(a,bd);pp=(px-avg) if d>0 else (avg-px);mfe=max(mfe,pp)
            if mfe>=MFE_ARM and pp<=mfe-MFE_GIVE:
                close_all(t,a,bd,"MFE_TRAIL");st["mfe_exit"]+=1;continue
            if pos and t-start>=HOLD:
                close_all(t,a,bd,"TIME");st["time_exit"]+=1;continue
            continue

        bs=sig(b);spr=a-bd
        if seed is None and bs:
            if spr>MAX_SPREAD or t-last_entry<MIN_BETWEEN:continue
            seed={"t":t,"d":bs,"m":b.mom(10)};continue
        if seed is None:continue
        age=t-seed["t"]
        if age>SEED_EXP:seed=None;continue
        if spr>MAX_SPREAD or not(CONFIRM_MIN<=age<=CONFIRM_MAX):continue
        m=b.mom(10);c=b.cons();d=seed["d"]
        if not((m*d)>0 and abs(m)>=max(abs(seed["m"]),MOM10) and abs(c)>=CONSEC and (1 if c>0 else -1)==d):continue
        m3=abs(b.mom(3))/2;m5=abs(b.mom(5))/4
        if not(m3>M3 and m5>M5):seed=None;continue
        vp=adaptive(vh,ret);vh.append(ret)
        if not vp:seed=None;continue
        hr=dt.datetime.fromtimestamp(t/1000,dt.timezone.utc).hour
        if hr not in HOURS:seed=None;continue
        td=-d;e=a if td>0 else bd
        pos=[{"d":td,"e":e,"base":True,"sl":e-SL if td>0 else e+SL,"tp":e+TP if td>0 else e-TP}]
        start=t;mfe=0;anchor=last_add=e;armed=False;adds=0;last_entry=t;st["entries"]+=1;seed=None
    return realized,st

def met(rows,c):
    pn=[r["pnl"]-c*r["legs"] for r in rows]
    w=[x for x in pn if x>0];l=[x for x in pn if x<0]
    pf=sum(w)/abs(sum(l)) if l else (999 if w else 0)
    eq=peak=mdd=0;streak=mx=0
    for x in pn:
        eq+=x;peak=max(peak,eq);mdd=min(mdd,eq-peak)
        if x<0:streak+=1;mx=max(mx,streak)
        else:streak=0
    return {"N":len(rows),"WR":len(w)/len(rows) if rows else 0,"PF":pf,"EV":sum(pn)/len(pn) if pn else 0,
            "Net":sum(pn),"MaxDD":mdd,"max_loss_streak":mx,"closed_legs":sum(r["legs"] for r in rows)}

periods={
"OOS2":(dt.datetime(2026,9,7,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,11,18,tzinfo=dt.timezone.utc)),
"OOS1":(dt.datetime(2026,9,14,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,18,18,tzinfo=dt.timezone.utc)),
"DISC":(dt.datetime(2026,9,21,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,25,18,tzinfo=dt.timezone.utc))}
loaded={k:load(*v) for k,v in periods.items()}
res={"caps":{}}
for cap in CAPS:
    item={}
    for pn,ticks in loaded.items():
        tr,st=replay(ticks,cap)
        item[pn]={"stats":st,"metrics":{f"{c:.2f}":met(tr,c) for c in COMMS}}
    res["caps"][str(cap)]=item
(OUT/"summary.json").write_text(json.dumps(res,indent=2))
print(json.dumps(res,indent=2))
