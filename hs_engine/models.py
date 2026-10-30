"""Deterministic models and time-ordered validation. numpy/pandas only (no extra dependencies).

Candidates per release: three naive baselines (last value, 3-month mean, 12-month mean), a ridge regression on the
autoregressive features, and a ridge regression that adds the related indicators. The estimate HS publishes is
whichever candidate has the lowest walk-forward mean absolute error; if a simple baseline wins, the HS estimate IS
that baseline and is labelled as such."""
import math

import numpy as np
import pandas as pd

from .config import (INTERVAL_LEVEL, MIN_OOS_FOR_INTERVAL, MIN_OOS_FOR_VALIDATION, RIDGE_LAMBDA)

AR = ["l1", "m3", "m12"]
BASELINES = {"naive": "l1", "mean3": "m3", "mean12": "m12"}
METHOD_LABEL = {"naive": "last value (baseline)", "mean3": "3-month average (baseline)",
                "mean12": "12-month average (baseline)", "ridge_ar": "ridge regression on own history",
                "ridge_full": "ridge regression on own history + related indicators"}


def ridge_fit(X, y, lam=RIDGE_LAMBDA):
    Xv, yv = np.asarray(X, float), np.asarray(y, float)
    mu, sd = Xv.mean(0), Xv.std(0)
    sd = np.where(sd == 0, 1.0, sd)
    Z = (Xv - mu) / sd
    ym = yv.mean()
    beta = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (yv - ym))
    return {"mu": mu, "sd": sd, "beta": beta, "ym": float(ym), "cols": list(X.columns)}


def ridge_predict(m, x):
    z = (np.asarray([x[c] for c in m["cols"]], float) - m["mu"]) / m["sd"]
    return float(m["ym"] + z @ m["beta"])


def ridge_contrib(m, x):
    """Per-feature push on the estimate relative to the training mean (beta * standardized value)."""
    z = (np.asarray([x[c] for c in m["cols"]], float) - m["mu"]) / m["sd"]
    return dict(zip(m["cols"], (m["beta"] * z).tolist()))


def _excluded(index, windows):
    mask = np.zeros(len(index), bool)
    for a, b in windows:
        mask |= (index >= pd.Timestamp(a)) & (index <= pd.Timestamp(b))
    return mask


def walk_forward(frame, y, extras, min_train, exclude=()):
    """Expanding-window, one-step-ahead. Row i is predicted from rows < i only; `frame` rows hold features that
    were frozen at each month's own forecast time (see features.build_dataset)."""
    idx = frame.index
    excl = _excluded(idx, exclude)
    out, abl = [], {e: [] for e in extras}
    for i in range(min_train, len(frame)):
        if excl[i]:
            continue
        tr = np.arange(i)[~excl[:i]]
        if len(tr) < min_train // 2:
            continue
        x, row = frame.iloc[i], {"p": idx[i], "y": float(y.iloc[i]), "l1": float(frame.iloc[i]["l1"])}
        for name, col in BASELINES.items():
            row[name] = float(x[col])
        row["ridge_ar"] = ridge_predict(ridge_fit(frame.iloc[tr][AR], y.iloc[tr]), x)
        if extras:
            row["ridge_full"] = ridge_predict(ridge_fit(frame.iloc[tr][AR + extras], y.iloc[tr]), x)
            for e in extras:
                row["ridge_ar+" + e] = ridge_predict(ridge_fit(frame.iloc[tr][AR + [e]], y.iloc[tr]), x)
        out.append(row)
    return pd.DataFrame(out).set_index("p") if out else pd.DataFrame()


def _sign_test_p(w, l):
    n = w + l
    if n == 0:
        return 1.0
    k = min(w, l)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def metrics(preds, col):
    e = preds["y"] - preds[col]
    d = np.sign(preds[col] - preds["l1"]) * np.sign(preds["y"] - preds["l1"])
    ok = (preds["y"] != preds["l1"]) & (preds[col] != preds["l1"])
    return {"mae": float(e.abs().mean()), "rmse": float(np.sqrt((e ** 2).mean())), "n": int(len(e)),
            "dir_acc": (float((d[ok] > 0).mean()) if ok.sum() >= 10 else None), "dir_n": int(ok.sum())}


def interval_from_errors(err, level=INTERVAL_LEVEL):
    a = (1 - level) / 2
    return float(np.quantile(err, a)), float(np.quantile(err, 1 - a))


def interval_coverage(preds, col, level=INTERVAL_LEVEL, window=120):
    """Walk-forward coverage: each interval uses only errors that were known before that month."""
    e = (preds["y"] - preds[col]).to_numpy()
    hit = []
    for k in range(MIN_OOS_FOR_INTERVAL, len(e)):
        lo, hi = interval_from_errors(e[max(0, k - window):k], level)
        hit.append(lo <= e[k] <= hi)
    return {"coverage": (float(np.mean(hit)) if hit else None), "n": len(hit), "nominal": level}


