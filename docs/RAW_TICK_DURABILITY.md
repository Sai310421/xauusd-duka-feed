# Raw Tick durable storage contract

This repository must not report Dukascopy Raw Tick acquisition as complete until **durable storage and independent restore** have been verified.

1. Discover existing tick files in local disks, Actions artifacts, releases, external storage and caches. Cache is recovery-only, not durable storage.
2. Store original Bid/Ask raw tick bytes in an access-controlled, persistent destination with sufficient quota. Never commit large data blindly to Git.
3. Run `python scripts/audit_rawtick_storage.py /path/to/ticks --output rawtick-manifest.json` and retain its SHA-256 inventory separately from the data.
4. Restore to a fresh workspace, rehash all files, parse timestamps and Bid/Ask, check duplicate ticks, gaps, chronological order, and source coverage. Record failures rather than interpolating missing data.
5. Track expected vs acquired hours and exact gaps. Existing reports mention 161 failed hours out of 504; **do not assume coverage is complete**.
6. Only after a verified independent restore and data-quality validation may a run be labelled `raw_tick_verified`. A trade-results CSV or OHLC file is not Raw Tick.
7. Never enable live orders automatically. Keep WR1–WR5 frozen. Never bypass required CI to merge.

The audit script produces an **inventory only**: its checksums do not prove data authenticity, completeness or durable backup. Record storage provider, object keys, restore test, timestamp and evidence in a separate operational ledger. Do not place credentials in the repository.
