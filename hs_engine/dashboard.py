"""Clean main-screen view for the Upcoming tab: NEXT EVENT hero + compact UPCOMING list.
Everything shown is either calculated from data, taken from the existing analyst, or labelled as unmeasured/collecting."""
import html

import pandas as pd

from . import releases as R

GREEN, RED, GREY, AMBER = "#00C853", "#FF3B30", "#8A8A8A", "#FFB300"

CSS = """<style>
.hero{background:#0a0a0a;border:1px solid #0b3040;border-radius:12px;padding:16px 16px 12px;margin-bottom:14px}
.hero .lab,.mini .lab{font-size:.6rem;letter-spacing:.18em;color:#7a7a7a}
.trio{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0 4px}
.cell{background:#0f0f0f;border:1px solid #151515;border-radius:8px;padding:8px 6px;text-align:center}
.cell .val{font-size:1.05rem;font-weight:700;color:#fff;margin-top:2px}
.cell .sm{font-size:.62rem;color:#8a8a8a;margin-top:1px}
.blk{margin-top:12px;padding-top:10px;border-top:1px solid #151515}
.line{display:flex;justify-content:space-between;gap:10px;font-size:.78rem;padding:3px 0;color:#d0d0d0}
.line span:first-child{color:#7a7a7a;letter-spacing:.08em;font-size:.66rem;padding-top:2px;white-space:nowrap}
.line b{text-align:right}
.note{font-size:.66rem;color:#7a7a7a;line-height:1.5;margin-top:6px}
.chip{display:inline-block;font-size:.6rem;letter-spacing:.12em;padding:2px 8px;border-radius:10px;border:1px solid #333;margin-right:6px}
.mini{background:#0a0a0a;border:1px solid #0b3040;border-radius:10px;padding:10px 12px;margin-bottom:8px}
.mini .top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.mini .t{font-size:.88rem;font-weight:700;color:#fff}
.mini .w{font-size:.68rem;color:#9a9a9a;white-space:nowrap}
.mini .nums{display:flex;gap:14px;flex-wrap:wrap;margin-top:6px;font-size:.72rem;color:#cfcfcf}
.mini .nums span em{font-style:normal;color:#7a7a7a;font-size:.58rem;letter-spacing:.12em;margin-right:4px}
</style>"""

STATUS = {"forecast_ready": ("HS READY", GREEN), "insufficient_data": ("HS: WAITING FOR INPUTS", GREY),
          "waiting_consensus": ("WAITING FOR CONSENSUS", AMBER), "waiting_release": ("WAITING FOR RELEASE", AMBER),
          "actual_released": ("ACTUAL RELEASED", "#00E5FF"), "provider_unavailable": ("HS: DATA UNAVAILABLE", RED)}
VAL = {"validated": "validated vs baselines", "preliminary": "preliminary", "baseline_only": "baseline only",
       "unvalidated": "not yet validated"}


def e(x):
    return html.escape(str(x))


def fmt(v, unit, signed=True):
    if v is None:
        return "—"
    return (f"{v:+,.0f}K" if signed else f"{v:,.0f}K") if unit == "K jobs" else f"{v:.2f}%"


def gap_txt(r):
    if r is None or r.get("diff") is None:
        return "—", None
    d = r["diff"]
    return (f"{d:+,.0f}K" if r["unit"] == "K jobs" else f"{d:+.2f}pp"), r.get("tier")


def sast(t):
    return pd.Timestamp(t).tz_convert("Africa/Johannesburg").strftime("%a %d %b, %H:%M")


def countdown(t, now):
    s = int((t - now).total_seconds())
    if s <= 0:
        return f"RELEASED {(-s) // 60}m {(-s) % 60:02d}s AGO"
    d, r_ = divmod(s, 86400)
    hh, r_ = divmod(r_, 3600)
    mm, ss = divmod(r_, 60)
    return f"IN {d}d {hh:02d}:{mm:02d}:{ss:02d}" if d else f"IN {hh:02d}:{mm:02d}:{ss:02d}"


