#!/usr/bin/env python3
"""
Modelled backtest: cash-secured short puts on single stocks, weekly vs monthly.

WHAT IS REAL: daily closing prices (Yahoo, split-adjusted), expiry dates, settlement.
WHAT IS MODELLED: option premiums. No free historical option-price data exists for these
names, so premiums are Black-Scholes prices using implied vol = K_IV x trailing 20-day
realised vol, then cut by a bid/ask haircut. Two IV assumptions bracket reality:
  - LOW  (K_IV=1.0): no volatility risk premium, no put skew  -> premiums understated
  - HIGH (K_IV=1.3): generous premium for OTM put skew + VRP -> premiums may be overstated
The truth for 0.20-delta single-stock puts is usually somewhere in between [not verified
per name]. Treat results as a range, not a number.

Rules (identical for every ticker, fixed in advance):
  - Capital $500,000 split equally into sleeves (one per ticker). A sleeve that has no
    price history yet (e.g. PLTR before its 2020 listing) sits in cash earning nothing.
  - Sell the put whose Black-Scholes delta is -0.20 (roughly 1-in-5 chance of finishing
    in the money under the model).
  - WEEKLY: sell at the close of the last trading day of each week, expiring at the close
    of the last trading day of the following week.
  - MONTHLY: sell at the close of each standard monthly expiry (third Friday, or the prior
    trading day if that's a holiday), expiring at the next one.
  - Size: sleeve cash (capped at the sleeve's remaining equity after any losses, so it is
    never levered) / strike = shares covered (fractional; real contracts are 100-share
    lots, so real results are lumpier). Fully cash-secured, no leverage, no margin.
  - Held to expiry, no early management. If it finishes in the money it is settled at the
    expiry close (equivalent to assignment then selling the shares at that close). No wheel.
  - Mark-to-market daily at the model price to measure drawdowns honestly.
  - Ignored: interest on the collateral, dividends, commissions, early assignment, and
    earnings-date IV spikes (a real source of both premium and gap losses).
"""
import json, math, sys, csv, os, statistics
from datetime import datetime, timezone, date, timedelta

CAPITAL = 500_000
TARGET_DELTA = -0.20
RATE = 0.02
HAIRCUT = 0.08          # sell at 8% below model mid (bid/ask on OTM single-stock puts)
RV_WINDOW = 20
TODAY_CUTOFF = "2026-09-23"   # drop the in-progress session
# (K_IV weekly, K_IV monthly, label, bid/ask haircut)
# CAL = calibrated so SPY at-the-money puts + T-bill interest reproduce Cboe's REAL indexes over
# 2016-2026: WPUT (weekly) needs 0.93, PUT (monthly) needs 1.12, with a 2% spread cost.
SCENARIOS = [(1.0, 1.0, "LOW", 0.08), (0.93, 1.12, "CAL", 0.02), (1.3, 1.3, "HIGH", 0.08)]
if os.environ.get("PUTS_SCEN"):
    SCENARIOS = [(float(a), float(b), c, float(d)) for a, b, c, d in (x.split(":") for x in os.environ["PUTS_SCEN"].split(","))]


