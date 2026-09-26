"""Ignition Watch — self-updating momentum tracker for Railway.

Serves the tracker page and re-runs the ignition scan automatically:
on boot, then every weekday at RUN_AT_UTC (default 22:15 UTC, ~2h after
the US close). The scan is stateless — fires are derived from price
history — so nothing is lost if the container restarts; a snapshot of
the previous run (for NEW badges) is kept on disk when possible.

Signal (backtested 2016-2026, see momentum/README.md):
  IGNITION = 5-day return > +12%
             AND 21-day avg volume > 1.5x its 126-day average
             AND 50-DMA > 200-DMA
  27% of fires gained another +40% within 63 trading days (5.8x baseline).
"""
from __future__ import annotations

import io
import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
from flask import Flask, jsonify, render_template_string

RUN_AT_UTC = os.environ.get("RUN_AT_UTC", "22:15")
SNAPSHOT = os.environ.get("SNAPSHOT_PATH", "/tmp/ignition_snapshot.json")
LOOKBACK_FIRES = 90  # sessions of fire history shown on the page
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

app = Flask(__name__)

STATE: dict = {
    "status": "starting",       # starting | scanning | ok | error
    "error": None,
    "asof": None,               # last trading day in the data
    "scanned_at": None,         # wall-clock UTC of the scan
    "next_run": None,
    "universe": 0,
    "fires": [],                # fires in the last LOOKBACK_FIRES sessions
    "new_tickers": [],          # fires first seen by this scan
    "leaders": [],
}
_lock = threading.Lock()


# ---------------------------------------------------------------- scan ----

def get_universe() -> list[str]:
    tickers: set[str] = set()
    for url in (
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    ):
        html = requests.get(url, headers=UA, timeout=30).text
        for t in pd.read_html(io.StringIO(html)):
            for c in ("Symbol", "Ticker", "Ticker symbol"):
                if c in t.columns:
                    tickers |= {str(s).replace(".", "-").strip() for s in t[c].dropna()}
    return sorted(t for t in tickers if t and len(t) <= 6 and t[0].isalpha())


