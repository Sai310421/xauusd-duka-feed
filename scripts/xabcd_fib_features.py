#!/usr/bin/env python3
"""Causal XABCD / Fibonacci / liquidity feature candidate generator for closed OHLC bars.
Pivots become usable only after right-side confirmation bars have closed.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path

RATIOS=(.654,.667,.697,.706,.825,.835,1.255,1.302)

def pivots(bars,left=3,right=3):
    result=[]
    for i in range(left,len(bars)-right):
        window=bars[i-left:i+right+1]
        high=bars[i]["high"];low=bars[i]["low"]
        if high==max(b["high"] for b in window) and sum(b["high"]==high for b in window)==1:
            result.append((i+right,i,"H",high))
        if low==min(b["low"] for b in window) and sum(b["low"]==low for b in window)==1:
            result.append((i+right,i,"L",low))
    return sorted(result)

def features(bars,left=3,right=3):
    confirmed=pivots(bars,left,right)
    active=[];pos=0
    for t,bar in enumerate(bars):
        while pos<len(confirmed) and confirmed[pos][0]<=t:
            active.append(confirmed[pos]);pos+=1
        out={"time":bar["time"],"pivot_count":len(active)}
        if len(active)>=2:
            a,b=active[-2:]
            span=abs(b[3]-a[3])
            atr=sum(x["high"]-x["low"] for x in bars[max(0,t-13):t+1])/min(t+1,14)
            atr=max(atr,1e-9)
            out["swing_span_atr"]=span/atr
            out["swing_age_bars"]=t-b[1]
            out["last_pivot_type"]=b[2]
            out["last_pivot_distance_atr"]=(bar["close"]-b[3])/atr
            if span>0:
                for r in RATIOS:
                    level=b[3]+(-1 if b[2]=="H" else 1)*r*span
                    out[f"fib_{str(r).replace('.','p')}_distance_atr"]=(bar["close"]-level)/atr
                out["fib_nearest_distance_atr"]=min(abs(v) for k,v in out.items() if k.startswith("fib_") and k.endswith("_distance_atr"))
        if len(active)>=5:
            five=active[-5:]
            if all(five[i][2]!=five[i+1][2] for i in range(4)):
                x,a,b,c,d=[v[3] for v in five]
                out["xabcd_ab_xa"]=abs(b-a)/max(abs(a-x),1e-9)
                out["xabcd_bc_ab"]=abs(c-b)/max(abs(b-a),1e-9)
                out["xabcd_cd_bc"]=abs(d-c)/max(abs(c-b),1e-9)
                out["xabcd_confirmed"]=1
        yield out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--bars",required=True,help="CSV time,open,high,low,close; closed bars only")
    ap.add_argument("--out",default="artifacts/xabcd/features.jsonl")
    args=ap.parse_args()
    with open(args.bars,newline="") as f:
        bars=[dict(row,**{k:float(row[k]) for k in ("open","high","low","close")}) for row in csv.DictReader(f)]
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w") as f:
        for row in features(bars):
            f.write(json.dumps(row)+"\n")
    print(json.dumps({"bars":len(bars),"output":str(out)}))

if __name__=="__main__":
    main()
