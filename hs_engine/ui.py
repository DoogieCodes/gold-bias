"""Streamlit section for the existing app. `render` is the only entry point and is wrapped by the caller in
try/except so a failure here can never break the calendar, Upcoming tab, analyst or track record."""
import html

import pandas as pd

from . import releases as R
from . import scenarios as S
from . import store as ST
from .config import MODEL_VERSION, SPECS
from .models import METHOD_LABEL
from . import runtime as RT

STATUS = {"forecast_ready": ("FORECAST READY", "#00C853"), "insufficient_data": ("INSUFFICIENT DATA", "#8A8A8A"),
          "waiting_consensus": ("WAITING FOR CONSENSUS", "#FFB300"), "waiting_release": ("WAITING FOR RELEASE", "#FFB300"),
          "actual_released": ("ACTUAL RELEASED", "#00E5FF"), "provider_unavailable": ("DATA PROVIDER UNAVAILABLE", "#FF3B30")}
VAL = {"validated": "Validated vs baselines", "preliminary": "Preliminary (not significant)",
       "baseline_only": "No model beat baseline", "unvalidated": "Model not yet validated"}


def fmt(v, unit):
    if v is None:
        return "Unavailable"
    return f"{v:+,.0f}K" if unit == "K jobs" else f"{v:.2f}%"


def sast(t):
    return pd.Timestamp(t).tz_convert("Africa/Johannesburg").strftime("%a %d %b, %H:%M SAST")


def _supabase(st):
    try:
        return {"url": st.secrets["supabase"]["url"], "key": st.secrets["supabase"]["key"]}
    except Exception:
        return None


