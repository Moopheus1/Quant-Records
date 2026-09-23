#!/usr/bin/env python3
"""
Forward paper tracker: cash-secured short puts, WEEKLY vs MONTHLY, per basket (stocks / etfs).
Usage: put_tracker.py <basket> [data_dir]
Uses REAL end-of-day option quotes (Cboe delayed quotes, free, no login).

Two independent paper portfolios, each $500,000 of cash collateral, split equally across
TICKERS. Every run (daily, after the US close) is idempotent:
  1. Fetch each ticker's option chain snapshot; its session date D = last trade date.
  2. Settle any position whose expiry <= D at the underlying's official close on expiry
     (as if assigned and the shares sold at that close; no wheel). Loss = intrinsic x 100 x n.
  3. Mark open positions to the contract's mid (bid+ask)/2 -> daily equity and drawdown.
  4. For every (strategy, ticker) with no open position, sell the put with delta closest to
     -0.20 at the BID (conservative), minus $0.65/contract. Contracts = floor(sleeve cash
     / (strike x 100)); sleeve cash is capped at the sleeve's own equity, so never levered.
       WEEKLY : nearest expiry 4-10 calendar days out (targets 7)
       MONTHLY: standard monthly (third-Friday) expiry 21-50 days out (targets 35)
  5. Append rows to data/ledger.csv, data/equity.csv, and rewrite data/summary.csv.
Not modelled: interest on idle collateral, early assignment, intraday fills (fills are at the
end-of-day bid, which you may not get the next morning).
"""
import csv, json, math, os, re, sys, urllib.request
from datetime import date, datetime, timedelta, timezone

BASKETS = {
    "stocks": ["NVDA", "PLTR", "TSM", "MRVL", "MSFT"],
    "etfs": ["SPY", "QQQ", "IWM", "TQQQ", "EFA"],   # EFA = iShares MSCI EAFE; VOO omitted (same index as SPY, thin options)
}
BASKET = sys.argv[1] if len(sys.argv) > 1 else "stocks"
TICKERS = BASKETS[BASKET]
CAPITAL = 500_000
TARGET_DELTA = -0.20
FEE = 0.65
STRATS = {"weekly": (4, 10, 7, False), "monthly": (21, 50, 35, True)}   # min, max, target DTE, third-Friday only
DATA = sys.argv[2] if len(sys.argv) > 2 else f"puts/data/{BASKET}"
UA = {"User-Agent": "Mozilla/5.0"}
LEDGER_COLS = ["id", "strategy", "ticker", "open_date", "expiry", "strike", "contracts", "delta",
               "bid", "ask", "spread_pct", "iv", "underlying_at_open", "premium_net_$", "collateral_$",
               "earnings_before_expiry", "status", "close_date", "settle_px", "assignment_loss_$",
               "pnl_$"]
EQ_COLS = ["date", "strategy", "realised_$", "open_mtm_$", "equity_$", "collateral_in_use_$",
           "open_positions", "premium_collected_cum_$"]


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        return json.loads(r.read())


def read_csv(path, cols):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def parse_chain(t, d):
    out = {}
    for o in d["data"]["options"]:
        m = re.match(rf"^{t}(\d{{6}})P(\d{{8}})$", o["option"])
        if m:
            exp = datetime.strptime(m.group(1), "%y%m%d").date()
            out[(exp.isoformat(), int(m.group(2)) / 1000)] = o
    return out


def is_monthly(e):
    return e.weekday() == 4 and 15 <= e.day <= 21 or (e.weekday() == 3 and 16 <= e.day <= 20)


def yahoo_close(t, day):
    try:
        r = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=3mo&interval=1d")["chart"]["result"][0]
        for ts, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
            if c and datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() == day:
                return float(c)
    except Exception:
        pass
    return None


def bcc_close(t, day, cache={}):
    try:
        if "d" not in cache:
            cache["d"] = get_json("https://moopheus1.github.io/Basket-Command-Centre-2/data.json")
        for b in cache["d"]["tickers"].get(t, {}).get("bars", []):
            if datetime.fromtimestamp(b[0], tz=timezone.utc).date().isoformat() == day:
                return float(b[4])
    except Exception:
        pass
    return None


def next_earnings(t, cache={}):
    try:
        if "d" not in cache:
            cache["d"] = get_json("https://moopheus1.github.io/Basket-Command-Centre-2/data.json")
        v = cache["d"]["tickers"].get(t, {}).get("nextEarnings")
        return str(v)[:10] if v else ""
    except Exception:
        return ""