def reaction_stats(h, label, tag):
    """What the existing tracker has actually recorded for this event type (descriptive only)."""
    try:
        d = h[(pd.to_numeric(h.pre_release, errors="coerce") == 1) & h.move30.notna() & (h.title.map(tag) == label)]
    except Exception:
        return {"n": 0}
    n = len(d)
    if n == 0:
        return {"n": 0}
    m5 = pd.to_numeric(d.move5, errors="coerce").abs().median()
    m15 = pd.to_numeric(d.move15, errors="coerce").abs().median()
    rev = pd.to_numeric(d.reversal, errors="coerce").dropna()
    return {"n": n, "m5": None if pd.isna(m5) else float(m5), "m15": None if pd.isna(m15) else float(m15),
            "rev": float(rev.mean()) if len(rev) else None, "rev_n": int(len(rev))}


def spike_lean(card, r):
    """Initial-spike lean. FOMC uses the existing evidence model. Others use the HS-implied surprise direction with the
    usual hawkish/dovish gold relationship. This is a CONDITIONAL lean, not a measured edge."""
    if card and card.get("base") == "FOMC":
        sp = card.get("spike")
        return (sp if sp in ("Bullish gold", "Bearish gold") else "No edge"), "FOMC evidence model (weak evidence)"
    if r is None:
        return "No edge", "HS engine does not cover this release yet"
    if r.get("estimate") is None:
        return "No edge", "HS estimate not available yet"
    if r.get("consensus") is None:
        return "No edge", "waiting for official consensus"
    if r.get("tier") in ("modest", "meaningful"):
        up = r["diff"] > 0
        return ("Bearish gold" if up else "Bullish gold"), (
            f"HS expects a {r['tier']} {'upside' if up else 'downside'} surprise; usual USD/gold response")
    return "No edge", "HS estimate is within model noise of consensus"


def _col(b):
    return GREEN if b == "Bullish gold" else RED if b == "Bearish gold" else GREY


def _arrow(b):
    return "▲" if b == "Bullish gold" else "▼" if b == "Bearish gold" else "●"


def engine_line(HS):
    if not HS:
        return "HS ENGINE: OFF"
    s = HS["status"]
    if s == "loading":
        return "HS ENGINE: WARMING UP · downloading free data in the background"
    if s == "unavailable":
        return "HS ENGINE: DATA UNAVAILABLE · retrying automatically (see HS Expectation tab)"
    n = len(HS["bundle"].errors) if HS.get("bundle") else 0
    return "HS ENGINE: READY" + (f" · {n} series using older or missing data" if n else "")


def hero_html(ev, card, r, stats, now, label):
    t = ev["t"]
    unit = r["unit"] if r else ""
    cons = ev["forecast"] or "—"
    est = fmt(r["estimate"], unit) if r and r.get("estimate") is not None else ("n/a" if r is None else "—")
    gtxt, tier = gap_txt(r)
    est_sub = ("not covered yet" if r is None else (r["status_text"][:40] if r["estimate"] is None and r["status_text"] else
               (R.RELEASE_LABEL.get(r["release"], "") + (" y/y" if r["measure"] == "yy" else " m/m" if r["measure"] == "mm" else ""))))
    chips = ""
    if r:
        lab, col = STATUS[r["status"]]
        chips += f'<span class="chip" style="color:{col};border-color:{col}55">{lab}</span>'
        vk = (r.get("validation") or {}).get("code")
        if vk:
            chips += f'<span class="chip" style="color:{GREEN if vk == "validated" else AMBER};border-color:#33333399">{VAL[vk].upper()}</span>'
    bias_html = ""
    if card:
        b = card["bias"]
        conf = f" · {card['conf']}%" if (b != "Neutral" or card.get("grade")) else ""
        bias_html = (f'<div class="line"><span>ANALYST BIAS</span><b style="color:{_col(b)}">{_arrow(b)} '
                     f'{e((card.get("grade") or b).upper())}{conf}</b></div>')
    sb, why = spike_lean(card, r)
    n5 = stats.get("n", 0)
    if n5 >= 3 and stats.get("m15") is not None:
        move = f'typical ${stats["m15"]:.2f} in 15 min ({n5} tracked)'
    elif card and card.get("vol"):
        move = f'{card["vol"]} (15 min, from recent volatility)'
    else:
        move = "collecting"
    rev = (f'{stats["rev"]:.0%} of {stats["rev_n"]} tracked releases reversed within 15 min' if stats.get("rev_n", 0) >= 5
           else f'collecting ({stats.get("rev_n", 0)} of 5 tracked releases)')
    est_line = ""
    if r and r.get("interval"):
        lo, hi = r["interval"]
        est_line = f'<div class="note">80% range for the actual: {fmt(lo, unit)} to {fmt(hi, unit)}. HS estimate is calculated from data, not copied from consensus.</div>'
    return (f'<div class="hero"><div class="tag">NEXT EVENT · <span style="color:#fff">{countdown(t, now)}</span></div>'
            f'<div class="ttl" style="font-size:1.2rem">{e(ev["title"])}</div>'
            f'<div class="sub">{sast(t)} SAST · previous {e(ev["previous"] or "—")}</div>'
            f'<div class="trio"><div class="cell"><div class="lab">CONSENSUS</div><div class="val">{e(cons)}</div><div class="sm">official</div></div>'
            f'<div class="cell"><div class="lab">HS ESTIMATE</div><div class="val" style="color:#00E5FF">{e(est)}</div><div class="sm">{e(est_sub)}</div></div>'
            f'<div class="cell"><div class="lab">HS − CONSENSUS</div><div class="val">{e(gtxt)}</div><div class="sm">{e(tier or "")}</div></div></div>'
            f'{est_line}<div style="margin-top:8px">{chips}</div>'
            f'<div class="blk">{bias_html}'
            f'<div class="line"><span>INITIAL SPIKE</span><b style="color:{_col(sb)}">{_arrow(sb)} {e(sb.upper())}</b></div>'
            f'<div class="note" style="margin-top:0">{e(why)}. Conditional lean, not yet measured against outcomes.</div>'
            f'<div class="line"><span>REVERSAL TENDENCY</span><b>{e(rev)}</b></div>'
            f'<div class="line"><span>EXPECTED MOVE</span><b>{e(move)}</b></div></div>'
            f'<div class="note">Decision support, not a trade signal. Details: HS Expectation tab.</div></div>')


