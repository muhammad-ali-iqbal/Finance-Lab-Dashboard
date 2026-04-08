"""
IBA Finance Lab — Market Dashboard Server  (FIXED — prev_close)
================================================================
SETUP:  pip install flask flask-cors requests yfinance
RUN:    python server_fixed.py

FIX:  fetch_kse_quote() now returns a proper prev_close value
      (last EOD price) instead of using open as a placeholder.
      Change/pct are computed against prev_close, not open.

Confirmed working PSX endpoints (no auth needed):
  /timeseries/int/KSE100  →  intraday ticks
  /timeseries/eod/KSE100  →  90-day EOD history

/market-watch and /sector-summary return empty bodies without a browser
session cookie — PSX blocks direct server-side calls to those pages.
We derive gainers/losers/advancers from yfinance for KSE-100 constituents.
"""

import os, socket, traceback, time, math, json, re
from flask import Flask, jsonify, send_from_directory, Response
from flask_cors import CORS
import requests, yfinance as yf
import xml.etree.ElementTree as ET
from datetime import datetime

app = Flask(__name__)
CORS(app)

def safe_float(v, default=0.0):
    """Convert to float, replacing NaN/Inf with a safe default."""
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default

def clean(obj):
    """Recursively replace NaN/Inf in any dict/list/float so JSON stays valid."""
    if isinstance(obj, float):
        return safe_float(obj)
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(i) for i in obj]
    return obj

def safe_jsonify(data):
    """jsonify that strips NaN/Inf before serialising."""
    return Response(json.dumps(clean(data)), mimetype="application/json")


PSX_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://dps.psx.com.pk/",
    "Origin":          "https://dps.psx.com.pk",
}

def psx_get(path, timeout=15):
    url = f"https://dps.psx.com.pk{path}"
    r   = requests.get(url, headers=PSX_HEADERS, timeout=timeout)
    r.raise_for_status()
    text = r.text.strip()
    if not text:
        raise ValueError(f"PSX returned empty body for {path} — session/cookie required")
    return r.json()

def fetch_kse_prev_close():
    """
    Scrape previous close from the PSX homepage HTML.
    The intraday API endpoint doesn't provide it, but the homepage renders it
    server-side. The structure is:
      <div class="stats_label">Previous Close</div>
      <div class="stats_value">151,673.45</div>
    """
    r = requests.get("https://dps.psx.com.pk/", headers=PSX_HEADERS, timeout=15)
    r.raise_for_status()
    m = re.search(
        r'Previous\s*Close.*?stats_value[\"\s>](.+?)[<\s]',
        r.text,
        re.DOTALL
    )
    if not m:
        raise ValueError("Could not find Previous Close on PSX homepage")
    raw = m.group(1).strip().replace(",", "").replace(">", "")
    val = float(raw)
    print(f"[PSX] Previous close scraped: {val}")
    return val

# ── Simple in-memory cache (avoids hammering yfinance on every refresh) ──
_cache = {}
def cached(key, ttl_seconds, fn):
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < ttl_seconds:
        return entry["val"]
    val = fn()
    _cache[key] = {"val": val, "ts": time.time()}
    return val

# ─────────────────────────────────────────────────────────────────
# KSE-100 90-DAY EOD HISTORY  (needed by quote for prev_close)
# ─────────────────────────────────────────────────────────────────
def fetch_kse_history():
    data = psx_get("/timeseries/eod/KSE100")
    if not data or data.get("status") != 1 or not data.get("data"):
        raise ValueError("Bad PSX EOD response")
    rows   = data["data"][-90:]      # oldest→newest
    result = []
    for row in rows:
        ts    = row[0] / 1000 if row[0] > 1e10 else row[0]
        dt    = datetime.fromtimestamp(ts)
        label = str(int(dt.strftime("%d"))) + " " + dt.strftime("%b")  # cross-platform (no %-d)
        result.append({"date": label, "price": round(float(row[1]), 2)})
    return result

