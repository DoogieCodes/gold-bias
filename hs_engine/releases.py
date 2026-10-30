"""Release registry, calendar-title classification, consensus parsing, and the per-event forecast pipeline."""
import re
import threading

import numpy as np
import pandas as pd

from . import features as F
from . import models as M
from .config import FEATURE_LABEL, FEATURE_SERIES, SPECS, MODEL_VERSION, MIN_OOS_FOR_INTERVAL

MODEL_KIND = {"nfp": "nfp", "cpi": "cpi_mm", "core_cpi": "core_cpi_mm", "core_pce": "core_pce_mm"}
RELEASE_LABEL = {"nfp": "Non-Farm Payrolls", "cpi": "CPI", "core_cpi": "Core CPI", "core_pce": "Core PCE"}


def classify_title(title):
    """Map a calendar title to (release_id, measure) or None. measure: 'level' (NFP change), 'mm', 'yy'."""
    t = str(title or "").lower()
    if "adp" in t or "revision" in t:
        return None
    if "non-farm" in t or "nonfarm" in t or "employment situation" in t:
        return {"release": "nfp", "measure": "level"}
    yy = "y/y" in t
    if "core pce" in t:
        return {"release": "core_pce", "measure": "yy" if yy else "mm"}
    if "core cpi" in t:
        return {"release": "core_cpi", "measure": "yy" if yy else "mm"}
    if "cpi" in t or "consumer price" in t:
        return {"release": "cpi", "measure": "yy" if yy else "mm"}
    return None


def parse_value(s, release):
    """Parse a calendar figure. NFP is returned in thousands; inflation in percent. None if unusable."""
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*([KkMm%]?)", s)
    if not m:
        return None
    v, suf = float(m.group(1)), m.group(2).upper()
    if release == "nfp":
        return v * 1000 if suf == "M" else v if suf == "K" else None   # unlabeled payroll units are ambiguous
    return v if suf in ("", "%") else None


def ref_period(release_time_utc):
    """Reference month = month before the release month, in New York time (BLS/BEA publish on ET dates)."""
    t = pd.Timestamp(release_time_utc)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    et = t.tz_convert("America/New_York")
    return pd.Timestamp(year=et.year, month=et.month, day=1) - pd.offsets.MonthBegin(1)


_LOCK = threading.Lock()
_REPORTS = {"fp": None, "data": None}


def get_reports(bundle):
    """Backtests for all four models. Cached by data fingerprint so reruns are instant."""
    fp = bundle.fingerprint()
    with _LOCK:
        if _REPORTS["fp"] == fp and _REPORTS["data"] is not None:
            return _REPORTS["data"]
        data = compute_reports(bundle)
        _REPORTS.update(fp=fp, data=data)
        return data


def compute_reports(bundle):
    out = {}
    for kind, spec in SPECS.items():
        if spec["target"] not in bundle.series:
            out[kind] = {"ok": False, "provider": True,
                         "reason": f"{spec['target']} unavailable: {bundle.errors.get(spec['target'], 'not loaded')}"}
            continue
        try:
            X, y = F.build_dataset(bundle, kind)
            missing = [s for s in {FEATURE_SERIES[c] for c in spec["extra"]} if s not in bundle.series]
            rep = M.run_backtest(X, y, spec, missing)
            rep.update(X=X, y=y)
        except Exception as e:  # never let one model take the page down
            rep = {"ok": False, "reason": f"model error: {type(e).__name__}: {e}"}
        out[kind] = rep
    return out


def _yy_from_mm(bundle, release, p, mm):
    sid = {"cpi": "CPIAUCSL", "core_cpi": "CPILFESL", "core_pce": "PCEPILFE"}[release]
    lv = F.known(bundle, sid, pd.Timestamp("2262-01-01"))
    a, b = pd.Timestamp(p) - pd.offsets.MonthBegin(1), pd.Timestamp(p) - pd.offsets.MonthBegin(12)
    if a not in lv.index or b not in lv.index:
        return None
    return float(100 * (lv[a] * (1 + mm / 100) / lv[b] - 1))


