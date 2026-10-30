"""Free data access: FRED public CSV downloads (no key). Same access method the existing app's macro() uses.

Limitations (see README): the CSV carries the LATEST vintage only, with no publication timestamps, so
availability is approximated with conservative lag rules from config.SERIES."""
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import pandas as pd
import requests

from .config import (CACHE_FAIL_SECONDS, CACHE_OK_SECONDS, FRED_CSV, HTTP_TIMEOUT, SERIES, USER_AGENT)


class ProviderError(Exception):
    pass


@dataclass
class SeriesData:
    sid: str
    df: pd.DataFrame            # index = observation date (naive); columns: value, avail (naive UTC timestamp)
    fetched_at: pd.Timestamp
    source: str = "FRED public CSV (latest vintage)"

    @property
    def last_obs(self):
        return self.df.index[-1] if len(self.df) else None


@dataclass
class Bundle:
    series: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    fetched_at: pd.Timestamp = None

    def fingerprint(self):
        return tuple(sorted((k, str(v.last_obs), len(v.df), round(float(v.df["value"].iloc[-1]), 6))
                            for k, v in self.series.items() if len(v.df)))


def _availability(idx, freq, lag):
    idx = pd.DatetimeIndex(idx)
    base = (idx + pd.offsets.MonthEnd(0)) if freq == "M" else idx
    return base.normalize() + pd.Timedelta(days=lag)


def parse_fred_csv(text, sid):
    """Parse FRED CSV text into a SeriesData frame. Raises ProviderError on malformed or empty content."""
    try:
        d = pd.read_csv(io.StringIO(text))
    except Exception as e:
        raise ProviderError(f"{sid}: malformed CSV ({type(e).__name__})") from e
    if d.shape[1] < 2 or len(d) == 0:
        raise ProviderError(f"{sid}: malformed CSV (unexpected shape)")
    idx = pd.to_datetime(d.iloc[:, 0], errors="coerce")
    val = pd.to_numeric(d.iloc[:, 1], errors="coerce")   # FRED writes missing values as "."
    out = pd.DataFrame({"value": val.values}, index=idx)
    out = out[out.index.notna() & out["value"].notna()]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    if out.empty:
        raise ProviderError(f"{sid}: no valid observations")
    meta = SERIES[sid]
    out["avail"] = _availability(out.index, meta["freq"], meta["lag"])
    return out


def fetch_series(sid, getter=requests.get):
    try:
        r = getter(FRED_CSV, params={"id": sid}, timeout=HTTP_TIMEOUT, headers=USER_AGENT)
    except requests.RequestException as e:
        raise ProviderError(f"{sid}: network error ({type(e).__name__})") from e
    if r.status_code == 429:
        raise ProviderError(f"{sid}: rate limited (HTTP 429)")
    if r.status_code >= 400:
        raise ProviderError(f"{sid}: HTTP {r.status_code}")
    return SeriesData(sid, parse_fred_csv(r.text, sid), pd.Timestamp.now(tz="UTC").tz_localize(None))


def load_bundle(sids=None, fetcher=fetch_series):
    """Fetch every series; a failure is recorded per series and never raised."""
    sids = list(sids or SERIES)
    b = Bundle(fetched_at=pd.Timestamp.now(tz="UTC").tz_localize(None))

    def one(sid):
        try:
            return sid, fetcher(sid), None
        except Exception as e:  # provider failure must never crash the app
            return sid, None, str(e) or type(e).__name__

    with ThreadPoolExecutor(max_workers=5) as ex:
        for sid, sd, err in ex.map(one, sids):
            if sd is not None:
                b.series[sid] = sd
            else:
                b.errors[sid] = err
    return b


_LOCK = threading.Lock()
_CACHE = {"bundle": None, "at": 0.0}


def get_bundle(force=False, fetcher=fetch_series):
    """Process-wide cache: 6 h after a clean load, 10 min after any failure. If a refresh fails completely the
    previous bundle is kept (its fetched_at shows how old it is) and the errors are attached."""
    with _LOCK:
        cur, age = _CACHE["bundle"], time.time() - _CACHE["at"]
        ttl = CACHE_FAIL_SECONDS if (cur is None or cur.errors) else CACHE_OK_SECONDS
        if cur is not None and age < ttl and not force:
            return cur
        new = load_bundle(fetcher=fetcher)
        if not new.series and cur is not None:
            cur.errors = {**cur.errors, **new.errors}
            _CACHE["at"] = time.time()
            return cur
        if cur is not None:   # keep a stale copy of any series that failed this time
            for sid, sd in cur.series.items():
                if sid not in new.series:
                    new.series[sid] = sd
                    new.errors[sid] = new.errors.get(sid, "refresh failed") + " (showing older copy)"
        _CACHE.update(bundle=new, at=time.time())
        return new
