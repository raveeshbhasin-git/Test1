"""Explosive-move study over the S&P 500/400 universe.

1. Detect "episodes": any stretch where a stock gained >= 80% within 63
   trading days (~3 months). Tag each as `breakout` (came from strength /
   a flat base, like Dell 2023 or Micron 2025) or `rebound` (prior-year
   return < -35%, i.e. a crash bounce like March 2020).
2. Compute observable daily features for every ticker-day: momentum,
   distance to 52-week high, volume ratio, MA structure, RSI, relative
   strength, volatility tightness.
3. Backtest candidate entry signals against a same-universe baseline,
   measuring forward 63-day point return and the probability that the
   stock tacks on another +20/40/80% within 63 days.
4. Score today's tape and write a current screen.

Outputs (in DATA_DIR): episodes.csv, backtest.json, current_screen.csv
"""
import gc
import json
import os

import numpy as np
import pandas as pd

DATA_DIR = os.environ.get("MOMENTUM_DATA", "./data")
W = 63  # forward window in trading days
EPISODE_GAIN = 0.8
MIN_PRICE = 3.0
MIN_DOLLAR_VOL = 5e6


def load_matrices() -> tuple[pd.DataFrame, pd.DataFrame]:
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
    return close, vol


def detect_episodes(close: pd.DataFrame, fwdmax: pd.DataFrame) -> pd.DataFrame:
    episodes = []
    for t in close.columns:
        c = close[t].dropna()
        if len(c) < 300:
            continue
        hot = (fwdmax[t].reindex(c.index) >= EPISODE_GAIN).values
        idx = c.index
        in_ep, last_end, start_i = False, None, 0
        for i, h in enumerate(hot):
            if h and not in_ep:
                start_i, in_ep = i, True
            elif not h and in_ep:
                in_ep = False
                s = start_i
                seg = c.iloc[s : min(s + W + 1, len(c))]
                pk = int(seg.values.argmax())
                if last_end is not None and (idx[s] - last_end).days < 180:
                    last_end = idx[min(s + pk, len(c) - 1)]
                    continue
                p1y = c.iloc[s] / c.iloc[max(0, s - 252)] - 1 if s >= 126 else np.nan
                episodes.append(
                    dict(ticker=t, start=str(idx[s].date()), days_to_peak=pk,
                         gain=round(float(seg.iloc[pk] / c.iloc[s] - 1), 3),
                         prior1y=round(float(p1y), 3) if p1y == p1y else None,
                         price=round(float(c.iloc[s]), 2))
                )
                last_end = idx[min(s + pk, len(c) - 1)]
    ep = pd.DataFrame(episodes)
    ep = ep[ep.price > MIN_PRICE].reset_index(drop=True)
    ep["regime"] = np.where(ep.prior1y.fillna(0) < -0.35, "rebound", "breakout")
    return ep


