#!/usr/bin/env python3
"""Download a public XAUUSD Parquet sample and emit machine-readable schema/data QA."""
import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://raw.githubusercontent.com/simom1/XAUUSD-history/main/2025/xauusd_2025_01.parquet"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--url",default=DEFAULT_URL)
    ap.add_argument("--out",default="artifacts/public_xauusd")
    args=ap.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    dest=out/"sample.parquet"
    req=urllib.request.Request(args.url,headers={"User-Agent":"AMOS-XAUUSD-data-audit/1"})
    with urllib.request.urlopen(req,timeout=120) as response, dest.open("wb") as target:
        while chunk:=response.read(1024*1024):
            target.write(chunk)
    import pyarrow.parquet as pq
    pf=pq.ParquetFile(dest)
    cols=pf.schema_arrow.names
    report={"source_url":args.url,"bytes":dest.stat().st_size,
            "sha256":hashlib.sha256(dest.read_bytes()).hexdigest(),
            "rows":pf.metadata.num_rows,"row_groups":pf.num_row_groups,
            "schema":str(pf.schema_arrow),"columns":cols,
            "bid_ask_columns_present":any(c.lower()=="bid" for c in cols) and any(c.lower()=="ask" for c in cols)}
    if report["bid_ask_columns_present"]:
        import pyarrow.compute as pc
        tab=pq.read_table(dest,columns=[next(c for c in cols if c.lower()=="bid"),next(c for c in cols if c.lower()=="ask")])
        b=tab.column(0);a=tab.column(1)
        report["bad_bid_ask_rows"]=int(pc.sum(pc.cast(pc.or_(pc.less_equal(b,0),pc.less(a,b)), "int64")).as_py() or 0)
    (out/"audit.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    if report["rows"]==0 or not report["bid_ask_columns_present"] or report.get("bad_bid_ask_rows",0)>0:
        raise SystemExit("Public data sample failed basic Bid/Ask QA")

if __name__=="__main__":
    main()
