"""Free data access from FRED.

Route 1 (default, no key): the public CSV download, same as the app's existing macro().
Route 2 (optional, free key): the FRED API on a different host (api.stlouisfed.org). Used first when a key is configured.
If both fail for a series, the last good copy saved on disk is used and flagged as older data.

Limitation: both routes give the LATEST vintage only, with no publication timestamps, so availability is approximated
with conservative lag rules from config.SERIES."""
import io
import os
import time

import pandas as pd
import requests

from .config import FRED_CSV, SERIES, USER_AGENT

API_URL = "https://api.stlouisfed.org/fred/series/observations"
START = "1990-01-01"
TIMEOUT = (10, 45)          # (connect, read) seconds; the old 15 s read timeout was too tight for FRED from cloud hosts
RETRY = (1.0, 3.0)          # sleeps between attempts (3 attempts in total)
CACHE_DIR = os.path.join("hs_data", "cache")
_KEY = {"v": None}


class ProviderError(Exception):
    pass


class SeriesData:
    def __init__(self, sid, df, fetched_at, source="FRED public CSV (latest vintage)", note=""):
        self.sid, self.df, self.fetched_at, self.source, self.note = sid, df, fetched_at, source, note

    @property
    def last_obs(self):
        return self.df.index[-1] if len(self.df) else None


class Bundle:
    def __init__(self, series=None, errors=None, fetched_at=None):
        self.series = series if series is not None else {}
        self.errors = errors if errors is not None else {}
        self.fetched_at = fetched_at

    def fingerprint(self):
        return tuple(sorted((k, str(v.last_obs), len(v.df), round(float(v.df["value"].iloc[-1]), 6))
                            for k, v in self.series.items() if len(v.df)))


def set_api_key(key):
    _KEY["v"] = key or os.environ.get("FRED_API_KEY") or None


def _availability(idx, freq, lag):
    idx = pd.DatetimeIndex(idx)
    base = (idx + pd.offsets.MonthEnd(0)) if freq == "M" else idx
    return base.normalize() + pd.Timedelta(days=lag)


def _frame(idx, val, sid):
    out = pd.DataFrame({"value": pd.to_numeric(pd.Series(list(val)), errors="coerce").values},
                       index=pd.to_datetime(pd.Series(list(idx)), errors="coerce"))
    out = out[out.index.notna() & out["value"].notna()]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if out.empty:
        raise ProviderError(f"{sid}: no valid observations")
    meta = SERIES[sid]
    out["avail"] = _availability(out.index, meta["freq"], meta["lag"])
    return out


def parse_fred_csv(text, sid):
    try:
        d = pd.read_csv(io.StringIO(text))
    except Exception as e:
        raise ProviderError(f"{sid}: malformed CSV ({type(e).__name__})") from e
    if d.shape[1] < 2 or len(d) == 0:
        raise ProviderError(f"{sid}: malformed CSV (unexpected shape)")
    return _frame(d.iloc[:, 0], d.iloc[:, 1], sid)


def parse_fred_json(obj, sid):
    obs = obj.get("observations") if isinstance(obj, dict) else None
    if not isinstance(obs, list) or not obs:
        msg = obj.get("error_message") if isinstance(obj, dict) else None
        raise ProviderError(f"{sid}: API returned no observations" + (f" ({msg})" if msg else ""))
    return _frame([o.get("date") for o in obs], [o.get("value") for o in obs], sid)


def _request(url, params, getter):
    last = "unknown error"
    for i in range(len(RETRY) + 1):
        try:
            r = getter(url, params=params, timeout=TIMEOUT, headers=USER_AGENT)
        except requests.RequestException as e:
            last = f"network error ({type(e).__name__})"
        else:
            if r.status_code == 200:
                return r
            if r.status_code not in (429, 500, 502, 503, 504):
                raise ProviderError(f"HTTP {r.status_code}")
            last = "rate limited (HTTP 429)" if r.status_code == 429 else f"HTTP {r.status_code}"
        if i < len(RETRY):
            time.sleep(RETRY[i])
    raise ProviderError(last)


def _cache_path(sid):
    return os.path.join(CACHE_DIR, sid + ".csv")


def _cache_write(sid, df):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        df[["value"]].to_csv(_cache_path(sid))
    except Exception:
        pass


def _cache_read(sid):
    p = _cache_path(sid)
    try:
        if os.path.exists(p):
            return parse_fred_csv(open(p).read(), sid), pd.Timestamp(os.path.getmtime(p), unit="s")
    except Exception:
        pass
    return None, None


def _now():
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def fetch_series(sid, getter=requests.get):
    errs = []
    if _KEY["v"]:
        try:
            r = _request(API_URL, {"series_id": sid, "api_key": _KEY["v"], "file_type": "json",
                                   "observation_start": START}, getter)
            sd = SeriesData(sid, parse_fred_json(r.json(), sid), _now(), "FRED API (latest vintage)")
            _cache_write(sid, sd.df)
            return sd
        except (ProviderError, ValueError) as e:
            errs.append(f"API: {e}")
    try:
        r = _request(FRED_CSV, {"id": sid, "cosd": START}, getter)
        sd = SeriesData(sid, parse_fred_csv(r.text, sid), _now())
        _cache_write(sid, sd.df)
        return sd
    except ProviderError as e:
        errs.append(f"CSV: {e}")
    df, when = _cache_read(sid)
    if df is not None:
        return SeriesData(sid, df, when, "saved copy on disk",
                          note=f"download failed ({'; '.join(errs)}); using saved copy from {when:%Y-%m-%d %H:%M} UTC")
    raise ProviderError(f"{sid}: " + "; ".join(errs))


def load_bundle(sids=None, fetcher=fetch_series):
    """Fetch every series one at a time (gentler on FRED than parallel requests). Failures are recorded per series
    and never raised."""
    b = Bundle(fetched_at=_now())
    for sid in list(sids or SERIES):
        try:
            sd = fetcher(sid)
            b.series[sid] = sd
            if getattr(sd, "note", ""):
                b.errors[sid] = sd.note
        except Exception as e:
            b.errors[sid] = str(e) or type(e).__name__
    return b