def main() -> None:
    close, vol = load_matrices()

    fwdmax = (close[::-1].rolling(W, min_periods=5).max()[::-1].shift(-1) / close - 1).astype("float32")
    fwd63 = (close.shift(-W) / close - 1).astype("float32")
    fwdmin = (close[::-1].rolling(W, min_periods=5).min()[::-1].shift(-1) / close - 1).astype("float32")

    ep = detect_episodes(close, fwdmax)
    ep.to_csv(f"{DATA_DIR}/episodes.csv", index=False)
    print("episodes", len(ep), ep.regime.value_counts().to_dict())

    # ---- features (all observable on the day) ----
    dret = close.pct_change(fill_method=None)
    r5 = close.pct_change(5, fill_method=None).astype("float32")
    r21 = close.pct_change(21, fill_method=None).astype("float32")
    r63 = close.pct_change(63, fill_method=None).astype("float32")
    r252 = close.pct_change(252, fill_method=None).astype("float32")
    hi252 = close.rolling(252).max()
    pct_hi = (close / hi252 - 1).astype("float32")
    new_hi = (close >= hi252 * 0.999).rolling(20).max().astype("float32")
    del hi252
    volr = (vol.rolling(21).mean() / vol.rolling(126).mean()).astype("float32")
    tight = (dret.rolling(63).std() / dret.rolling(252).std()).astype("float32")
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    golden = ma50 > ma200
    above50 = close > ma50
    del ma50, ma200
    uni63 = r63.mean(axis=1)
    rs63 = r63.sub(uni63, axis=0).astype("float32")
    dv = (close * vol).rolling(21).mean().astype("float32")
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
    rsi = (100 - 100 / (1 + up / dn)).astype("float32")
    del d, up, dn
    gc.collect()

    valid = (close > MIN_PRICE) & (dv > MIN_DOLLAR_VOL) & r252.notna()
    valid.iloc[-W:] = False  # forward window incomplete
    valid.iloc[:280] = False  # features incomplete

    # ---- candidate signals ----
    signals = {
        # new 52w high recently + volume up + strong RS + uptrend
        "S1_hi_vol_rs": (new_hi > 0) & (volr > 1.3) & (rs63 > 0.10) & golden,
        # heavy sustained volume with a 10%+ month
        "S2_vol_shock": (volr > 2.0) & (r21 > 0.10),
        # quiet tight base breaking to highs (classic VCP-style)
        "S3_tight_break": (new_hi > 0) & (tight < 1.0) & golden,
        # oversold dip inside a strong uptrend
        "S4_dip_uptrend": golden & (r252 > 0.3) & (pct_hi > -0.25) & (pct_hi < -0.10) & (rsi < 35),
        # momentum ignition: violent week on heavy volume, trend intact
        "S5_ignition": (r5 > 0.12) & (volr > 1.5) & golden,
        "baseline": valid.copy(),
    }

    def stats(mask: pd.DataFrame) -> dict | None:
        m = (mask & valid).values
        f, fm, fl = fwd63.values[m], fwdmax.values[m], fwdmin.values[m]
        f, fm, fl = f[np.isfinite(f)], fm[np.isfinite(fm)], fl[np.isfinite(fl)]
        if len(f) < 50:
            return None
        return dict(n=int(len(f)), mean63=round(float(np.mean(f)), 4), med63=round(float(np.median(f)), 4),
                    p_max20=round(float(np.mean(fm >= 0.2)), 4), p_max40=round(float(np.mean(fm >= 0.4)), 4),
                    p_max80=round(float(np.mean(fm >= 0.8)), 4), p_dd20=round(float(np.mean(fl <= -0.2)), 4))

    res = {k: stats(v) for k, v in signals.items()}

    # per-year robustness for the ignition signal
    yr = {}
    sig = signals["S5_ignition"]
    for y in range(2016, close.index[-1].year + 1):
        m = close.index.year == y
        s, v = (sig & valid).values[m], valid.values[m]
        f, fx = fwd63.values[m], fwdmax.values[m]
        fs, fb = f[s], f[v]
        xs, xb = fx[s], fx[v]
        fs, fb = fs[np.isfinite(fs)], fb[np.isfinite(fb)]
        xs, xb = xs[np.isfinite(xs)], xb[np.isfinite(xb)]
        if len(fs) > 20:
            yr[y] = dict(n=int(s.sum()), sig_mean63=round(float(np.mean(fs)), 3),
                         base_mean63=round(float(np.mean(fb)), 3),
                         sig_pmax40=round(float(np.mean(xs >= 0.4)), 3),
                         base_pmax40=round(float(np.mean(xb >= 0.4)), 3))
    json.dump(dict(signals=res, ignition_per_year=yr), open(f"{DATA_DIR}/backtest.json", "w"), indent=1)
    print(json.dumps(res, indent=1))

    # ---- current screen ----
    cur = pd.DataFrame({
        "price": close.iloc[-1], "r5": r5.iloc[-1], "r21": r21.iloc[-1], "r63": r63.iloc[-1],
        "r252": r252.iloc[-1], "pct_from_52wh": pct_hi.iloc[-1], "new_hi_20d": new_hi.iloc[-1],
        "volr": volr.iloc[-1], "tightness": tight.iloc[-1], "rsi14": rsi.iloc[-1],
        "golden": golden.iloc[-1].astype(int), "above50": above50.iloc[-1].astype(int),
        "rs63": rs63.iloc[-1], "dv_musd": (dv.iloc[-1] / 1e6).round(1),
    })
    cur["ignition_now"] = ((cur.r5 > 0.12) & (cur.volr > 1.5) & (cur.golden == 1)).astype(int)
    cur = cur[(cur.price > MIN_PRICE) & (cur.dv_musd > 5)]
    cur.round(3).to_csv(f"{DATA_DIR}/current_screen.csv")
    print("asof", close.index[-1].date(), "screened", len(cur), "ignition now:", int(cur.ignition_now.sum()))


if __name__ == "__main__":
    main()
