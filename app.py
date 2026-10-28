import re, os, json, time, requests, pandas as pd, streamlit as st, yfinance as yf

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FEED_NEXT = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
HIST = "history.csv"
USD_DOWN = ["unemployment rate", "jobless claims", "unemployment claims"]
USD_UP = ["cpi", "ppi", "non-farm", "retail sales", "gdp", "ism", "pmi",
          "hourly earnings", "pce", "jolts", "durable"]
GREEN, RED, GREY, BLUE = "#00C853", "#FF3B30", "#8A8A8A", "#00E5FF"
LATER = [
    ("NFP", "Non-Farm Employment Change (October)", "2026-11-06 08:30"),
    ("CPI", "CPI (October)", "2026-11-10 08:30"),
    ("PPI", "PPI (October)", "2026-11-13 08:30"),
    ("NFP", "Non-Farm Employment Change (November)", "2026-12-04 08:30"),
    ("FOMC", "FOMC Rate Decision", "2026-12-09 14:00"),
    ("CPI", "CPI (November)", "2026-12-10 08:30"),
    ("PPI", "PPI (November)", "2026-12-15 08:30"),
]

MODEL_VERSION = "v3"
COST = 1.0  # assumed spread + slippage in USD per ounce, used only for the "profitable after costs" view
UNIT = {"CPI": 0.1, "PPI": 0.1, "PCE": 0.1, "NFP": 50000, "JOBS": 0.1, "CLAIMS": 10000,
        "GDP": 0.5, "PMI": 1.0, "RETAIL": 0.3}  # size of a forecast-vs-previous gap that counts as a 50-point shift

