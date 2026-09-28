#!/usr/bin/env python3
from __future__ import annotations
import csv,datetime as dt,io,json,statistics,subprocess,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'results'/'amos-multisymbol-edge-factory-v3'; CACHE=ROOT/'nautilus'/'cache'/'exness-multisymbol'
OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)
SYMS=['XAUUSD','XAGUSD','EURUSD','GBPUSD','USDJPY','BTCUSD']; TFS=(1,5,15,60); COMM_R=(0.0,0.03,0.05)
PERIODS={'OOS2':(dt.datetime(2026,9,7,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,11,18,tzinfo=dt.timezone.utc)),'OOS1':(dt.datetime(2026,9,14,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,18,18,tzinfo=dt.timezone.utc)),'DISC':(dt.datetime(2026,9,21,7,tzinfo=dt.timezone.utc),dt.datetime(2026,9,25,18,tzinfo=dt.timezone.utc))}
def parse(x):
 d=dt.datetime.fromisoformat(x.strip().replace('Z','+00:00'));return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)
def path(sym):
 v=f'{sym}_Zero_Spread';p=CACHE/f'Exness_{v}_2026_09.zip'
 if not p.exists():subprocess.run(['curl','-fL','--retry','3','-o',str(p),f'https://ticks.ex2archive.com/ticks/{v}/2026/09/Exness_{v}_2026_09.zip'],check=True,timeout=240)
 return p
def load(sym,s,e):
 out=[]
 with zipfile.ZipFile(path(sym)) as z:
  for n in [x for x in z.namelist() if x.lower().endswith('.csv')]:
   with z.open(n) as raw:
    for r in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline='')):
     try:t=parse(r.get('Timestamp') or r.get('timestamp'));b=float(r.get('Bid') or r.get('bid'));a=float(r.get('Ask') or r.get('ask'))
     except:continue
     if s<=t<e and 7<=t.hour<18 and 0<b<=a:out.append((int(t.timestamp()*1000),a,b))
 out.sort();return out
def bars(ts,m):
 d={};step=m*60000
 for t,a,b in ts:
  k=t//step*step;mid=(a+b)/2
  if k not in d:d[k]=[k,mid,mid,mid,mid,a-b,a,b,b,b,a,a]
  else:
   x=d[k];x[2]=max(x[2],mid);x[3]=min(x[3],mid);x[4]=mid;x[5]=(x[5]+a-b)/2;x[6]=a;x[7]=b;x[8]=max(x[8],b);x[9]=min(x[9],b);x[10]=max(x[10],a);x[11]=min(x[11],a)
 return [d[k] for k in sorted(d)]
def ema(xs,n):
 a=2/(n+1);o=[];v=None
 for x in xs:v=x if v is None else a*x+(1-a)*v;o.append(v)
 return o
def atr(bs,n=14):
 tr=[]
 for i,b in enumerate(bs):tr.append(b[2]-b[3] if i==0 else max(b[2]-b[3],abs(b[2]-bs[i-1][4]),abs(b[3]-bs[i-1][4])))
 return ema(tr,n)
def ex(bs,i,d,e,sl,tp,maxbars,risk):
 end=min(len(bs),i+1+maxbars)
 for j in range(i+1,end):
  if d>0:
   if bs[j][9]<=sl:return j,-1.0
   if bs[j][8]>=tp:return j,(tp-e)/risk
  else:
   if bs[j][10]>=sl:return j,-1.0
   if bs[j][11]<=tp:return j,(e-tp)/risk
 j=end-1;px=bs[j][7] if d>0 else bs[j][6];return j,(px-e)*d/risk
def trend(ts,tf):
 bs=bars(ts,tf);c=[x[4] for x in bs];e20=ema(c,20);e60=ema(c,60);at=atr(bs);o=[];busy=-1
 for i in range(61,len(bs)-1):
  if i<=busy or at[i]<=0:continue
  slope=e20[i]-e20[i-3];d=1 if e20[i]>e60[i] and slope>0 else (-1 if e20[i]<e60[i] and slope<0 else 0)
  if d>0 and not(bs[i-1][3]<=e20[i-1] and c[i]>e20[i] and c[i]>c[i-1]):continue
  if d<0 and not(bs[i-1][2]>=e20[i-1] and c[i]<e20[i] and c[i]<c[i-1]):continue
  if not d:continue
  e=bs[i][6] if d>0 else bs[i][7];risk=1.1*at[i];j,r=ex(bs,i,d,e,e-d*risk,e+d*2*at[i],12,risk);busy=j;o.append((bs[i][0],bs[j][0],r))
 return o
