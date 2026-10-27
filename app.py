import re, os, requests, pandas as pd, streamlit as st, yfinance as yf

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
    strength = int(min(abs(f - p) / max(abs(p), 0.1) * 200, 100))
    if strength < 10 or f == p:
        return "Neutral", 0
    usd = d if f > p else -d
    return ("Bearish gold" if usd > 0 else "Bullish gold"), strength

@st.cache_data(ttl=600)
def events():
    df = pd.DataFrame(requests.get(FEED, timeout=15).json())
    try:
        nxt = requests.get(FEED_NEXT, timeout=15).json()
        if isinstance(nxt, list) and nxt:
            df = pd.concat([df, pd.DataFrame(nxt)], ignore_index=True)
    except Exception:
        pass
    df = df[(df.country == "USD") & (df.impact == "High")].copy()
    if df.empty:
        return pd.DataFrame({"title": [], "forecast": [], "previous": [], "bias": [],
                             "strength": [], "time": pd.to_datetime([], utc=True)})
    df["time"] = pd.to_datetime(df["date"], utc=True)
    out = df.apply(lambda r: lean(r["title"], r["forecast"], r["previous"]), axis=1)
    df["bias"], df["strength"] = [o[0] for o in out], [o[1] for o in out]
    return df.sort_values("time")

def gold_series(period, interval):
    for tk, name in [("XAUUSD=X", "Spot XAUUSD"), ("GC=F", "Futures GC=F")]:
        try:
            s = yf.Ticker(tk).history(period=period, interval=interval)["Close"].dropna()
            if len(s) > 5:
                s.index = s.index.tz_localize("UTC") if s.index.tz is None else s.index.tz_convert("UTC")
                return s, name
        except Exception:
            pass
    return pd.Series(dtype=float), "unavailable"

@st.cache_data(ttl=120)
def prices():
    return gold_series("5d", "1m")

@st.cache_data(ttl=900)
def market():
    out = {}
    g, _ = gold_series("1mo", "1d")
    series = {"Gold": g}
    for k, tk in {"Dollar (DXY)": "DX-Y.NYB", "US 10Y yield": "^TNX"}.items():
        series[k] = yf.Ticker(tk).history(period="1mo", interval="1d")["Close"].dropna()
    for k, c in series.items():
        out[k] = (float(c.iloc[-1]), float(c.iloc[-1] - c.iloc[-6]),
                  float(c.iloc[-1] / c.iloc[-6] - 1) * 100)
    return out

def market_lean(m):
    clip = lambda x: max(-1, min(1, x))
    score = (-clip(m["Dollar (DXY)"][2] / 1.0) - clip(m["US 10Y yield"][1] / 0.20)
             + clip(m["Gold"][2] / 3.0)) / 3
    bias = "Bullish gold" if score > 0.15 else "Bearish gold" if score < -0.15 else "Neutral"
    return bias, (int(abs(score) * 100) if bias != "Neutral" else 0)

def update_history(ev, px):
    h = pd.read_csv(HIST) if os.path.exists(HIST) else pd.DataFrame(
        columns=["key", "title", "time", "bias", "strength", "move5", "move15", "hit"])
    for _, r in ev.iterrows():
        key = f"{r['title']}|{r['time']}"
        if key not in set(h.key):
            h.loc[len(h)] = [key, r["title"], str(r["time"]), r["bias"], r["strength"], None, None, None]
    for i, r in h[h.move15.isna()].iterrows():
        t = pd.Timestamp(r["time"])
        if len(px) and t + pd.Timedelta(minutes=15) <= px.index[-1] and t >= px.index[0]:
            p0 = px.asof(t)
            h.at[i, "move5"] = round(px.asof(t + pd.Timedelta(minutes=5)) - p0, 2)
            h.at[i, "move15"] = round(px.asof(t + pd.Timedelta(minutes=15)) - p0, 2)
            if r["bias"] != "Neutral":
                up = h.at[i, "move5"] > 0
                h.at[i, "hit"] = int((r["bias"] == "Bullish gold") == up)
    h.to_csv(HIST, index=False)
    return h

def tag(t):
    t = t.lower()
    if "fomc" in t or "federal funds" in t: return "FOMC"
    if "cpi" in t: return "CPI"
    if "ppi" in t: return "PPI"
    if "non-farm" in t: return "NFP"
    return "NEWS"

def bias_color(b):
    return GREEN if b == "Bullish gold" else RED if b == "Bearish gold" else GREY

def conf_color(bias, s):
    a = 0.45 if s < 40 else 0.75 if s < 70 else 1.0
    r, g, b = (0, 200, 83) if bias == "Bullish gold" else (255, 59, 48)
    return f"rgba({r},{g},{b},{a})"

def arrow(b):
    return "▲" if b == "Bullish gold" else "▼" if b == "Bearish gold" else "●"

def sast(t):
    return t.tz_convert("Africa/Johannesburg").strftime("%a %d %b, %H:%M")

st.markdown('<div class="brand"><div class="wm">HIGH SOCIETY</div>'
            '<div class="tg">GOLD NEWS BIAS</div></div>', unsafe_allow_html=True)
