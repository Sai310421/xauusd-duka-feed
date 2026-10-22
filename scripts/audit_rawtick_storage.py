#!/usr/bin/env python3
"""Audit persisted Dukascopy raw tick files. Never treat Actions cache as durable storage."""
import argparse, hashlib, json, pathlib, datetime
EXTS={".bi5",".csv",".parquet",".arrow",".feather"}
def audit(root):
    root=pathlib.Path(root).resolve()
    if not root.is_dir(): raise SystemExit(f"Missing data directory: {root}")
    items=[]
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in EXTS: continue
        h=hashlib.sha256()
        with p.open("rb") as f:
            for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
        items.append({"path":str(p.relative_to(root)),"bytes":p.stat().st_size,"sha256":h.hexdigest(),"format":p.suffix.lower(),"verified":"checksum_only","raw_bid_ask_verified":False})
    return {"schema_version":1,"created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"root":str(root),"file_count":len(items),"total_bytes":sum(x["bytes"] for x in items),"files":items,"status":"inventory_only_not_backtest_validated"}
if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    result=audit(a.directory)
    out=pathlib.Path(a.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"file_count":result["file_count"],"total_bytes":result["total_bytes"],"output":str(out)}))
    if not result["file_count"]: raise SystemExit("No candidate tick files found; cannot claim durable backup")
