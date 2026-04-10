# IBA Finance Lab — KSE-100 Market Dashboard

A real-time financial dashboard for the **Pakistan Stock Exchange (KSE-100 Index)**, built for the Institute of Business Administration. Displays live index prices, intraday charts, sector breakdowns, top contributors, commodities, and market news — all auto-refreshing every 5 minutes.

![Dashboard Preview](preview.png)

---

## Features

| Feature | Data Source |
|---|---|
| **KSE-100 Live Price** | PSX Intraday API (dps.psx.com.pk) |
| **Intraday Price Chart** | PSX tick data, rendered as line chart |
| **Open / High / Low / Prev Close** | PSX Intraday + EOD history |
| **Sector Breakdown (Donut)** | PSX sector constituents via yfinance |
| **Top Contributors (Bar Chart)** | PSX gainers/losers via yfinance |
| **Commodities (Gold, Oil, USD/PKR, EUR/PKR)** | Yahoo Finance → Stooq fallback |
| **Market News** | Google News RSS (Pakistan/PSX) |
| **Market Status** | Computed from PKT time + PSX schedule |
| **Ticker Tape** | KSE-100, USD/PKR, EUR/PKR |

---

## Quick Start

### Prerequisites

- **Python 3.8+**
- **pip**

### Installation

**Windows** (double-click):
```
install.bat
```

**Manual**:
```bash
pip install -r requirements.txt
```

### Run

```bash
python server_fixed.py
```

Then open **http://localhost:5000** in your browser.

### Network Access

The server binds to `0.0.0.0:5000`. Any device on the same network can access it:
```
http://<this-pc-ip>:5000
```

The server prints both URLs on startup.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 Browser (Dashboard)                  │
│  Chart.js  ·  CSS Responsive  ·  Auto-refresh 5min  │
└──────────────────────┬──────────────────────────────┘
                       │  HTTP /api/all  (single call)
                       ▼
┌─────────────────────────────────────────────────────┐
│              Flask Server (server_fixed.py)          │
│                                                     │
│  /api/quote        →  PSX intraday or market-closed │
│  /api/intraday     →  PSX tick data or market-closed│
│  /api/market       →  yfinance KSE-100 constituents │
│  /api/commodities  →  yfinance → Stooq fallback     │
│  /api/news         →  Google News RSS (cached 30m)  │
│  /api/market-status →  PKT time vs PSX schedule     │
│  /api/history      →  PSX 90-day EOD                │
│  /api/all          →  Everything above in one call  │
└────┬──────────┬──────────────┬──────────────────────┘
     │          │              │
     ▼          ▼              ▼
  PSX API    yfinance      Google News
  (dps.    (Yahoo          RSS + Stooq
  psx.      Finance)       (commodities)
  com.pk)