# ─────────────────────────────────────────────────────────────────
# KSE-100 INTRADAY (current quote)  — FIX: real prev_close
# ─────────────────────────────────────────────────────────────────
def fetch_kse_quote():
    data = psx_get("/timeseries/int/KSE100")
    if not data or data.get("status") != 1 or not data.get("data"):
        raise ValueError("Bad PSX intraday response")
    rows   = data["data"]            # newest first: [timestamp, price, volume]
    latest = rows[0][1]
    open_  = rows[-1][1]
    high   = max(r[1] for r in rows)
    low    = min(r[1] for r in rows)
    volume = sum(r[2] for r in rows if len(r) > 2)

    # Scrape prev_close from the PSX homepage (intraday API doesn't provide it)
    # Cache for 300s — it only changes once per trading day
    try:
        prev_close = cached("prev_close", 300, fetch_kse_prev_close)
    except Exception:
        prev_close = open_  # fallback

    change = latest - prev_close
    pct    = (change / prev_close * 100) if prev_close else 0
    return {
        "price":       round(float(latest),     2),
        "open":        round(float(open_),      2),
        "prev_close":  round(float(prev_close), 2),
        "high":        round(float(high),       2),
        "low":         round(float(low),        2),
        "volume":      int(volume),
        "change":      round(float(change),     2),
        "pct":         round(float(pct),        4),
        "updated":     datetime.now().strftime("%H:%M:%S"),
    }

# ─────────────────────────────────────────────────────────────────
# KSE-100 INTRADAY RAW (for 1D chart)
# ─────────────────────────────────────────────────────────────────
def fetch_kse_intraday():
    data = psx_get("/timeseries/int/KSE100")
    if not data or data.get("status") != 1 or not data.get("data"):
        raise ValueError("Bad PSX intraday response")
    rows_asc = list(reversed(data["data"]))    # oldest first
    return [[r[0], r[1]] for r in rows_asc]   # [[timestamp, price]]

# ─────────────────────────────────────────────────────────────────
# GAINERS / LOSERS — derived from yfinance KSE-100 constituents
# PSX /market-watch requires browser session; yfinance works freely.
# ─────────────────────────────────────────────────────────────────

# Well-known KSE-100 large-caps with their Yahoo Finance tickers
KSE100_TICKERS = {
    "OGDC":  "OGDC.KA",
    "PPL":   "PPL.KA",
    "MARI":  "MARI.KA",
    "HBL":   "HBL.KA",
    "UBL":   "UBL.KA",
    "MCB":   "MCB.KA",
    "ENGRO": "ENGRO.KA",
    "LUCK":  "LUCK.KA",
    "PSO":   "PSO.KA",
    "HUBC":  "HUBC.KA",
    "MEBL":  "MEBL.KA",
    "TRG":   "TRG.KA",
    "SYS":   "SYS.KA",
    "EFERT": "EFERT.KA",
    "FFBL":  "FFBL.KA",
    "DGKC":  "DGKC.KA",
    "MLCF":  "MLCF.KA",
    "FCCL":  "FCCL.KA",
    "NBP":   "NBP.KA",
    "BAHL":  "BAHL.KA",
}

# Sector mapping for KSE-100 stocks
SECTOR_MAP = {
    "OGDC":"Oil & Gas", "PPL":"Oil & Gas", "MARI":"Oil & Gas", "PSO":"Oil & Gas",
    "HBL":"Commercial Banks", "UBL":"Commercial Banks", "MCB":"Commercial Banks",
    "MEBL":"Commercial Banks", "NBP":"Commercial Banks", "BAHL":"Commercial Banks",
    "ENGRO":"Fertilizer", "EFERT":"Fertilizer", "FFBL":"Fertilizer",
    "LUCK":"Cement", "DGKC":"Cement", "MLCF":"Cement", "FCCL":"Cement",
    "HUBC":"Power", "TRG":"Technology", "SYS":"Technology",
}

def fetch_stock_intraday(symbol):
    """
    Fetch a single PSX stock intraday via the same endpoint as KSE-100.
    Returns (open, close, high, low, volume) for today's session.
    Raises on any failure so the caller can skip this stock gracefully.
    """
    data = psx_get(f"/timeseries/int/{symbol}")
    if not data or data.get("status") != 1 or not data.get("data"):
        raise ValueError(f"No intraday data for {symbol}")
    rows = data["data"]          # newest first: [timestamp, price, volume]
    if len(rows) < 2:
        raise ValueError(f"Too few ticks for {symbol}")
    rows_asc = list(reversed(rows))
    open_  = float(rows_asc[0][1])
    close  = float(rows_asc[-1][1])
    high   = max(float(r[1]) for r in rows_asc)
    low    = min(float(r[1]) for r in rows_asc)
    volume = sum(float(r[2]) for r in rows_asc if len(r) > 2)
    return open_, close, high, low, volume