try:
    ev = events()
    px, src = prices()
except Exception as e:
    st.error(f"Could not load data: {e}")
    st.stop()
try:
    mk = market()
    ml = market_lean(mk)
except Exception:
    mk, ml = None, None

stamp = pd.Timestamp.now(tz="Africa/Johannesburg").strftime("%H:%M")
st.markdown(f'<div class="meta">{src.upper()} · UPDATED {stamp} SAST</div>', unsafe_allow_html=True)

c1, c2, c3 = st.columns(3)
gold_now = px.iloc[-1] if len(px) else (mk["Gold"][0] if mk else None)
if gold_now is not None and mk:
    c1.metric("XAUUSD", f"{gold_now:,.2f}", f"{mk['Gold'][2]:+.1f}% 5d", delta_color="off")
if mk:
    c2.metric("DOLLAR", f"{mk['Dollar (DXY)'][0]:.1f}", f"{mk['Dollar (DXY)'][2]:+.1f}% 5d", delta_color="off")
    c3.metric("US 10Y", f"{mk['US 10Y yield'][0]:.2f}%", f"{mk['US 10Y yield'][1]:+.2f} 5d", delta_color="off")

with st.expander("FOMC view"):
    fm = st.selectbox("FOMC bias source", ["Auto (market)", "Bearish gold", "Bullish gold", "Neutral"])
    mc = st.slider("Manual confidence", 0, 100, 60)
    if ml:
        st.caption(f"Market lean now: {ml[0]} · {ml[1]}% (dollar, yields and gold over 5 days)")

def fomc_view():
    if fm == "Auto (market)":
        b, s = ml if ml else ("Neutral", 0)
        return b, s, "FOMC · MARKET LEAN"
    return fm, (mc if fm != "Neutral" else 0), "FOMC · MANUAL VIEW"

def resolve(title, bias, s):
    lbl = tag(title)
    if lbl == "FOMC":
        return fomc_view()
    return bias, s, lbl

h = update_history(ev, px)
now = pd.Timestamp.now(tz="UTC")
up = ev[ev.time > now]

tab1, tab2, tab3 = st.tabs(["Upcoming", "Calendar", "Track record"])

with tab1:
    if up.empty:
        st.info("No more high-impact USD events this week or next.")
    for _, r in up.iterrows():
        bias, s, lbl = resolve(r["title"], r["bias"], int(r["strength"]))
        c = bias_color(bias)
        cc = conf_color(bias, s)
        bar = (f'<div class="bar"><div style="width:{s}%;background:{cc}"></div></div>'
               f'<div class="sub">Confidence: <b style="color:{cc}">{s}%</b></div>') if bias != "Neutral" else ""
        st.markdown(
            f'<div class="card" style="border-left:6px solid {c};box-shadow:0 0 14px {c}33">'
            f'<div class="tag">{lbl}</div><div class="ttl">{r["title"]}</div>'
            f'<div class="sub">{sast(r["time"])} SAST</div>'
            f'<div class="sub">Forecast: {r["forecast"] or "-"} | Previous: {r["previous"] or "-"}</div>'
            f'<div class="bias" style="color:{c}">Bias: {arrow(bias)} {bias.upper()}</div>{bar}</div>',
            unsafe_allow_html=True)

with tab2:
    rows = []
    for _, r in up.iterrows():
        bias, s, _ = resolve(r["title"], r["bias"], int(r["strength"]))
        txt = f"{arrow(bias)} {bias.replace(' gold', '')}" + (f" · {s}%" if bias != "Neutral" else "")
        rows.append((r["time"], r["title"], txt))
    cutoff = max(ev.time.max(), now) if len(ev) else now
    for lbl, name, when_et in LATER:
        t = pd.Timestamp(when_et, tz="America/New_York").tz_convert("UTC")
        if t <= cutoff:
            continue
        if lbl == "FOMC":
            b, s, _ = fomc_view()
            txt = f"{arrow(b)} {b.replace(' gold', '')}" + (f" · {s}%" if b != "Neutral" else "")
        else:
            txt = "Waiting for forecast"
        rows.append((t, name, txt))
    cal = pd.DataFrame([(sast(t), n, b) for t, n, b in sorted(rows)], columns=["When (SAST)", "Event", "Bias"])
    st.dataframe(cal, hide_index=True, use_container_width=True)
    st.caption("Forecasts appear about a week before release, and the bias shows up then. "
               "Dates beyond next week are typed in by hand and can change.")

with tab3:
    done = h.dropna(subset=["hit"])
    if len(done):
        st.metric("Hit rate (5 min after release)", f"{done.hit.mean():.0%}", f"{len(done)} calls")
    else:
        st.caption("No finished calls yet. Results appear after events pass.")
    st.dataframe(h.drop(columns=["key"]).tail(30), hide_index=True)

st.caption("Confidence is an uncalibrated rule-based score, not a probability. FOMC views are not "
           "counted in the track record. Not financial advice.")
st.markdown('<div class="foot">HIGH SOCIETY</div>', unsafe_allow_html=True)
