# xauusd-duka-feed

Cache-first XAUUSD feed. **Each GitHub Actions run fills the next missing days.**

- Hourly cron + `workflow_dispatch` + `repository_dispatch` (`duka-sync`)
- Monthly H1 Bid/Ask first, GitHub bulk M5/M1 second, daily M1 only for gaps
- M1 BI5 stays in Actions cache. Books (`M5/M15/H1/H4/D1`) are committed
- Re-runs skip hits. Coverage only moves forward

Manifest: `books/duka-sync.json`  
Books: `books/xauusd_mtf.json`