st.set_page_config(page_title="High Society | Gold News Bias", layout="centered")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@300;500;700&display=swap');
#MainMenu, footer, header{visibility:hidden}
.stApp{background:#000;color:#fff}
.block-container{padding-top:1.6rem;max-width:720px}
.brand{text-align:center;padding:10px 0 18px;border-bottom:1px solid #0b3040;margin-bottom:18px}
.wm{font-family:'Montserrat',sans-serif;font-weight:300;font-size:1.7rem;letter-spacing:.45em;
    color:#fff;padding-left:.45em}
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
.foot{text-align:center;font-size:.68rem;letter-spacing:.25em;color:#5a5a5a;margin-top:26px}
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
    u = UNIT.get(tag(title))
    if u:
        strength = int(min(abs(f - p) / u * 50, 100))
    else:
        strength = int(min(abs(f - p) / max(abs(p), 0.1) * 200, 100))
    if strength < 10 or f == p:
        return "Neutral", 0
    usd = d if f > p else -d
    return ("Bearish gold" if usd > 0 else "Bullish gold"), strength

CAL_COLS = ["key", "title", "time", "impact", "forecast", "previous", "status", "updated_at"]
CAL_FILE = "calendar.csv"

def load_cal():
    d = sb_load("calendar", CAL_COLS)
    if d is None:
        d = pd.read_csv(CAL_FILE, dtype=str).fillna("") if os.path.exists(CAL_FILE) else pd.DataFrame(columns=CAL_COLS)
    return d.fillna("")

def fetch_feed():
    frames, got_next = [], False
    for i, u in enumerate([FEED, FEED_NEXT]):
        try:
            r = requests.get(u, timeout=15)
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
    df["key"] = df["title"] + "|" + df["time"].dt.strftime("%Y-%m-%d")
    return df.drop_duplicates("key"), got_next

@st.cache_data(ttl=60)
def sync_calendar():
    """Pull the feed, merge into the saved calendar (revisions, duplicates, cancellations), persist it."""
    old = load_cal()
    now = pd.Timestamp.now(tz="UTC")
    err = None
    try:
        df, got_next = fetch_feed()
        new = pd.DataFrame({"key": df.key, "title": df.title, "time": df.time.astype(str), "impact": df.impact,
                            "forecast": df.forecast.fillna(""), "previous": df.previous.fillna(""),
                            "status": "scheduled", "updated_at": str(now)})
        # the feed covers this week (and next week when that request worked); only judge events inside that window
        wk = now.normalize() - pd.Timedelta(days=now.weekday())
        lo, hi = now, wk + pd.Timedelta(days=14 if got_next else 7)
        old_ev = old[old.status != "meta"]
        old_sig = set(zip(old_ev.key, old_ev.time, old_ev.forecast, old_ev.previous, old_ev.status))
        changed = new[[(k, t, f, p, "scheduled") not in old_sig
                       for k, t, f, p in zip(new.key, new.time, new.forecast, new.previous)]]
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
                              "forecast": "", "previous": "", "status": "meta", "updated_at": str(now)}])
        delta = pd.concat([changed, gone, meta], ignore_index=True)
        merged.to_csv(CAL_FILE, index=False)
        if not sb_save("calendar", delta) and sb():
            st.session_state["db_err"] = True
        last_ok = str(now)
    except Exception as e:
        merged = old[old.status != "meta"]
        err = type(e).__name__
        m = old[old.status == "meta"]
        last_ok = m.updated_at.max() if len(m) else (merged.updated_at.max() if len(merged) else None)
    return merged, last_ok, err

def events():
    cal, last_ok, err = sync_calendar()
    df = cal[(cal.status == "scheduled") & (cal.impact == "High")].copy()
    empty = pd.DataFrame({"key": [], "title": [], "forecast": [], "previous": [], "bias": [],
                          "strength": [], "time": pd.to_datetime([], utc=True)})
    if df.empty:
        return empty, last_ok, err, cal
    df["time"] = pd.to_datetime(df["time"], utc=True)
    out = df.apply(lambda r: lean(r["title"], r["forecast"], r["previous"]), axis=1)
    df["bias"], df["strength"] = [o[0] for o in out], [o[1] for o in out]
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
    return out

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
        "logged_at", "pre_release", "move1", "move5", "move15", "move30", "mfe", "mae", "reversal", "net15", "hit"]

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
        b, s0, conf = cmap.get(key, (r["bias"], r["strength"], None))
        new = [str(t), b, s0, conf, r["forecast"], r["previous"]]
        idx = h.index[h.key == key]
        if len(idx) == 0:
            h.loc[len(h)] = [key, r["title"], str(t), b, s0, conf, r["forecast"], r["previous"], MODEL_VERSION,
                             str(now), 1] + [None] * 9
            dirty.append(key)
        else:
            i = idx[0]
            cur = [h.at[i, c] for c in ["time", "bias", "strength", "confidence", "forecast", "previous"]]
            if [_n(x) for x in cur] != [_n(x) for x in new]:
                for c, v in zip(["time", "bias", "strength", "confidence", "forecast", "previous"], new):
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
    if dirty:
        save_hist(h, dirty)
    return h

def tag(t):
    t = t.lower()
    if "fomc" in t or "federal funds" in t: return "FOMC"
    if "cpi" in t: return "CPI"
    if "ppi" in t: return "PPI"
    if "non-farm" in t: return "NFP"
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

st.markdown('<div class="brand"><div class="wm">HIGH SOCIETY</div>'
            '<div class="tg">GOLD NEWS BIAS</div></div>', unsafe_allow_html=True)
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

def fomc_view():
    if fm == "Auto (market)":
        b, s = ml if ml else ("Neutral", 0)
        return b, s, "FOMC · MARKET LEAN"
    return fm, (mc if fm != "Neutral" else 0), "FOMC · MANUAL VIEW"

h = load_hist()

def evaluable():
    d = h[(pd.to_numeric(h.pre_release, errors="coerce") == 1) & h.hit.notna()].copy()
    d["hit"] = pd.to_numeric(d.hit, errors="coerce")
    return d

def reliability(label):
    d = evaluable()
    d = d[d.title.map(tag) == label]
    if len(d) < 5:
        return None, len(d)
    return (d.hit.sum() + 2.5) / (len(d) + 5), len(d)

