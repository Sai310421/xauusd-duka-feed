#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'results'/'amos-3edge-portfolio-v5';OUT.mkdir(parents=True,exist_ok=True)
# Isolated namespaces avoid executing result loops and preserve each engine's globals.
n4={'__file__':str(ROOT/'scripts'/'amos_multisymbol_edge_factory_v3.py')};src3=(ROOT/'scripts'/'amos_multisymbol_edge_factory_v3.py').read_text();exec(src3.split("res={'note'")[0],n4)
src4=(ROOT/'scripts'/'amos_diverse_edge_factory_v4.py').read_text();tail4=src4.split("def rsi(xs,n=7):",1)[1].split("res={'note'",1)[0];exec("def rsi(xs,n=7):"+tail4,n4)
n2={'__file__':str(ROOT/'scripts'/'amos_multi_edge_portfolio_v1.py')};src2=(ROOT/'scripts'/'amos_multi_edge_portfolio_v1.py').read_text();exec(src2.split("res={'design'")[0],n2)
PER=n4['PERIODS']; RISK_PCTS=(0.10,0.25,0.50,1.00); INITIAL=1000.0
def metrics(rows,risk_pct,gov=False):
 ev=sorted(rows,key=lambda x:x['exit_t']);eq=INITIAL;peak=INITIAL;maxdd=0.;wins=loss=0.;gp=gl=0.;st=mx=0;scaled=0
 for r in ev:
  dd=(peak-eq)/peak if peak else 0.;mult=1.
  if gov:
   if dd>=.035:mult=.10
   elif dd>=.025:mult=.25
   elif dd>=.015:mult=.50
  if mult<1:scaled+=1
  pnl=eq*(risk_pct/100)*r['R']*mult
  eq+=pnl;peak=max(peak,eq);maxdd=max(maxdd,(peak-eq)/peak if peak else 0)
  if pnl>0:wins+=1;gp+=pnl;st=0
  elif pnl<0:loss+=1;gl+=-pnl;st+=1;mx=max(mx,st)
 return {'N':len(ev),'WR':wins/len(ev) if ev else 0,'PF':gp/gl if gl else 999,'Final':eq,'Return_pct':(eq/INITIAL-1)*100,'MaxDD_pct':maxdd*100,'max_loss_streak':mx,'scaled_trades':scaled}
def overlap(a,b):
 if not a or not b:return 0.
 def q(x,y):
  ints=[(z['entry_t'],z['exit_t']) for z in y];return sum(any(s<=z['entry_t']<=e for s,e in ints) for z in x)/len(x)
 return (q(a,b)+q(b,a))/2
res={'note':'3-edge portfolio. XAU OPP3 R=(gross pnl-$0.03)/$0.60 initial stop risk. USDJPY candidate R subtracts modeled 0.03R cost. Compounded equal-risk sizing. DD governor: 1.5%=>0.5x, 2.5%=>0.25x, 3.5%=>0.1x.','periods':{}}
for pn,(s,e) in PER.items():
 x=n2['edge_tickreverse'](n2['load'](s,e))
 a=[{'edge':'XAU_OPP3','entry_t':z['entry_t'],'exit_t':z['exit_t'],'R':(z['pnl']-.03)/.60} for z in x]
 uj=n4['load']('USDJPY',s,e)
 r=n4['rsi_mean'](uj,1);b=[{'edge':'USDJPY_RSIMEAN_M1','entry_t':z[0],'exit_t':z[1],'R':z[2]-.03} for z in r]
 w=n4['sweep_rev'](uj,5);c=[{'edge':'USDJPY_SWEEP_M5','entry_t':z[0],'exit_t':z[1],'R':z[2]-.03} for z in w]
 lanes={'XAU_OPP3':a,'USDJPY_RSIMEAN_M1':b,'USDJPY_SWEEP_M5':c};allr=a+b+c
 res['periods'][pn]={'lane_N':{k:len(v) for k,v in lanes.items()},'overlap':{f'{i}__{j}':overlap(lanes[i],lanes[j]) for ix,i in enumerate(lanes) for j in list(lanes)[ix+1:]},'risk':{}}
 for rp in RISK_PCTS:
  res['periods'][pn]['risk'][str(rp)]={'uncontrolled':metrics(allr,rp,False),'dd_governor':metrics(allr,rp,True)}
(OUT/'summary.json').write_text(json.dumps(res,indent=2));print(json.dumps(res,indent=2))
