#!/usr/bin/env python3
from __future__ import annotations
import csv,datetime as dt,io,json,subprocess,zipfile
from collections import deque
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"results"/"amos-jev-off-exit-ab"; CACHE=ROOT/"nautilus"/"cache"/"exness-ticks"
OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)
SYMBOL="XAUUSD_Zero_Spread"; BASE="https://ticks.ex2archive.com/ticks"; M3=.09899999999970532; M5=.09174999999981992
MAX_SPREAD=.40; MIN_BETWEEN=500; CONFIRM_MIN=250; CONFIRM_MAX=1000; SEED_EXP=10000
MOM10=.03; WMOM=.02; CONSEC=3; TP=.60; SL=.60; HOLD=120000; HOURS={13,14,15}; VOL_HIST=200; VOL_WARM=50
MFE_ARM=.20; MFE_GIVE=.12; COMMS=(0.0,.03); MODES=("MFE","OPP3","MFE_OPP3")
class B:
 def __init__(self): self.q=deque(maxlen=60)
 def push(self,x): self.q.append(x)
 def last(self,n): return list(self.q)[-min(n,len(self.q)):]
 def mom(self,n):
  x=self.last(n); return x[-1]-x[0] if len(x)>=2 else 0.
 def avg(self,n):
  x=self.last(n); return sum(x)/len(x) if x else 0.
 def wm(self,n=10):
  x=self.last(n)
  if len(x)<2:return 0.
  return sum((x[i]-x[i-1])*i for i in range(1,len(x)))/sum(range(1,len(x)))
 def cons(self):
  x=list(self.q); d=c=0
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
 d=1 if m>0 else -1; c=b.cons(); w=b.wm(10); f=b.avg(5); s=b.avg(20)
 if abs(c)<CONSEC or (1 if c>0 else -1)!=d:return 0
 if abs(w)<WMOM or (1 if w>0 else -1)!=d:return 0
 return d if f and s and (1 if f>s else -1)==d else 0
def parse(s):
 x=dt.datetime.fromisoformat(s.strip().replace("Z","+00:00"))
 if x.tzinfo is None:x=x.replace(tzinfo=dt.timezone.utc)
 return x.astimezone(dt.timezone.utc)
def arc():
 p=CACHE/"Exness_XAUUSD_Zero_Spread_2026_09.zip"
 if not p.exists():
  u=f"{BASE}/{SYMBOL}/2026/09/Exness_XAUUSD_Zero_Spread_2026_09.zip"
  r=subprocess.run(["curl","-fL","--retry","3","-o",str(p),u],capture_output=True,text=True,timeout=180)
  if r.returncode:raise RuntimeError(r.stderr[-500:])
 return p
def load(s,e):
 out=[]
 with zipfile.ZipFile(arc()) as z:
  for n in [x for x in z.namelist() if x.lower().endswith(".csv")]:
   with z.open(n) as raw:
    for r in csv.DictReader(io.TextIOWrapper(raw,encoding="utf-8-sig",newline="")):
     try:x=parse(r.get("Timestamp") or r.get("timestamp")); b=float(r.get("Bid") or r.get("bid")); a=float(r.get("Ask") or r.get("ask"))
     except:continue
     if s<=x<e and 7<=x.hour<18 and 500<b<10000 and b<=a and a-b<5:out.append((int(x.timestamp()*1000),a,b))
 out.sort();return out
def adaptive(h,x):
 if len(h)<VOL_WARM:return False
 s=sorted(h); return s[int((len(s)-1)*.25)]<x<=s[int((len(s)-1)*.75)]