def N(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def Ninv(p):
    # Acklam-free: bisection is plenty here
    lo, hi = -10, 10
    for _ in range(100):
        m = (lo + hi) / 2
        if N(m) < p:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


D1_TARGET = Ninv(1 + TARGET_DELTA)   # put delta = N(d1) - 1


def bs_put(S, K, sig, T, r=RATE):
    if T <= 0:
        return max(K - S, 0.0)
    sig = max(sig, 1e-4)
    d1 = (math.log(S / K) + (r + sig * sig / 2) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    return K * math.exp(-r * T) * N(-d2) - S * N(-d1)


def strike_for_delta(S, sig, T, r=RATE):
    return S * math.exp(-(D1_TARGET * sig * math.sqrt(T)) + (r + sig * sig / 2) * T)


def load(t, folder):
    rows = json.load(open(os.path.join(folder, f"px_{t}.json")))
    out = []
    for ts, c in rows:
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        if d.isoformat() < TODAY_CUTOFF:
            out.append((d, float(c)))
    return out


def third_friday(y, m):
    d = date(y, m, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d + timedelta(days=14)


def expiry_schedule(dates, kind):
    """Return list of trading-day indices used as roll points."""
    idx = []
    if kind == "weekly":
        for i, d in enumerate(dates):
            nxt = dates[i + 1] if i + 1 < len(dates) else None
            if nxt is None or nxt.isocalendar()[1] != d.isocalendar()[1]:
                idx.append(i)
    else:
        dset = {d: i for i, d in enumerate(dates)}
        seen = set()
        for d in dates:
            key = (d.year, d.month)
            if key in seen:
                continue
            seen.add(key)
            tf = third_friday(*key)
            while tf not in dset and tf > date(d.year, d.month, 1):
                tf -= timedelta(days=1)       # holiday -> prior trading day
            if tf in dset:
                idx.append(dset[tf])
    return idx


def run_sleeve(px, cal, kind, k_iv, sleeve_cash):
    """px: dict date->close for this ticker. cal: master trading calendar (SPY dates)."""
    dates = [d for d in cal if d in px]
    closes = [px[d] for d in dates]
    rolls = expiry_schedule(dates, kind)
    roll_set = set(rolls)
    daily = {d: 0.0 for d in cal}           # cumulative P&L (realised + open MTM) by date
    trades = []
    realised = 0.0
    pos = None
    for i, d in enumerate(dates):
        # settle
        if pos and i == pos["exp_i"]:
            S = closes[i]
            intrinsic = max(pos["K"] - S, 0.0)
            pnl = (pos["prem"] - intrinsic) * pos["sh"]
            realised += pnl
            trades.append({**{k: pos[k] for k in ("open", "K", "S0", "sig", "prem", "sh")},
                           "expiry": d.isoformat(), "S_T": S, "itm": intrinsic > 0,
                           "premium_$": pos["prem"] * pos["sh"], "pnl_$": pnl,
                           "pnl_pct_of_sleeve": pnl / sleeve_cash * 100})
            pos = None
        # open at roll points
        if pos is None and i in roll_set and i >= RV_WINDOW + 1 and sleeve_cash + realised > 0:
            nxt = next((j for j in rolls if j > i), None)
            if nxt is not None:
                rets = [math.log(closes[j] / closes[j - 1]) for j in range(i - RV_WINDOW + 1, i + 1)]
                rv = statistics.pstdev(rets) * math.sqrt(252)
                sig = k_iv * rv
                T = (nxt - i) / 252
                S = closes[i]
                K = strike_for_delta(S, sig, T)
                prem = bs_put(S, K, sig, T) * (1 - HAIRCUT)
                pos = {"open": d.isoformat(), "open_i": i, "exp_i": nxt, "K": K, "S0": S,
                       "sig": sig, "prem": prem, "sh": min(sleeve_cash, sleeve_cash + realised) / K}
        # mark
        mtm = 0.0
        if pos:
            T_rem = (pos["exp_i"] - i) / 252
            mid = bs_put(closes[i], pos["K"], pos["sig"], T_rem)
            mtm = (pos["prem"] - mid) * pos["sh"]
        daily[d] = realised + mtm
    # carry forward across calendar gaps (before listing = 0)
    last = 0.0
    for d in cal:
        if d in px:
            last = daily[d]
        else:
            daily[d] = last
    return daily, trades


def stats(equity_by_date, cal, start_cap):
    eq = [equity_by_date[d] for d in cal]
    # monthly realised-ish P&L from equity (MTM) month-end to month-end
    months = {}
    for d, e in zip(cal, eq):
        months[(d.year, d.month)] = e
    keys = sorted(months)
    prev = start_cap
    mp = []
    for k in keys:
        mp.append((f"{k[0]}-{k[1]:02d}", months[k] - prev))
        prev = months[k]
    vals = [v for _, v in mp]
    peak, mdd, mdd_at, peak_at = -1e18, 0.0, None, None
    cur_peak_at = cal[0]
    for d, e in zip(cal, eq):
        if e > peak:
            peak, cur_peak_at = e, d
        dd = (e - peak) / peak
        if dd < mdd:
            mdd, mdd_at, peak_at = dd, d, cur_peak_at
    years = (cal[-1] - cal[0]).days / 365.25
    cagr = (eq[-1] / start_cap) ** (1 / years) - 1
    worst = sorted(mp, key=lambda x: x[1])[:5]
    return {
        "months": len(vals), "avg_month_$": statistics.mean(vals), "median_month_$": statistics.median(vals),
        "stdev_month_$": statistics.pstdev(vals), "pct_months_negative": sum(v < 0 for v in vals) / len(vals) * 100,
        "worst_months": worst, "max_drawdown_pct": mdd * 100,
        "mdd_peak": peak_at.isoformat() if peak_at else None, "mdd_trough": mdd_at.isoformat() if mdd_at else None,
        "cagr_pct": cagr * 100, "end_equity": eq[-1],
    }, mp


def main(folder=".", tickers=("NVDA", "PLTR", "TSM", "MRVL", "MSFT"), out="bt_out"):
    os.makedirs(out, exist_ok=True)
    spy = load("SPY", folder)
    cal = [d for d, _ in spy]
    pxs = {t: dict(load(t, folder)) for t in tickers}
    sleeve = CAPITAL / len(tickers)
    summary = {}
    for kind in ("weekly", "monthly"):
        for k_w, k_m, label, hc in SCENARIOS:
            global HAIRCUT
            HAIRCUT = hc
            k_iv = k_w if kind == "weekly" else k_m
            tot = {d: CAPITAL for d in cal}
            all_trades = []
            for t in tickers:
                daily, trades = run_sleeve(pxs[t], cal, kind, k_iv, sleeve)
                for d in cal:
                    tot[d] += daily[d]
                for tr in trades:
                    tr["ticker"] = t
                all_trades += trades
            st, mp = stats(tot, cal, CAPITAL)
            n = len(all_trades)
            st["trades"] = n
            st["pct_trades_itm"] = sum(t["itm"] for t in all_trades) / n * 100
            st["avg_premium_per_trade_$"] = statistics.mean(t["premium_$"] for t in all_trades)
            st["worst_trade"] = min(all_trades, key=lambda t: t["pnl_$"])
            summary[f"{kind}_{label}"] = st
            with open(os.path.join(out, f"trades_{kind}_{label}.csv"), "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(all_trades[0].keys()))
                w.writeheader(); w.writerows(all_trades)
            with open(os.path.join(out, f"monthly_{kind}_{label}.csv"), "w", newline="") as f:
                w = csv.writer(f); w.writerow(["month", "pnl_$"]); w.writerows([(m, round(v, 2)) for m, v in mp])
            with open(os.path.join(out, f"equity_{kind}_{label}.csv"), "w", newline="") as f:
                w = csv.writer(f); w.writerow(["date", "equity_$"]); w.writerows([(d.isoformat(), round(tot[d], 2)) for d in cal])
    # benchmark: buy & hold same basket, equal $ per sleeve at each ticker's first date, idle cash until then
    bh = {d: 0.0 for d in cal}
    for t in tickers:
        px = pxs[t]
        first = min(px)
        sh = sleeve / px[first]
        last = sleeve
        for d in cal:
            if d in px and d >= first:
                last = sh * px[d]
            bh[d] += last
    st, mp = stats(bh, cal, CAPITAL)
    summary["buy_and_hold_basket"] = st
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1, default=str)
    return summary


if __name__ == "__main__":
    tick = sys.argv[1].split(",") if len(sys.argv) > 1 else ("NVDA", "PLTR", "TSM", "MRVL", "MSFT")
    outdir = sys.argv[2] if len(sys.argv) > 2 else "bt_out"
    s = main(".", tick, outdir)
    for k, v in s.items():
        print(f"\n== {k}")
        for kk, vv in v.items():
            if kk in ("worst_trade",):
                vv = {x: (round(y, 2) if isinstance(y, float) else y) for x, y in vv.items()}
            elif isinstance(vv, float):
                vv = round(vv, 2)
            elif kk == "worst_months":
                vv = [(m, round(x)) for m, x in vv]
            print(f"  {kk}: {vv}")