def mini_html(ev, r, now):
    unit = r["unit"] if r else ""
    est = fmt(r["estimate"], unit) if r and r.get("estimate") is not None else "—"
    gtxt, _ = gap_txt(r)
    st_ = ""
    if r:
        lab, col = STATUS[r["status"]]
        st_ = f'<span class="chip" style="color:{col};border-color:{col}55;margin:6px 0 0">{lab}</span>'
    return (f'<div class="mini"><div class="top"><div class="t">{e(ev["title"])}</div><div class="w">{sast(ev["t"])} · {countdown(ev["t"], now).replace("IN ", "in ")}</div></div>'
            f'<div class="nums"><span><em>CONSENSUS</em>{e(ev["forecast"] or "—")}</span><span><em>HS</em>{e(est)}</span>'
            f'<span><em>GAP</em>{e(gtxt)}</span><span><em>PREV</em>{e(ev["previous"] or "—")}</span></div>{st_}</div>')


def upcoming_events(cal_all, now, n=5):
    if cal_all is None or not len(cal_all):
        return []
    d = cal_all[(cal_all.status == "scheduled") & (cal_all.impact == "High")].copy()
    d["t"] = pd.to_datetime(d["time"], utc=True, errors="coerce")
    d = d[d.t.notna() & (d.t > now)].sort_values("t").head(n)
    return [dict(key=r["key"], title=r["title"], t=r["t"], forecast=str(r["forecast"] or "").strip(),
                 previous=str(r["previous"] or "").strip()) for _, r in d.iterrows()]


def render_main(st, now, cards, cal_all, HS, h, tag):
    st.markdown(f'<div class="meta" style="margin:0 0 10px">{e(engine_line(HS))}</div>', unsafe_allow_html=True)
    evs = upcoming_events(cal_all, now)
    if not evs:
        st.info("No upcoming high-impact USD events are currently available.")
        return
    cmap = {c["key"]: c for c in (cards or [])}
    res = (HS or {}).get("results", {})
    first = evs[0]
    r0, c0 = res.get(first["key"]), cmap.get(first["key"])
    stats = reaction_stats(h, tag(first["title"]), tag) if h is not None else {"n": 0}
    st.markdown(hero_html(first, c0, r0, stats, now, tag(first["title"])), unsafe_allow_html=True)
    if len(evs) > 1:
        st.markdown('<div class="sec">UPCOMING</div>', unsafe_allow_html=True)
        st.markdown("".join(mini_html(ev, res.get(ev["key"]), now) for ev in evs[1:]), unsafe_allow_html=True)
