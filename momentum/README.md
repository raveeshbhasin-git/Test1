# Explosive-Move Study & Momentum Watchlist

A study of "Dell/Micron-style" moves: major (S&P 500/400) stocks that gained
80%+ within ~3 months, what preceded those moves, and a backtested signal for
catching them near the start.

## What's here

```
pipeline/
  download_data.py   # ~11 years of daily OHLCV for S&P 500 + 400 (yfinance)
  analyze.py         # episode detection, feature study, signal backtest, current screen
  recent_fires.py    # ignition fires in the last 40 sessions (the live watchlist)
results/
  results.json       # study output as of 2026-09-18 (feeds the web page)
```

## Headline findings (2015–2026, 903 tickers, 628 episodes)

1. **The best entry day looks weak, not strong.** The optimal entry before an
   80%-in-63-days burst is usually a local low: RSI ~38, below the 50-day MA,
   ~29% off the high. It is only visible in hindsight — don't try to time it.
2. **The tradeable tell is the ignition.** A violent +12% week on ≥1.5x volume
   inside an uptrend (50dma > 200dma) marks the start of the discoverable part
   of the move. After that day: mean +17% over the next 63 sessions vs +4.5%
   baseline; 27% tack on another +40% (5.8x baseline); 8.2% another +80%
   (13x baseline). Edge positive in 10 of 11 years, including 2022.
3. **Quiet breakouts alone have no edge.** "Tight base + new high" without a
   volume/momentum shock underperformed baseline. Volume is the load-bearing
   ingredient.
4. **Catalysts do the work.** Nearly every big episode maps to an earnings
   inflection, product/AI cycle, index inclusion, M&A, or a sector supercycle.
   The signal finds the footprint; the news tells you if it's real.

## Run it

```bash
pip install yfinance pandas numpy requests lxml
export MOMENTUM_DATA=./data
python pipeline/download_data.py
python pipeline/analyze.py
python pipeline/recent_fires.py
```

*Not investment advice. Past patterns don't guarantee future returns.*