def calibrated_prob(label, bias):
    d = evaluable()
    d = d[(d.title.map(tag) == label) & (d.bias == bias)]
    if len(d) < 20:
        return None, len(d)
    return (d.hit.sum() + 1) / (len(d) + 2), len(d)

def expected_move(label):
    d = h[(pd.to_numeric(h.pre_release, errors="coerce") == 1) & h.move15.notna() & (h.title.map(tag) == label)]
    if len(d) >= 3:
        return float(pd.to_numeric(d.move15).abs().median()), f"median 15m move over {len(d)} past {label} releases"
    if len(px) > 100:
        return float(px.diff(15).abs().median()), "typical non-event 15m move (no event history yet)"
    return None, "no price history"

def confidence(label, bias, strength):
    """Confidence in the EVIDENCE: forecast shift adjusted by cross-market agreement. Track record is kept
    out of this number and only used for signal quality, so it is not counted twice."""
    if bias == "Neutral":
        return 0, []
    c, why = float(strength), [f"forecast shift {strength}"]
    if ml and ml[0] != "Neutral":
        if ml[0] == bias:
            c *= 1.15
            why.append("market agrees")
        else:
            c *= 0.7
            why.append("market disagrees")
    return int(min(c, 95)), why

def resolve(title, bias, s):
    lbl = tag(title)
    if lbl == "FOMC":
        b, c, l = fomc_view()
        return b, c, l, (ML_WHY if fm == "Auto (market)" else ["your manual view"])
    c, why = confidence(lbl, bias, s)
    return bias, c, lbl, why

def build_card(r):
    base = tag(r["title"])
    bias, conf, lbl, why = resolve(r["title"], r["bias"], int(r["strength"]))
    manual = base == "FOMC" and fm != "Auto (market)"
    missing = []
    if base != "FOMC" and (num(r["forecast"]) is None or num(r["previous"]) is None):
        missing.append("forecast or previous figure")
    if not ml:
        missing.append("market data")
    if cal_stale:
        missing.append("fresh calendar")
    if bias != "Neutral" and not manual:
        conf = int(conf * (0.85 ** len([m for m in missing if m != "forecast or previous figure"])))
    rel, n = reliability(base)
    capped = False
    if bias != "Neutral" and not manual and rel is None and conf > 60:
        conf, capped = 60, True
    pr, pn = calibrated_prob(base, bias) if bias != "Neutral" else (None, 0)
    vol, vsrc = expected_move(base)
    q = float(conf) * (max(0.6, min(1.2, 2 * rel)) if rel is not None else 1.0) - 8 * len(missing)
    quality = "No signal" if bias == "Neutral" else "High" if q >= 60 else "Medium" if q >= 35 else "Low"
    if rel is None and quality == "High":
        quality = "Medium"
    f_, p_ = r["forecast"] or "-", r["previous"] or "-"
    if base == "FOMC":
        expl = [("Why: " if bias != "Neutral" else "No clear edge: ") + "; ".join(why)]
    elif bias == "Neutral":
        expl = [f"No clear edge: forecast {f_} vs previous {p_} gives no usable shift"]
    else:
        expl = [f"Pre-release: forecast {f_} vs previous {p_} points to a "
                f"{'stronger' if bias == 'Bearish gold' else 'weaker'} USD (shift score {int(r['strength'])})"]
    if base != "FOMC" and bias != "Neutral" and ml:
        drivers = ", ".join(ML_WHY[:-1])
        expl.append(f"Cross-market supports it ({drivers})" if ml[0] == bias else
                    "Cross-market is neutral" if ml[0] == "Neutral" else
                    f"Conflict: cross-market leans {ml[0].lower()} ({drivers})")
    if base != "FOMC" and bias != "Neutral":
        expl.append(f"{base} track record: fewer than 5 finished calls" if rel is None else
                    (f"Conflict: past {base} calls hit only {rel:.0%} ({n})" if rel < 0.5 else
                     f"Past {base} calls hit {rel:.0%} ({n})"))
    if capped:
        expl.append(f"Confidence capped at 60 until {base} has 5 finished calls (untested model)")
    if missing:
        expl.append("Missing or stale: " + ", ".join(missing))
    return {"key": r["key"], "time": r["time"], "title": r["title"], "lbl": lbl, "base": base, "bias": bias,
            "base_s": int(r["strength"]), "conf": conf, "forecast": r["forecast"], "previous": r["previous"],
            "prob": f"{pr:.0%} (n={pn})" if pr is not None else f"needs 20 calls ({pn})",
            "vol": f"~${vol:.1f}/15m" if vol is not None else "n/a", "vsrc": vsrc,
            "quality": quality, "expl": expl, "manual": manual}