def run_backtest(X, y, spec, series_missing=()):
    """X, y from features.build_dataset. Returns the full validation report for one release."""
    extras = [c for c in spec["extra"] if c in X and X[c].notna().sum() >= spec["min_train"] + 12]
    dropped = [c for c in spec["extra"] if c not in extras]
    cols = AR + extras
    frame = X[cols].dropna()
    yy = y.reindex(frame.index)
    rep = {"extras": extras, "dropped_extras": dropped, "n_rows": int(len(frame)),
           "train_start": (str(frame.index[0].date()) if len(frame) else None), "ok": False}
    if len(frame) < spec["min_train"] + 12:
        rep["reason"] = f"only {len(frame)} usable monthly observations (need {spec['min_train'] + 12})"
        return rep
    preds = walk_forward(frame, yy, extras, spec["min_train"], spec.get("exclude", ()))
    if len(preds) < 12:
        rep["reason"] = "too few out-of-sample observations"
        return rep
    cands = list(BASELINES) + ["ridge_ar"] + (["ridge_full"] if extras else [])
    m = {c: metrics(preds, c) for c in cands}
    sel = min(cands, key=lambda c: (m[c]["mae"], cands.index(c)))
    base_best = min(BASELINES, key=lambda c: m[c]["mae"])
    ew = (preds["y"] - preds[sel]).abs()
    eb = (preds["y"] - preds[base_best]).abs()
    wins, losses = int((ew < eb).sum()), int((ew > eb).sum())
    errs = (preds["y"] - preds[sel]).to_numpy()
    rep.update(ok=True, preds=preds, metrics=m, selected=sel, baseline_best=base_best, n_oos=int(len(preds)),
               oos_start=str(preds.index[0].date()), oos_end=str(preds.index[-1].date()),
               mae_ratio=(m[sel]["mae"] / m[base_best]["mae"] if m[base_best]["mae"] else None),
               sign_test_p=(None if sel == base_best else _sign_test_p(wins, losses)), wins=wins, losses=losses,
               ablation={e: metrics(preds, "ridge_ar+" + e)["mae"] for e in extras},
               ar_mae=m["ridge_ar"]["mae"], errors=errs,
               coverage=interval_coverage(preds, sel), series_missing=list(series_missing))
    rep["validation"] = validation_status(rep)
    return rep


def validation_status(rep):
    n = rep["n_oos"]
    if n < MIN_OOS_FOR_VALIDATION:
        return {"code": "unvalidated", "text": f"Unvalidated: only {n} out-of-sample months (need {MIN_OOS_FOR_VALIDATION})."}
    if rep["selected"] in BASELINES:
        return {"code": "baseline_only", "text": "No model beat the best simple baseline out of sample, so the HS estimate "
                f"is that baseline ({METHOD_LABEL[rep['selected']]}); it adds no skill over naive methods."}
    p, r = rep["sign_test_p"], rep["mae_ratio"]
    if r is not None and r < 1 and p is not None and p < 0.10:
        return {"code": "validated", "text": f"Validated against simple baselines (walk-forward, {n} months, MAE ratio {r:.2f}, "
                f"sign-test p={p:.3f}). Not validated against official consensus. Pseudo-real-time: see limitations."}
    return {"code": "preliminary", "text": f"Preliminary: lower error than baselines (ratio {r:.2f}) but not statistically "
            f"distinguishable (sign-test p={p:.2f}, {n} months)."}


def live_estimate(X, y, spec, rep, x_live):
    """Fit on everything known now and predict the target month. Falls back along the ranking if a preferred
    method needs a feature that is not yet available. Returns None if nothing can be computed."""
    order = sorted(rep["metrics"], key=lambda c: rep["metrics"][c]["mae"])
    excl = _excluded(X.index, spec.get("exclude", ()))
    for c in order:
        need = AR + rep["extras"] if c == "ridge_full" else AR if c == "ridge_ar" else [BASELINES[c]]
        if any(not np.isfinite(x_live.get(k, np.nan)) for k in need):
            continue
        out = {"method": c, "preferred": order[0], "fell_back": c != order[0]}
        if c in BASELINES:
            out.update(estimate=float(x_live[BASELINES[c]]), contrib={}, n_train=int(X[AR].dropna().shape[0]))
        else:
            fr = X[need].dropna()
            tr = fr.index[~_excluded(fr.index, spec.get("exclude", ()))]
            mdl = ridge_fit(fr.loc[tr], y.reindex(tr))
            out.update(estimate=ridge_predict(mdl, x_live), contrib=ridge_contrib(mdl, x_live),
                       n_train=int(len(tr)), train_mean=mdl["ym"])
        return out
    return None
