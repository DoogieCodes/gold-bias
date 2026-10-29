import re, os, io, math, json, time, requests, pandas as pd, streamlit as st, yfinance as yf

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FEED_NEXT = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
HIST = "history.csv"
USD_DOWN = ["unemployment rate", "jobless claims", "unemployment claims"]
USD_UP = ["cpi", "ppi", "non-farm", "retail sales", "gdp", "ism", "pmi",
          "hourly earnings", "pce", "jolts", "durable"]
GREEN, RED, GREY, BLUE = "#00C853", "#FF3B30", "#8A8A8A", "#00E5FF"
LATER = [
    ("FOMC", "FOMC Rate Decision", "2026-10-28 14:00"),
    ("NFP", "Non-Farm Employment Change (October)", "2026-11-06 08:30"),
    ("CPI", "CPI (October)", "2026-11-10 08:30"),
    ("PPI", "PPI (October)", "2026-11-13 08:30"),
    ("NFP", "Non-Farm Employment Change (November)", "2026-12-04 08:30"),
    ("FOMC", "FOMC Rate Decision", "2026-12-09 14:00"),
    ("CPI", "CPI (November)", "2026-12-10 08:30"),
    ("PPI", "PPI (November)", "2026-12-15 08:30"),
]

MODEL_VERSION = "v4"
CTX = {"CPI": "Hotter inflation lifts rate expectations: usually USD and yields up, gold down.",
       "PPI": "Hotter producer prices feed through to inflation and rate expectations.",
       "PCE": "PCE is the Fed's preferred inflation gauge, so surprises move rate expectations.",
       "NFP": "A stronger jobs report lifts rate expectations: usually USD up, gold down.",
       "JOBS": "Higher wages or lower unemployment is hawkish; the reverse is dovish.",
       "CLAIMS": "Fewer jobless claims signals a tighter labour market.",
       "GDP": "Stronger growth supports USD and yields, but GDP is noisy and gets revised.",
       "RETAIL": "Stronger spending supports USD and yields.", "PMI": "Stronger surveys support USD and yields."}
COST = 1.0  # assumed spread + slippage in USD per ounce, used only for the "profitable after costs" view
UNIT = {"CPI": 0.1, "PPI": 0.1, "PCE": 0.1, "NFP": 50000, "JOBS": 0.1, "CLAIMS": 10000,
        "GDP": 1.0, "PMI": 1.0, "RETAIL": 0.3}  # size of a forecast-vs-previous gap that counts as a 50-point shift

