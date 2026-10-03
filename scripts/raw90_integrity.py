"""Integrity gate for rolling Dukascopy XAUUSD raw-tick cache."""
from __future__ import annotations
import csv,datetime as dt,json,os
from pathlib import Path

ROOT=Path(os.environ.get("DUKA_RAW_ROOT","nautilus/cache/raw/XAUUSD"))
MIN_TICKS=int(os.environ.get("RAW_MIN_TICKS_PER_DAY","100"))
MAX_BAD_SPREAD_RATIO=float(os.environ.get("RAW_MAX_BAD_SPREAD_RATIO","0.0001"))

def inspect_day(p:Path)->dict:
    n=bad=backward=0; prev=None
    with p.open("r",encoding="utf-8",newline="") as f:
        for r in csv.DictReader(f):
            try:
                t=dt.datetime.fromisoformat(r["time"]);bid=float(r["bid"]);ask=float(r["ask"])
                if ask < bid or bid <= 0: bad+=1
                if prev is not None and t < prev: backward+=1
                prev=t;n+=1
            except Exception: bad+=1
    ratio=bad/max(n,1)
    return {"ticks":n,"bad":bad,"backward":backward,"bad_ratio":ratio,
            "valid":n>=MIN_TICKS and backward==0 and ratio<=MAX_BAD_SPREAD_RATIO}

def audit(root:Path=ROOT,days:int=90)->dict:
    today=dt.datetime.now(dt.timezone.utc).date()
    wanted=[today-dt.timedelta(days=i) for i in range(days,0,-1)]
    checks={}; missing=[]
    for d in wanted:
        # Saturday/Sunday are not required cache partitions.
        if d.weekday()>=5: continue
        p=root/f"{d.isoformat()}.csv"
        if not p.exists(): missing.append(str(d));continue
        checks[str(d)]=inspect_day(p)
    invalid=[d for d,v in checks.items() if not v["valid"]]
    return {"symbol":"XAUUSD","window_days":days,"required_weekdays":len([d for d in wanted if d.weekday()<5]),
            "present":len(checks),"missing":missing,"invalid":invalid,
            "total_ticks":sum(v["ticks"] for v in checks.values()),
            "ready":not missing and not invalid,"checks":checks}

if __name__=="__main__":
    r=audit();ROOT.mkdir(parents=True,exist_ok=True)
    (ROOT/"integrity.json").write_text(json.dumps(r,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:r[k] for k in ("ready","required_weekdays","present","total_ticks","missing","invalid")},indent=2))
    raise SystemExit(0 if r["ready"] else 2)