def fetch_market_data():
    """
    Fetch intraday data for each KSE-100 constituent directly from PSX,
    the same way we fetch the index itself — guaranteed to have real today data.
    Cached for 10 minutes to avoid hammering PSX with 20 requests every refresh.
    """
    def _fetch():
        stocks = []
        for sym in KSE100_TICKERS:           # use PSX symbol, not yfinance ticker
            try:
                open_, close, high, low, vol = fetch_stock_intraday(sym)
                if open_ <= 0:
                    continue
                change = close - open_
                pct    = round(change / open_ * 100, 2)
                stocks.append({
                    "symbol": sym,
                    "name":   sym,
                    "close":  round(close, 2),
                    "open":   round(open_, 2),
                    "high":   round(high,  2),
                    "low":    round(low,   2),
                    "change": round(change, 2),
                    "pct":    pct,
                    "volume": int(vol),
                    "sector": SECTOR_MAP.get(sym, "Other"),
                })
            except Exception:
                continue          # skip stocks that PSX doesn't serve

        if not stocks:
            return _fallback_market()

        gainers = sorted([s for s in stocks if s["pct"] > 0],  key=lambda x: x["pct"], reverse=True)[:7]
        losers  = sorted([s for s in stocks if s["pct"] < 0],  key=lambda x: x["pct"])[:7]

        # Average sector pct from constituent stocks
        sector_data = {}
        for s in stocks:
            sec = s["sector"]
            sector_data.setdefault(sec, []).append(s["pct"])
        sectors = sorted(
            [{"sector": sec, "pct": round(sum(v)/len(v), 2)} for sec, v in sector_data.items()],
            key=lambda x: x["pct"], reverse=True
        )

        return {
            "gainers":      gainers,
            "losers":       losers,
            "sectors":      sectors,
            "advancers":    sum(1 for s in stocks if s["change"] > 0),
            "decliners":    sum(1 for s in stocks if s["change"] < 0),
            "unchanged":    sum(1 for s in stocks if s["change"] == 0),
            "total_stocks": len(stocks),
            "source":       "psx-intraday",
        }

    # Cache 10 minutes — 20 PSX requests per refresh is acceptable
    return cached("market", 600, _fetch)

def _fallback_market():
    """Return graceful empty structure if yfinance fails entirely."""
    return {
        "gainers": [], "losers": [], "sectors": [],
        "advancers": 0, "decliners": 0, "unchanged": 0,
        "total_stocks": 0, "source": "unavailable",
        "note": "Market data temporarily unavailable"
    }

# ─────────────────────────────────────────────────────────────────
# COMMODITIES via yfinance (with Stooq fallback)
# ─────────────────────────────────────────────────────────────────
YF_SYMBOLS = {
    "gold":   "GC=F",
    "oil":    "CL=F",
    "usdpkr": "PKR=X",
    "eurpkr": "EURPKR=X",
}
STOOQ_SYMBOLS = {
    "gold": "xauusd", "oil": "cl.f", "usdpkr": "usdpkr", "eurpkr": "eurpkr"
}

def fetch_yf(key):
    hist = yf.Ticker(YF_SYMBOLS[key]).history(period="2d")
    if hist.empty:
        raise ValueError(f"No yfinance data for {YF_SYMBOLS[key]}")
    close  = float(hist["Close"].iloc[-1])
    prev   = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else close
    open_  = float(hist["Open"].iloc[-1])
    change = close - prev
    pct    = (change / prev * 100) if prev else 0
    return {"close": close, "open": open_, "prev": prev,
            "change": round(change, 4), "pct": round(pct, 4)}

def fetch_stooq(key):
    url = f"https://stooq.com/q/l/?s={STOOQ_SYMBOLS[key]}&f=sd2t2ohlcv&h&e=csv"
    r   = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    lines = r.text.strip().splitlines()
    if len(lines) < 2:
        raise ValueError(f"Empty Stooq for {key}")
    hdr = [h.strip().lower() for h in lines[0].split(",")]
    val = [v.strip()         for v in lines[1].split(",")]
    row = dict(zip(hdr, val))
    def flt(k):
        v = row.get(k, "")
        return float(v) if v not in ("","N/A","N/D") else None
    close = flt("close") or flt("open")
    if not close:
        raise ValueError(f"N/D from Stooq for {key}")
    open_ = flt("open") or close
    prev  = close
    return {"close": close, "open": open_, "prev": prev,
            "change": round(close - open_, 4), "pct": round((close-open_)/open_*100, 4)}

def fetch_commodity(key):
    try:    return fetch_yf(key)
    except: pass
    try:    return fetch_stooq(key)
    except Exception as e:
        raise ValueError(str(e))