def rangeedge(ts,tf):
 bs=bars(ts,tf);c=[x[4] for x in bs];e20=ema(c,20);e60=ema(c,60);at=atr(bs);o=[];busy=-1
 for i in range(60,len(bs)-1):
  if i<=busy or at[i]<=0 or abs(e20[i]-e60[i])/at[i]>.65:continue
  hi=max(x[2] for x in bs[i-20:i]);lo=min(x[3] for x in bs[i-20:i]);d=1 if bs[i][3]<lo and c[i]>lo else (-1 if bs[i][2]>hi and c[i]<hi else 0)
  if not d:continue
  e=bs[i][6] if d>0 else bs[i][7];risk=.9*at[i];j,r=ex(bs,i,d,e,e-d*risk,e+d*1.35*at[i],8,risk);busy=j;o.append((bs[i][0],bs[j][0],r))
 return o
def breakout(ts,tf):
 bs=bars(ts,tf);c=[x[4] for x in bs];at=atr(bs);o=[];busy=-1
 for i in range(30,len(bs)-1):
  if i<=busy or at[i]<=0:continue
  pr=[x[2]-x[3] for x in bs[i-20:i]];med=statistics.median(pr);hi=max(x[2] for x in bs[i-20:i]);lo=min(x[3] for x in bs[i-20:i]);rg=bs[i][2]-bs[i][3]
  d=1 if c[i]>hi and rg>1.4*med else (-1 if c[i]<lo and rg>1.4*med else 0)
  if not d:continue
  e=bs[i][6] if d>0 else bs[i][7];risk=at[i];j,r=ex(bs,i,d,e,e-d*risk,e+d*2.2*at[i],10,risk);busy=j;o.append((bs[i][0],bs[j][0],r))
 return o
def met(rows,cost=0):
 p=[r[2]-cost for r in rows];w=[x for x in p if x>0];l=[x for x in p if x<0];eq=pk=dd=0;ls=mx=0
 for x in p:eq+=x;pk=max(pk,eq);dd=min(dd,eq-pk);ls=ls+1 if x<0 else 0;mx=max(mx,ls)
 return {'N':len(p),'WR':len(w)/len(p) if p else 0,'PF':sum(w)/abs(sum(l)) if l else (999 if w else 0),'EV_R':sum(p)/len(p) if p else 0,'Net_R':sum(p),'MaxDD_R':dd,'max_loss_streak':mx}
res={'note':'Bid/Ask tick-derived bars; pnl normalized to 1R initial stop risk. Cost scenarios are modeled R deductions, not broker-confirmed commissions.','periods':{}}
for pn,(s,e) in PERIODS.items():
 res['periods'][pn]={}
 for sym in SYMS:
  ts=load(sym,s,e);lanes={}
  for tf in TFS:
   lanes[f'{sym}_TREND_M{tf}']=trend(ts,tf);lanes[f'{sym}_RANGE_M{tf}']=rangeedge(ts,tf);lanes[f'{sym}_BREAKOUT_M{tf}']=breakout(ts,tf)
  res['periods'][pn][sym]={'ticks':len(ts),'lanes':{k:{f'costR_{c:.2f}':met(v,c) for c in COMM_R} for k,v in lanes.items()}}
# cross-period gate: both OOS PF>=1.2,N>=10 at costR .03; DISC reported but not required
passed=[]
for sym in SYMS:
 for tf in TFS:
  for typ in ('TREND','RANGE','BREAKOUT'):
   k=f'{sym}_{typ}_M{tf}';a=res['periods']['OOS2'][sym]['lanes'][k]['costR_0.03'];b=res['periods']['OOS1'][sym]['lanes'][k]['costR_0.03']
   if a['N']>=10 and b['N']>=10 and a['PF']>=1.2 and b['PF']>=1.2:passed.append(k)
res['passed_oos']=passed
(OUT/'summary.json').write_text(json.dumps(res,indent=2));print(json.dumps(res,indent=2))
