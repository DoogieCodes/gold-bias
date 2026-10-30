"""Point-in-time feature construction.

Every feature at (target month p, as-of time) is computed ONLY from observations whose availability timestamp is
<= as-of. The target observation itself is never visible because as-of is earlier than its availability."""
import numpy as np
import pandas as pd

from .config import FC_OFFSET_DAYS, SPECS

MB = pd.offsets.MonthBegin
NAN = float("nan")


def month_end(p):
    return (pd.Timestamp(p) + pd.offsets.MonthEnd(0)).normalize()


def fc_time(kind, p):
    """Backtest forecast time: end of target month + offset (always earlier than the real release)."""
    return month_end(p) + pd.Timedelta(days=FC_OFFSET_DAYS[kind])


def known(B, sid, asof):
    sd = B.series.get(sid)
    if sd is None:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    d = sd.df
    return d.loc[d["avail"] <= asof, "value"]


def pct(level):
    return (100.0 * (level / level.shift(1) - 1.0)).dropna()


def target_series(B, kind):
    """Target as of NOW (latest vintage), indexed by month-start."""
    sid = SPECS[kind]["target"]
    lv = known(B, sid, pd.Timestamp("2262-01-01"))
    return (lv.diff().dropna() if kind == "nfp" else pct(lv))


def _ar(m, p):
    prev = pd.Timestamp(p) - MB(1)
    if len(m) < 13 or m.index[-1] != prev:
        return {"l1": NAN, "m3": NAN, "m12": NAN}
    return {"l1": float(m.iloc[-1]), "m3": float(m.iloc[-3:].mean()), "m12": float(m.iloc[-12:].mean())}


def survey_week_end(p):
    """Saturday ending the week that contains the 12th (the payroll reference week)."""
    d = pd.Timestamp(p) + pd.Timedelta(days=11)
    return d + pd.Timedelta(days=(5 - d.weekday()) % 7)


def _claims_avg(s, p):
    end = survey_week_end(p)
    w = s[s.index <= end]
    if len(w) < 4 or w.index[-1] != end:
        return NAN
    return float(w.iloc[-4:].mean())


def _claims_chg(B, sid, p, asof):
    s = known(B, sid, asof)
    a, b = _claims_avg(s, p), _claims_avg(s, pd.Timestamp(p) - MB(1))
    return float(np.log(a / b)) if (a == a and b == b and a > 0 and b > 0) else NAN


def _gas_mom(B, p, asof):
    g = known(B, "GASREGW", asof)
    p = pd.Timestamp(p)
    cur = g[(g.index >= p) & (g.index <= month_end(p))]
    prv = g[(g.index >= p - MB(1)) & (g.index < p)]
    # the month must be essentially complete: last weekly observation within the final 6 days
    if len(cur) == 0 or len(prv) == 0 or cur.index[-1] < month_end(p) - pd.Timedelta(days=6):
        return NAN
    return float(100 * (cur.mean() / prv.mean() - 1))


def _lag1_pct(B, sid, p, asof):
    m = pct(known(B, sid, asof))
    return float(m.iloc[-1]) if len(m) and m.index[-1] == pd.Timestamp(p) - MB(1) else NAN


def _same_month_pct(B, sid, p, asof):
    m = pct(known(B, sid, asof))
    return float(m.iloc[-1]) if len(m) and m.index[-1] == pd.Timestamp(p) else NAN


def features_at(B, kind, p, asof):
    """All candidate features for target month p using only data available at `asof`. NaN = not available."""
    sid = SPECS[kind]["target"]
    lv = known(B, sid, asof)
    own = lv.diff().dropna() if kind == "nfp" else pct(lv)
    f = _ar(own, p)
    f["claims_chg"] = _claims_chg(B, "ICSA", p, asof)
    f["cclaims_chg"] = _claims_chg(B, "CCSA", p, asof)
    f["gas_mom"] = _gas_mom(B, p, asof)
    f["shelter_l1"] = _lag1_pct(B, "CUSR0000SAH1", p, asof)
    f["ppi_l1"] = _lag1_pct(B, "PPIFIS", p, asof)
    f["core_cpi_p"] = _same_month_pct(B, "CPILFESL", p, asof)
    return f


def build_dataset(B, kind):
    """Training frame: one row per historical target month, features frozen at that month's forecast time."""
    spec = SPECS[kind]
    y = target_series(B, kind)
    y = y[y.index >= pd.Timestamp(spec["start"])]
    rows = [features_at(B, kind, p, fc_time(kind, p)) for p in y.index]
    X = pd.DataFrame(rows, index=y.index)
    return X, y