def actual_from_fred(bundle, release, measure, p):
    """Latest-vintage actual for reference month p (used only to fill revised values after the fact)."""
    kind = MODEL_KIND[release]
    y = F.target_series(bundle, kind) if SPECS[kind]["target"] in bundle.series else None
    if y is None or pd.Timestamp(p) not in y.index:
        return None
    mm = float(y[pd.Timestamp(p)])
    if measure == "yy":
        return _yy_actual(bundle, release, p)
    return mm


def _yy_actual(bundle, release, p):
    sid = {"cpi": "CPIAUCSL", "core_cpi": "CPILFESL", "core_pce": "PCEPILFE"}[release]
    lv = F.known(bundle, sid, pd.Timestamp("2262-01-01"))
    p, b = pd.Timestamp(p), pd.Timestamp(p) - pd.offsets.MonthBegin(12)
    return float(100 * (lv[p] / lv[b] - 1)) if (p in lv.index and b in lv.index) else None


def tier(diff, sigma):
    if diff is None or not sigma:
        return None
    z = abs(diff) / sigma
    return "negligible" if z < 0.25 else "modest" if z < 0.75 else "meaningful"


def evaluate_event(ev, bundle, reports, now=None):
    """ev: dict(key, title, time(UTC Timestamp), forecast, actual). Returns the full engine result for display and logging."""
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    c = classify_title(ev["title"])
    if not c:
        return None
    rel, meas = c["release"], c["measure"]
    kind = MODEL_KIND[rel]
    spec = SPECS[kind]
    t = pd.Timestamp(ev["time"])
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    p = ref_period(t)
    unit = "K jobs" if rel == "nfp" else "%"
    cons = parse_value(ev.get("forecast"), rel)
    actual = parse_value(ev.get("actual"), rel)
    r = {"event_key": ev["key"], "title": ev["title"], "release": rel, "measure": meas, "kind": kind, "unit": unit,
         "scheduled_utc": t, "ref_period": p, "consensus": cons, "actual": actual, "estimate": None, "interval": None,
         "model_version": MODEL_VERSION, "notes": [], "status": "insufficient_data", "status_text": "",
         "validation": None, "evidence_for": [], "evidence_against": [], "baselines": {}, "freshness": {},
         "sigma": None, "diff": None, "tier": None, "method": None, "n_train": None, "asof": now}
    for sid in {spec["target"]} | {FEATURE_SERIES[c_] for c_ in spec["extra"]}:
        sd = bundle.series.get(sid)
        r["freshness"][sid] = {"last_obs": (str(sd.last_obs.date()) if sd is not None else None),
                               "fetched_at": (str(sd.fetched_at) if sd is not None else None),
                               "error": bundle.errors.get(sid)}
    rep = reports.get(kind, {})
    if not rep.get("ok"):
        prov = rep.get("provider") or any(v["error"] for s_, v in r["freshness"].items() if s_ == spec["target"])
        r["status"] = "provider_unavailable" if prov else "insufficient_data"
        r["status_text"] = rep.get("reason", "model could not be built")
        return r
    r["validation"] = rep["validation"]
    r["backtest"] = {k: rep[k] for k in ("n_oos", "oos_start", "oos_end", "selected", "baseline_best", "metrics",
                                          "ablation", "coverage", "mae_ratio", "sign_test_p", "n_rows", "train_start",
                                          "extras", "dropped_extras", "wins", "losses")}
    if t <= now and actual is not None:
        r["status"], r["status_text"] = "actual_released", "Actual figure is in the calendar feed."
    x_live = F.features_at(bundle, kind, p, now.tz_convert("UTC").tz_localize(None))
    live = M.live_estimate(rep["X"], rep["y"], spec, rep, x_live)
    if live is None:
        need = [FEATURE_LABEL[c_] for c_ in M.AR]
        r["status_text"] = ("Inputs for the target month are not yet available (" + ", ".join(need) + "); the model needs "
                            "the previous month's data and, for some indicators, the target month's data.")
        if r["status"] != "actual_released":
            r["status"] = "insufficient_data"
        return r
    mm_est = live["estimate"]
    r.update(method=live["method"], n_train=live["n_train"])
    if live["fell_back"]:
        r["notes"].append(f"Preferred model ({M.METHOD_LABEL[live['preferred']]}) is waiting for an input that is not yet "
                          f"published; using {M.METHOD_LABEL[live['method']]}.")
    sel = rep["selected"]
    err = rep["errors"]
    r["sigma"] = rep["metrics"][live["method"]]["rmse"]
    iv = None
    if len(err) >= MIN_OOS_FOR_INTERVAL and live["method"] == sel:
        lo, hi = M.interval_from_errors(err[-120:])
        iv = (mm_est + lo, mm_est + hi)
    elif len(err) >= MIN_OOS_FOR_INTERVAL:
        e2 = (rep["preds"]["y"] - rep["preds"][live["method"]]).to_numpy()[-120:]
        lo, hi = M.interval_from_errors(e2)
        iv = (mm_est + lo, mm_est + hi)
    est = mm_est
    if meas == "yy":
        est = _yy_from_mm(bundle, rel, p, mm_est)
        if est is None:
            r["status_text"] = "Not enough index history to derive the year-over-year figure."
            return r
        if iv:
            iv = (_yy_from_mm(bundle, rel, p, iv[0]), _yy_from_mm(bundle, rel, p, iv[1]))
        r["notes"].append("Year-over-year is DERIVED from the month-over-month estimate and the published index level "
                          "twelve months earlier; it has no separate validation and ignores later index revisions.")
    r.update(estimate=est, interval=iv, mm_estimate=mm_est)
    r["baselines"] = {M.METHOD_LABEL[k]: float(x_live[v]) for k, v in M.BASELINES.items() if np.isfinite(x_live[v])}
    r["interval_basis"] = (f"{int(rep['coverage'].get('nominal', 0.8) * 100)}% empirical interval from {len(err[-120:])} "
                           f"walk-forward errors; walk-forward coverage {rep['coverage']['coverage']:.0%} over "
                           f"{rep['coverage']['n']} months" if iv and rep["coverage"]["coverage"] is not None else None)
    # evidence
    ref = cons if cons is not None else None
    base_pt = live.get("train_mean")
    contrib = live["contrib"]
    if contrib:
        target = (est - cons) if cons is not None else (mm_est - (base_pt or 0))
        for k_, v in sorted(contrib.items(), key=lambda kv: -abs(kv[1])):
            line = f"{FEATURE_LABEL.get(k_, k_)} = {x_live[k_]:+.3g} → pushes the estimate {'up' if v > 0 else 'down'} by {abs(v):.3g}"
            (r["evidence_for"] if v * target > 0 else r["evidence_against"]).append(line)
    else:
        r["evidence_for"].append(f"Estimate is the {M.METHOD_LABEL[live['method']]}: no regression inputs were used.")
    for e_, mae in rep["ablation"].items():
        verdict = "improves" if mae < rep["ar_mae"] else "does not improve"
        r["notes"].append(f"Indicator test: adding {FEATURE_LABEL.get(e_, e_)} {verdict} walk-forward MAE "
                          f"({mae:.3g} vs {rep['ar_mae']:.3g} for own-history ridge).")
    if rep["dropped_extras"]:
        r["notes"].append("Indicators unavailable this run (provider): " + ", ".join(rep["dropped_extras"]))
    # consensus comparison
    if cons is not None:
        r["diff"] = est - cons
        r["tier"] = tier(r["diff"], r["sigma"])
    if r["status"] == "actual_released":
        return r
    if cons is None:
        r["status"], r["status_text"] = "waiting_consensus", "Official consensus is not yet in the calendar feed."
    elif t <= now:
        r["status"], r["status_text"] = "waiting_release", "Scheduled time has passed; actual not yet in the feed."
    else:
        r["status"], r["status_text"] = "forecast_ready", ""
    return r
