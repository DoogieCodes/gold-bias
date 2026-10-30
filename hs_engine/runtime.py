"""Background runtime. Downloading data and running the backtests takes seconds to minutes, so it happens in a
background thread; the app never waits on it. Results for the calendar events are then computed quickly on demand."""
import threading
import time

import pandas as pd

from . import providers as P
from . import releases as R
from . import store as ST
from .config import CACHE_FAIL_SECONDS, CACHE_OK_SECONDS

_LOCK = threading.Lock()
_S = {"bundle": None, "reports": None, "running": False, "finished": 0.0, "error": None}
_DONE = set()   # forecasts / outcomes already logged by this process (avoids repeated store calls)


def _ttl():
    b = _S["bundle"]
    return CACHE_FAIL_SECONDS if (b is None or b.errors) else CACHE_OK_SECONDS


def ensure_started(api_key=None, loader=None, force=False):
    """Start a background refresh if the data is missing or stale. Returns immediately."""
    with _LOCK:
        if _S["running"]:
            return
        if not force and _S["finished"] and time.time() - _S["finished"] < _ttl():
            return
        _S["running"] = True
    P.set_api_key(api_key)
    threading.Thread(target=_work, args=(loader or P.load_bundle,), daemon=True).start()


def _work(loader):
    try:
        new = loader()
        with _LOCK:
            old = _S["bundle"]
        if not new.series and old is None:
            raise RuntimeError("; ".join(f"{k}: {v}" for k, v in new.errors.items()) or "no data")
        if old is not None:
            for sid, sd in old.series.items():
                if sid not in new.series:
                    new.series[sid] = sd
                    new.errors[sid] = new.errors.get(sid, "refresh failed") + " (showing older copy)"
        reports = R.get_reports(new)
        with _LOCK:
            _S.update(bundle=new, reports=reports, error=None)
    except Exception as e:
        with _LOCK:
            _S["error"] = str(e) or type(e).__name__
    finally:
        with _LOCK:
            _S["running"], _S["finished"] = False, time.time()


def snapshot():
    with _LOCK:
        s = dict(_S)
    s["status"] = ("ready" if s["bundle"] is not None and s["reports"] is not None
                   else "loading" if s["running"] or not s["finished"] else "unavailable")
    return s


def _events(cal_all, now, days=21):
    if cal_all is None or not len(cal_all):
        return []
    d = cal_all[cal_all.status == "scheduled"].copy()
    d["t"] = pd.to_datetime(d["time"], utc=True, errors="coerce")
    d = d[d.t.notna() & (d.t > now - pd.Timedelta(days=2)) & (d.t < now + pd.Timedelta(days=days))].sort_values("t")
    return [dict(key=r["key"], title=r["title"], time=r["t"], forecast=r["forecast"], actual=r["actual"]) for _, r in d.iterrows()]


def results(cal_all, now=None, api_key=None, supabase=None, store_factory=None, loader=None):
    """Called on every page run. Never blocks on the network. Returns status plus per-event engine results."""
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    ensure_started(api_key, loader)
    s = snapshot()
    out = {"status": s["status"], "error": s["error"], "bundle": s["bundle"], "reports": s["reports"],
           "results": {}, "store": None, "refreshing": s["running"]}
    if s["status"] != "ready":
        return out
    store = (store_factory or (lambda sb: ST.Store(supabase=sb)))(supabase)
    out["store"] = store
    for ev in _events(cal_all, now):
        r = R.evaluate_event(ev, s["bundle"], s["reports"], now)
        if r is None:
            continue
        out["results"][ev["key"]] = r
        try:
            if r["actual"] is not None and r["scheduled_utc"] <= now:
                k = ("o", r["event_key"])
                if k not in _DONE:
                    store.record_outcome(r["event_key"], r["release"], r["measure"], r["unit"], r["actual"], now,
                                         R.actual_from_fred(s["bundle"], r["release"], r["measure"], r["ref_period"]))
                    _DONE.add(k)
            elif r["status"] in ("forecast_ready", "waiting_consensus"):
                k = ("f", r["event_key"], r["consensus"], round(r["estimate"], 4), r["model_version"])
                if k not in _DONE:
                    store.log_forecast(r, now)
                    _DONE.add(k)
        except Exception as e:
            ST.last_error["msg"] = f"log: {type(e).__name__}"
    return out
