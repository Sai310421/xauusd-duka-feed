#!/usr/bin/env python3
"""Retry only missing or invalid weekday partitions; never waive integrity failures."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import time
from pathlib import Path
from duka_raw90 import day
from raw90_integrity import audit

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--delay", type=float, default=15)
    parser.add_argument("--root", default=os.environ.get("DUKA_RAW_ROOT", str(Path.home()/".research-line"/"raw")))
    parser.add_argument("--symbols", default=os.environ.get("DUKA_SYMBOLS", "XAUUSD"))
    args = parser.parse_args()
    root = Path(args.root)
    for symbol in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        folder = root/symbol
        folder.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, args.attempts+1):
            report = audit(folder, days=args.days, symbol=symbol)
            pending = report["missing"] + report["invalid"]
            if not pending:
                break
            print(json.dumps({"symbol":symbol,"attempt":attempt,"pending":pending}),flush=True)
            for stamp in pending:
                target = folder/f"{stamp}.csv"
                if stamp in report["invalid"]:
                    target.unlink(missing_ok=True)
                try:
                    outcome = day(dt.date.fromisoformat(stamp),folder,symbol)
                    print(json.dumps(outcome),flush=True)
                except Exception as exc:
                    print(json.dumps({"day":stamp,"error":str(exc)}),flush=True)
            if attempt < args.attempts:
                time.sleep(args.delay*attempt)
        final = audit(folder,days=args.days,symbol=symbol)
        (folder/"repair-report.json").write_text(json.dumps({
            "symbol":symbol,"ready":final["ready"],"missing":final["missing"],
            "invalid":final["invalid"],"present":final["present"],
            "required_weekdays":final["required_weekdays"]
        },indent=2)+"\n",encoding="utf-8")
        print(json.dumps({"symbol":symbol,"ready":final["ready"],
                          "missing":final["missing"],"invalid":final["invalid"]}),flush=True)

if __name__ == "__main__":
    main()
