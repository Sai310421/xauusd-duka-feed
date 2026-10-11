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

def sha256(path:Path)->str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()

def verified_receipt(target:Path,symbol:str,day:dt.date)->dict|None:
    """Only complete, matching 24-hour receipts permit a cache hit."""
    receipt_path=target.with_suffix(".receipt.json")
    if not target.is_file() or not receipt_path.is_file():
        return None
    try:
        receipt=json.loads(receipt_path.read_text(encoding="utf-8"))
        hours=receipt.get("hours")
        if not (receipt.get("complete") is True and receipt.get("symbol")==symbol
                and receipt.get("day")==day.isoformat()
                and receipt.get("hours_expected")==24
                and isinstance(hours,list) and len(hours)==24
                and [h.get("hour") for h in hours]==list(range(24))
                and all(h.get("status") in ("ok","verified_empty") for h in hours)
                and type(receipt.get("ticks")) is int and receipt["ticks"]>0
                and receipt.get("bytes")==target.stat().st_size
                and receipt.get("sha256")==sha256(target)):
            return None
        return receipt
    except (OSError,ValueError,TypeError,AttributeError,KeyError):
        return None

def day(day:dt.date,out:Path,symbol:str)->dict:
    if day.weekday()>=5:
        return {"day":str(day),"weekend":True,"skipped":True}
    scale=SCALES[symbol]
    out.mkdir(parents=True,exist_ok=True)
    target=out/f"{day.isoformat()}.csv"
    receipt_path=target.with_suffix(".receipt.json")
    prior=verified_receipt(target,symbol,day)
    if prior is not None:
        return {"day":str(day),"hit":True,"complete":True,
                "ticks":prior["ticks"],"sha256":prior["sha256"],"path":str(target)}
    tmp=target.with_suffix(".tmp")
    hours=[]
    n=0
    complete=True
    try:
        with tmp.open("w",encoding="utf-8",newline="") as f:
            f.write("time,bid,ask,bid_size,ask_size\n")
            for hour in range(24):
                url=f"{HOST}/{symbol}/{day.year}/{day.month-1:02d}/{day.day:02d}/{hour:02d}h_ticks.bi5"
                try:
                    raw=lzma.decompress(get(url))
                    if len(raw)%REC.size:
                        raise ValueError(f"invalid BI5 byte length: {len(raw)}")
                    origin=dt.datetime(day.year,day.month,day.day,hour,tzinfo=dt.timezone.utc)
                    count=0
                    prev_ms=-1
                    for i in range(0,len(raw),REC.size):
                        ms,ask,bid,askv,bidv=REC.unpack_from(raw,i)
                        if not (0<=ms<3_600_000 and ms>=prev_ms):
                            raise ValueError("invalid or unsorted tick milliseconds")
                        if not (bid>0 and ask>=bid and askv>=0 and bidv>=0):
                            raise ValueError("invalid bid/ask quote or volume")
                        prev_ms=ms
                        ts=origin+dt.timedelta(milliseconds=ms)
                        f.write(f"{ts.isoformat()},{bid/scale:.5f},{ask/scale:.5f},{bidv:.6f},{askv:.6f}\n")
                        count+=1
                    hours.append({"hour":hour,"status":"ok" if count else "verified_empty","ticks":count})
                    n+=count
                except Exception as exc:
                    # 404, timeout and malformed BI5 are NOT verified empty hours.
                    hours.append({"hour":hour,"status":"error","error_type":type(exc).__name__})
                    complete=False
        if not complete or n==0:
            return {"day":str(day),"hit":False,"complete":False,
                    "ticks_downloaded":n,
                    "hours_verified":sum(h["status"]!="error" for h in hours),
                    "failed_hours":[h["hour"] for h in hours if h["status"]=="error"],
                    "reason":"incomplete_hours" if not complete else "no_ticks"}
        digest=sha256(tmp)
        size=tmp.stat().st_size
        receipt={"symbol":symbol,"day":str(day),"complete":True,
                 "hours_expected":24,"hours":hours,"ticks":n,"bytes":size,"sha256":digest}
        # A stale receipt cannot authenticate a replaced CSV: hash must match.
        tmp.replace(target)
        receipt_tmp=receipt_path.with_suffix(".receipt.tmp")
        receipt_tmp.write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")
        receipt_tmp.replace(receipt_path)
        return {"day":str(day),"hit":False,"complete":True,"ticks":n,
                "bytes":size,"sha256":digest,"path":str(target)}
    finally:
        tmp.unlink(missing_ok=True)

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
                    rows.append({"day":str(futs[fut]),"complete":False,"error_type":type(e).__name__})
        rows.sort(key=lambda x:x["day"])
        out.mkdir(parents=True,exist_ok=True)
        (out/"download-manifest.json").write_text(
            json.dumps({"symbol":symbol,"days":a.days,"workers":a.workers,"partitions":rows},indent=2)+"\n"
        )
        summary[symbol]={
            "ready_days":sum(x.get("complete") is True for x in rows),
            "weekday_targets":sum(1 for d in days if d.weekday()<5),
            "days":a.days,
            "workers":a.workers,
        }
    print(json.dumps(summary,indent=2))

if __name__=="__main__":
    main()