def replay(ticks,mode):
 b=B(); mids=deque(maxlen=120); vh=deque(maxlen=VOL_HIST); seed=None; last_entry=-10**18; pos=None; rows=[]; mfe=0.; start=0; prev_px=None; adverse=0
 stats={"entries":0,"mfe_exit":0,"opp3_exit":0,"time_exit":0,"tp":0,"sl":0}
 for t,a,bd in ticks:
  b.push(bd); mids.append((a+bd)/2)
  ret=0.
  if len(mids)>=20:
   xs=list(mids)[-20:]; ret=sum(abs(xs[i]-xs[i-1]) for i in range(1,len(xs)))/19
  if pos:
   d=pos["d"]; px=bd if d>0 else a
   hit=None
   if d>0:
    if bd<=pos["sl"]:hit=("SL",bd)
    elif bd>=pos["tp"]:hit=("TP",bd)
   else:
    if a>=pos["sl"]:hit=("SL",a)
    elif a<=pos["tp"]:hit=("TP",a)
   if hit:
    pnl=(hit[1]-pos["e"]) if d>0 else (pos["e"]-hit[1]); rows.append({"pnl":pnl,"reason":hit[0]});stats[hit[0].lower()]+=1;pos=None;prev_px=None;adverse=0;continue
   pp=(px-pos["e"]) if d>0 else (pos["e"]-px); mfe=max(mfe,pp)
   if prev_px is not None:
    delta=(px-prev_px)*d
    if delta<0:adverse+=1
    elif delta>0:adverse=0
   prev_px=px
   do_mfe=mode in ("MFE","MFE_OPP3") and mfe>=MFE_ARM and pp<=mfe-MFE_GIVE
   do_opp=mode in ("OPP3","MFE_OPP3") and adverse>=3
   if do_mfe or do_opp:
    rows.append({"pnl":pp,"reason":"MFE" if do_mfe else "OPP3"});stats["mfe_exit" if do_mfe else "opp3_exit"]+=1;pos=None;prev_px=None;adverse=0;continue
   if t-start>=HOLD:
    rows.append({"pnl":pp,"reason":"TIME"});stats["time_exit"]+=1;pos=None;prev_px=None;adverse=0;continue
   continue
  bs=sig(b); spr=a-bd
  if seed is None and bs:
   if spr<=MAX_SPREAD and t-last_entry>=MIN_BETWEEN:seed={"t":t,"d":bs,"m":b.mom(10)}
   continue
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
  if dt.datetime.fromtimestamp(t/1000,dt.timezone.utc).hour not in HOURS:seed=None;continue
  td=-d;e=a if td>0 else bd;pos={"d":td,"e":e,"sl":e-SL if td>0 else e+SL,"tp":e+TP if td>0 else e-TP}
  start=t;mfe=0.;prev_px=e;adverse=0;last_entry=t;stats["entries"]+=1;seed=None
 return rows,stats
def met(rows,c):
 pn=[r["pnl"]-c for r in rows];w=[x for x in pn if x>0];l=[x for x in pn if x<0];pf=sum(w)/abs(sum(l)) if l else (999 if w else 0);eq=peak=mdd=0.;st=mx=0
 for x in pn:
  eq+=x;peak=max(peak,eq);mdd=min(mdd,eq-peak)
  if x<0:st+=1;mx=max(mx,st)
  else:st=0
 return {"N":len(pn),"WR":len(w)/len(pn) if pn else 0,"PF":pf,"EV":sum(pn)/len(pn) if pn else 0,"Net":sum(pn),"MaxDD":mdd,"max_loss_streak":mx}
periods={"OOS2":(dt.datetime(2026,9,7,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,11,18,tzinfo=dt.timezone.utc)),"OOS1":(dt.datetime(2026,9,14,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,18,18,tzinfo=dt.timezone.utc)),"DISC":(dt.datetime(2026,9,21,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,25,18,tzinfo=dt.timezone.utc))}
loaded={k:load(*v) for k,v in periods.items()};res={"variant":"JEV_OFF_G75_ADD0","modes":{}}
for mode in MODES:
 res["modes"][mode]={}
 for p,ticks in loaded.items():
  rows,st=replay(ticks,mode);res["modes"][mode][p]={"stats":st,"metrics":{f"{c:.2f}":met(rows,c) for c in COMMS}}
(OUT/"summary.json").write_text(json.dumps(res,indent=2));print(json.dumps(res,indent=2))