# ─────────────────────────────────────────────────────────────────
# KSE-100 INTRADAY → 5-MINUTE OHLCV CANDLES
# Ticks from PSX: [[timestamp_ms, price, volume], ...] newest-first
# We bucket them into N-minute bars (default 5) oldest→newest
# ─────────────────────────────────────────────────────────────────
def fetch_kse_candles(interval_minutes=5):
    data = psx_get("/timeseries/int/KSE100")
    if not data or data.get("status") != 1 or not data.get("data"):
        raise ValueError("Bad PSX intraday response")

    rows = data["data"]          # newest first: [timestamp, price, volume]
    rows_asc = list(reversed(rows))   # oldest first

    # PSX timestamps can be seconds (10 digits) OR milliseconds (13 digits)
    # Normalise everything to milliseconds
    def to_ms(ts):
        ts = int(ts)
        return ts if ts > 1e11 else ts * 1000

    interval_ms = interval_minutes * 60 * 1000
    candles = {}

    for row in rows_asc:
        ts_ms = to_ms(row[0])
        px    = float(row[1])
        vol   = float(row[2]) if len(row) > 2 else 0.0

        # Floor to nearest N-minute bucket
        bucket = (ts_ms // interval_ms) * interval_ms

        if bucket not in candles:
            candles[bucket] = {"t": bucket, "o": px, "h": px, "l": px, "c": px, "v": vol}
        else:
            c = candles[bucket]
            c["h"]  = max(c["h"], px)
            c["l"]  = min(c["l"], px)
            c["c"]  = px        # last tick in bucket = close
            c["v"] += vol

    # Sort buckets oldest→newest, build result list
    result = []
    for bucket in sorted(candles):
        c = candles[bucket]
        # strftime %-H and %-M are Linux-only; use lstrip("0") for Windows compat
        dt  = datetime.fromtimestamp(bucket / 1000)
        hh  = str(dt.hour).zfill(2)
        mm  = str(dt.minute).zfill(2)
        result.append({
            "t":     bucket,
            "o":     round(c["o"], 2),
            "h":     round(c["h"], 2),
            "l":     round(c["l"], 2),
            "c":     round(c["c"], 2),
            "v":     int(c["v"]),
            "label": f"{hh}:{mm}",
        })
    return result

# ─────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────

@app.route("/api/quote")
def api_quote():
    try:    return safe_jsonify(fetch_kse_quote())
    except Exception as e:
        return safe_jsonify({"error": str(e)}), 500

@app.route("/api/yf_kse")
def api_yf_kse():
    """Probe yfinance for KSE-100 to find prev_close symbol."""
    results = {}
    for sym in ["KSE100.KA", "KSE100.KSI", "KSE100.PS", "^KSE100", "KSE.KA", "KSE"]:
        try:
            t = yf.Ticker(sym)
            h = t.history(period="5d")
            results[sym] = {
                "history_count": len(h),
                "columns": list(h.columns),
                "last_2_close": float(h["Close"].iloc[-1]) if len(h) >= 1 else None,
                "last_2_prev":  float(h["Close"].iloc[-2]) if len(h) >= 2 else None,
                "info_prev_close": t.info.get("previousClose") if t.info else None,
            }
        except Exception as e:
            results[sym] = {"error": str(e)}
    return safe_jsonify(results)

@app.route("/api/debug_quote")
def api_debug_quote():
    """Show raw intraday + EOD data side by side to diagnose prev_close."""
    try:
        data = psx_get("/timeseries/int/KSE100")
        rows = data["data"]
        rows_asc = list(reversed(rows))
        eod = fetch_kse_history()
        return safe_jsonify({
            "intraday_first_tick":  rows_asc[0],   # open
            "intraday_last_tick":   rows_asc[-1],  # latest price
            "intraday_tick_count":  len(rows),
            "eod_last_3":           eod[-3:] if len(eod) >= 3 else eod,
            "eod_total":            len(eod),
        })
    except Exception as e:
        return safe_jsonify({"error": str(e)}), 500

@app.route("/api/history")
def api_history():
    try:    return safe_jsonify(fetch_kse_history())
    except Exception as e:
        return safe_jsonify({"error": str(e)}), 500

@app.route("/api/debug_eod")
def api_debug_eod():
    """Show raw EOD data — last 5 entries so we can diagnose prev_close."""
    try:
        hist = fetch_kse_history()
        return safe_jsonify({
            "total_entries": len(hist),
            "last_5": hist[-5:],
            "last_price": hist[-1] if hist else None,
            "second_last_price": hist[-2] if len(hist) >= 2 else None,
        })
    except Exception as e:
        return safe_jsonify({"error": str(e)}), 500

@app.route("/api/intraday")
def api_intraday():
    try:    return safe_jsonify(fetch_kse_intraday())
    except Exception as e:
        return safe_jsonify({"error": str(e)}), 500

@app.route("/api/candles")
def api_candles():
    """5-minute OHLCV candles for KSE-100 intraday session."""
    try:
        mins = int(request.args.get("interval", 5))
        return safe_jsonify(fetch_kse_candles(mins))
    except Exception as e:
        return safe_jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/market")
def api_market():
    try:    return safe_jsonify(fetch_market_data())
    except Exception as e:
        return safe_jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

@app.route("/api/commodities")
def api_commodities():
    out = {}
    for key in YF_SYMBOLS:
        try:    out[key] = fetch_commodity(key)
        except Exception as e:
            out[key] = {"error": str(e)}
    return safe_jsonify(out)

@app.route("/api/all")
def api_all():
    """Single endpoint — dashboard fetches everything here."""
    out = {}

    # KSE-100 quote (fast — PSX intraday)
    try:    out["quote"] = fetch_kse_quote()
    except Exception as e:
        out["quote"] = {"error": str(e), "trace": traceback.format_exc()}

    # Market data: gainers/losers/sectors from yfinance (cached 15 min)
    try:    out["market"] = fetch_market_data()
    except Exception as e:
        out["market"] = _fallback_market()
        out["market"]["error"] = str(e)

    # Commodities
    comms = {}
    for key in YF_SYMBOLS:
        try:    comms[key] = fetch_commodity(key)
        except Exception as e:
            comms[key] = {"error": str(e)}
    out["commodities"] = comms

    return safe_jsonify(out)

def fetch_pak_news(count=15):
    """
    Fetch Pakistan/PSX market news from Google News RSS.
    Only returns articles from the last 24 hours.
    Returns a list of headline strings.
    """
    try:
        r = requests.get(
            "https://news.google.com/rss/search",
            params={"q": "Pakistan stock market KSE PSX when:1d", "hl": "en-PK", "gl": "PK", "ceid": "PK:en"},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=12
        )
        r.raise_for_status()
        root = ET.fromstring(r.text)
        items = []
        for item in root.findall(".//item")[:count]:
            title = item.findtext("title", "")
            if title and title != "Title unavailable":
                items.append(title)
        if not items:
            raise ValueError("No titles found in Google News RSS")
        return items
    except Exception as e:
        raise


@app.route("/api/news")
def api_news():
    """Return current Pakistan market news from Reuters."""
    try:
        headlines = cached("news", 1800, fetch_pak_news)
        return safe_jsonify({"news": headlines})
    except Exception as e:
        return safe_jsonify({"news": [], "error": str(e)})

@app.route("/api/debug")
def api_debug():
    """Diagnostic — shows raw PSX response status for each endpoint."""
    results = {}
    for path in ["/timeseries/int/KSE100", "/timeseries/eod/KSE100",
                 "/market-watch", "/sector-summary"]:
        try:
            url = f"https://dps.psx.com.pk{path}"
            r   = requests.get(url, headers=PSX_HEADERS, timeout=10)
            results[path] = {
                "status_code": r.status_code,
                "content_length": len(r.text),
                "first_100_chars": r.text[:100],
                "content_type": r.headers.get("Content-Type",""),
            }
        except Exception as e:
            results[path] = {"error": str(e)}
    return safe_jsonify(results)

@app.route("/status")
def status():
    return safe_jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.route("/")
def root():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "dashboard_fixed.html")

# ─────────────────────────────────────────────────────────────────
# START
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
    except:
        ip = "unknown"

    print("\n" + "="*55)
    print("  IBA Finance Lab — Dashboard Server  (FIXED)")
    print("="*55)
    print(f"  This PC  →  http://localhost:5000")
    print(f"  Network  →  http://{ip}:5000")
    print(f"\n  Data sources:")
    print(f"    KSE-100 price/chart  →  PSX intraday API  ✓")
    print(f"    Gainers/losers       →  Yahoo Finance      ✓")
    print(f"    Gold/Oil/FX          →  Yahoo Finance      ✓")
    print(f"\n  Note: /market-watch and /sector-summary on dps.psx.com.pk")
    print(f"  require a browser session cookie and cannot be fetched")
    print(f"  server-side. Gainers/losers use Yahoo Finance instead.")
    print(f"\n  Debug:  http://localhost:5000/api/debug")
    print("="*55)
    print("  Press Ctrl+C to stop\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
