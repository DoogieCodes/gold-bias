"""Append-only forecast log + outcome log.

* forecasts: never updated. A new row is added only when consensus, estimate or model version changes, and only
  BEFORE the scheduled release.
* outcomes: first-print actual is written once and never overwritten; the latest-vintage value is a separate column.
Persistence: local CSV (ephemeral on Streamlit Community Cloud) mirrored to the app's existing Supabase project when
[supabase] secrets exist (tables hs_forecasts / hs_outcomes, see supabase_schema.sql)."""
import os
import tempfile

import numpy as np
import pandas as pd
import requests

from .config import MIN_LIVE_FOR_PERFORMANCE

DEFAULT_DIR = "hs_data"
FC_COLS = ["forecast_id", "event_key", "release", "measure", "title", "scheduled_utc", "consensus", "hs_estimate",
           "method", "baseline_estimate", "interval_lo", "interval_hi", "interval_basis", "unit", "model_version",
           "validation_status", "n_train", "inputs_last_obs", "generated_at_utc", "data_origin"]
OUT_COLS = ["event_key", "release", "measure", "unit", "actual_initial", "actual_source", "recorded_at_utc",
            "actual_latest", "latest_recorded_at_utc"]
last_error = {"msg": None}


class Store:
    def __init__(self, data_dir=DEFAULT_DIR, supabase=None, origin="live"):
        if origin != "live" and os.path.abspath(data_dir) == os.path.abspath(DEFAULT_DIR):
            raise ValueError("non-live (synthetic) data may not be written to the production directory")
        self.dir, self.sb, self.origin = data_dir, supabase, origin
        os.makedirs(self.dir, exist_ok=True)

    # ---- local io
    def _path(self, name):
        return os.path.join(self.dir, name + ".csv")

    def _read(self, name, cols):
        p = self._path(name)
        d = pd.read_csv(p, dtype=str) if os.path.exists(p) else pd.DataFrame(columns=cols)
        for c in cols:
            if c not in d.columns:
                d[c] = None
        return d[cols]

    def _write(self, name, df):
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        os.close(fd)
        df.to_csv(tmp, index=False)
        os.replace(tmp, self._path(name))

    # ---- supabase (best effort, never raises)
    def _hd(self, prefer):
        return {"apikey": self.sb["key"], "Authorization": f"Bearer {self.sb['key']}",
                "Content-Type": "application/json", "Prefer": prefer}

    def _sb_push(self, table, df, conflict, prefer):
        if not self.sb or df.empty:
            return
        try:
            r = requests.post(f"{self.sb['url'].rstrip('/')}/rest/v1/{table}?on_conflict={conflict}",
                              headers=self._hd(prefer), data=df.where(df.notna(), None).to_json(orient="records"), timeout=15)
            if not r.ok:
                last_error["msg"] = f"{table}: HTTP {r.status_code} (run supabase_schema.sql?)"
        except Exception as e:
            last_error["msg"] = f"{table}: {type(e).__name__}"

    def _sb_pull(self, table, cols):
        if not self.sb:
            return None
        try:
            r = requests.get(f"{self.sb['url'].rstrip('/')}/rest/v1/{table}?select=*&limit=5000",
                             headers=self._hd("return=representation"), timeout=10)
            rows = r.json()
            if r.ok and isinstance(rows, list):
                d = pd.DataFrame(rows)
                for c in cols:
                    if c not in d.columns:
                        d[c] = None
                return d[cols].astype(object).where(d[cols].notna(), None).astype(str).replace("None", np.nan)
        except Exception as e:
            last_error["msg"] = f"{table}: {type(e).__name__}"
        return None

    # ---- forecasts
    def forecasts(self):
        loc = self._read("forecasts", FC_COLS)
        rem = self._sb_pull("hs_forecasts", FC_COLS)
        if rem is not None and len(rem):
            loc = pd.concat([rem, loc]).drop_duplicates("forecast_id", keep="first")
        return loc.reset_index(drop=True)

    def log_forecast(self, r, now):
        """Record a pre-release forecast. Returns True if a new row was written."""
        sched = pd.Timestamp(r["scheduled_utc"])
        if r.get("estimate") is None or now >= sched:
            return False
        cur = self.forecasts()
        same = cur[(cur.event_key == r["event_key"]) & (cur.model_version == r["model_version"])]
        sig = (_f(r["consensus"]), _f(r["estimate"], 4))
        if len(same):
            last = same.sort_values("generated_at_utc").iloc[-1]
            if (_f(_num(last.consensus)), _f(_num(last.hs_estimate), 4)) == sig:
                return False
        iv = r.get("interval")
        base = r.get("baselines", {})
        row = {"forecast_id": f"{r['event_key']}|{r['model_version']}|{now:%Y%m%dT%H%M%S}",
               "event_key": r["event_key"], "release": r["release"], "measure": r["measure"], "title": r["title"],
               "scheduled_utc": str(sched), "consensus": r["consensus"], "hs_estimate": r["estimate"],
               "method": r["method"], "baseline_estimate": (list(base.values())[0] if base else None),
               "interval_lo": iv[0] if iv else None, "interval_hi": iv[1] if iv else None,
               "interval_basis": r.get("interval_basis"), "unit": r["unit"], "model_version": r["model_version"],
               "validation_status": (r.get("validation") or {}).get("code"), "n_train": r.get("n_train"),
               "inputs_last_obs": str({k: v["last_obs"] for k, v in r["freshness"].items()}),
               "generated_at_utc": str(now), "data_origin": self.origin}
        df = pd.concat([self._read("forecasts", FC_COLS), pd.DataFrame([row])], ignore_index=True)
        self._write("forecasts", df)
        self._sb_push("hs_forecasts", pd.DataFrame([row]), "forecast_id", "resolution=ignore-duplicates")
        return True

    # ---- outcomes
    def outcomes(self):
        loc = self._read("outcomes", OUT_COLS)
        rem = self._sb_pull("hs_outcomes", OUT_COLS)
        if rem is not None and len(rem):
            loc = pd.concat([rem, loc]).drop_duplicates("event_key", keep="first")
        return loc.reset_index(drop=True)

    def record_outcome(self, event_key, release, measure, unit, actual, now, latest=None):
        """First-print actual written once; later calls only update the latest-vintage column."""
        cur = self._read("outcomes", OUT_COLS)
        i = cur.index[cur.event_key == event_key]
        if len(i) == 0:
            if actual is None:
                return False
            row = {"event_key": event_key, "release": release, "measure": measure, "unit": unit,
                   "actual_initial": actual, "actual_source": "calendar feed", "recorded_at_utc": str(now),
                   "actual_latest": latest, "latest_recorded_at_utc": str(now) if latest is not None else None}
            cur = pd.concat([cur, pd.DataFrame([row])], ignore_index=True)
            self._write("outcomes", cur)
            self._sb_push("hs_outcomes", pd.DataFrame([row]), "event_key", "resolution=ignore-duplicates")
            return True
        if latest is not None and str(cur.at[i[0], "actual_latest"]) != str(latest):
            cur.at[i[0], "actual_latest"], cur.at[i[0], "latest_recorded_at_utc"] = str(latest), str(now)
            self._write("outcomes", cur)
            self._sb_push("hs_outcomes", cur.loc[i], "event_key", "resolution=merge-duplicates")
        return False

    # ---- evaluation
    def evaluation(self):
        """One row per evaluated event, using the LAST forecast generated before the scheduled release."""
        f, o = self.forecasts(), self.outcomes()
        if f.empty or o.empty:
            return pd.DataFrame()
        rows = []
        for k, g in f.groupby("event_key"):
            oo = o[o.event_key == k]
            if oo.empty or pd.isna(oo.iloc[0].actual_initial):
                continue
            g = g[pd.to_datetime(g.generated_at_utc, utc=True) < pd.to_datetime(g.scheduled_utc, utc=True)]
            if g.empty:
                continue
            last = g.sort_values("generated_at_utc").iloc[-1]
            a, hs, cons = _num(oo.iloc[0].actual_initial), _num(last.hs_estimate), _num(last.consensus)
            lo, hi = _num(last.interval_lo), _num(last.interval_hi)
            rows.append({"event_key": k, "release": last.release, "unit": last.unit, "actual": a, "hs": hs, "consensus": cons,
                         "baseline": _num(last.baseline_estimate), "hs_error": a - hs,
                         "cons_error": None if cons is None else a - cons,
                         "surprise": None if cons is None else a - cons,
                         "in_interval": None if (lo is None or hi is None) else bool(lo <= a <= hi),
                         "dir_correct": None if cons is None or a == cons or hs == cons else bool((hs - cons) * (a - cons) > 0),
                         "model_version": last.model_version, "actual_latest": _num(oo.iloc[0].actual_latest)})
        return pd.DataFrame(rows)