st.set_page_config(page_title="High Society × TFF@HIGHTABLE | Gold News Bias", layout="centered")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@300;500;700&display=swap');
#MainMenu, footer, header{visibility:hidden}
.stApp{background:#000;color:#fff}
.block-container{padding-top:1.6rem;max-width:720px}
.brand{text-align:center;padding:10px 0 18px;border-bottom:1px solid #0b3040;margin-bottom:18px}
.wm{font-family:'Montserrat',sans-serif;font-weight:300;font-size:clamp(1.05rem,6.2vw,1.7rem);letter-spacing:.32em;color:#fff;padding-left:.32em;line-height:1.2}
.bx{font-family:'Montserrat',sans-serif;font-weight:300;font-size:clamp(.9rem,3.6vw,1.15rem);color:#00E5FF;text-shadow:0 0 12px rgba(0,229,255,.7);margin:4px 0;line-height:1}
.wm2{font-family:'Montserrat',sans-serif;font-weight:500;font-size:clamp(.8rem,4.2vw,1.1rem);letter-spacing:.28em;color:#fff;padding-left:.28em;line-height:1.2}
.tg{font-family:'Montserrat',sans-serif;font-weight:500;font-size:.68rem;letter-spacing:.35em;
    color:#00E5FF;margin-top:8px;text-shadow:0 0 12px rgba(0,229,255,.6);padding-left:.35em}
.meta{text-align:center;font-size:.72rem;color:#7a7a7a;margin:-8px 0 14px;letter-spacing:.05em}
.sec{font-family:'Montserrat',sans-serif;font-size:.72rem;letter-spacing:.3em;color:#00E5FF;
     margin:18px 0 10px;font-weight:500}
.card{background:#0a0a0a;border:1px solid #0b3040;border-radius:10px;padding:14px 16px;margin-bottom:12px}
.tag{font-size:.68rem;letter-spacing:.16em;font-weight:700;color:#00E5FF}
.ttl{font-size:1.05rem;font-weight:700;color:#fff;margin-top:2px}
.sub{font-size:.8rem;color:#9a9a9a;margin-top:2px}
.bias{font-size:1.1rem;font-weight:800;margin:8px 0 6px;letter-spacing:.04em}
.bar{background:#1a1a1a;border-radius:6px;height:6px;overflow:hidden;margin-top:4px}
.bar div{height:6px}
.pairs{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.pair{background:#111;border:1px solid #0b3040;border-radius:6px;padding:3px 9px;font-size:.72rem;
      color:#ddd;letter-spacing:.06em}
.kv{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:10px;font-size:.66rem;letter-spacing:.08em;color:#7a7a7a}
.kv b{color:#fff;font-size:.8rem;letter-spacing:0}
.why{margin-top:10px;padding-top:8px;border-top:1px solid #151515;font-size:.74rem;color:#b5b5b5;line-height:1.55}
.foot{text-align:center;font-family:'Montserrat',sans-serif;font-size:.62rem;letter-spacing:.25em;color:#5a5a5a;margin-top:26px;line-height:1.7}
.foot .fx{color:#00E5FF;opacity:.7}
[data-testid="stMetricLabel"]{color:#00E5FF}
[data-testid="stMetricValue"]{color:#fff}
[data-testid="stExpander"]{border:1px solid #0b3040;border-radius:10px}
.stTabs [aria-selected="true"]{color:#00E5FF!important}
.stTabs [data-baseweb="tab-highlight"]{background:#00E5FF!important}
</style>
""", unsafe_allow_html=True)

def num(s):
    if not s:
        return None
    m = re.search(r"-?\d+(\.\d+)?", s.replace(",", ""))
    if not m:
        return None
    return float(m.group()) * {"K": 1e3, "M": 1e6, "B": 1e9}.get(s.strip()[-1].upper(), 1)

def lean(title, fc, prev):
    t, f, p = title.lower(), num(fc), num(prev)
    if "federal funds rate" in t:
        if f is None or p is None or f == p:
            return "Neutral", 0
        return ("Bearish gold" if f > p else "Bullish gold"), int(min(abs(f - p) / 0.25 * 60, 100))
    if f is None or p is None:
        return "Neutral", 0
    if any(k in t for k in USD_DOWN):
        d = -1
    elif any(k in t for k in USD_UP):
        d = 1
    else:
        return "Neutral", 0
    u = UNIT.get(tag(title)) or max(abs(p), 0.1) / 2
    # forecast-vs-previous is only a prior (the real surprise is actual vs forecast), so it is capped at 60
    # and flattens out for big gaps instead of producing extreme scores
    strength = int(60 * math.tanh(abs(f - p) / (2 * u)))
    if strength < 8 or f == p:
        return "Neutral", 0
    usd = d if f > p else -d
    return ("Bearish gold" if usd > 0 else "Bullish gold"), strength

CAL_COLS = ["key", "title", "time", "impact", "forecast", "previous", "actual", "status", "updated_at"]
CAL_FILE = "calendar.csv"

def load_cal():
    d = sb_load("calendar", CAL_COLS)
    if d is None:
        d = pd.read_csv(CAL_FILE, dtype=str).fillna("") if os.path.exists(CAL_FILE) else pd.DataFrame(columns=CAL_COLS)
    for c in CAL_COLS:
        if c not in d.columns:
            d[c] = ""
    return d.fillna("")[CAL_COLS]

def schedule_events(now):
    """Official release dates (BLS schedule plus typed FOMC dates). Used only when the live feed is down and
    nothing upcoming is saved, so the dashboard still shows tonight's events (without forecasts)."""
    try:
        src = bls_dates()
    except Exception:
        src = []
    items = (src + [x for x in LATER if x[0] == "FOMC"]) if src else LATER
    rows = []
    for lbl, name, when_et in items:
        t = pd.Timestamp(when_et, tz="America/New_York").tz_convert("UTC")
        if now - pd.Timedelta(hours=1) < t < now + pd.Timedelta(days=45):
            rows.append({"key": f"{name}|{t:%Y-%m-%d}", "title": name, "time": str(t), "impact": "High",
                         "forecast": "", "previous": "", "actual": "", "status": "scheduled", "updated_at": "schedule"})
    return pd.DataFrame(rows, columns=CAL_COLS)

def fetch_feed():
    frames, got_next = [], False
    for i, u in enumerate([FEED, FEED_NEXT]):
        try:
            r = requests.get(u, timeout=15, headers={"User-Agent": "HighSocietyGoldBias/1.0"})
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                raise ValueError("unexpected feed format")
            frames.append(pd.DataFrame(data))
            got_next = got_next or i == 1
        except Exception:
            if i == 0:
                raise
    df = pd.concat(frames, ignore_index=True)
    df = df[(df.country == "USD") & (df.impact == "High")].copy()
    df["time"] = pd.to_datetime(df["date"], utc=True)
    if "actual" not in df.columns:
        df["actual"] = ""
    df["key"] = df["title"] + "|" + df["time"].dt.strftime("%Y-%m-%d")
    return df.drop_duplicates("key"), got_next

def surprise_lean(title, act, fc):
    """Post-release: actual versus forecast (the surprise), only when the feed supplies an actual figure."""
    t, a, f = title.lower(), num(act), num(fc)
    if a is None or f is None:
        return None
    d = -1 if any(k in t for k in USD_DOWN) else 1 if any(k in t for k in USD_UP) else 0
    if d == 0:
        return None
    u = UNIT.get(tag(title)) or max(abs(f), 0.1) / 2
    z = (a - f) / u
    strength = int(min(abs(z) * 40, 100))
    if strength < 8:
        return "Neutral", 0, z
    return ("Bearish gold" if (d if a > f else -d) > 0 else "Bullish gold"), strength, z

@st.cache_data(ttl=600)
def sync_calendar():
    """Pull the feed, merge into the saved calendar (revisions, duplicates, cancellations), persist it."""
    old = load_cal()
    now = pd.Timestamp.now(tz="UTC")
    err = None
    try:
        df, got_next = fetch_feed()
        new = pd.DataFrame({"key": df.key, "title": df.title, "time": df.time.astype(str), "impact": df.impact,
                            "forecast": df.forecast.fillna(""), "previous": df.previous.fillna(""),
                            "actual": df.actual.fillna(""), "status": "scheduled", "updated_at": str(now)})
        # the feed covers this week (and next week when that request worked); only judge events inside that window
        wk = now.normalize() - pd.Timedelta(days=now.weekday())
        lo, hi = now, wk + pd.Timedelta(days=14 if got_next else 7)
        old_ev = old[old.status != "meta"]
        old_sig = set(zip(old_ev.key, old_ev.time, old_ev.forecast, old_ev.previous, old_ev.actual, old_ev.status))
        changed = new[[(k, t, f, p, a, "scheduled") not in old_sig
                       for k, t, f, p, a in zip(new.key, new.time, new.forecast, new.previous, new.actual)]]
        gone = old_ev.iloc[0:0]
        if len(old_ev) and len(df):
            ot = pd.to_datetime(old_ev["time"], utc=True)
            gone = old_ev[(~old_ev.key.isin(new.key)) & (ot > now) & (ot >= lo) & (ot <= hi)
                          & (old_ev.status == "scheduled")].copy()
            gone["status"] = "cancelled_or_moved"
            gone["updated_at"] = str(now)
        keep = old_ev[~old_ev.key.isin(new.key) & ~old_ev.key.isin(gone.key)]
        merged = pd.concat([keep, gone, new], ignore_index=True)
        merged = merged[pd.to_datetime(merged["time"], utc=True) > now - pd.Timedelta(days=60)]
        meta = pd.DataFrame([{"key": "__last_ok__", "title": "meta", "time": str(now), "impact": "",
                              "forecast": "", "previous": "", "actual": "", "status": "meta", "updated_at": str(now)}])
        delta = pd.concat([changed, gone, meta], ignore_index=True)
        merged.to_csv(CAL_FILE, index=False)
        if not sb_save("calendar", delta) and sb():
            st.session_state["db_err"] = True
        last_ok = str(now)
    except Exception as e:
        merged = old[old.status != "meta"]
        code = getattr(getattr(e, "response", None), "status_code", None)
        err = f"HTTP {code}" if code else type(e).__name__
        m = old[old.status == "meta"]
        last_ok = m.updated_at.max() if len(m) else (merged.updated_at.max() if len(merged) else None)
    soon = merged[
        (merged["status"] == "scheduled") &
        (merged["impact"] == "High") &
        (pd.to_datetime(merged["time"], utc=True, errors="coerce") > now)
    ]
    if soon.empty:
        backup = schedule_events(now)
        if len(backup):
            merged = pd.concat([merged, backup], ignore_index=True)
            merged = merged.drop_duplicates("key", keep="last")
            merged.to_csv(CAL_FILE, index=False)
    return merged, last_ok, err

def events():
    cal, last_ok, err = sync_calendar()
    df = cal[(cal.status == "scheduled") & (cal.impact == "High")].copy()
    empty = pd.DataFrame({"key": [], "title": [], "forecast": [], "previous": [], "actual": [], "bias": [],
                          "strength": [], "post": [], "z": [], "time": pd.to_datetime([], utc=True)})
    if df.empty:
        return empty, last_ok, err, cal
    df["time"] = pd.to_datetime(df["time"], utc=True)
    nowt = pd.Timestamp.now(tz="UTC")
    def score(r):
        if str(r["actual"]).strip() != "" and r["time"] <= nowt:
            s_ = surprise_lean(r["title"], r["actual"], r["forecast"])
            if s_:
                return s_[0], s_[1], True, s_[2]
        b_, st_ = lean(r["title"], r["forecast"], r["previous"])
        return b_, st_, False, None
    out = [score(r) for _, r in df.iterrows()]
    df["bias"], df["strength"] = [o[0] for o in out], [o[1] for o in out]
    df["post"], df["z"] = [o[2] for o in out], [o[3] for o in out]
    return df.sort_values("time"), last_ok, err, cal

@st.cache_data(ttl=86400)
def bls_dates():
    txt = requests.get("https://www.bls.gov/schedule/news_release/bls.ics",
                       headers={"User-Agent": "Mozilla/5.0"}, timeout=15).text
    out = []
    for blk in txt.split("BEGIN:VEVENT")[1:]:
        sm = re.search(r"SUMMARY:(.+)", blk)
        dt = re.search(r"DTSTART[^:\r\n]*:(\d{8})", blk)
        if not (sm and dt):
            continue
        name = sm.group(1).strip()
        low = name.lower()
        if "state" in low or "metro" in low:
            continue
        lab = ("CPI" if "consumer price index" in low else "PPI" if "producer price index" in low
               else "NFP" if "employment situation" in low else None)
        if lab:
            d = dt.group(1)
            out.append((lab, name, f"{d[:4]}-{d[4:6]}-{d[6:]} 08:30"))
    return out

def gold_series(period, interval):
    for tk, name in [("XAUUSD=X", "Spot XAUUSD"), ("GC=F", "Futures GC=F")]:
        try:
            d = yf.Ticker(tk).history(period=period, interval=interval)[["Close", "High", "Low"]].dropna()
            if len(d) > 5:
                d.index = d.index.tz_localize("UTC") if d.index.tz is None else d.index.tz_convert("UTC")
                return d, name
        except Exception:
            pass
    return pd.DataFrame(columns=["Close", "High", "Low"], dtype=float), "unavailable"

def oanda_cfg():
    try:
        return dict(st.secrets["oanda"])
    except Exception:
        return None

@st.cache_data(ttl=2)
def live_price():
    """Newest price available. Uses the OANDA v20 pricing endpoint when credentials are in Secrets,
    otherwise gold-api.com. Short cache so repeated calls inside a few seconds don't hammer the provider."""
    o = oanda_cfg()
    if o:
        try:
            r = requests.get(f"{o['base_url'].rstrip('/')}/v3/accounts/{o['account_id']}/pricing",
                             params={"instruments": "XAU_USD"},
                             headers={"Authorization": f"Bearer {o['token']}"}, timeout=5)
            r.raise_for_status()
            p = r.json()["prices"][0]
            return (float(p["bids"][0]["price"]) + float(p["asks"][0]["price"])) / 2, "OANDA XAU_USD", p.get("time")
        except Exception:
            pass
    r = requests.get("https://api.gold-api.com/price/XAU", timeout=6)
    r.raise_for_status()
    j = r.json()
    return float(j["price"]), "gold-api.com spot", j.get("updatedAt")

@st.cache_data(ttl=30)
def prices():
    return gold_series("5d", "1m")

@st.cache_data(ttl=300)
def market():
    out = {}
    gd, _ = gold_series("1mo", "1d")
    series = {"Gold": gd["Close"]}
    for k, tk in {"Dollar (DXY)": "DX-Y.NYB", "US 10Y yield": "^TNX"}.items():
        series[k] = yf.Ticker(tk).history(period="1mo", interval="1d")["Close"].dropna()
    for k, tk in {"Fed funds futures": "ZQ=F", "13-week yield": "^IRX"}.items():
        try:
            c = yf.Ticker(tk).history(period="1mo", interval="1d")["Close"].dropna()
            if len(c) >= 6:
                series[k] = c
        except Exception:
            pass
    for k, c in series.items():
        out[k] = (float(c.iloc[-1]), float(c.iloc[-1] - c.iloc[-6]),
                  float(c.iloc[-1] / c.iloc[-6] - 1) * 100)
    out["_asof"] = series["Dollar (DXY)"].index[-1]
    return out

@st.cache_data(ttl=21600)
def macro():
    """Inflation, unemployment and the effective Fed funds rate from FRED's public CSV download links."""
    def series(sid):
        r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=15,
                         headers={"User-Agent": "HighSocietyGoldBias/1.0"})
        r.raise_for_status()
        d = pd.read_csv(io.StringIO(r.text))
        return pd.to_numeric(d.iloc[:, 1], errors="coerce").dropna().reset_index(drop=True)
    raw, res = {}, {}
    for k, sid in {"dff": "DFF", "cpi": "CPIAUCSL", "unrate": "UNRATE"}.items():
        try:
            raw[k] = series(sid)
        except Exception:
            pass
    if "dff" in raw and len(raw["dff"]):
        res["DFF"] = float(raw["dff"].iloc[-1])
    c = raw.get("cpi")
    if c is not None and len(c) >= 16:
        res["cpi_trend"] = float(((c.iloc[-1] / c.iloc[-13]) - (c.iloc[-4] / c.iloc[-16])) * 100)
    u = raw.get("unrate")
    if u is not None and len(u) >= 4:
        res["unrate_trend"] = float(u.iloc[-1] - u.iloc[-4])
    return res

ML_WHY = []

def market_lean(m):
    global ML_WHY
    clip = lambda x: max(-1, min(1, x))
    comps = {"dollar": -clip(m["Dollar (DXY)"][2] / 1.0),
             "10y yield": -clip(m["US 10Y yield"][1] / 0.20),
             "gold trend": clip(m["Gold"][2] / 3.0)}
    if "Fed funds futures" in m:
        comps["rate expectations"] = clip(m["Fed funds futures"][1] / 0.10)
    if "13-week yield" in m:
        comps["short yield"] = -clip(m["13-week yield"][1] / 0.10)
    score = sum(comps.values()) / len(comps)
    bias = "Bullish gold" if score > 0.15 else "Bearish gold" if score < -0.15 else "Neutral"
    sign = 1 if score > 0 else -1
    active = [v for v in comps.values() if abs(v) > 0.15]
    agree = sum(1 for v in active if v * sign > 0)
    ML_WHY = [f"{k} → gold {'bullish' if v > 0.15 else 'bearish' if v < -0.15 else 'neutral'}" for k, v in comps.items()]
    ML_WHY.append(f"{agree} of {len(comps)} signals agree")
    frac = agree / len(active) if active else 0
    conf = int(min(95, abs(score) * 100 * (0.6 + 0.8 * frac))) if bias != "Neutral" else 0
    return bias, conf

COLS = ["key", "title", "time", "bias", "strength", "confidence", "forecast", "previous", "model_version",
        "logged_at", "pre_release", "move1", "move5", "move15", "move30", "mfe", "mae", "reversal", "net15", "hit",
        "spike_bias", "move60"]

def sb():
    try:
        return st.secrets["supabase"]["url"].rstrip("/"), st.secrets["supabase"]["key"]
    except Exception:
        return None

def sb_load(table, cols):
    cfg = sb()
    if cfg:
        try:
            hd = {"apikey": cfg[1], "Authorization": f"Bearer {cfg[1]}"}
            rows = requests.get(f"{cfg[0]}/rest/v1/{table}?select=*&limit=3000", headers=hd, timeout=10).json()
            if isinstance(rows, list):
                return pd.DataFrame(rows, columns=cols)
        except Exception:
            pass
    return None

def sb_save(table, df):
    cfg = sb()
    if cfg and len(df):
        try:
            hd = {"apikey": cfg[1], "Authorization": f"Bearer {cfg[1]}",
                  "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
            return requests.post(f"{cfg[0]}/rest/v1/{table}?on_conflict=key", headers=hd,
                                 data=df.to_json(orient="records"), timeout=15).ok
        except Exception:
            return False
    return bool(cfg is None)

def load_hist():
    d = sb_load("calls", COLS)
    if d is None:
        d = pd.read_csv(HIST) if os.path.exists(HIST) else pd.DataFrame(columns=COLS)
    for c in COLS:
        if c not in d.columns:
            d[c] = None
    return d[COLS]

def save_hist(h, keys):
    h.to_csv(HIST, index=False)
    d = h[h.key.isin(keys)].copy()
    d["strength"] = pd.to_numeric(d.strength, errors="coerce").round().astype("Int64")
    if not sb_save("calls", d) and sb():
        st.session_state["db_err"] = True

def _n(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, (int, float)) and float(v).is_integer():
        return str(int(v))
    return str(v)

def update_history(h, ev, pxd, cmap):
    """Log each prediction BEFORE its release (latest pre-release snapshot wins, frozen at release time),
    then fill in the price reaction once enough 1-minute data exists."""
    px = pxd["Close"]
    now = pd.Timestamp.now(tz="UTC")
    dirty = []
    for _, r in ev.iterrows():
        key, t = r["key"], r["time"]
        if now >= t or (key in cmap and cmap[key] is None):
            continue
        b, s0, conf, sp = cmap.get(key, (r["bias"], r["strength"], None, None))
        new = [str(t), b, s0, conf, r["forecast"], r["previous"], sp]
        idx = h.index[h.key == key]
        if len(idx) == 0:
            h.loc[len(h)] = [key, r["title"], str(t), b, s0, conf, r["forecast"], r["previous"], MODEL_VERSION,
                             str(now), 1] + [None] * 9 + [sp, None]
            dirty.append(key)
        else:
            i = idx[0]
            cols6 = ["time", "bias", "strength", "confidence", "forecast", "previous", "spike_bias"]
            cur = [h.at[i, c] for c in cols6]
            if [_n(x) for x in cur] != [_n(x) for x in new]:
                for c, v in zip(cols6, new):
                    h.at[i, c] = v
                h.at[i, "logged_at"] = str(now)
                dirty.append(key)
    todo = h[(pd.to_numeric(h.pre_release, errors="coerce") == 1) & h.move30.isna()]
    one = pd.Timedelta(minutes=1)
    for i, r in todo.iterrows():
        t = pd.Timestamp(r["time"])
        if not len(px) or t - one < px.index[0] or t + pd.Timedelta(minutes=30) > px.index[-1] + one:
            continue
        at = lambda x: float(px.asof(x - one))
        p0 = at(t)
        mv = {m: round(at(t + pd.Timedelta(minutes=m)) - p0, 2) for m in (1, 5, 15, 30)}
        h.at[i, "move1"], h.at[i, "move5"], h.at[i, "move15"], h.at[i, "move30"] = mv[1], mv[5], mv[15], mv[30]
        h.at[i, "reversal"] = int(mv[1] * mv[15] < 0)
        if r["bias"] in ("Bullish gold", "Bearish gold"):
            d = 1 if r["bias"] == "Bullish gold" else -1
            w = pxd.loc[t: t + pd.Timedelta(minutes=14)]
            hi, lo = float(w["High"].max()), float(w["Low"].min())
            h.at[i, "mfe"] = round((hi - p0) if d > 0 else (p0 - lo), 2)
            h.at[i, "mae"] = round((p0 - lo) if d > 0 else (hi - p0), 2)
            h.at[i, "hit"] = int(d * mv[5] > 0)
            h.at[i, "net15"] = round(d * mv[15] - COST, 2)
        dirty.append(r["key"])
    f60 = h[(pd.to_numeric(h.pre_release, errors="coerce") == 1) & h.move30.notna() & h.move60.isna()
            & (h.title.map(tag) == "FOMC")]
    for i, r in f60.iterrows():
        t = pd.Timestamp(r["time"])
        if len(px) and t - one >= px.index[0] and t + pd.Timedelta(minutes=60) <= px.index[-1] + one:
            h.at[i, "move60"] = round(float(px.asof(t + pd.Timedelta(minutes=59))) - float(px.asof(t - one)), 2)
            dirty.append(r["key"])
    if dirty:
        save_hist(h, dirty)
    return h

def tag(t):
    t = t.lower()
    if "fomc" in t or "federal funds" in t: return "FOMC"
    if "cpi" in t or "consumer price" in t: return "CPI"
    if "ppi" in t or "producer price" in t: return "PPI"
    if "non-farm" in t or "employment situation" in t: return "NFP"
    for k, v in [("pce", "PCE"), ("gdp", "GDP"), ("ism", "PMI"), ("pmi", "PMI"), ("retail", "RETAIL"),
                 ("jobless", "CLAIMS"), ("claims", "CLAIMS"), ("unemployment", "JOBS"), ("hourly", "JOBS")]:
        if k in t: return v
    return "NEWS"

def bias_color(b):
    return GREEN if b == "Bullish gold" else RED if b == "Bearish gold" else GREY

def conf_color(bias, s):
    a = 0.45 if s < 40 else 0.75 if s < 70 else 1.0
    r, g, b = (0, 200, 83) if bias == "Bullish gold" else (255, 59, 48)
    return f"rgba({r},{g},{b},{a})"

def arrow(b):
    return "▲" if b == "Bullish gold" else "▼" if b == "Bearish gold" else "●"

def pair_block(bias):
    usd = 0 if bias == "Neutral" else (1 if bias == "Bearish gold" else -1)
    chips = ""
    for name, sign in [("USD", 1), ("XAUUSD", -1), ("EURUSD", -1), ("GBPUSD", -1), ("USDJPY", 1)]:
        d = usd * sign
        col = GREEN if d > 0 else RED if d < 0 else GREY
        sym = "▲" if d > 0 else "▼" if d < 0 else "●"
        chips += f'<span class="pair">{name} <b style="color:{col}">{sym}</b></span>'
    return f'<div class="pairs">{chips}</div>'

def sast(t):
    return t.tz_convert("Africa/Johannesburg").strftime("%a %d %b, %H:%M")

def gate():
    try:
        codes = dict(st.secrets["access_codes"])
    except Exception:
        return None
    inv = {str(v): k for k, v in codes.items()}
    if st.session_state.get("member"):
        return st.session_state["member"]
    entered = st.query_params.get("k", "")
    if not entered:
        st.markdown('<div class="meta">MEMBERS ONLY</div>', unsafe_allow_html=True)
        entered = st.text_input("Access code", type="password", placeholder="Enter your member code")
    if entered in inv:
        st.session_state["member"] = inv[entered]
        st.rerun()
    elif entered:
        st.error("That code isn't recognised.")
    st.stop()

st.markdown('<div class="brand"><div class="wm">HIGH SOCIETY</div><div class="bx">×</div>'
            '<div class="wm2">TFF@HIGHTABLE</div><div class="tg" style="margin-top:12px">GOLD NEWS BIAS</div></div>',
            unsafe_allow_html=True)
member = gate()
who = f" · {member.upper()}" if member else ""
try:
    ev, cal_ok, cal_err, cal_all = events()
    pxd, src = prices()
    px = pxd["Close"]
except Exception as e:
    st.error(f"Could not load data: {e}")
    st.stop()
try:
    mk = market()
    ml = market_lean(mk)
except Exception:
    mk, ml = None, None

try:
    mac = macro()
except Exception:
    mac = {}

def clipv(x):
    return max(-1.0, min(1.0, x))

def grade_of(S):
    a, side = abs(S), ("bullish" if S > 0 else "bearish")
    return (f"Strongly {side}" if a >= 0.60 else side.capitalize() if a >= 0.30
            else f"Slightly {side}" if a >= 0.10 else "Neutral")

# Factor groups, so correlated inputs are averaged inside a group instead of counted twice.
FOMC_W = {"rate path": 0.35, "dollar": 0.20, "10y yield": 0.15, "inflation/jobs": 0.30}

def cons_of(e):
    for _, r_ in e.iterrows():
        if "federal funds rate" in str(r_["title"]).lower():
            f, p = num(r_["forecast"]), num(r_["previous"])
            if f is not None and p is not None:
                return f - p
    return None

def fomc_engine(m, mac, cons_pp):
    """Evidence-based FOMC outlook for gold. Direction (S) and confidence are calculated separately.
    Positive S = bullish for gold. Neutral only when the evidence is genuinely balanced; missing inputs
    lower confidence (or return 'Insufficient data') instead of silently becoming Neutral."""
    g = {}
    if m:
        parts = []
        if "Fed funds futures" in m:
            parts.append(clipv(m["Fed funds futures"][1] / 0.10))
        if "13-week yield" in m:
            parts.append(-clipv(m["13-week yield"][1] / 0.10))
        if parts:
            g["rate path"] = sum(parts) / len(parts)
        g["dollar"] = -clipv(m["Dollar (DXY)"][2] / 1.0)
        g["10y yield"] = -clipv(m["US 10Y yield"][1] / 0.20)
    parts = []
    if "cpi_trend" in mac:
        parts.append(-clipv(mac["cpi_trend"] / 0.5))
    if "unrate_trend" in mac:
        parts.append(clipv(mac["unrate_trend"] / 0.3))
    if parts:
        g["inflation/jobs"] = sum(parts) / len(parts)
    avail = [k for k in FOMC_W if k in g]
    missing = [k for k in FOMC_W if k not in g]
    if not avail:
        return {"S": None, "grade": "Insufficient data", "bias": "Neutral", "conf": 0, "spike": "No edge",
                "lines": ["Insufficient data: no market or macro inputs could be loaded. This is not a neutral call; "
                          "the outlook returns as soon as the inputs do."]}
    wsum = sum(FOMC_W[k] for k in avail)
    S = sum(g[k] * FOMC_W[k] for k in avail) / wsum
    bias = "Bullish gold" if S >= 0.10 else "Bearish gold" if S <= -0.10 else "Neutral"
    cov = len(avail) / len(FOMC_W)
    sign = 1 if S > 0 else -1
    if bias == "Neutral":
        agr = 1 - min(1.0, max(abs(g[k]) for k in avail))
    else:
        gross = sum(FOMC_W[k] * abs(g[k]) for k in avail)
        agr = abs(sum(FOMC_W[k] * g[k] for k in avail)) / gross if gross else 0.0
    conf = 100 * (cov ** 0.7) * (0.35 + 0.65 * agr)  # coverage and agreement of the evidence; not its size
    # is the decision already priced in? rough: front-month fed funds futures vs the effective rate
    implied = None
    if m and "Fed funds futures" in m and mac.get("DFF") is not None:
        implied = ((100 - m["Fed funds futures"][0]) - mac["DFF"]) * 100
    cat = lambda bp: "cut" if bp <= -15 else "hike" if bp >= 15 else "hold"
    cons = None if cons_pp is None else cons_pp * 100
    if implied is not None and cons is not None:
        priced = "aligned" if cat(implied) == cat(cons) else "disagree"
    elif implied is not None or cons is not None:
        priced = "partial"
    else:
        priced = "unknown"
    stale = False
    if m and m.get("_asof") is not None:
        try:
            a = pd.Timestamp(m["_asof"])
            a = a.tz_localize("UTC") if a.tzinfo is None else a.tz_convert("UTC")
            stale = (pd.Timestamp.now(tz="UTC") - a).days > 4
        except Exception:
            pass
    unc = []
    if missing:
        unc.append("no data for " + ", ".join(missing))
    if stale:
        conf *= 0.8
        unc.append("market data looks stale")
    if priced in ("unknown", "partial"):
        conf *= 0.9
        unc.append("priced-in check incomplete")
    conf = int(max(5, min(95, conf)))
    dtxt = lambda k: f"{k} ({'bullish' if g[k] > 0 else 'bearish'} for gold, {g[k]:+.2f})"
    lines = [f"Broader outlook: {grade_of(S)} for gold (evidence score {S:+.2f}; confidence is a separate measure)"]
    if bias != "Neutral":
        sup = sorted([k for k in avail if g[k] * sign > 0.10], key=lambda k: -abs(g[k] * FOMC_W[k]))
        con = [k for k in avail if g[k] * sign < -0.10]
        if sup:
            lines.append("Supports: " + "; ".join(dtxt(k) for k in sup))
        if con:
            lines.append("Conflicts: " + "; ".join(dtxt(k) for k in con))
    else:
        lean_ = [dtxt(k) for k in avail if abs(g[k]) > 0.10]
        lines.append("Evidence is genuinely balanced: " + ("; ".join(lean_) if lean_ else "no factor leans either way"))
    if priced == "aligned":
        lines.append(f"Priced in: futures imply {implied:+.0f}bp (rough) and consensus expects {cons:+.0f}bp, both a {cat(cons)}")
    elif priced == "disagree":
        lines.append(f"Not fully priced in: futures imply {implied:+.0f}bp (rough) vs consensus {cons:+.0f}bp, so surprise risk is higher")
    elif priced == "partial":
        lines.append("Priced-in check incomplete: only one of futures-implied and consensus rate change is available")
    else:
        lines.append("Priced-in unknown: no futures or consensus data")
    gold5 = m["Gold"][2] if m else None
    if priced == "aligned" and gold5 is not None and abs(gold5) >= 2 and bias != "Neutral" and (gold5 > 0) == (S > 0):
        spike = "Bearish gold" if S > 0 else "Bullish gold"
        lines.append(f"Initial spike: leans AGAINST the broader outlook (weak evidence): the decision looks priced in and gold "
                     f"already moved {gold5:+.1f}% in the outlook's direction, so a sell-the-news reversal is plausible")
    elif priced == "aligned":
        spike = "No edge"
        lines.append("Initial spike: likely modest because the decision looks priced in; direction unknown")
    else:
        spike = "No edge"
        lines.append("Initial spike: no reliable evidence for its direction")
    lines.append("Reversal vs continuation: no probability until FOMC history exists. Statement, projections and press "
                 "conference are separate catalysts and are not scored automatically (they need text analysis)")
    if unc:
        lines.append("Limits: " + "; ".join(unc))
    return {"S": S, "grade": grade_of(S), "bias": bias, "conf": conf, "spike": spike, "lines": lines}

ENG = fomc_engine(mk, mac, cons_of(ev))

now = pd.Timestamp.now(tz="UTC")
cal_age = (now - pd.Timestamp(cal_ok)).total_seconds() if cal_ok else None
cal_stale = cal_age is None or cal_age > 6 * 3600
if cal_ok:
    ok_t = pd.Timestamp(cal_ok).tz_convert("Africa/Johannesburg").strftime("%H:%M")
    cal_label = f"CALENDAR REFRESH FAILING · LAST OK {ok_t} SAST" if cal_err else f"CALENDAR UPDATED {ok_t} SAST"
else:
    cal_label = "CALENDAR NOT LOADED"
st.markdown(f'<div class="meta">{cal_label}{who}</div>', unsafe_allow_html=True)
if cal_stale or cal_err:
    st.warning("Calendar data may be stale" + (f" (last refresh failed: {cal_err})" if cal_err else "")
               + ". Showing the last saved calendar; times and forecasts may have changed.")

def fetch_live():
    try:
        p, s_, ts = live_price()
        st.session_state["live"] = {"p": p, "src": s_, "ts": ts, "got": time.time()}
        st.session_state["live_err"] = None
    except Exception as e:
        st.session_state["live_err"] = type(e).__name__

@st.fragment(run_every=5)
def top_row():
    fetch_live()
    lv, err = st.session_state.get("live"), st.session_state.get("live_err")
    try:
        m = market()
    except Exception:
        m = None
    c1, c2, c3 = st.columns(3)
    if lv:
        c1.metric("XAUUSD", f"{lv['p']:,.2f}", f"{m['Gold'][2]:+.1f}% 5d" if m else None, delta_color="off")
    elif len(px):
        c1.metric("XAUUSD (delayed)", f"{px.iloc[-1]:,.2f}")
    if m:
        c2.metric("DOLLAR", f"{m['Dollar (DXY)'][0]:.1f}", f"{m['Dollar (DXY)'][2]:+.1f}% 5d", delta_color="off")
        c3.metric("US 10Y", f"{m['US 10Y yield'][0]:.2f}%", f"{m['US 10Y yield'][1]:+.2f} 5d", delta_color="off")
    tick = pd.Timestamp.now(tz="Africa/Johannesburg").strftime("%H:%M:%S")
    if lv:
        age = int(time.time() - lv["got"])
        flag = ' · <span style="color:#FF3B30">STALE</span>' if (age > 30 or err) else ""
        st.markdown(f'<div class="meta">{lv["src"].upper()} · PRICE REQUESTED {age}s AGO · SCREEN {tick} SAST{flag}</div>',
                    unsafe_allow_html=True)
    else:
        st.markdown('<div class="meta" style="color:#FF3B30">LIVE PRICE UNAVAILABLE · RETRYING EVERY 5s</div>',
                    unsafe_allow_html=True)

top_row()

with st.expander("FOMC view"):
    fm = st.selectbox("FOMC bias source", ["Auto (market)", "Bearish gold", "Bullish gold", "Neutral"])
    mc = st.slider("Manual confidence", 0, 100, 60)
    if ml:
        st.caption(f"Market lean now: {ml[0]} · {ml[1]}% — " + " · ".join(ML_WHY))
    st.caption(f"FOMC evidence model: {ENG['grade']} · confidence {ENG['conf']}/100")

def fomc_view():
    if fm == "Auto (market)":
        return ENG["bias"], ENG["conf"], "FOMC · EVIDENCE MODEL"
    return fm, (mc if fm != "Neutral" else 0), "FOMC · MANUAL VIEW"

h = load_hist()

def evaluable():
    d = h[(pd.to_numeric(h.pre_release, errors="coerce") == 1) & h.hit.notna()].copy()
    d["hit"] = pd.to_numeric(d.hit, errors="coerce")
    return d

def validation(label):
    """Per event type: pending (awaiting outcome), completed (outcome recorded after the 30-minute window),
    and whether the record is validated. Five completed calls lift the confidence cap but are not proof."""
    d = h[(pd.to_numeric(h.pre_release, errors="coerce") == 1) & (h.title.map(tag) == label)]
    done = d[d.move30.notna()]
    dirn = done[done.hit.notna()]
    n = len(dirn)
    hits = float(pd.to_numeric(dirn.hit).sum()) if n else 0.0
    out = {"pending": len(d) - len(done), "completed": len(done), "n": n, "hits": hits, "state": "collecting"}
    if n >= 30:
        p, z = hits / n, 1.96
        den = 1 + z * z / n
        c = (p + z * z / (2 * n)) / den
        w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
        out["lo"], out["hi"] = c - w, c + w
        out["state"] = "validated" if out["lo"] > 0.5 else "no_clear_edge"
    elif n >= 5:
        out["state"] = "preliminary"
    return out

def validation_text(label):
    v = validation(label)
    pend = f"{v['pending']} awaiting outcome"
    if v["state"] == "collecting":
        return f"Historical validation: waiting for 5 completed {label} predictions ({v['n']} completed, {pend})."
    rate = v["hits"] / v["n"]
    if v["state"] == "preliminary":
        return (f"Historical validation (preliminary): {int(v['hits'])}/{v['n']} {label} predictions directionally correct "
                f"({rate:.0%}), {pend}. Five results are not proof of accuracy, so tracking continues.")
    span = f"95% interval {v['lo']:.0%}-{v['hi']:.0%}"
    if v["state"] == "validated":
        return f"Historical validation: {rate:.0%} directional accuracy over {v['n']} {label} predictions ({span}), {pend}."
    return f"Historical validation: no statistically clear edge yet over {v['n']} {label} predictions ({span}), {pend}."

def reliability(label):
    v = validation(label)
    if v["n"] < 5:
        return None, v["n"]
    return (v["hits"] + 2.5) / (v["n"] + 5), v["n"]

def calibrated_prob(label, bias):
    d = evaluable()
    d = d[(d.title.map(tag) == label) & (d.bias == bias)]
    if len(d) < 20:
        return None, len(d)
    return (d.hit.sum() + 1) / (len(d) + 2), len(d)

def expected_move(label):
    """Typical size, in USD per ounce, of XAUUSD's price change over the 15 minutes after a release.
    Magnitude only: it says nothing about direction and is not a target."""
    base = float(px.diff(15).abs().median()) if len(px) > 100 else None
    d = h[(pd.to_numeric(h.pre_release, errors="coerce") == 1) & h.move30.notna() & (h.title.map(tag) == label)]
    n = len(d)
    e5 = e15 = e30 = None
    if n >= 3:
        e15 = float(pd.to_numeric(d.move15).abs().median())
        e5 = float(pd.to_numeric(d.move5).abs().median())
        e30 = float(pd.to_numeric(d.move30).abs().median())
    if e15 is not None and base is not None:
        w = n / (n + 5)
        v = w * e15 + (1 - w) * base
        method = (f"blend of {n} past {label} reactions (median 15m move ${e15:.2f}) and the 5-day typical 15m move "
                  f"(${base:.2f})")
    elif e15 is not None:
        v, method = e15, f"median 15m move over {n} past {label} releases"
    elif base is not None:
        v, method = base, f"typical 15m XAUUSD move over the last 5 days (no completed {label} reactions yet)"
    else:
        return {"v": None, "n": n, "status": "unavailable", "method": "no price history available", "v5": None, "v30": None}
    return {"v": v, "n": n, "status": "event-based" if n >= 10 else "provisional", "method": method, "v5": e5, "v30": e30}

def confidence(label, bias, strength):
    """Confidence in the EVIDENCE for this call. It is not a probability and is independent of expected move."""
    if bias == "Neutral":
        return 0, []
    c, why = float(strength), [f"signal score {strength}"]
    if ml:
        if ml[0] == bias:
            c *= 1.15
            why.append("market agrees")
        elif ml[0] == "Neutral":
            c *= 0.85
            why.append("market neutral")
        else:
            c *= 0.7
            why.append("market disagrees")
    return int(min(c, 95)), why

def resolve(title, bias, s):
    lbl = tag(title)
    if lbl == "FOMC":
        b, c, l = fomc_view()
        return b, c, l, ENG["lines"]
    c, why = confidence(lbl, bias, s)
    return bias, c, lbl, why

def build_card(r):
    base = tag(r["title"])
    post = bool(r.get("post", False))
    bias, conf, lbl, _ = resolve(r["title"], r["bias"], int(r["strength"]))
    manual = base == "FOMC" and fm != "Auto (market)"
    f_, p_ = r["forecast"] or "-", r["previous"] or "-"
    missing = []
    if base != "FOMC" and not post and (num(r["forecast"]) is None or num(r["previous"]) is None):
        missing.append("forecast or previous figure")
    if not ml:
        missing.append("market data")
    if cal_stale:
        missing.append("fresh calendar")
    if bias != "Neutral" and not manual:
        conf = int(conf * (0.85 ** len([m for m in missing if m != "forecast or previous figure"])))
    v = validation(base)
    rel, n = reliability(base)
    has_call = (bias != "Neutral") or (base == "FOMC" and not manual)
    collecting = has_call and not manual and v["n"] < 5
    if collecting and conf > 60:
        conf = 60
    if bias != "Neutral" and not manual and rel is not None and rel < 0.5:
        conf = int(conf * max(0.6, 2 * rel))  # measured performance can only pull confidence down, never auto-raise it
    pr, pn = calibrated_prob(base, bias) if bias != "Neutral" else (None, 0)
    em = expected_move(base)
    q = float(conf) - 8 * len(missing)
    quality = "No signal" if (bias == "Neutral" and not (base == "FOMC" and not manual)) else (
        "High" if q >= 60 else "Medium" if q >= 35 else "Low")
    if v["state"] != "validated" and quality == "High":
        quality = "Medium"
    grade, spike = None, None
    if base == "FOMC" and not manual:
        grade, spike = ENG["grade"], ENG["spike"]
        expl = list(ENG["lines"])
    elif base == "FOMC":
        expl = ["Why: your manual view (not generated by the model)"]
    else:
        side = "stronger" if bias == "Bearish gold" else "weaker"
        if post:
            expl = [f"Why (post-release surprise): actual {r['actual']} vs forecast {f_} points to a {side} USD "
                    f"(surprise score {int(r['strength'])})"]
        elif bias == "Neutral":
            expl = ["Waiting for data: forecast and previous values are too close or unavailable"]
        else:
            expl = [f"Why: forecast {f_} vs previous {p_} implies a {side} USD (prior score {int(r['strength'])}). "
                    f"{CTX.get(base, '')}"]
        sup, con = [], []
        if bias != "Neutral" and ml:
            drv = ", ".join(ML_WHY[:-1])
            if ml[0] == bias:
                sup.append(f"cross-market agrees ({drv})")
            elif ml[0] == "Neutral":
                con.append(f"cross-market is neutral, so nothing confirms it ({drv})")
            else:
                con.append(f"cross-market leans {ml[0].lower()} ({drv})")
        if rel is not None and bias != "Neutral":
            (sup if rel >= 0.5 else con).append(f"past {base} calls were {rel:.0%} directionally correct ({n})")
        if sup:
            expl.append("Supports: " + "; ".join(sup))
        if con:
            expl.append("Conflicts: " + "; ".join(con))
    if bias != "Neutral" or base == "FOMC":
        expl.append(validation_text(base))
    if collecting:
        expl.append("Confidence: capped at 60/100 until sufficient historical results are available.")
    if em["v"] is not None:
        extra = f" Initial 5m ~${em['v5']:.2f}, 30m ~${em['v30']:.2f}." if em["v5"] is not None else ""
        expl.append(f"Expected move: estimated size of XAUUSD's 15-minute price change (magnitude only, not a direction "
                    f"or target). Method: {em['method']} ({em['status']}).{extra}")
    else:
        expl.append("Expected move: unavailable (no price history).")
    lim = []
    if not post and base != "FOMC":
        lim.append("the real surprise (actual vs forecast) is unknown until release; revisions are not in the feed")
    if missing:
        lim.append("missing or stale: " + ", ".join(missing))
    if lim:
        expl.append("Limits: " + "; ".join(lim))
    vol = "unavailable" if em["v"] is None else f"~${em['v']:.2f}" + ("" if em["status"] == "event-based" else " (provisional)")
    return {"key": r["key"], "time": r["time"], "title": r["title"], "lbl": lbl, "base": base, "bias": bias,
            "base_s": int(r["strength"]), "conf": conf, "forecast": r["forecast"], "previous": r["previous"],
            "prob": f"{pr:.0%} (n={pn})" if pr is not None else f"needs 20 calls ({pn})",
            "vol": vol, "quality": quality, "expl": expl, "manual": manual, "grade": grade, "spike": spike}

recent = ev[ev.time > now - pd.Timedelta(minutes=30)]
cards = [build_card(r) for _, r in recent.iterrows()]
st.session_state["cards"] = cards
cmap = {c["key"]: (None if c["manual"] else (c["bias"], c["base_s"] if c["base"] != "FOMC" else c["conf"],
                                             c["conf"], c.get("spike")))
        for c in cards}
h = update_history(h, ev, pxd, cmap)

def sig_of(e, m, err, extra=None):
    k = tuple(map(tuple, e[["key", "time", "forecast", "previous", "actual"]].astype(str).values.tolist())) if len(e) else ()
    return (k, m, bool(err), extra)

st.session_state["sig"] = sig_of(ev, ml, cal_err, (ENG["grade"], ENG["conf"]))

@st.fragment(run_every=60)
def watcher():
    """Re-checks calendar and market every minute; reruns the whole page only if something that feeds
    bias or confidence actually changed."""
    try:
        e2, _, er2, _ = events()
        m2 = market()
        eng2 = fomc_engine(m2, macro(), cons_of(e2))
        s2 = sig_of(e2, market_lean(m2), er2, (eng2["grade"], eng2["conf"]))
    except Exception:
        return
    if s2 != st.session_state.get("sig"):
        st.rerun()

watcher()

def countdown(t, nowt):
    s = int((t - nowt).total_seconds())
    if s > 0:
        d, r_ = divmod(s, 86400)
        hh, r_ = divmod(r_, 3600)
        mm, ss = divmod(r_, 60)
        return f"IN {d}d {hh:02d}:{mm:02d}:{ss:02d}" if d else f"IN {hh:02d}:{mm:02d}:{ss:02d}"
    a = -s
    return f"RELEASED {a // 60}m {a % 60:02d}s AGO"

def post_line(c, nowt, pxs):
    t = c["time"]
    if nowt < t:
        return ""
    if not len(pxs) or pxs.index[-1] < t:
        return "Post-release: waiting for price data (1-minute feed lags)"
    mv = float(pxs.iloc[-1] - pxs.asof(t - pd.Timedelta(minutes=1)))
    d = 1 if c["bias"] == "Bullish gold" else -1 if c["bias"] == "Bearish gold" else 0
    verdict = "" if d == 0 else (" and confirms the pre-release bias" if d * mv > 0 else
                                 " and contradicts the pre-release bias" if d * mv < 0 else "")
    return f"Post-release (separate from the prediction): gold {mv:+.2f} since release{verdict}"

def card_html(c, nowt, pxs):
    bias, conf = c["bias"], c["conf"]
    col, cc = bias_color(bias), conf_color(bias, conf)
    bar = f'<div class="bar"><div style="width:{conf}%;background:{cc}"></div></div>' if bias != "Neutral" else ""
    conf_txt = f"{conf}%" if (bias != "Neutral" or c.get("grade")) else "n/a"
    kv = (f'<div class="kv"><span>CONFIDENCE <b style="color:{cc}">{conf_txt}</b></span>'
          f'<span>P(DIRECTION) <b>{c["prob"]}</b></span><span>EXPECTED 15M MOVE <b>{c["vol"]}</b></span>'
          f'<span>SIGNAL QUALITY <b>{c["quality"]}</b></span></div>')
    post = post_line(c, nowt, pxs)
    why = "<br>".join(c["expl"] + ([post] if post else []))
    return (f'<div class="card" style="border-left:6px solid {col};box-shadow:0 0 14px {col}33">'
            f'<div class="tag">{c["lbl"]} · {countdown(c["time"], nowt)}</div><div class="ttl">{c["title"]}</div>'
            f'<div class="sub">{sast(c["time"])} SAST</div>'
            f'<div class="sub">Forecast: {c["forecast"] or "-"} | Previous: {c["previous"] or "-"}</div>'
            f'<div class="bias" style="color:{col}">Bias: {arrow(bias)} {(c.get("grade") or bias).upper()}</div>{bar}{kv}'
            f'<div class="why">{why}</div>{pair_block(bias)}</div>')

@st.fragment(run_every=1)
def cards_view():
    nowt = pd.Timestamp.now(tz="UTC")
    try:
        pxs = prices()[0]["Close"]
    except Exception:
        pxs = pd.Series(dtype=float)

    upcoming = cal_all.copy()
    if len(upcoming):
        upcoming = upcoming[
            (upcoming["status"] == "scheduled") &
            (upcoming["impact"] == "High")
        ].copy()
        upcoming["time"] = pd.to_datetime(upcoming["time"], utc=True, errors="coerce")
        upcoming = upcoming[
            upcoming["time"].notna() & (upcoming["time"] > nowt)
        ].sort_values("time").head(3)

    if upcoming.empty:
        st.info("No upcoming high-impact USD events are currently available.")
        return

    cards_by_key = {c["key"]: c for c in st.session_state.get("cards", [])}
    for _, r in upcoming.iterrows():
        c = cards_by_key.get(r["key"])
        if c:
            st.markdown(card_html(c, nowt, pxs), unsafe_allow_html=True)
        else:
            forecast = str(r.get("forecast", "") or "").strip() or "Pending"
            previous = str(r.get("previous", "") or "").strip() or "Unavailable"
            st.markdown(
                f"<div class=\"card\"><div class=\"ttl\">{r['title']}</div>"
                f"<div class=\"sub\">{sast(r['time'])} SAST</div>"
                f"<div class=\"bias\">Prediction pending</div>"
                f"<div class=\"why\">Forecast: {forecast} · Previous: {previous}</div></div>",
                unsafe_allow_html=True,
            )

tab1, tab2, tab3 = st.tabs(["Upcoming", "Calendar", "Track record"])

with tab1:
    cards_view()

with tab2:
    rows = []
    for c in cards:
        if c["time"] > now:
            txt = f"{arrow(c['bias'])} {c.get('grade') or c['bias'].replace(' gold', '')}" + (f" · {c['conf']}%" if (c["bias"] != "Neutral" or c.get("grade")) else "")
            rows.append((c["time"], c["title"], txt))
    cutoff = max(ev.time.max(), now) if len(ev) else now
    try:
        auto = bls_dates()
    except Exception:
        auto = []
    later_list = (auto + [x for x in LATER if x[0] == "FOMC"]) if auto else LATER
    for lbl, name, when_et in later_list:
        t = pd.Timestamp(when_et, tz="America/New_York").tz_convert("UTC")
        if t <= cutoff or t > now + pd.Timedelta(days=75):
            continue
        if lbl == "FOMC":
            b, s, _ = fomc_view()
            gl = ENG["grade"] if fm == "Auto (market)" else b.replace(" gold", "")
            txt = f"{arrow(b)} {gl}" + (f" · {s}%" if (b != "Neutral" or fm == "Auto (market)") else "")
        else:
            txt = "Waiting for forecast"
        rows.append((t, name, txt))
    cal = pd.DataFrame([(sast(t), n, b) for t, n, b in sorted(rows)], columns=["When (SAST)", "Event", "Bias"])
    st.dataframe(cal, hide_index=True, use_container_width=True)
    gone = cal_all[(cal_all.status == "cancelled_or_moved") & (pd.to_datetime(cal_all.time, utc=True) > now)] if len(cal_all) else cal_all
    if len(gone):
        st.warning("Removed or rescheduled since the last check: " + ", ".join(gone.title))
    st.caption("The calendar re-checks every 60 seconds and keeps itself up to date. Events past next week come "
               "from the BLS schedule (FOMC dates are typed in) and can change.")

with tab3:
    ok = evaluable()
    for c_ in ["move15", "net15", "mfe", "mae", "reversal", "confidence"]:
        ok[c_] = pd.to_numeric(ok[c_], errors="coerce")
    if len(ok):
        a1, a2, a3 = st.columns(3)
        a1.metric("Directional accuracy (5m)", f"{ok.hit.mean():.0%}", f"{len(ok)} calls", delta_color="off")
        a2.metric("Profitable after costs (15m)", f"{(ok.net15 > 0).mean():.0%}", f"cost ${COST:.2f}", delta_color="off")
        a3.metric("Reversal rate", f"{ok.reversal.mean():.0%}")
        by = ok.assign(event=ok.title.map(tag)).groupby("event").agg(
            hit=("hit", "mean"), calls=("hit", "count"), move15=("move15", lambda x: x.abs().median()),
            mfe=("mfe", "mean"), mae=("mae", "mean")).round(2)
        by["hit"] = (by["hit"] * 100).round(0).astype(int).astype(str) + "%"
        st.dataframe(by.rename(columns={"hit": "accuracy", "move15": "median |15m|"}), use_container_width=True)
        bands = pd.cut(ok.confidence, [-1, 30, 60, 100], labels=["0-30", "30-60", "60-100"])
        band = ok.groupby(bands, observed=True).agg(accuracy=("hit", "mean"), calls=("hit", "count"))
        if len(band):
            band["accuracy"] = (band["accuracy"] * 100).round(0).astype(int).astype(str) + "%"
            st.caption("Does higher confidence actually win more often?")
            st.dataframe(band, use_container_width=True)
    else:
        st.caption("No completed pre-release calls yet. A call counts as completed 30 minutes after its release, once its outcome is recorded.")
    pre = h[pd.to_numeric(h.pre_release, errors="coerce") == 1]
    tags = sorted(set(pre.title.map(tag)))
    if tags:
        vt = pd.DataFrame([{"event": t_, "pending": validation(t_)["pending"], "completed": validation(t_)["completed"],
                            "directional": validation(t_)["n"], "status": validation(t_)["state"].replace("_", " ")}
                           for t_ in tags])
        st.caption("Validation status by event type (pending = awaiting outcome)")
        st.dataframe(vt, hide_index=True, use_container_width=True)
    if len(ok):
        dd = ok.bias.map({"Bullish gold": 1, "Bearish gold": -1})
        b1, b2 = st.columns(2)
        b1.metric("False signals (5m)", f"{int((ok.hit == 0).sum())} of {len(ok)}", delta_color="off")
        b2.metric("Initial 1m move agreed", f"{(dd * pd.to_numeric(ok.move1, errors='coerce') > 0).mean():.0%}", delta_color="off")
        st.caption("Forecast error needs actual figures, which the calendar feed does not reliably supply, so it is not tracked yet.")
    fo = pre[(pre.title.map(tag) == "FOMC") & pre.move30.notna()].copy()
    if len(fo):
        for c_ in ["move5", "move30", "move60"]:
            fo[c_] = pd.to_numeric(fo[c_], errors="coerce")
        fo["decision 0-5m"], fo["statement 5-30m"], fo["press conf 30-60m"] = fo.move5, fo.move30 - fo.move5, fo.move60 - fo.move30
        st.caption("FOMC reactions by phase ($). Outlook = broader view, spike = predicted first move.")
        st.dataframe(fo[["time", "bias", "spike_bias", "decision 0-5m", "statement 5-30m", "press conf 30-60m"]].round(2), hide_index=True)
    if st.session_state.get("db_err"):
        st.warning("The database rejected a save. Run the SQL migration, then reload.")
    st.dataframe(h[pd.to_numeric(h.pre_release, errors="coerce") == 1]
                 .drop(columns=["key", "pre_release"]).sort_values("time", ascending=False).head(30), hide_index=True)
    st.caption("Directionally correct is not the same as profitable: the profit view subtracts an assumed "
               f"${COST:.2f} per ounce for spread and slippage and ignores execution risk. Only predictions logged "
               "before release are evaluated. Reaction data is 1-minute resolution, so 5s/15s/30s moves are not available.")

st.caption("Countdowns tick every second (screen only). Price is re-requested every 5 seconds, the calendar and "
           "market inputs every 60 seconds, and bias and confidence are recalculated when those inputs change. "
           "Pair arrows show the usual USD relationship if the bias plays out. Confidence reflects the evidence, "
           "not a calibrated probability. Not financial advice.")
st.markdown('<div class="foot">HIGH SOCIETY<br><span class="fx">×</span><br>TFF@HIGHTABLE</div>', unsafe_allow_html=True)
