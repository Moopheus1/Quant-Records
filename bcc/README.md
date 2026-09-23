# BCC tracker — forward test of Basket Command Centre 2 categories

Runs nightly via `.github/workflows/bcc-tracker.yml` (06:30 SGT Tue–Sat, plus manual "Run workflow").

Each run fetches `https://moopheus1.github.io/Basket-Command-Centre-2/data.json`, **drops any
incomplete intraday bar**, recomputes category membership for every session since 2026-09-10,
and measures 5/10/20-session forward returns.

Outputs in `data/`:
- `stats.csv` – per category: N, win rate, avg return, avg return vs SPY, % beating SPY, and
  avg return with a next-open entry minus 10 bps round-trip.
- `log.csv` / `BCC-tracker.xlsx` – every flagged ticker per session with forward returns.
- `membership/membership_YYYY-MM-DD.csv` – append-only record of each session's flags.

The original laptop log (computed from partial intraday bars, not used) is archived in Google Drive.

Caveats: raw price drift (no stops/sizing); consecutive sessions share tickers, so the
effective sample is much smaller than N; treat any category with N < 20 as insufficient.
Changing tickers.txt shifts the peer-relative Performance scores retroactively on rebuild —
the membership/ files are the as-logged record.