def main():
    os.makedirs(DATA, exist_ok=True)
    ledger = read_csv(f"{DATA}/ledger.csv", LEDGER_COLS)
    equity = read_csv(f"{DATA}/equity.csv", EQ_COLS)
    closes = {(r["ticker"], r["date"]): float(r["close"]) for r in read_csv(f"{DATA}/closes.csv", None)}

    chains, sessions, spot = {}, {}, {}
    for t in TICKERS:
        d = get_json(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{t}.json")
        sessions[t] = d["data"]["last_trade_time"][:10]
        spot[t] = float(d["data"]["close"] or d["data"]["current_price"])
        closes[(t, sessions[t])] = spot[t]
        chains[t] = parse_chain(t, d)
    D = max(sessions.values())
    if any(s != D for s in sessions.values()):
        print("WARNING: chains disagree on session date:", sessions)
    if any(r["date"] == D for r in equity):
        print(f"Session {D} already processed; nothing to do.")
        return
    Dd = date.fromisoformat(D)

    # 1) settle
    for r in ledger:
        if r["status"] == "open" and r["expiry"] <= D:
            t, e = r["ticker"], r["expiry"]
            px = closes.get((t, e)) or yahoo_close(t, e) or bcc_close(t, e)
            if px is None:
                print(f"Cannot find close for {t} on {e}; leaving open this run.")
                continue
            n, K = int(r["contracts"]), float(r["strike"])
            loss = max(K - px, 0.0) * 100 * n
            r["status"] = "assigned" if loss > 0 else "expired"
            r["close_date"], r["settle_px"] = e, round(px, 4)
            r["assignment_loss_$"] = round(loss, 2)
            r["pnl_$"] = round(float(r["premium_net_$"]) - loss, 2)

    def sleeve_realised(strat, t):
        return sum(float(r["pnl_$"]) for r in ledger if r["strategy"] == strat and r["ticker"] == t and r["status"] != "open")

    # 2) open new
    sleeve = CAPITAL / len(TICKERS)
    skipped = []
    for strat, (lo, hi, tgt, monthly_only) in STRATS.items():
        for t in TICKERS:
            if any(r["status"] == "open" and r["strategy"] == strat and r["ticker"] == t for r in ledger):
                continue
            ch = chains[t]
            exps = sorted({date.fromisoformat(e) for e, _ in ch})
            exps = [e for e in exps if lo <= (e - Dd).days <= hi and (not monthly_only or is_monthly(e))]
            if not exps:
                skipped.append(f"{strat}/{t}: no expiry in window"); continue
            e = min(exps, key=lambda x: abs((x - Dd).days - tgt)).isoformat()
            cands = [(K, o) for (ee, K), o in ch.items() if ee == e and o["bid"] and o["bid"] > 0 and o["delta"] < 0]
            if not cands:
                skipped.append(f"{strat}/{t}: no bids"); continue
            K, o = min(cands, key=lambda x: abs(x[1]["delta"] - TARGET_DELTA))
            cash = min(sleeve, sleeve + sleeve_realised(strat, t))
            n = int(cash // (K * 100))
            if n < 1:
                skipped.append(f"{strat}/{t}: 1 contract needs ${K*100:,.0f} > sleeve ${cash:,.0f}"); continue
            ne = next_earnings(t)
            ledger.append({
                "id": f"{strat[0].upper()}-{t}-{D}-{e}", "strategy": strat, "ticker": t, "open_date": D, "expiry": e,
                "strike": K, "contracts": n, "delta": round(o["delta"], 3), "bid": o["bid"], "ask": o["ask"],
                "spread_pct": round((o["ask"] - o["bid"]) / ((o["ask"] + o["bid"]) / 2) * 100, 1),
                "iv": round(o["iv"], 4), "underlying_at_open": spot[t],
                "premium_net_$": round(o["bid"] * 100 * n - FEE * n, 2), "collateral_$": round(K * 100 * n, 2),
                "earnings_before_expiry": "yes" if ne and D < ne <= e else "",
                "status": "open", "close_date": "", "settle_px": "", "assignment_loss_$": "", "pnl_$": ""})

    # 3) mark + equity rows
    for strat in STRATS:
        rows = [r for r in ledger if r["strategy"] == strat]
        realised = sum(float(r["pnl_$"]) for r in rows if r["status"] != "open")
        mtm, coll, nopen = 0.0, 0.0, 0
        for r in rows:
            if r["status"] != "open":
                continue
            o = chains[r["ticker"]].get((r["expiry"], float(r["strike"])))
            mid = ((o["bid"] + o["ask"]) / 2) if o else max(float(r["strike"]) - spot[r["ticker"]], 0)
            mtm += float(r["premium_net_$"]) - mid * 100 * int(r["contracts"])
            coll += float(r["collateral_$"]); nopen += 1
        prem = sum(float(r["premium_net_$"]) for r in rows)
        equity.append({"date": D, "strategy": strat, "realised_$": round(realised, 2), "open_mtm_$": round(mtm, 2),
                       "equity_$": round(CAPITAL + realised + mtm, 2), "collateral_in_use_$": round(coll, 2),
                       "open_positions": nopen, "premium_collected_cum_$": round(prem, 2)})

    # 4) summary
    summ = []
    for strat in STRATS:
        eq = [r for r in equity if r["strategy"] == strat]
        vals = [float(r["equity_$"]) for r in eq]
        peak, mdd = CAPITAL, 0.0
        for v in vals:
            peak = max(peak, v); mdd = min(mdd, (v - peak) / peak)
        closed = [r for r in ledger if r["strategy"] == strat and r["status"] != "open"]
        summ.append({"strategy": strat, "since": eq[0]["date"], "through": D, "sessions": len(eq),
                     "trades_closed": len(closed), "assigned": sum(r["status"] == "assigned" for r in closed),
                     "premium_collected_$": eq[-1]["premium_collected_cum_$"],
                     "realised_pnl_$": eq[-1]["realised_$"], "equity_$": eq[-1]["equity_$"],
                     "return_pct": round((vals[-1] / CAPITAL - 1) * 100, 3), "max_drawdown_pct": round(mdd * 100, 3),
                     "collateral_in_use_$": eq[-1]["collateral_in_use_$"]})

    write_csv(f"{DATA}/ledger.csv", LEDGER_COLS, ledger)
    write_csv(f"{DATA}/equity.csv", EQ_COLS, equity)
    write_csv(f"{DATA}/closes.csv", ["ticker", "date", "close"],
              [{"ticker": t, "date": d_, "close": c} for (t, d_), c in sorted(closes.items())])
    write_csv(f"{DATA}/summary.csv", list(summ[0].keys()), summ)
    print(f"session={D}")
    for s in summ:
        print(s)
    for s in skipped:
        print("skipped:", s)


if __name__ == "__main__":
    main()
