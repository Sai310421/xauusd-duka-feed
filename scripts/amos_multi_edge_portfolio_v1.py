#!/usr/bin/env python3
from __future__ import annotations
import csv,datetime as dt,io,json,statistics,subprocess,zipfile
from collections import deque
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'results'/'amos-multi-edge-portfolio-v1'; CACHE=ROOT/'nautilus'/'cache'/'exness-ticks'
OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)
SYMBOL='XAUUSD_Zero_Spread'; BASE='https://ticks.ex2archive.com/ticks'; COMM=.03; INITIAL=1000.0
PERIODS={'OOS2':(dt.datetime(2026,9,7,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,11,18,tzinfo=dt.timezone.utc)),'OOS1':(dt.datetime(2026,9,14,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,18,18,tzinfo=dt.timezone.utc)),'DISC':(dt.datetime(2026,9,21,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,25,18,tzinfo=dt.timezone.utc))}
def parse(s):
 x=dt.datetime.fromisoformat(s.strip().replace('Z','+00:00')); return (x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)
def arc():
 p=CACHE/'Exness_XAUUSD_Zero_Spread_2026_09.zip'
 if not p.exists():
  u=f'{BASE}/{SYMBOL}/2026/09/Exness_XAUUSD_Zero_Spread_2026_09.zip'; subprocess.run(['curl','-fL','--retry','3','-o',str(p),u],check=True,timeout=180)
 return p
def load(s,e):
 out=[]
 with zipfile.ZipFile(arc()) as z:
  for n in [x for x in z.namelist() if x.lower().endswith('.csv')]:
   with z.open(n) as raw:
    for r in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline='')):
     try:x=parse(r.get('Timestamp') or r.get('timestamp')); b=float(r.get('Bid') or r.get('bid')); a=float(r.get('Ask') or r.get('ask'))
     except:continue
     if s<=x<e and 7<=x.hour<18 and 500<b<10000 and b<=a and a-b<5: out.append((int(x.timestamp()*1000),a,b))
 out.sort(); return out
def bars(ticks,mins):
 d={}; step=mins*60000
 for t,a,b in ticks:
  k=t//step*step; m=(a+b)/2
  if k not in d:d[k]=[k,m,m,m,m,a-b]
  else:
   x=d[k]; x[2]=max(x[2],m);x[3]=min(x[3],m);x[4]=m;x[5]=(x[5]+(a-b))/2
 return [d[k] for k in sorted(d)]
def ema(xs,n):
 a=2/(n+1); out=[]; v=None
 for x in xs:v=x if v is None else a*x+(1-a)*v;out.append(v)
 return out
def atr(bs,n=14):
 tr=[]
 for i,b in enumerate(bs):tr.append(b[2]-b[3] if i==0 else max(b[2]-b[3],abs(b[2]-bs[i-1][4]),abs(b[3]-bs[i-1][4])))
 return ema(tr,n)
def metric(rows,comm=COMM):
 pn=[r['pnl']-comm for r in rows];w=[x for x in pn if x>0];l=[x for x in pn if x<0];eq=peak=dd=0.;st=mx=0
 for x in pn:eq+=x;peak=max(peak,eq);dd=min(dd,eq-peak);st=st+1 if x<0 else 0;mx=max(mx,st)
 return {'N':len(pn),'WR':len(w)/len(pn) if pn else 0,'PF':sum(w)/abs(sum(l)) if l else (999 if w else 0),'EV':sum(pn)/len(pn) if pn else 0,'Net':sum(pn),'MaxDD':dd,'DD_pct_1000':-dd/INITIAL*100,'max_loss_streak':mx}
def exit_bar(bs,i,d,e,tp,sl,maxbars):
 end=min(len(bs),i+1+maxbars)
 for j in range(i+1,end):
  h,l=bs[j][2],bs[j][3]
  if d>0:
   if l<=sl:return j,sl-e,'SL'
   if h>=tp:return j,tp-e,'TP'
  else:
   if h>=sl:return j,e-sl,'SL'
   if l<=tp:return j,e-tp,'TP'
 j=end-1;return j,(bs[j][4]-e)*d,'TIME'
