#!/usr/bin/env python3
# AMOS diverse-edge factory v4: independent candidate families, risk-normalized Bid/Ask bar replay.
exec(open('scripts/amos_multisymbol_edge_factory_v3.py').read().split("res={'note'")[0])
def rsi(xs,n=7):
 o=[50.0]*len(xs);g=[];l=[]
 for i in range(1,len(xs)):
  d=xs[i]-xs[i-1];g.append(max(d,0));l.append(max(-d,0))
  if i>=n:
   ag=sum(g[i-n:i])/n;al=sum(l[i-n:i])/n;o[i]=100 if al==0 else 100-100/(1+ag/al)
 return o
def sweep_rev(ts,tf):
 bs=bars(ts,tf);at=atr(bs);o=[];busy=-1
 for i in range(25,len(bs)-1):
  if i<=busy or at[i]<=0:continue
  hi=max(x[2] for x in bs[i-20:i]);lo=min(x[3] for x in bs[i-20:i])
  d=1 if bs[i][3]<lo and bs[i][4]>lo else (-1 if bs[i][2]>hi and bs[i][4]<hi else 0)
  if not d:continue
  e=bs[i][6] if d>0 else bs[i][7];risk=max(.7*at[i],abs(e-(bs[i][3] if d>0 else bs[i][2])));j,r=ex(bs,i,d,e,e-d*risk,e+d*1.8*risk,10,risk);busy=j;o.append((bs[i][0],bs[j][0],r))
 return o
def ema_pullback(ts,tf):
 bs=bars(ts,tf);c=[x[4] for x in bs];e9=ema(c,9);e21=ema(c,21);e55=ema(c,55);at=atr(bs);o=[];busy=-1
 for i in range(56,len(bs)-1):
  if i<=busy or at[i]<=0:continue
  d=1 if e9[i]>e21[i]>e55[i] else (-1 if e9[i]<e21[i]<e55[i] else 0)
  if not d:continue
  touch=bs[i][3]<=e21[i] and c[i]>e9[i] if d>0 else bs[i][2]>=e21[i] and c[i]<e9[i]
  if not touch:continue
  e=bs[i][6] if d>0 else bs[i][7];risk=1.0*at[i];j,r=ex(bs,i,d,e,e-d*risk,e+d*2.0*risk,10,risk);busy=j;o.append((bs[i][0],bs[j][0],r))
 return o
def compression(ts,tf):
 bs=bars(ts,tf);at=atr(bs);o=[];busy=-1
 for i in range(35,len(bs)-1):
  if i<=busy or at[i]<=0:continue
  recent=[x[2]-x[3] for x in bs[i-8:i]];base=[x[2]-x[3] for x in bs[i-28:i-8]]
  if statistics.mean(recent)>=.65*statistics.mean(base):continue
  hi=max(x[2] for x in bs[i-8:i]);lo=min(x[3] for x in bs[i-8:i]);d=1 if bs[i][4]>hi else (-1 if bs[i][4]<lo else 0)
  if not d:continue
  e=bs[i][6] if d>0 else bs[i][7];risk=.9*at[i];j,r=ex(bs,i,d,e,e-d*risk,e+d*2.2*risk,12,risk);busy=j;o.append((bs[i][0],bs[j][0],r))
 return o
def rsi_mean(ts,tf):
 bs=bars(ts,tf);c=[x[4] for x in bs];rv=rsi(c,7);e50=ema(c,50);at=atr(bs);o=[];busy=-1
 for i in range(51,len(bs)-1):
  if i<=busy or at[i]<=0:continue
  # only fade extremes when price is not >1.5 ATR away from slow mean
  if abs(c[i]-e50[i])/at[i]>1.5:continue
  d=1 if rv[i]<20 and c[i]>bs[i][3] else (-1 if rv[i]>80 and c[i]<bs[i][2] else 0)
  if not d:continue
  e=bs[i][6] if d>0 else bs[i][7];risk=.8*at[i];j,r=ex(bs,i,d,e,e-d*risk,e+d*1.4*risk,8,risk);busy=j;o.append((bs[i][0],bs[j][0],r))
 return o
res={'note':'Diverse candidate families. Bid/Ask tick-derived bars; 1R normalized. costR scenarios are modeling only. Selection gate OOS2+OOS1; DISC is forward check.','periods':{}}
for pn,(s,e) in PERIODS.items():
 res['periods'][pn]={}
 for sym in SYMS:
  ts=load(sym,s,e);lanes={}
  for tf in (1,5,15):
   lanes[f'{sym}_SWEEP_M{tf}']=sweep_rev(ts,tf);lanes[f'{sym}_PULLBACK_M{tf}']=ema_pullback(ts,tf);lanes[f'{sym}_COMPRESS_M{tf}']=compression(ts,tf);lanes[f'{sym}_RSIMEAN_M{tf}']=rsi_mean(ts,tf)
  res['periods'][pn][sym]={'ticks':len(ts),'lanes':{k:{f'costR_{c:.2f}':met(v,c) for c in COMM_R} for k,v in lanes.items()}}
passed=[]
for sym in SYMS:
 for tf in (1,5,15):
  for typ in ('SWEEP','PULLBACK','COMPRESS','RSIMEAN'):
   k=f'{sym}_{typ}_M{tf}';a=res['periods']['OOS2'][sym]['lanes'][k]['costR_0.03'];b=res['periods']['OOS1'][sym]['lanes'][k]['costR_0.03'];c=res['periods']['DISC'][sym]['lanes'][k]['costR_0.03']
   if a['N']>=10 and b['N']>=10 and a['PF']>=1.2 and b['PF']>=1.2:passed.append({'lane':k,'OOS2':a,'OOS1':b,'DISC':c,'forward_pass':c['N']>=10 and c['PF']>=1.0})
res['passed_oos']=passed
OUT2=ROOT/'results'/'amos-diverse-edge-factory-v4';OUT2.mkdir(parents=True,exist_ok=True);(OUT2/'summary.json').write_text(json.dumps(res,indent=2));print(json.dumps(res,indent=2))

# trigger v4 factory