def performance(ev):
    """Live (genuinely pre-release) performance per release. Metrics are withheld below MIN_LIVE_FOR_PERFORMANCE."""
    out = []
    for rel, g in (ev.groupby("release") if len(ev) else []):
        n = len(g)
        row = {"release": rel, "n_evaluated": n, "status": "collecting"}
        if n >= MIN_LIVE_FOR_PERFORMANCE:
            row.update(status="reported", hs_mae=float(g.hs_error.abs().mean()),
                       hs_rmse=float(np.sqrt((g.hs_error ** 2).mean())))
            gc = g.dropna(subset=["cons_error"])
            if len(gc) >= MIN_LIVE_FOR_PERFORMANCE:
                row.update(n_consensus=len(gc), cons_mae=float(gc.cons_error.abs().mean()),
                           hs_mae_same=float(gc.hs_error.abs().mean()))
            d = g.dir_correct.dropna()
            if len(d) >= MIN_LIVE_FOR_PERFORMANCE:
                row["surprise_dir_acc"] = float(d.astype(float).mean())
            c = g.in_interval.dropna()
            if len(c) >= MIN_LIVE_FOR_PERFORMANCE:
                row["interval_coverage"] = float(c.astype(float).mean())
        out.append(row)
    return pd.DataFrame(out)


def _num(v):
    try:
        x = float(v)
        return None if np.isnan(x) else x
    except (TypeError, ValueError):
        return None


def _f(v, nd=6):
    return None if v is None else round(float(v), nd)
