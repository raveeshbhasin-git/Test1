"""Download ~11 years of daily OHLCV for the S&P 500 + S&P 400 universe via yfinance.

Writes per-chunk pickles to DATA_DIR/px/chunk{i}.pkl (Close/Volume/High,
float32, dividend/split adjusted). Re-runnable: existing chunks are skipped.
"""
import io
import json
import os

import pandas as pd
import requests
import yfinance as yf

DATA_DIR = os.environ.get("MOMENTUM_DATA", "./data")
START = "2015-01-01"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def get_tickers(url: str) -> list[str]:
    html = requests.get(url, headers=UA, timeout=30).text
    for t in pd.read_html(io.StringIO(html)):
        for c in ("Symbol", "Ticker", "Ticker symbol"):
            if c in t.columns:
                return [str(s).replace(".", "-").strip() for s in t[c].dropna()]
    return []


def main() -> None:
    os.makedirs(f"{DATA_DIR}/px", exist_ok=True)
    sp500 = get_tickers("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    sp400 = get_tickers("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies")
    uni = sorted({t for t in set(sp500) | set(sp400) if t and len(t) <= 6 and t[0].isalpha()})
    json.dump(uni, open(f"{DATA_DIR}/universe.json", "w"))
    print("universe", len(uni))

    chunks = [uni[i : i + 150] for i in range(0, len(uni), 150)]
    for ci, ch in enumerate(chunks):
        f = f"{DATA_DIR}/px/chunk{ci}.pkl"
        if os.path.exists(f):
            continue
        d = yf.download(ch, start=START, auto_adjust=True, progress=False, threads=True)
        d[["Close", "Volume", "High"]].astype("float32").to_pickle(f)
        print("chunk", ci, d.shape)
    print("DONE")


if __name__ == "__main__":
    main()
