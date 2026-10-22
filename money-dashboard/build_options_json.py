"""
Build options_flow.json for the Money Rotation Dashboard from saved Rallies
get_options_flow_summary_for_ticker results.

Usage:  python build_options_json.py <input_dir> <output_json>

<input_dir> holds one or more files, each the raw JSON returned by Rallies
({"result":[{"ticker":..,"data":{..}}, ..]}). Standard library only.
Refuses to write if too few of the expected tickers came back (fetch failure).
"""
import json, sys, glob, os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

EXPECTED = ["SIL","GDX","IGV","BOTZ","QTUM","KWEB","SLX","WGMI","TAN","IWF","SMH","XLU","COPX","PHO","JETS",
 "WCLD","CIBR","XBI","ITA","PAVE","OIH","XLE","XRT","IYT","XLK","XLF","XLI","XLB","XLY","XLV","XLC","XLP",
 "XLRE","ICLN","GLD","SLV","SPY","QQQ","IWM"]

def rec_from(item):
    d = item["data"]
    if "overall" not in d:
        return dict(trades=0)
    s, p = d["sentiment_summary"], d["premium_totals"]
    return dict(date=d["data_date"], trades=d["overall"]["trade_count"],
                call_prem=p["call_premium"], put_prem=p["put_premium"],
                bull=s["bullish_premium"], bear=s["bearish_premium"], neutral=s["neutral_premium"],
                net=s["net_bullish_minus_bearish_premium"], iv=d["overall"]["premium_weighted_iv"])

def main(in_dir, out_path):
    recs = {}
    for f in sorted(glob.glob(os.path.join(in_dir, "*"))):
        try:
            payload = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            print("skip", f, e); continue
        for item in payload.get("result", []):
            recs[item["ticker"]] = rec_from(item)
    missing = [t for t in EXPECTED if t not in recs]
    if len(missing) > len(EXPECTED) * 0.4:
        sys.exit(f"ABORT: {len(missing)} of {len(EXPECTED)} tickers missing ({missing}); not writing {out_path}")
    # Session date = the date carrying the most trades (a pre-market fragment of the next day is tiny).
    weight = defaultdict(int)
    for r in recs.values():
        if r.get("trades"):
            weight[r["date"]] += r["trades"]
    as_of = max(weight, key=weight.get) if weight else None
    sgt = timezone(timedelta(hours=8))
    out = dict(as_of=as_of, fetched_at=datetime.now(sgt).isoformat(timespec="seconds"),
               source="Rallies get_options_flow_summary_for_ticker (min premium 25000, spreads excluded, latest session)",
               tickers=recs)
    json.dump(out, open(out_path, "w", encoding="utf-8"), indent=1)
    with_data = [t for t, r in recs.items() if r.get("trades")]
    off = [t for t in with_data if recs[t]["date"] != as_of]
    print(f"wrote {out_path}: as_of={as_of}, {len(recs)} tickers, {len(with_data)} with flow, "
          f"{len(recs)-len(with_data)} empty, missing={missing}, off-session={off}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
