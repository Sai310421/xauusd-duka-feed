#!/usr/bin/env python3
"""Incremental XAUUSD feed. Cache-first. Bulk first. Daily M1 only for gaps.

Does not dump M1 into the app bundle. Writes a tiny manifest + compact M5/M15/H1/H4/D1.
Re-runs are no-ops on cache hits — no token loop, no re-download.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import lzma
import struct
import subprocess
import time
import os
from pathlib import Path

REC = struct.Struct(">5If")
SCALE = 1000.0
HOST = "https://datafeed.dukascopy.com/datafeed"
GH = "https://raw.githubusercontent.com/Sai310421/xauusd-data/main/csv/XAUUSD"
ROOT = Path(os.environ.get("DUKA_ROOT", Path(__file__).resolve().parents[1]))
CACHE = ROOT / "nautilus/cache"
DUKA = CACHE / "duka"
GH_DIR = CACHE / "github"
BOOKS = ROOT / "books"
MANIFEST = BOOKS / "duka-sync.json"
PUBLIC_MANIFEST = ROOT / "public/duka-sync.json"
MTF = ROOT / "src/lib/case/xauusd_mtf.json"
BOOKS_MTF = BOOKS / "xauusd_mtf.json"
START = dt.date(2025, 9, 1)
END = dt.date(2026, 8, 28)

GH_FILES = {
    "M1": "XAUUSD_M1_2026Q1Q2.csv",
    "M5": "XAUUSD_M5_2026Q1Q2.csv",
    "H1": "XAUUSD_H1_2026Q1Q2.csv",
    "D1": "XAUUSD_D1_2026Q1Q2.csv",
}


def curl(url: str, dest: Path, retries: int = 5) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = 0
    for i in range(retries):
        r = subprocess.run(
            [
                "curl", "-sS", "-L", "--http1.1", "-m", "30",
                "-A", "Mozilla/5.0", "-o", str(dest), "-w", "%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=40,
        )
        try:
            last = int((r.stdout or "0").strip() or 0)
        except ValueError:
            last = 0
        if last == 200 and dest.exists() and dest.stat().st_size > 64:
            return 200
        if last in (404, 204):
            if dest.exists():
                dest.unlink()
            return last
        time.sleep(2.0 * (i + 1) + (6 if last == 503 else 0))
    return last


def pull_github() -> dict:
    stats = {}
    GH_DIR.mkdir(parents=True, exist_ok=True)
    for tf, name in GH_FILES.items():
        path = GH_DIR / name
        if path.exists() and path.stat().st_size > 1000:
            stats[tf] = {"hit": True, "bytes": path.stat().st_size}
            continue
        code = curl(f"{GH}/{name}", path)
        stats[tf] = {"hit": False, "code": code, "bytes": path.stat().st_size if path.exists() else 0}
        time.sleep(0.2)
    return stats


def month_span(start: dt.date, end: dt.date):
    y, m = start.year, start.month
    while dt.date(y, m, 1) <= end:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def trading_days(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        if d.weekday() != 5:
            yield d
        d += dt.timedelta(days=1)


def parse_bi5(blob: bytes, origin: dt.datetime) -> list[dict]:
    out = []
    for i in range(0, len(blob) - REC.size + 1, REC.size):
        sec, o, c, lo, hi, vol = REC.unpack_from(blob, i)
        ts = origin + dt.timedelta(seconds=int(sec))
        o, c, lo, hi = o / SCALE, c / SCALE, lo / SCALE, hi / SCALE
        if o <= 0 or hi < lo:
            continue
        out.append({"t": int(ts.replace(tzinfo=dt.timezone.utc).timestamp()), "o": o, "h": hi, "l": lo, "c": c})
    return out


def load_cached_month(y: int, m: int, side: str, kind: str) -> list[dict] | None:
    path = DUKA / f"{y}{m:02d}_{side}_{kind}.bi5"
    if not path.exists() or path.stat().st_size < 80:
        return None
    try:
        blob = lzma.decompress(path.read_bytes())
    except Exception:
        return None
    return parse_bi5(blob, dt.datetime(y, m, 1))


def fetch_month(y: int, m: int, side: str, kind: str) -> list[dict]:
    cached = load_cached_month(y, m, side, kind)
    if cached is not None:
        return cached
    path = DUKA / f"{y}{m:02d}_{side}_{kind}.bi5"
    url = f"{HOST}/XAUUSD/{y}/{m - 1:02d}/{side}_candles_{kind}.bi5"
    code = curl(url, path)
    if code != 200:
        print(f"month {y}-{m:02d} {side} {kind} {code}")
        return []
    recs = load_cached_month(y, m, side, kind) or []
    print(f"month {y}-{m:02d} {side} {kind} {len(recs)}")
    return recs


def mid_join(bid: list[dict], ask: list[dict]) -> list[dict]:
    by_b = {r["t"]: r for r in bid}
    by_a = {r["t"]: r for r in ask}
    out = []
    for t in sorted(set(by_b) & set(by_a)):
        b, a = by_b[t], by_a[t]
        h = (b["h"] + a["h"]) / 2
        l = (b["l"] + a["l"]) / 2
        if h - l < 0.02:
            continue
        out.append(
            {
                "t": t,
                "o": round((b["o"] + a["o"]) / 2, 3),
                "h": round(h, 3),
                "l": round(l, 3),
                "c": round((b["c"] + a["c"]) / 2, 3),
            }
        )
    return out


def resample(src: list[dict], seconds: int) -> list[dict]:
    buckets: dict[int, dict] = {}
    for r in src:
        key = r["t"] - (r["t"] % seconds)
        cur = buckets.get(key)
        if cur is None:
            buckets[key] = {"t": key, "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"]}
        else:
            cur["h"] = max(cur["h"], r["h"])
            cur["l"] = min(cur["l"], r["l"])
            cur["c"] = r["c"]
    out = []
    for k in sorted(buckets):
        b = buckets[k]
        if b["h"] - b["l"] < 0.02:
            continue
        out.append({k: round(b[k], 3) if k != "t" else b[k] for k in ("t", "o", "h", "l", "c")})
    return out


def csv_bars(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for row in csv.DictReader(f):
            raw = row["datetime"].replace(" ", "T")
            ts = dt.datetime.fromisoformat(raw).replace(tzinfo=dt.timezone.utc)
            out.append(
                {
                    "t": int(ts.timestamp()),
                    "o": round(float(row["open"]), 3),
                    "h": round(float(row["high"]), 3),
                    "l": round(float(row["low"]), 3),
                    "c": round(float(row["close"]), 3),
                }
            )
    return out


def merge(a: list[dict], b: list[dict]) -> list[dict]:
    by = {r["t"]: r for r in a}
    for r in b:
        by[r["t"]] = r
    return [by[k] for k in sorted(by)]


def coverage(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "start": None, "end": None}
    return {
        "n": len(rows),
        "start": dt.datetime.utcfromtimestamp(rows[0]["t"]).isoformat() + "Z",
        "end": dt.datetime.utcfromtimestamp(rows[-1]["t"]).isoformat() + "Z",
    }


def fetch_daily_m1(max_files: int = 24) -> int:
    """Only missing days. Stop after max_files network calls so a run stays cheap."""
    got = 0
    for day in trading_days(START, END):
        if got >= max_files:
            break
        origin = dt.datetime(day.year, day.month, day.day)
        for side in ("BID", "ASK"):
            path = DUKA / "m1" / f"{day.isoformat()}_{side}.bi5"
            if path.exists() and path.stat().st_size > 80:
                continue
            url = (
                f"{HOST}/XAUUSD/{day.year}/{day.month - 1:02d}/"
                f"{day.day:02d}/{side}_candles_min_1.bi5"
            )
            code = curl(url, path)
            print(f"m1 {day} {side} {code} {path.stat().st_size if path.exists() else 0}")
            got += 1
            if code == 503:
                time.sleep(8)
            else:
                time.sleep(0.25)
    return got


def m1_from_cache() -> tuple[list[dict], list[dict]]:
    bid: list[dict] = []
    ask: list[dict] = []
    folder = DUKA / "m1"
    if not folder.exists():
        return bid, ask
    for path in sorted(folder.glob("*.bi5")):
        if path.stat().st_size < 80:
            continue
        day_s, side = path.stem.rsplit("_", 1)
        origin = dt.datetime.fromisoformat(day_s)
        try:
            recs = parse_bi5(lzma.decompress(path.read_bytes()), origin)
        except Exception:
            continue
        (bid if side == "BID" else ask).extend(recs)
    return bid, ask


def cached_m1_days() -> int:
    folder = DUKA / "m1"
    if not folder.exists():
        return 0
    return len({p.stem.split("_")[0] for p in folder.glob("*.bi5") if p.stat().st_size > 80})


def main() -> None:
    DUKA.mkdir(parents=True, exist_ok=True)
    gh = pull_github()

    existing: list[dict] = []
    exist_m5: list[dict] = []
    exist_m15: list[dict] = []
    src_mtf = MTF if MTF.exists() else BOOKS_MTF
    if src_mtf.exists():
        try:
            pack = json.loads(src_mtf.read_text())
            existing = pack.get("tfs", {}).get("H1") or []
            exist_m5 = pack.get("tfs", {}).get("M5") or []
            exist_m15 = pack.get("tfs", {}).get("M15") or []
        except Exception:
            pass

    bid_h1: list[dict] = []
    ask_h1: list[dict] = []
    have_months = {dt.datetime.utcfromtimestamp(r["t"]).strftime("%Y-%m") for r in existing}
    for y, m in month_span(START, dt.date(END.year, END.month, 1)):
        if f"{y}-{m:02d}" in have_months and not (y == END.year and m == END.month):
            print(f"h1 {y}-{m:02d} cache-json {sum(1 for r in existing if dt.datetime.utcfromtimestamp(r['t']).month==m and dt.datetime.utcfromtimestamp(r['t']).year==y)}")
            continue
        bid_h1.extend(fetch_month(y, m, "BID", "hour_1"))
        ask_h1.extend(fetch_month(y, m, "ASK", "hour_1"))
        time.sleep(0.2)

    h1 = merge(existing, mid_join(bid_h1, ask_h1))
    h4 = resample(h1, 4 * 3600)
    d1 = resample(h1, 24 * 3600)

    m5 = merge(exist_m5, csv_bars(GH_DIR / GH_FILES["M5"]))
    m15 = merge(exist_m15, resample(m5, 15 * 60) if m5 else [])

    fetched = fetch_daily_m1(max_files=16)
    m1_bid, m1_ask = m1_from_cache()
    m1_mid = mid_join(m1_bid, m1_ask)
    if m1_mid:
        m5 = merge(m5, resample(m1_mid, 5 * 60))
        m15 = merge(m15, resample(m1_mid, 15 * 60))
        h1 = merge(h1, resample(m1_mid, 3600))
        h4 = resample(h1, 4 * 3600)
        d1 = resample(h1, 24 * 3600)

    tfs = {
        "M5": m5,
        "M15": m15,
        "H1": h1,
        "H4": h4,
        "D1": d1,
    }
    payload = json.dumps(
        {
            "source": "cache-first: Duka monthly Bid/Ask H1 + GitHub bulk M1/M5 + daily M1 gaps",
            "start": START.isoformat(),
            "end": END.isoformat(),
            "tfs": tfs,
        },
        separators=(",", ":"),
    )
    MTF.parent.mkdir(parents=True, exist_ok=True)
    MTF.write_text(payload, encoding="utf-8")
    BOOKS.mkdir(parents=True, exist_ok=True)
    BOOKS_MTF.write_text(payload, encoding="utf-8")

    wanted = list(trading_days(START, END))
    have = cached_m1_days()
    manifest = {
        "updated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "policy": "cache-first, bulk-first, daily M1 only for gaps, max 16 files/run",
        "github": gh,
        "m1_days_cached": have,
        "m1_days_wanted": len(wanted),
        "m1_this_run": fetched,
        "books": {tf: coverage(rows) for tf, rows in tfs.items()},
        "mtf_bytes": MTF.stat().st_size,
        "repo": "Sai310421/xauusd-duka-feed",
        "note": "M1 stays on disk. App reads M5/M15/H1/H4/D1 only. Each run fills the next gaps.",
    }
    BOOKS.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    PUBLIC_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("updated", "books", "m1_days_cached", "m1_days_wanted", "m1_this_run")}, indent=2))


if __name__ == "__main__":
    main()
