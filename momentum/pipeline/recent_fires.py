"""List ignition-signal fires from the last N sessions — the live watchlist feed.

A "fire" is the most recent day a ticker satisfied the ignition signal
(5-day return > 12%, 21-day volume > 1.5x its 126-day average, 50dma above
200dma). Backtest: after such a day, ~27% of names gained another +40%
within 63 trading days (vs 4.6% baseline).
"""
import gc
import os

import numpy as np
import pandas as pd

DATA_DIR = os.environ.get("MOMENTUM_DATA", "./data")
LOOKBACK = 40


def main() -> None:
    cs, vs = [], []
    i = 0
    while os.path.exists(f"{DATA_DIR}/px/chunk{i}.pkl"):
        p = pd.read_pickle(f"{DATA_DIR}/px/chunk{i}.pkl")
        cs.append(p["Close"])
        vs.append(p["Volume"])
        i += 1
    close = pd.concat(cs, axis=1)
    vol = pd.concat(vs, axis=1)
    close = close.loc[:, ~close.columns.duplicated()].sort_index()
    vol = vol.loc[:, ~vol.columns.duplicated()].sort_index()
    del cs, vs
    gc.collect()

    r5 = close.pct_change(5, fill_method=None)
    r63 = close.pct_change(63, fill_method=None)
    r252 = close.pct_change(252, fill_method=None)
    volr = vol.rolling(21).mean() / vol.rolling(126).mean()
    golden = close.rolling(50).mean() > close.rolling(200).mean()
    dv = (close * vol).rolling(21).mean()
    hi252 = close.rolling(252).max()
    rs63 = r63.sub(r63.mean(axis=1), axis=0)
    valid = (close > 3) & (dv > 5e6) & r252.notna()
    sig = (r5 > 0.12) & (volr > 1.5) & golden & valid

    fires = []
    recent = sig.iloc[-LOOKBACK:]
    for t in recent.columns:
        col = recent[t]
        if not col.any():
            continue
        d0 = col[col].index[-1]
        i = close.index.get_loc(d0)
        fires.append(dict(
            ticker=t, fired=str(d0.date()), days_ago=int(len(close.index) - 1 - i),
            r5_at_fire=round(float(r5[t].iloc[i]), 3), volr_at_fire=round(float(volr[t].iloc[i]), 2),
            price_now=round(float(close[t].iloc[-1]), 2),
            ret_since=round(float(close[t].iloc[-1] / close[t].loc[d0] - 1), 3),
            r63=round(float(r63[t].iloc[-1]), 3), r252=round(float(r252[t].iloc[-1]), 3),
            rs63=round(float(rs63[t].iloc[-1]), 3),
            pct_from_hi=round(float(close[t].iloc[-1] / hi252[t].iloc[-1] - 1), 3),
            dv_musd=round(float(dv[t].iloc[-1] / 1e6), 1),
        ))
    out = pd.DataFrame(fires).sort_values("fired", ascending=False)
    out.to_csv(f"{DATA_DIR}/ignition_recent.csv", index=False)
    print(out.to_string())


if __name__ == "__main__":
    main()