```

---

## Market Status Handling

### PSX Trading Hours (Pakistan Standard Time, UTC+5)

| Day | Pre-Open | Regular Session |
|---|---|---|
| **Mon – Thu** | 09:15 – 09:30 | 09:32 – 15:30 |
| **Friday** | 09:00 – 09:17 | 09:17 – 12:00 (Session 1)<br>14:32 – 16:30 (Session 2) |
| **Sat – Sun** | — | **Closed** |

### How It Works

The dashboard **never shows confusing stale data** when the market is closed:

1. **`/api/market-status`** — Computed purely from the current PKT time against the known PSX schedule. No external API call needed. Returns:
   ```json
   {
     "state": "closed",
     "session": "After Hours",
     "next_event": "Tomorrow 09:15 AM",
     "countdown_secs": 51420,
     "is_trading": false,
     "pk_time": "Thursday 16:45"
   }
   ```

2. **When market is closed**, the dashboard:
   - **Live dot turns grey** (no pulse animation)
   - **Label changes** from "LIVE DATA" → "MARKET CLOSED" / "PRE-OPEN" / "AFTER HOURS"
   - **Index card** shows `———` with session info and next-open time
   - **Status bar** displays: `"After Hours · Tomorrow 09:15 AM · Refreshed 16:45:00"`
   - **Commodities & FX** (gold, oil, USD/PKR) continue showing since they trade 24h
   - **Server skips PSX API calls** for quote/intraday/candles — returns clean `"is_trading": false` responses instead of errors

3. **When market is open**, everything works normally with live data.

### Holidays

Edit `PSX_HOLIDAYS_2026` in `server_fixed.py` to add market-closure dates. Eid holidays vary by moon sighting and should be updated annually.

---

## Responsive Design

The dashboard is optimized for **all screen sizes**:

| Breakpoint | Target | Key Adjustments |
|---|---|---|
| **≤ 400px** | Small phones | Smallest fonts, compact donut |
| **≤ 768px** | Phones / tablets | Stacked layout, smaller KSE value |
| **Default** | Desktop (769–1279px) | Original design |
| **≥ 1280px** | TV browsers (1080p) | Larger fonts, more spacing, higher contrast |
| **≥ 2560px** | 4K displays | Even larger scale |

### TV Browser Tips

- Works best in **landscape mode** on 1080p/4K TVs
- TCL, Samsung, LG Smart TV browsers are supported
- Use the TV's browser zoom if needed — the layout scales proportionally

---

## API Endpoints

| Endpoint | Description | Response |
|---|---|---|
| `GET /api/all` | **Everything** — dashboard fetches this single endpoint | Quote + Market + Commodities |
| `GET /api/quote` | KSE-100 current price | Price, open, high, low, prev_close, change, pct |
| `GET /api/intraday` | Today's tick data | `[[timestamp, price], ...]` |
| `GET /api/history` | 90-day EOD history | `[{date, price}, ...]` |
| `GET /api/market` | Gainers, losers, sectors | From yfinance KSE-100 constituents |
| `GET /api/commodities` | Gold, Oil, USD/PKR, EUR/PKR | Close, open, change, pct |
| `GET /api/news` | Pakistan market news | List of headlines |
| `GET /api/market-status` | PSX market state | State, session, next_event, is_trading |
| `GET /status` | Server health check | `{"status": "ok", "time": "..."}` |
| `GET /api/debug` | PSX endpoint diagnostics | Raw response info for each PSX path |
| `GET /api/debug_quote` | Debug quote data | Raw intraday + EOD comparison |
| `GET /api/debug_eod` | Debug EOD data | Last 5 EOD entries |
| `GET /api/yf_kse` | Debug yfinance KSE-100 | Probe all known yfinance symbols |

---

## Data Sources & Fallbacks

| Data | Primary | Fallback |
|---|---|---|
| KSE-100 Price | PSX `/timeseries/int/KSE100` | None (market must be open) |
| Prev Close | PSX homepage scrape | Open price (intraday API) |
| EOD History | PSX `/timeseries/eod/KSE100` | None |
| Gainers/Losers | yfinance KSE-100 constituents | Empty graceful state |
| Commodities | Yahoo Finance | Stooq CSV |
| News | Google News RSS | Silent fail (cosmetic) |

---

## Caching Strategy

| Data | TTL | Reason |
|---|---|---|
| KSE-100 prev_close | 300s (5 min) | Only changes once per day |
| Market data (gainers/losers) | 600s (10 min) | 20 PSX requests per refresh |
| News | 1800s (30 min) | Doesn't change frequently |

---

## File Structure

```
Finance-Lab-Dashboard/
├── server_fixed.py        # Flask backend + all data fetching
├── dashboard_fixed.html   # Single-page frontend (Chart.js)
├── requirements.txt       # Python dependencies
├── install.bat           # Windows one-click installer
└── README.md             # This file
```

---

## Customization

### Adding KSE-100 Constituents

Edit `KSE100_TICKERS` and `SECTOR_MAP` in `server_fixed.py`:

```python
KSE100_TICKERS = {
    "OGDC":  "OGDC.KA",
    "NEWCO": "NEWCO.KA",  # Add new stock
    # ...
}

SECTOR_MAP = {
    "NEWCO": "Technology",  # Map to sector
    # ...
}
```

### Adding Market Holidays

Edit `PSX_HOLIDAYS_2026` in `server_fixed.py`:

```python
PSX_HOLIDAYS_2026 = [
    "01-01",  # Kashmir Day
    "03-23",  # Pakistan Day
    "03-31",  # Eid ul-Fitr (update yearly)
    # ...
]
```

### Changing Refresh Interval

In `dashboard_fixed.html`, modify:
```javascript
setInterval(loadAll, 5*60*1000);  // 5 minutes
```

### Changing Chart Candle Interval

The server supports configurable candle intervals:
```
GET /api/candles?interval=15   // 15-minute candles
```

---

## Troubleshooting

### "Bad PSX intraday response"

- **Market is closed** — this is expected. The `/api/market-status` endpoint prevents this error now.
- **Network issue** — PSX servers may be slow or block requests from certain IPs.
- **PSX is down** — the debug endpoint (`/api/debug`) shows raw PSX response status.

### Commodities not loading

- Yahoo Finance may block requests from your IP.
- Stooq fallback should activate automatically.
- Check server console for detailed errors.

### Dashboard won't load on network

- Ensure Windows Firewall allows port 5000.
- Both devices must be on the **same network**.
- Try `http://<ip>:5000` (not localhost).

### Python import errors

```bash
pip install --upgrade flask flask-cors requests yfinance
```

---

## License

Educational use only. Not affiliated with Pakistan Stock Exchange (PSX).

---

## Credits

- **PSX Data**: [dps.psx.com.pk](https://dps.psx.com.pk/)
- **Commodities**: Yahoo Finance, Stooq
- **News**: Google News RSS
- **Charts**: [Chart.js](https://www.chartjs.org/)
- **Fonts**: Playfair Display, IBM Plex Mono, IBM Plex Sans
