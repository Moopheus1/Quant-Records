# money-dashboard

Daily options-flow feed for the local Money Rotation Dashboard (Windows `update_money_rotation_dashboard.py`).

- `options_flow.json` - per-ETF large-trade options flow (Rallies, latest session). Rewritten by a Claude
  scheduled task Tue-Sat ~06:17 SGT (after the US close, before the next pre-market).
- `build_options_json.py` - turns saved Rallies `get_options_flow_summary_for_ticker` results into that JSON.

The Windows updater downloads the raw JSON at 07:30 SGT and falls back to its local copy if the download fails.
Display / cross-check only - it is not part of the dashboard's theme score.