def edge_trend(ticks,tf=5):
 bs=bars(ticks,tf);c=[x[4] for x in bs];e20=ema(c,20);e60=ema(c,60);at=atr(bs);rows=[];busy=-1
 for i in range(61,len(bs)-1):
  if i<=busy:continue
  slope=e20[i]-e20[i-3];d=1 if e20[i]>e60[i] and slope>0 else (-1 if e20[i]<e60[i] and slope<0 else 0)
  if not d:continue
  if d>0 and not(bs[i-1][3]<=e20[i-1] and c[i]>e20[i] and c[i]>c[i-1]):continue
  if d<0 and not(bs[i-1][2]>=e20[i-1] and c[i]<e20[i] and c[i]<c[i-1]):continue
  e=c[i];sl=e-d*1.1*at[i];tp=e+d*2.0*at[i];j,p,r=exit_bar(bs,i,d,e,tp,sl,12);busy=j;rows.append({'edge':f'TREND_M{tf}','entry_t':bs[i][0],'exit_t':bs[j][0],'pnl':p,'reason':r})
 return rows
def edge_range(ticks,tf=5):
 bs=bars(ticks,tf);c=[x[4] for x in bs];e20=ema(c,20);e60=ema(c,60);at=atr(bs);rows=[];busy=-1
 for i in range(60,len(bs)-1):
  if i<=busy or at[i]<=0 or abs(e20[i]-e60[i])/at[i]>.65:continue
  hi=max(x[2] for x in bs[i-20:i]);lo=min(x[3] for x in bs[i-20:i]);d=1 if bs[i][3]<lo and c[i]>lo else (-1 if bs[i][2]>hi and c[i]<hi else 0)
  if not d:continue
  e=c[i];sl=e-d*.9*at[i];tp=e+d*1.35*at[i];j,p,r=exit_bar(bs,i,d,e,tp,sl,8);busy=j;rows.append({'edge':f'RANGE_M{tf}','entry_t':bs[i][0],'exit_t':bs[j][0],'pnl':p,'reason':r})
 return rows
def edge_breakout(ticks,tf=5):
 bs=bars(ticks,tf);c=[x[4] for x in bs];at=atr(bs);rows=[];busy=-1
 for i in range(30,len(bs)-1):
  if i<=busy or at[i]<=0:continue
  prev=[x[2]-x[3] for x in bs[i-20:i]];med=statistics.median(prev);hi=max(x[2] for x in bs[i-20:i]);lo=min(x[3] for x in bs[i-20:i]);rng=bs[i][2]-bs[i][3]
  d=1 if c[i]>hi and rng>1.4*med else (-1 if c[i]<lo and rng>1.4*med else 0)
  if not d:continue
  e=c[i];sl=e-d*at[i];tp=e+d*2.2*at[i];j,p,r=exit_bar(bs,i,d,e,tp,sl,10);busy=j;rows.append({'edge':f'BREAKOUT_M{tf}','entry_t':bs[i][0],'exit_t':bs[j][0],'pnl':p,'reason':r})
 return rows
class B:
 def __init__(self):self.q=deque(maxlen=60)
 def push(self,x):self.q.append(x)
 def last(self,n):return list(self.q)[-min(n,len(self.q)):]
 def mom(self,n):
  x=self.last(n);return x[-1]-x[0] if len(x)>=2 else 0.
 def avg(self,n):
  x=self.last(n);return sum(x)/len(x) if x else 0.
 def wm(self,n=10):
  x=self.last(n);return 0. if len(x)<2 else sum((x[i]-x[i-1])*i for i in range(1,len(x)))/sum(range(1,len(x)))
 def cons(self):
  x=list(self.q);d=c=0
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
 if abs(m)<.03:return 0
 d=1 if m>0 else -1;c=b.cons();w=b.wm();f=b.avg(5);s=b.avg(20)
 return d if abs(c)>=3 and (1 if c>0 else -1)==d and abs(w)>=.02 and (1 if w>0 else -1)==d and f and s and (1 if f>s else -1)==d else 0
