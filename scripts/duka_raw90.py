#!/usr/bin/env python3
"""Rolling Dukascopy raw-tick cache. Parallelized for GitHub-hosted runners."""
from __future__ import annotations
import argparse,datetime as dt,hashlib,json,lzma,os,struct,urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REC=struct.Struct(">3I2f")
HOST="https://datafeed.dukascopy.com/datafeed"
SCALES={"XAUUSD":1000.0,"XAGUSD":1000.0,"EURUSD":100000.0,"GBPUSD":100000.0,"USDJPY":1000.0}

def get(url:str)->bytes:
    req=urllib.request.Request(url,headers={"User-Agent":"research-line-rawtick/2"})
    with urllib.request.urlopen(req,timeout=15) as r:
        return r.read()

def day(day:dt.date,out:Path,symbol:str)->dict:
    if day.weekday() >= 5:
        return {"day":str(day),"weekend":True,"skipped":True}
    scale=SCALES[symbol]
    target=out/f"{day.isoformat()}.csv"
    if target.exists() and target.stat().st_size>100:
        return {"day":str(day),"hit":True,"path":str(target)}
    out.mkdir(parents=True,exist_ok=True)
    tmp=target.with_suffix(".tmp")
    n=0
    with tmp.open("w",encoding="utf-8",newline="") as f:
        f.write("time,bid,ask,bid_size,ask_size\n")
        for hour in range(24):
            url=f"{HOST}/{symbol}/{day.year}/{day.month-1:02d}/{day.day:02d}/{hour:02d}h_ticks.bi5"
            try:
                raw=lzma.decompress(get(url))
            except Exception:
                continue
            origin=dt.datetime(day.year,day.month,day.day,hour,tzinfo=dt.timezone.utc)
            for i in range(0,len(raw)-REC.size+1,REC.size):
                ms,ask,bid,askv,bidv=REC.unpack_from(raw,i)
                if not ask or not bid:
                    continue
                ts=origin+dt.timedelta(milliseconds=ms)
                f.write(f"{ts.isoformat()},{bid/scale:.5f},{ask/scale:.5f},{bidv:.6f},{askv:.6f}\n")
                n+=1
    if n==0:
        tmp.unlink(missing_ok=True)
        return {"day":str(day),"hit":False,"ticks":0}
    tmp.replace(target)
    h=hashlib.sha256(target.read_bytes()).hexdigest()
    return {"day":str(day),"hit":False,"ticks":n,"bytes":target.stat().st_size,"sha256":h,"path":str(target)}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--days",type=int,default=90)
    ap.add_argument("--root",default=os.environ.get("DUKA_RAW_ROOT") or str(Path.home()/".research-line"/"raw"))
    ap.add_argument("--symbols",default=os.environ.get("DUKA_SYMBOLS","XAUUSD"))
    ap.add_argument("--workers",type=int,default=int(os.environ.get("DUKA_WORKERS","8")))
    a=ap.parse_args()
    root=Path(a.root)
    today=dt.datetime.now(dt.timezone.utc).date()
    summary={}
    for symbol in [x.strip().upper() for x in a.symbols.split(",") if x.strip()]:
        if symbol not in SCALES:
            raise ValueError(f"unsupported symbol: {symbol}")
        out=root/symbol
        days=[today-dt.timedelta(days=i) for i in range(a.days,0,-1)]
        rows=[]
        with ThreadPoolExecutor(max_workers=max(1,a.workers)) as ex:
            futs={ex.submit(day,d,out,symbol):d for d in days}
            for fut in as_completed(futs):
                try:
                    rows.append(fut.result())
                except Exception as e:
                    rows.append({"day":str(futs[fut]),"error":repr(e)})
        rows.sort(key=lambda x:x["day"])
        out.mkdir(parents=True,exist_ok=True)
        (out/"download-manifest.json").write_text(
            json.dumps({"symbol":symbol,"days":a.days,"workers":a.workers,"partitions":rows},indent=2)+"\n"
        )
        summary[symbol]={
            "ready_days":sum(bool(x.get("path")) for x in rows),
            "weekday_targets":sum(1 for d in days if d.weekday()<5),
            "days":a.days,
            "workers":a.workers,
        }
    print(json.dumps(summary,indent=2))

if __name__=="__main__":
    main()
