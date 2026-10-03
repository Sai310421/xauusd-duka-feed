#!/usr/bin/env python3
"""Rolling XAUUSD Dukascopy raw-tick cache. Separate from candle feed."""
from __future__ import annotations
import argparse,datetime as dt,hashlib,json,lzma,os,struct,urllib.request
from pathlib import Path
REC=struct.Struct(">3I2f")
HOST="https://datafeed.dukascopy.com/datafeed"
SCALE=1000.0

def get(url:str)->bytes:
    req=urllib.request.Request(url,headers={"User-Agent":"research-line-rawtick/1"})
    with urllib.request.urlopen(req,timeout=30) as r:return r.read()

def day(day:dt.date,out:Path)->dict:
    target=out/f"{day.isoformat()}.csv"
    if target.exists() and target.stat().st_size>100:return {"day":str(day),"hit":True,"path":str(target)}
    out.mkdir(parents=True,exist_ok=True); tmp=target.with_suffix(".tmp")
    n=0
    with tmp.open("w",encoding="utf-8",newline="") as f:
        f.write("time,bid,ask,bid_size,ask_size\n")
        for hour in range(24):
            url=f"{HOST}/XAUUSD/{day.year}/{day.month-1:02d}/{day.day:02d}/{hour:02d}h_ticks.bi5"
            try: raw=lzma.decompress(get(url))
            except Exception: continue
            origin=dt.datetime(day.year,day.month,day.day,hour,tzinfo=dt.timezone.utc)
            for i in range(0,len(raw)-REC.size+1,REC.size):
                ms,ask,bid,askv,bidv=REC.unpack_from(raw,i)
                if not ask or not bid:continue
                ts=origin+dt.timedelta(milliseconds=ms)
                f.write(f"{ts.isoformat()},{bid/SCALE:.3f},{ask/SCALE:.3f},{bidv:.6f},{askv:.6f}\n");n+=1
    if n==0: tmp.unlink(missing_ok=True);return {"day":str(day),"hit":False,"ticks":0}
    tmp.replace(target)
    h=hashlib.sha256(target.read_bytes()).hexdigest()
    return {"day":str(day),"hit":False,"ticks":n,"bytes":target.stat().st_size,"sha256":h,"path":str(target)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--days",type=int,default=90);ap.add_argument("--root",default=os.environ.get("DUKA_RAW_ROOT","nautilus/cache/raw/XAUUSD"));a=ap.parse_args()
    root=Path(a.root); today=dt.datetime.now(dt.timezone.utc).date(); rows=[]
    for i in range(a.days,0,-1): rows.append(day(today-dt.timedelta(days=i),root))
    (root/"download-manifest.json").write_text(json.dumps({"symbol":"XAUUSD","days":a.days,"partitions":rows},indent=2)+"\n")
    print(json.dumps({"days":a.days,"ready_days":sum(bool(x.get("path")) for x in rows)},indent=2))
if __name__=="__main__":main()