def _card(st, r):
    label, col = STATUS[r["status"]]
    vk = (r.get("validation") or {}).get("code")
    badge = f' · <span style="color:#FFB300">{VAL[vk].upper()}</span>' if vk and vk != "validated" else ""
    st.markdown(f'<div class="card" style="border-left:6px solid {col}"><div class="tag">{html.escape(R.RELEASE_LABEL[r["release"]].upper())}'
                f' · <span style="color:{col}">{label}</span>{badge}</div><div class="ttl">{html.escape(r["title"])}</div>'
                f'<div class="sub">{sast(r["scheduled_utc"])} · reference month {r["ref_period"]:%b %Y}</div>'
                + (f'<div class="why">{html.escape(r["status_text"])}</div>' if r["status_text"] else "") + "</div>",
                unsafe_allow_html=True)
    u = r["unit"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Official consensus", fmt(r["consensus"], u))
    c2.metric("HS estimate", fmt(r["estimate"], u))
    c3.metric("HS − consensus", ("Unavailable" if r["diff"] is None else (f"{r['diff']:+,.0f}K" if u == "K jobs" else f"{r['diff']:+.2f} pp")),
              (r["tier"] if r["tier"] else None), delta_color="off")
    if r["estimate"] is None:
        return
    st.caption(f"HS method: {METHOD_LABEL.get(r['method'], r['method'])} · {r['n_train']} training months · "
               f"estimate is independently calculated, not copied from consensus")
    if r["interval"]:
        lo, hi = r["interval"]
        st.caption(f"80% prediction interval: {fmt(lo, u)} to {fmt(hi, u)} ({r['interval_basis']})")
    else:
        st.caption("Prediction interval: not shown (too few walk-forward errors to justify one).")
    if r["validation"]:
        st.caption("Validation: " + r["validation"]["text"])


def _details(st, r, mk, h):
    if r["estimate"] is None:
        return
    with st.expander("Evidence, inputs and data freshness"):
        if r["evidence_for"]:
            st.markdown("**Supports the HS-vs-consensus gap**" if r["consensus"] is not None else "**Inputs pushing the estimate**")
            st.markdown("\n".join("- " + x for x in r["evidence_for"]))
        if r["evidence_against"]:
            st.markdown("**Conflicting evidence**")
            st.markdown("\n".join("- " + x for x in r["evidence_against"]))
        if r["baselines"]:
            st.markdown("**Historical baselines**")
            st.markdown("\n".join(f"- {k}: {fmt(v, r['unit'])}" for k, v in r["baselines"].items()))
        for n in r["notes"]:
            st.caption(n)
        fr = pd.DataFrame([{"series": k, "last observation": v["last_obs"], "fetched (UTC)": v["fetched_at"],
                            "problem": v["error"] or ""} for k, v in r["freshness"].items()])
        st.dataframe(fr, hide_index=True, use_container_width=True)
        b = r.get("backtest")
        if b:
            st.caption(f"Backtest: {b['n_oos']} out-of-sample months ({b['oos_start']} to {b['oos_end']}), {b['n_rows']} rows from "
                       f"{b['train_start']}. Revised data and approximate publication lags: pseudo-real-time, not a perfect replay.")
            mt = pd.DataFrame([{"method": METHOD_LABEL.get(k, k), "MAE": v["mae"], "RMSE": v["rmse"],
                                "directional accuracy": v["dir_acc"], "months": v["n"]} for k, v in b["metrics"].items()])
            st.dataframe(mt.round(3), hide_index=True, use_container_width=True)
    with st.expander("Conditional USD and gold scenarios"):
        sc = S.pre_release(r, mk)
        st.markdown(sc["headline"])
        for f in sc["facts"]:
            st.caption(f)
        for s_ in sc["scenarios"]:
            st.markdown(f"**{s_['name']}** — {s_['usd']}. {s_['gold']}.")
            st.caption("Would invalidate: " + "; ".join(s_["invalidates"]))
        st.caption(sc["not_signal"])
    if r["actual"] is not None:
        with st.expander("Post-release review", expanded=True):
            m5 = m15 = None
            try:
                row = h[h.key == r["event_key"]]
                if len(row):
                    m5, m15 = pd.to_numeric(row.iloc[0].move5, errors="coerce"), pd.to_numeric(row.iloc[0].move15, errors="coerce")
                    m5 = None if pd.isna(m5) else float(m5)
                    m15 = None if pd.isna(m15) else float(m15)
            except Exception:
                pass
            for line in S.post_release(r, m5, m15):
                st.markdown("- " + line)


def render(st, cal_all, h, mk, HS, now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    st.markdown('<div class="sec">HS EXPECTATION ENGINE</div>', unsafe_allow_html=True)
    st.caption(f"Independent statistical estimates from free public data (FRED). Model {MODEL_VERSION}. Official forecasts come "
               "from the calendar feed and are shown separately. Analytical decision support, not a trade signal.")
    status = HS["status"] if HS else "unavailable"
    if status == "loading":
        st.info("Downloading free data and running backtests in the background. This page updates automatically when ready.")
        return
    if status != "ready":
        st.error("Data provider unavailable: no FRED series could be loaded. " + (HS.get("error") or "")[:600] if HS else "Engine not running.")
        st.caption("The app retries automatically every 10 minutes. If it keeps timing out, add a free FRED API key "
                   "(see README) to use a second route to the same data.")
        if st.button("Retry data download now"):
            RT.ensure_started(force=True)
            st.rerun()
        return
    bundle, reports = HS["bundle"], HS["reports"]
    store = HS.get("store") or ST.Store()
    age = (pd.Timestamp.now(tz="UTC").tz_localize(None) - bundle.fetched_at).total_seconds() / 3600
    if bundle.errors:
        st.warning("Some data failed to load, using older copies where available: " +
                   "; ".join(f"{k}: {v}" for k, v in bundle.errors.items())[:500])
        if st.button("Retry data download now"):
            RT.ensure_started(force=True)
            st.rerun()
    st.caption(f"Data fetched {age:.1f} h ago")
    shown = 0
    for r in sorted(HS["results"].values(), key=lambda r: r["scheduled_utc"]):
        shown += 1
        _card(st, r)
        _details(st, r, mk, h)
    if not shown:
        st.info("No supported releases (NFP, CPI, Core CPI, Core PCE) are in the calendar window right now.")

    st.markdown('<div class="sec">MODEL VALIDATION (WALK-FORWARD BACKTEST)</div>', unsafe_allow_html=True)
    rows = []
    for k, spec in SPECS.items():
        rep = reports.get(k, {})
        rows.append({"release": spec["label"], "status": VAL.get((rep.get("validation") or {}).get("code"), rep.get("reason", "unavailable")),
                     "method": METHOD_LABEL.get(rep.get("selected"), "-"), "OOS months": rep.get("n_oos", 0),
                     "MAE": (round(rep["metrics"][rep["selected"]]["mae"], 3) if rep.get("ok") else None),
                     "best baseline MAE": (round(rep["metrics"][rep["baseline_best"]]["mae"], 3) if rep.get("ok") else None),
                     "interval coverage": (None if not rep.get("ok") or rep["coverage"]["coverage"] is None else f"{rep['coverage']['coverage']:.0%}")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption("Backtests use the latest-revised FRED values and approximate publication lags, so they are pseudo-real-time and "
               "somewhat optimistic. They say nothing about beating official consensus; that needs the live record below.")

    st.markdown('<div class="sec">LIVE HS FORECAST RECORD</div>', unsafe_allow_html=True)
    ev_df = store.evaluation()
    perf = ST.performance(ev_df)
    fc = store.forecasts()
    st.caption(f"{len(fc)} pre-release forecasts logged, {len(ev_df)} evaluated against actuals. "
               "Performance is shown only once at least 10 genuine observations exist per release.")
    if len(perf):
        st.dataframe(perf.round(3), hide_index=True, use_container_width=True)
    if len(fc):
        st.dataframe(fc.drop(columns=["forecast_id", "inputs_last_obs"]).sort_values("generated_at_utc", ascending=False).head(30),
                     hide_index=True, use_container_width=True)
    if ST.last_error["msg"]:
        st.warning("Forecast log sync issue (local copy still saved): " + ST.last_error["msg"])
    st.caption("Local files on Streamlit Community Cloud are wiped on reboot or redeploy; configure the Supabase tables "
               "(supabase_schema.sql) to keep the record permanently.")
