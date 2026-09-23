# Short-put income test — weekly vs monthly, $500k, stocks vs ETFs

Question: can cash-secured puts produce **stable income** on USD 500,000?

## 1. Forward paper tracker (real prices) — `put_tracker.py`
Runs daily via `.github/workflows/puts-tracker.yml`, for two baskets, each with two independent
$500k paper portfolios (WEEKLY and MONTHLY), split equally across 5 names:
- `stocks`: NVDA, PLTR, TSM, MRVL, MSFT
- `etfs`: SPY, QQQ, IWM, TQQQ, EFA (EFA = iShares MSCI EAFE; VOO left out: same index as SPY, thin options)

Rules: sell the put with delta closest to -0.20 at the **real end-of-day bid** (Cboe delayed
quotes) minus $0.65/contract. Weekly = nearest expiry 4-10 days out; monthly = third-Friday
expiry 21-50 days out. Whole contracts, fully cash-secured, never levered. Held to expiry;
in-the-money puts are settled at the official close (assigned + sold; no wheel). Marked to mid daily.
Output in `data/<basket>/`: `ledger.csv`, `equity.csv`, `summary.csv`, `closes.csv`.
Not modelled: interest on the idle cash (real accounts earn roughly the T-bill rate on top),
early assignment, and whether next-morning fills match the end-of-day bid.

## 2. Real index evidence (not modelled) — Cboe PUT and WPUT
Cboe's own indexes sell at-the-money S&P 500 puts fully collateralised in T-bills
(`backtest/PUT_History.csv`, `backtest/WPUT_History.csv`). Sep 2016 → Sep 2026, per $500k:

| | Return/yr (incl. T-bills) | Months negative | Worst month | Max drawdown | Longest below prior peak |
|---|---|---|---|---|---|
| PUT (monthly) | 8.6% | 23% | -13.4% (Mar 2020) | -28.9% | 436 days |
| WPUT (weekly) | 3.9% | 32% | -10.3% (Feb 2020) | -25.9% | 1,314 days |
| SPY (price only) | 13.6% | 31% | -13.0% | -34.1% | 746 days |

## 3. Modelled 10-year backtest (0.20-delta puts) — `put_backtest.py`, results in `backtest/`
Prices are real (Yahoo daily closes); **premiums are modelled** (Black-Scholes on trailing 20-day
realised vol x K, minus a bid/ask haircut). Three settings:
- LOW: K=1.0, 8% haircut
- CAL: K=0.93 weekly / 1.12 monthly, 2% haircut. Calibrated so the model reproduces the real
  WPUT/PUT indexes on SPY. It ignores put skew, so it probably under-prices out-of-the-money
  index puts.
- HIGH: K=1.3, 8% haircut

Return per year / months negative / max drawdown (premium only, excluding T-bill interest):

| $500k, 2016-09 → 2026-09 | Weekly LOW | Weekly CAL | Weekly HIGH | Monthly LOW | Monthly CAL | Monthly HIGH | Buy & hold |
|---|---|---|---|---|---|---|---|
| Stocks | 4.4% / 33% / -11% | 2.3% / 36% / -16% | 12.9% / 12% / -5% | 4.7% / 28% / -14% | 7.6% / 23% / -12% | 9.5% / 20% / -10% | 44.8% / 37% / -57% |
| ETFs | -3.1% / 38% / -39% | -5.1% / 40% / -47% | 7.4% / 24% / -23% | -1.9% / 26% / -37% | 1.6% / 24% / -32% | 4.0% / 19% / -30% | 24.2% / 34% / -63% |
| INTC+PYPL (control) | -4.7% / 45% / -51% | -7.6% / 50% / -62% | 9.5% / 26% / -11% | -6.9% / 40% / -63% | -1.7% / 35% / -46% | 2.9% / 30% / -25% | 8.7% / 48% / -79% |

Real-bid check on 2026-09-22 (one day only): stock bids were ~0.9x the LOW model; SPY/QQQ
monthly bids were ~1.8-1.9x LOW (i.e. near HIGH), weekly ~1.0-1.1x LOW. EFA weekly bid was
$0.02 with a 197% spread — effectively untradeable at 0.20 delta.

The stock basket is today's winners (survivorship bias); the INTC+PYPL control shows the same
rules on names that declined. Treat every modelled number as a range, not a forecast.