recent = ev[ev.time > now - pd.Timedelta(minutes=30)]
cards = [build_card(r) for _, r in recent.iterrows()]
st.session_state["cards"] = cards
cmap = {c["key"]: (None if c["manual"] else (c["bias"], c["base_s"] if c["base"] != "FOMC" else c["conf"], c["conf"]))
        for c in cards}
h = update_history(h, ev, pxd, cmap)

def sig_of(e, m, err):
    k = tuple(map(tuple, e[["key", "time", "forecast", "previous"]].astype(str).values.tolist())) if len(e) else ()
    return (k, m, bool(err))

st.session_state["sig"] = sig_of(ev, ml, cal_err)

@st.fragment(run_every=60)
def watcher():
    """Re-checks calendar and market every minute; reruns the whole page only if something that feeds
    bias or confidence actually changed."""
    try:
        e2, _, er2, _ = events()
        s2 = sig_of(e2, market_lean(market()), er2)
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
    kv = (f'<div class="kv"><span>CONFIDENCE <b style="color:{cc}">{conf}%</b></span>'
          f'<span>P(DIRECTION) <b>{c["prob"]}</b></span><span>EXPECTED MOVE <b>{c["vol"]}</b></span>'
          f'<span>SIGNAL QUALITY <b>{c["quality"]}</b></span></div>') if bias != "Neutral" else ""
    post = post_line(c, nowt, pxs)
    why = "<br>".join(c["expl"] + ([post] if post else []))
    return (f'<div class="card" style="border-left:6px solid {col};box-shadow:0 0 14px {col}33">'
            f'<div class="tag">{c["lbl"]} · {countdown(c["time"], nowt)}</div><div class="ttl">{c["title"]}</div>'
            f'<div class="sub">{sast(c["time"])} SAST</div>'
            f'<div class="sub">Forecast: {c["forecast"] or "-"} | Previous: {c["previous"] or "-"}</div>'
            f'<div class="bias" style="color:{col}">Bias: {arrow(bias)} {bias.upper()}</div>{bar}{kv}'
            f'<div class="why">{why}</div>{pair_block(bias)}</div>')

@st.fragment(run_every=1)
def cards_view():
    nowt = pd.Timestamp.now(tz="UTC")
    cs = [c for c in st.session_state.get("cards", []) if c["time"] > nowt - pd.Timedelta(minutes=30)]
    if not cs:
        st.info("No more high-impact USD events this week or next.")
        return
    try:
        pxs = prices()[0]["Close"]
    except Exception:
        pxs = pd.Series(dtype=float)
    for c in cs:
        st.markdown(card_html(c, nowt, pxs), unsafe_allow_html=True)

tab1, tab2, tab3 = st.tabs(["Upcoming", "Calendar", "Track record"])

with tab1:
    cards_view()

with tab2:
    rows = []
    for c in cards:
        if c["time"] > now:
            txt = f"{arrow(c['bias'])} {c['bias'].replace(' gold', '')}" + (f" · {c['conf']}%" if c["bias"] != "Neutral" else "")
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
            txt = f"{arrow(b)} {b.replace(' gold', '')}" + (f" · {s}%" if b != "Neutral" else "")
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
        st.caption("No finished pre-release calls yet. Results appear 30 minutes after each event.")
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
st.markdown('<div class="foot">HIGH SOCIETY</div>', unsafe_allow_html=True)