def edge_tickreverse(ticks):
 M3=.09899999999970532;M5=.09174999999981992;b=B();mids=deque(maxlen=120);vh=deque(maxlen=200);seed=None;last=-10**18;pos=None;rows=[];prev=None;adv=0;start=0
 for t,a,bd in ticks:
  b.push(bd);mids.append((a+bd)/2);ret=0.
  if len(mids)>=20:
   xs=list(mids)[-20:];ret=sum(abs(xs[i]-xs[i-1]) for i in range(1,len(xs)))/19
  if pos:
   d=pos['d'];px=bd if d>0 else a;pp=(px-pos['e'])*d;hit=('SL',-.60) if pp<=-.60 else (('TP',.60) if pp>=.60 else None)
   if prev is not None:
    de=(px-prev)*d
    if de<0:adv+=1
    elif de>0:adv=0
   prev=px
   if hit or adv>=3 or t-start>=120000:
    p=hit[1] if hit else pp;reason=hit[0] if hit else ('OPP3' if adv>=3 else 'TIME');rows.append({'edge':'TICKREV_OPP3','entry_t':pos['t'],'exit_t':t,'pnl':p,'reason':reason});pos=None;prev=None;adv=0
   continue
  bs=sig(b);spr=a-bd
  if seed is None and bs:
   if spr<=.40 and t-last>=500:seed={'t':t,'d':bs,'m':b.mom(10)}
   continue
  if seed is None:continue
  age=t-seed['t']
  if age>10000:seed=None;continue
  if spr>.40 or not(250<=age<=1000):continue
  m=b.mom(10);c=b.cons();d=seed['d']
  if not((m*d)>0 and abs(m)>=max(abs(seed['m']),.03) and abs(c)>=3 and (1 if c>0 else -1)==d):continue
  if not(abs(b.mom(3))/2>M3 and abs(b.mom(5))/4>M5):seed=None;continue
  ok=False
  if len(vh)>=50:
   s=sorted(vh);ok=s[int((len(s)-1)*.25)]<ret<=s[int((len(s)-1)*.75)]
  vh.append(ret)
  if not ok or dt.datetime.fromtimestamp(t/1000,dt.timezone.utc).hour not in {13,14,15}:seed=None;continue
  td=-d;e=a if td>0 else bd;pos={'d':td,'e':e,'t':t};start=t;prev=e;adv=0;last=t;seed=None
 return rows
def overlap(a,b):
 if not a or not b:return 0
 def one(x,y):
  ints=[(r['entry_t'],r['exit_t']) for r in y];return sum(any(s<=r['entry_t']<=e for s,e in ints) for r in x)/len(x)
 return (one(a,b)+one(b,a))/2
def portfolio(edges,governor=False):
 ev=sorted([r for rs in edges.values() for r in rs],key=lambda r:r['exit_t']);eq=peak=0.;accepted=[];blocked=0;hist={k:deque(maxlen=20) for k in edges}
 for r in ev:
  dd=(peak-eq)/INITIAL;h=hist[r['edge']];roll=sum(h)/len(h) if h else 0;allow=not(governor and (dd>=.035 or (dd>=.015 and len(h)>=8 and roll<=0)))
  if allow:
   x=r['pnl']-COMM;accepted.append({'pnl':r['pnl']});eq+=x;peak=max(peak,eq);h.append(x)
  else:blocked+=1
 m=metric(accepted);m['blocked']=blocked;return m
res={'design':'Multi-edge candidate portfolio v1. Only TICKREV_OPP3 is previously frozen positive; other lanes must pass OOS independently.','commission_per_trade':COMM,'initial_equity':INITIAL,'periods':{}}
for p,(s,e) in PERIODS.items():
 ticks=load(s,e);ed={'TICKREV_OPP3':edge_tickreverse(ticks),'TREND_M5':edge_trend(ticks),'RANGE_M5':edge_range(ticks),'BREAKOUT_M5':edge_breakout(ticks)}
 mr={k:metric(v) for k,v in ed.items()};ovs={};ks=list(ed)
 for i in range(len(ks)):
  for j in range(i+1,len(ks)):ovs[f'{ks[i]}__{ks[j]}']=overlap(ed[ks[i]],ed[ks[j]])
 eligible={k:v for k,v in ed.items() if mr[k]['PF']>=1.2 and mr[k]['N']>=10}
 res['periods'][p]={'ticks':len(ticks),'edges':mr,'overlap':ovs,'all_uncontrolled':portfolio(ed),'all_dd_governor':portfolio(ed,True),'eligible_names':list(eligible),'eligible_uncontrolled':portfolio(eligible),'eligible_dd_governor':portfolio(eligible,True)}
(OUT/'summary.json').write_text(json.dumps(res,indent=2));print(json.dumps(res,indent=2))