def download_prices(tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """~2.5y of adjusted daily Close/Volume, enough for a 252d lookback."""
    import yfinance as yf

    start = (datetime.now(timezone.utc) - pd.Timedelta(days=920)).strftime("%Y-%m-%d")
    closes, vols = [], []
    for i in range(0, len(tickers), 200):
        d = yf.download(tickers[i : i + 200], start=start, auto_adjust=True,
                        progress=False, threads=True)
        closes.append(d["Close"].astype("float32"))
        vols.append(d["Volume"].astype("float32"))
    close = pd.concat(closes, axis=1)
    vol = pd.concat(vols, axis=1)
    close = close.loc[:, ~close.columns.duplicated()].sort_index()
    vol = vol.loc[:, ~vol.columns.duplicated()].sort_index()
    return close, vol


def run_scan() -> None:
    with _lock:
        STATE["status"] = "scanning"
    try:
        tickers = get_universe()
        close, vol = download_prices(tickers)

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

        # Railway's disk is ephemeral across deploys, so without a snapshot
        # "new" falls back to "fired in the last 3 sessions".
        prev: set[str] | None
        try:
            prev = set(json.load(open(SNAPSHOT))["keys"])
        except Exception:
            prev = None

        fires = []
        recent = sig.iloc[-LOOKBACK_FIRES:]
        for t in recent.columns:
            col = recent[t]
            if not col.any():
                continue
            d0 = col[col].index[-1]
            i = close.index.get_loc(d0)
            key = f"{t}|{d0.date()}"
            days_ago = int(len(close.index) - 1 - i)
            fires.append(dict(
                ticker=t, fired=str(d0.date()),
                days_ago=days_ago,
                r5_at_fire=round(float(r5[t].iloc[i]) * 100, 1),
                volr_at_fire=round(float(volr[t].iloc[i]), 2),
                price_now=round(float(close[t].iloc[-1]), 2),
                ret_since=round(float(close[t].iloc[-1] / close[t].loc[d0] - 1) * 100, 1),
                r63=round(float(r63[t].iloc[-1]) * 100, 1),
                rs63=round(float(rs63[t].iloc[-1]) * 100, 1),
                pct_from_hi=round(float(close[t].iloc[-1] / hi252[t].iloc[-1] - 1) * 100, 1),
                new=(key not in prev) if prev is not None else days_ago < 3,
            ))
        fires.sort(key=lambda f: (f["fired"], f["ticker"]), reverse=True)

        cur_valid = valid.iloc[-1]
        leaders = pd.DataFrame({
            "price": close.iloc[-1], "r63": r63.iloc[-1], "rs63": rs63.iloc[-1],
            "volr": volr.iloc[-1], "pct_hi": close.iloc[-1] / hi252.iloc[-1] - 1,
            "golden": golden.iloc[-1],
        })
        leaders = leaders[cur_valid & leaders.golden & (leaders.pct_hi > -0.15) & (leaders.rs63 > 0.15)]
        leaders = leaders.sort_values("rs63", ascending=False).head(12)
        leaders_out = [dict(ticker=t, price=round(float(r.price), 2),
                            r63=round(float(r.r63) * 100, 1), rs63=round(float(r.rs63) * 100, 1),
                            volr=round(float(r.volr), 2), pct_from_hi=round(float(r.pct_hi) * 100, 1))
                       for t, r in leaders.iterrows()]

        try:
            json.dump({"keys": [f"{f['ticker']}|{f['fired']}" for f in fires]}, open(SNAPSHOT, "w"))
        except Exception:
            pass

        with _lock:
            STATE.update(
                status="ok", error=None,
                asof=str(close.index[-1].date()),
                scanned_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                universe=int(len(close.columns)),
                fires=fires,
                new_tickers=[f["ticker"] for f in fires if f["new"]],
                leaders=leaders_out,
            )
    except Exception as e:
        traceback.print_exc()
        with _lock:
            STATE["status"] = "error"
            STATE["error"] = f"{type(e).__name__}: {e}"


# ----------------------------------------------------------- scheduler ----

def _seconds_until_next_run() -> tuple[float, str]:
    h, m = (int(x) for x in RUN_AT_UTC.split(":"))
    now = datetime.now(timezone.utc)
    nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    while nxt <= now or nxt.weekday() >= 5:  # only Mon-Fri
        nxt += pd.Timedelta(days=1)
        nxt = nxt.replace(hour=h, minute=m)
    return (nxt - now).total_seconds(), nxt.strftime("%a %Y-%m-%d %H:%M UTC")


def scheduler() -> None:
    run_scan()  # boot scan, so a fresh deploy never shows an empty page
    while True:
        wait, label = _seconds_until_next_run()
        with _lock:
            STATE["next_run"] = label
        time.sleep(wait)
        run_scan()
        for _ in range(3):  # Yahoo hiccups are usually transient
            if STATE["status"] != "error":
                break
            time.sleep(1800)
            run_scan()


# ----------------------------------------------------------------- web ----

PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ignition Watch — live</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Spectral:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{--paper:#FAF9F6;--card:#FFF;--ink:#1A2230;--muted:#5A6472;--faint:#8B93A0;--line:#E5E2D9;
--accent:#E8501A;--accent-soft:#FBEAE2;--good:#1E7A46;--bad:#B3261E;--chip:#F1EFE9;
--shadow:0 1px 3px rgba(26,34,48,.07),0 8px 24px rgba(26,34,48,.05)}
@media (prefers-color-scheme:dark){:root{--paper:#171D26;--card:#1F2733;--ink:#ECEDE8;--muted:#A8AFB9;
--faint:#7A828E;--line:#313A47;--accent:#EE5F26;--accent-soft:#3A2418;--good:#5BBE85;--bad:#E58B85;
--chip:#28303C;--shadow:0 1px 3px rgba(0,0,0,.3),0 8px 24px rgba(0,0,0,.25)}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);
font:15.5px/1.6 "IBM Plex Sans",system-ui,sans-serif;padding-inline:16px}
.wrap{max-width:980px;margin:0 auto;padding-block:36px 70px}
h1{font:600 clamp(30px,5vw,42px)/1.15 Spectral,Georgia,serif;margin:8px 0 8px}
h2{font:600 22px/1.3 Spectral,Georgia,serif;margin:44px 0 6px}
.eyebrow{font:600 11.5px/1.4 "IBM Plex Mono",monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
.sub{color:var(--muted);max-width:70ch;margin:0}
.meta{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
.pill{font:500 12px/1 "IBM Plex Mono",monospace;background:var(--chip);border-radius:99px;padding:7px 12px;color:var(--muted)}
.pill b{color:var(--ink)}.pill.hot{background:var(--accent-soft);color:var(--accent)}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:1px}
.tablewrap{overflow-x:auto;margin-top:14px;border:1px solid var(--line);border-radius:10px;background:var(--card);box-shadow:var(--shadow)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th{font:600 11px/1.3 "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);
text-align:left;padding:11px 13px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px 13px;border-bottom:1px solid var(--line);font-size:14px;white-space:nowrap}
tr:last-child td{border-bottom:0}td.num,th.num{text-align:right}
.tick{font:600 13px/1 "IBM Plex Mono",monospace;background:var(--chip);border-radius:6px;padding:4px 7px}
.new{font:700 10px/1 "IBM Plex Mono",monospace;background:var(--accent);color:#fff;border-radius:4px;padding:3px 5px;margin-left:7px;letter-spacing:.08em}
.pos{color:var(--good);font-weight:600}.neg{color:var(--bad);font-weight:600}
.rulebox{background:var(--accent-soft);border:1px solid var(--accent);border-radius:10px;padding:14px 18px;margin-top:20px;font-size:14px}
.rulebox b{font-family:"IBM Plex Mono",monospace}
.note{color:var(--faint);font-size:12.5px;margin-top:10px}
.empty{padding:26px;text-align:center;color:var(--muted)}
a{color:var(--accent)}
.disclaimer{margin-top:56px;padding-top:16px;border-top:1px solid var(--line);font-size:12.5px;color:var(--faint);max-width:75ch}
</style></head><body><div class="wrap">
<div class="eyebrow">Ignition Watch · S&amp;P 500 + 400 · auto-scan</div>
<h1>Momentum ignition tracker</h1>
<p class="sub">Every weekday after the US close this service rescans ~900 major stocks for the backtested
<b>ignition</b> signal and logs every fire. New names since the previous scan are flagged
<span class="new" style="margin-left:2px">NEW</span>.</p>

<div class="meta">
  {% if s.status == 'ok' %}<span class="pill"><span class="dot" style="background:var(--good)"></span>data through <b>{{ s.asof }}</b></span>
  {% elif s.status == 'error' %}<span class="pill"><span class="dot" style="background:var(--bad)"></span>last scan failed — retrying at next schedule</span>
  {% else %}<span class="pill"><span class="dot" style="background:var(--accent)"></span><b>scanning now…</b> first results in ~2 min, refresh shortly</span>{% endif %}
  {% if s.scanned_at %}<span class="pill">scanned <b>{{ s.scanned_at }}</b></span>{% endif %}
  {% if s.next_run %}<span class="pill">next run <b>{{ s.next_run }}</b></span>{% endif %}
  {% if s.universe %}<span class="pill">universe <b>{{ s.universe }}</b> stocks</span>{% endif %}
  {% if s.new_tickers %}<span class="pill hot">⚡ new this scan: <b>{{ s.new_tickers|join(', ') }}</b></span>{% endif %}
</div>

<div class="rulebox"><b>IGNITION</b> = 5-day return &gt; +12% · 21-day volume &gt; 1.5× its 126-day avg · 50-DMA &gt; 200-DMA
&nbsp;→&nbsp; historically 27% went on to another +40% within 63 sessions (5.8× base rate), mean +17% vs +4.5%.
<a href="https://github.com/raveeshbhasin-git/Test1/tree/claude/stock-momentum-patterns-op28f6/momentum">methodology &amp; backtest</a></div>

<h2>Fire log — last {{ lookback }} sessions</h2>
{% if s.fires %}
<div class="tablewrap"><table>
<thead><tr><th>Stock</th><th>Fired</th><th class="num">Days ago</th><th class="num">Week at fire</th>
<th class="num">Vol at fire</th><th class="num">Since fire</th><th class="num">3-mo</th><th class="num">vs mkt</th>
<th class="num">Off 52w-hi</th><th class="num">Price</th></tr></thead><tbody>
{% for f in s.fires %}
<tr><td><span class="tick">{{ f.ticker }}</span>{% if f.new %}<span class="new">NEW</span>{% endif %}</td>
<td>{{ f.fired }}</td><td class="num">{{ f.days_ago }}</td>
<td class="num pos">+{{ f.r5_at_fire }}%</td><td class="num">{{ f.volr_at_fire }}×</td>
<td class="num {{ 'pos' if f.ret_since >= 0 else 'neg' }}">{{ '%+.1f' % f.ret_since }}%</td>
<td class="num {{ 'pos' if f.r63 >= 0 else 'neg' }}">{{ '%+.1f' % f.r63 }}%</td>
<td class="num">{{ '%+.1f' % f.rs63 }}%</td><td class="num">{{ '%.1f' % f.pct_from_hi }}%</td>
<td class="num">${{ f.price_now }}</td></tr>
{% endfor %}</tbody></table></div>
<p class="note">A fire stays listed for {{ lookback }} sessions. Backtested guidance: the edge persists ~63 sessions
after the fire; expect a −20% shakeout in roughly 1 of 4 cases. Check the catalyst before acting.</p>
{% elif s.status == 'ok' %}<div class="tablewrap"><div class="empty">No ignition fires in the last {{ lookback }} sessions — rare, but it happens in quiet tapes.</div></div>
{% else %}<div class="tablewrap"><div class="empty">Waiting for first scan…</div></div>{% endif %}

<h2>Relative-strength leaders near highs</h2>
<p class="sub">Context list, not fires: strongest 3-month performers vs the market, within 15% of 52-week highs, uptrend intact.</p>
{% if s.leaders %}
<div class="tablewrap"><table>
<thead><tr><th>Stock</th><th class="num">3-mo</th><th class="num">vs mkt</th><th class="num">Vol ratio</th>
<th class="num">Off 52w-hi</th><th class="num">Price</th></tr></thead><tbody>
{% for l in s.leaders %}
<tr><td><span class="tick">{{ l.ticker }}</span></td>
<td class="num pos">+{{ l.r63 }}%</td><td class="num">+{{ l.rs63 }}%</td><td class="num">{{ l.volr }}×</td>
<td class="num">{{ '%.1f' % l.pct_from_hi }}%</td><td class="num">${{ l.price }}</td></tr>
{% endfor %}</tbody></table></div>
{% else %}<div class="tablewrap"><div class="empty">Populates after the first scan.</div></div>{% endif %}

<p class="disclaimer">Research tool, not investment advice. Signals are statistical tendencies from a 2016–2026 backtest;
they carry elevated drawdown risk and no guarantee. Data: Yahoo Finance via yfinance, dividend-adjusted.
JSON: <a href="/api/fires">/api/fires</a> · Health: <a href="/healthz">/healthz</a></p>
</div></body></html>"""


@app.route("/")
def index():
    with _lock:
        s = dict(STATE)
    return render_template_string(PAGE, s=s, lookback=LOOKBACK_FIRES)


@app.route("/api/fires")
def api_fires():
    with _lock:
        return jsonify(dict(STATE))


@app.route("/healthz")
def healthz():
    with _lock:
        return jsonify(status=STATE["status"], asof=STATE["asof"], scanned_at=STATE["scanned_at"])


if __name__ == "__main__":
    from waitress import serve

    if os.environ.get("SKIP_SCHEDULER") != "1":
        threading.Thread(target=scheduler, daemon=True).start()
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), threads=4)
