import re, os, requests, pandas as pd, streamlit as st, yfinance as yf

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
HIST = "history.csv"
USD_DOWN = ["unemployment rate", "jobless claims", "unemployment claims"]
USD_UP = ["cpi", "ppi", "non-farm", "retail sales", "gdp", "ism", "pmi",
          "hourly earnings", "pce", "jolts", "durable"]

st.set_page_config(page_title="Gold News Bias", page_icon="🥇", layout="centered")

def num(s):
    if not s:
        return None
    m = re.search(r"-?\d+(\.\d+)?", s.replace(",", ""))
    if not m:
        return None
    return float(m.group()) * {"K": 1e3, "M": 1e6, "B": 1e9}.get(s.strip()[-1].upper(), 1)

def lean(title, fc, prev):
    t, f, p = title.lower(), num(fc), num(prev)
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
    df = df[(df.country == "USD") & (df.impact == "High")].copy()
    df["time"] = pd.to_datetime(df["date"], utc=True)
    out = df.apply(lambda r: lean(r["title"], r["forecast"], r["previous"]), axis=1)
    df["bias"], df["strength"] = [o[0] for o in out], [o[1] for o in out]
    return df.sort_values("time")

@st.cache_data(ttl=120)
def prices():
    s = yf.Ticker("GC=F").history(period="5d", interval="1m")["Close"]
    s.index = s.index.tz_convert("UTC")
    return s

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

st.markdown("""
<style>
.stApp{background:#000;color:#fff}
.card{background:#111;border-radius:10px;padding:14px 16px;margin-bottom:12px}
.ttl{font-size:1.05rem;font-weight:700;color:#fff}
.sub{font-size:.8rem;color:#9a9a9a;margin-top:2px}
.bias{font-size:1.15rem;font-weight:800;margin:8px 0 6px}
.bar{background:#2a2a2a;border-radius:6px;height:8px;overflow:hidden;margin-top:4px}
.bar div{height:8px}
</style>
""", unsafe_allow_html=True)

GREEN, RED, GREY = "#00C853", "#FF3B30", "#8A8A8A"

def bias_color(b):
    return GREEN if b == "Bullish gold" else RED if b == "Bearish gold" else GREY

def strength_color(s):
    return "#8A8A8A" if s < 40 else "#FFB300" if s < 70 else "#FFD700"

st.title("🥇 Gold News Bias")
try:
    ev, px = events(), prices()
except Exception as e:
    st.error(f"Could not load data: {e}")
    st.stop()

if len(px):
    st.metric("Gold futures (GC=F)", f"{px.iloc[-1]:,.2f}")
h = update_history(ev, px)

now = pd.Timestamp.now(tz="UTC")
up = ev[ev.time > now].copy()
st.subheader("Upcoming high-impact USD events")
if up.empty:
    st.info("No more high-impact USD events this week.")
for _, r in up.iterrows():
    c, s = bias_color(r["bias"]), int(r["strength"])
    when = r["time"].tz_convert("Africa/Johannesburg").strftime("%a %d %b, %H:%M")
    arrow = "▲" if r["bias"] == "Bullish gold" else "▼" if r["bias"] == "Bearish gold" else "●"
    strength_html = (
        f'<div class="bar"><div style="width:{s}%;background:{strength_color(s)}"></div></div>'
        f'<div class="sub">Signal strength {s}/100</div>' if r["bias"] != "Neutral" else "")
    st.markdown(
        f'<div class="card" style="border-left:6px solid {c}">'
        f'<div class="ttl">{r["title"]}</div>'
        f'<div class="sub">{when} SAST</div>'
        f'<div class="sub">Forecast: {r["forecast"] or "-"} | Previous: {r["previous"] or "-"}</div>'
        f'<div class="bias" style="color:{c}">{arrow} {r["bias"].upper()}</div>'
        f'{strength_html}</div>', unsafe_allow_html=True)

st.subheader("Track record")
done = h.dropna(subset=["hit"])
if len(done):
    st.metric("Hit rate (5 min after release)", f"{done.hit.mean():.0%}", f"{len(done)} calls")
else:
    st.caption("No finished calls yet. Results appear after events pass.")
st.dataframe(h.drop(columns=["key"]).tail(30), hide_index=True)

st.caption("Signal strength is an uncalibrated rule-based score, not a probability. "
           "Trust it only once the track record has enough calls. Not financial advice.")
