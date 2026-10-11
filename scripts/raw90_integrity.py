"""Integrity audit for rolling Dukascopy raw-tick cache.

A CSV without a matching 24-hour acquisition receipt is not verified data.
"""
from __future__ import annotations
import csv
import datetime as dt
import json
import os
from pathlib import Path

from duka_raw90 import verified_receipt

ROOT = Path(os.environ.get("DUKA_RAW_ROOT") or str(Path.home() / ".research-line" / "raw"))
SYMBOLS = [x.strip().upper() for x in os.environ.get("DUKA_SYMBOLS", "XAUUSD,EURUSD,GBPUSD,USDJPY,XAGUSD").split(",") if x.strip()]
MIN_TICKS = int(os.environ.get("RAW_MIN_TICKS_PER_DAY", "100"))
MAX_BAD_SPREAD_RATIO = float(os.environ.get("RAW_MAX_BAD_SPREAD_RATIO", "0.0001"))


def inspect_day(p: Path, expected_day: dt.date | None = None) -> dict:
    n = bad = backward = 0
    prev = None
    with p.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                t = dt.datetime.fromisoformat(row["time"])
                bid = float(row["bid"])
                ask = float(row["ask"])
                if (not (bid > 0 and ask >= bid) or t.tzinfo is None
                        or t.utcoffset() != dt.timedelta(0)
                        or (expected_day is not None and t.date() != expected_day)):
                    bad += 1
                if prev is not None and t < prev:
                    backward += 1
                prev = t
                n += 1
            except (KeyError, ValueError, TypeError, OverflowError):
                bad += 1
    ratio = bad / max(n, 1)
    return {"ticks": n, "bad": bad, "backward": backward, "bad_ratio": ratio,
            "valid": n >= MIN_TICKS and backward == 0 and ratio <= MAX_BAD_SPREAD_RATIO}


def audit(root: Path, days: int = 90, symbol: str = "XAUUSD",
          today: dt.date | None = None) -> dict:
    if days < 1:
        raise ValueError("days must be positive")
    today = today or dt.datetime.now(dt.timezone.utc).date()
    wanted = [today - dt.timedelta(days=i) for i in range(days, 0, -1)]
    checks = {}
    missing = []
    unverified = []
    receipt_mismatch = []
    for day in wanted:
        if day.weekday() >= 5:
            continue
        p = root / f"{day.isoformat()}.csv"
        if not p.is_file():
            missing.append(str(day))
            continue
        receipt = verified_receipt(p, symbol, day)
        if receipt is None:
            unverified.append(str(day))
            continue
        check = inspect_day(p, expected_day=day)
        if check["ticks"] != receipt["ticks"]:
            receipt_mismatch.append(str(day))
        checks[str(day)] = check
    invalid = [day for day, value in checks.items() if not value["valid"]]
    ready = not (missing or unverified or receipt_mismatch or invalid)
    return {"symbol": symbol, "window_days": days,
            "required_weekdays": sum(day.weekday() < 5 for day in wanted),
            "present": len(checks) + len(unverified),
            "present_verified": len(checks), "missing": missing,
            "unverified": unverified, "receipt_mismatch": receipt_mismatch,
            "invalid": invalid,
            "total_ticks": sum(value["ticks"] for value in checks.values()),
            "ready": ready, "checks": checks}


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    reports = {}
    ready = True
    for symbol in SYMBOLS:
        folder = ROOT / symbol
        report = audit(folder, symbol=symbol)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "integrity.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        reports[symbol] = report
        ready = ready and report["ready"]
    print(json.dumps({symbol: {"ready": report["ready"],
                               "present": report["present"],
                               "present_verified": report["present_verified"],
                               "total_ticks": report["total_ticks"],
                               "missing": len(report["missing"]),
                               "unverified": len(report["unverified"]),
                               "receipt_mismatch": len(report["receipt_mismatch"]),
                               "invalid": len(report["invalid"])}
                      for symbol, report in reports.items()}, indent=2))
    raise SystemExit(0 if ready else 2)
