#!/usr/bin/env python3
"""Normalize candidate backtest metrics; fail closed on absent evidence."""
import argparse
import json
from pathlib import Path

REQUIRED=("candidate_id","dataset_id","engine","start","end","trades","net_profit","gross_profit","gross_loss","max_floating_dd","initial_equity","execution_costs","oos")
def normalize(data):
    missing=[k for k in REQUIRED if k not in data]
    if missing:
        return {"status":"INCOMPLETE","missing":missing,"candidate_id":data.get("candidate_id"),"eligible_for_promotion":False}
    if data["engine"]!="nautilus_raw_tick":
        return {"status":"NOT_RAW_TICK","candidate_id":data["candidate_id"],"eligible_for_promotion":False}
    if data["initial_equity"]<=0 or data["trades"]<0 or data["gross_loss"]>0 or data["max_floating_dd"]<0:
        return {"status":"INVALID_INPUT","candidate_id":data["candidate_id"],"eligible_for_promotion":False}
    loss=abs(data["gross_loss"])
    pf=(data["gross_profit"]/loss) if loss else None
    rf=(data["net_profit"]/data["max_floating_dd"]) if data["max_floating_dd"] else None
    return {"status":"MEASURED_NOT_PROMOTED","candidate_id":data["candidate_id"],"dataset_id":data["dataset_id"],"period":[data["start"],data["end"]],"trades":data["trades"],"pf":pf,"rf":rf,"net_profit":data["net_profit"],"max_floating_dd":data["max_floating_dd"],"max_floating_dd_pct":100*data["max_floating_dd"]/data["initial_equity"],"execution_costs":data["execution_costs"],"oos":data["oos"],"eligible_for_promotion":False}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    result=normalize(json.loads(Path(a.input).read_text()))
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__":
    main()
