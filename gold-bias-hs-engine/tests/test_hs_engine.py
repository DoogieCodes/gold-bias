"""Tests for HS EXPECTATION ENGINE. ALL DATA HERE IS SYNTHETIC (clearly labelled) and is never used in live forecasts.
Run:  python -m unittest discover -s tests -v"""
import ast
import os
import re
import tempfile
import time
import unittest
from unittest import mock

import numpy as np
import pandas as pd
import requests

from hs_engine import features as F
from hs_engine import models as M
from hs_engine import providers as P
from hs_engine import releases as R
from hs_engine import scenarios as SC
from hs_engine import store as ST
from hs_engine.config import SERIES, SPECS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = pd.Timestamp("2026-10-30 12:00", tz="UTC")


def _sd(sid, idx, vals):
    meta = SERIES[sid]
    df = pd.DataFrame({"value": vals}, index=pd.DatetimeIndex(idx))
    df["avail"] = P._availability(df.index, meta["freq"], meta["lag"])
    return P.SeriesData(sid, df, pd.Timestamp("2026-10-30"))


def synthetic_bundle(seed=0, end="2026-09-01", drop=()):
    """SYNTHETIC economy with a KNOWN structure: payroll change depends on its own lag and on claims."""
    rng = np.random.default_rng(seed)
    months = pd.date_range("1990-01-01", end, freq="MS")
    weeks = pd.date_range("1990-01-06", pd.Timestamp(end) + pd.offsets.MonthEnd(0) + pd.Timedelta(days=21), freq="7D")
    B = P.Bundle(fetched_at=pd.Timestamp("2026-10-30"))
    icsa = 300000 + np.cumsum(rng.normal(0, 4000, len(weeks))) * 0.3 + rng.normal(0, 8000, len(weeks))
    B.series["ICSA"] = _sd("ICSA", weeks, np.abs(icsa) + 100000)
    B.series["CCSA"] = _sd("CCSA", weeks, np.abs(icsa) * 6)
    gas = 2.5 + np.cumsum(rng.normal(0, 0.03, len(weeks)))
    B.series["GASREGW"] = _sd("GASREGW", pd.date_range("1990-01-01", periods=len(weeks), freq="7D"), np.abs(gas) + 1)
    # claims feature per month, using the engine's own definition, then payroll depends on it
    tmp = P.Bundle(series={"ICSA": B.series["ICSA"]})
    cc = np.array([F._claims_chg(tmp, "ICSA", p, pd.Timestamp("2262-01-01")) for p in months])
    cc = np.nan_to_num(cc)
    d = np.zeros(len(months))
    for i in range(1, len(months)):
        d[i] = 0.5 * d[i - 1] + 20 - 1500 * cc[i] + rng.normal(0, 25)
    B.series["PAYEMS"] = _sd("PAYEMS", months, 100000 + np.cumsum(d))
    for sid, base in [("CPIAUCSL", 0.25), ("CPILFESL", 0.22), ("CUSR0000SAH1", 0.3), ("PPIFIS", 0.2), ("PCEPILFE", 0.2)]:
        m = base + 0.4 * np.convolve(rng.normal(0, 0.1, len(months)), [0.6, 0.4], "same")
        B.series[sid] = _sd(sid, months, 100 * np.exp(np.cumsum(m / 100)))
    for k in drop:
        B.series.pop(k, None)
        B.errors[k] = "synthetic outage"
    return B


class TestProviders(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = [mock.patch.object(P, "RETRY", ()), mock.patch.object(P, "CACHE_DIR", self._tmp.name)]
        [x.start() for x in self._p]

    def tearDown(self):
        [x.stop() for x in self._p]
        self._tmp.cleanup()

    def test_retry_then_success(self):
        good = mock.Mock(status_code=200, text="observation_date,PAYEMS\n2024-01-01,100\n2024-02-01,101\n")
        calls = [requests.Timeout(), good]
        with mock.patch.object(P, "RETRY", (0.0,)):
            sd = P.fetch_series("PAYEMS", getter=mock.Mock(side_effect=calls))
        self.assertEqual(len(sd.df), 2)

    def test_falls_back_to_saved_copy_and_flags_it(self):
        good = mock.Mock(status_code=200, text="observation_date,PAYEMS\n2024-01-01,100\n2024-02-01,101\n")
        P.fetch_series("PAYEMS", getter=mock.Mock(return_value=good))           # saves a copy
        sd = P.fetch_series("PAYEMS", getter=mock.Mock(side_effect=requests.Timeout()))
        self.assertEqual(len(sd.df), 2)
        self.assertIn("saved copy", sd.note)
        b = P.load_bundle(["PAYEMS"], fetcher=lambda sid: P.fetch_series(sid, getter=mock.Mock(side_effect=requests.Timeout())))
        self.assertIn("PAYEMS", b.series)
        self.assertIn("saved copy", b.errors["PAYEMS"])   # surfaced to the UI, not hidden

    def test_api_route_used_when_key_set(self):
        js = {"observations": [{"date": "2024-01-01", "value": "100"}, {"date": "2024-02-01", "value": "."},
                               {"date": "2024-03-01", "value": "102"}]}
        resp = mock.Mock(status_code=200)
        resp.json.return_value = js
        g = mock.Mock(return_value=resp)
        P.set_api_key("TESTKEY")
        try:
            sd = P.fetch_series("PAYEMS", getter=g)
        finally:
            P.set_api_key(None)
        self.assertEqual(len(sd.df), 2)
        self.assertIn("api.stlouisfed.org", g.call_args[0][0])

    def test_api_json_errors(self):
        for bad in [{}, {"error_message": "bad key"}, {"observations": []}, []]:
            with self.assertRaises(P.ProviderError):
                P.parse_fred_json(bad, "PAYEMS")

    def test_no_data_and_no_cache_raises(self):
        with self.assertRaises(P.ProviderError):
            P.fetch_series("ICSA", getter=mock.Mock(side_effect=requests.Timeout()))
    def test_parse_good_and_missing_dots(self):
        d = P.parse_fred_csv("observation_date,PAYEMS\n2024-01-01,100\n2024-02-01,.\n2024-03-01,102\n", "PAYEMS")
        self.assertEqual(len(d), 2)

    def test_malformed_and_empty(self):
        for txt in ["", "a\n", "x,y\n.,.\n"]:
            with self.assertRaises(P.ProviderError):
                P.parse_fred_csv(txt, "PAYEMS")

    def test_timeout_429_http(self):
        with self.assertRaises(P.ProviderError):
            P.fetch_series("PAYEMS", getter=mock.Mock(side_effect=requests.Timeout()))
        for code in (429, 503):
            resp = mock.Mock(status_code=code, text="")
            with self.assertRaises(P.ProviderError):
                P.fetch_series("PAYEMS", getter=mock.Mock(return_value=resp))

    def test_bundle_partial_failure_never_raises(self):
        def f(sid):
            if sid == "ICSA":
                raise P.ProviderError("down")
            return _sd(sid, pd.date_range("2000-01-01", periods=5, freq="MS") if SERIES[sid]["freq"] == "M"
                       else pd.date_range("2000-01-01", periods=5, freq="7D"), [1.0] * 5)
        b = P.load_bundle(fetcher=f)
        self.assertIn("ICSA", b.errors)
        self.assertIn("PAYEMS", b.series)


class TestPointInTime(unittest.TestCase):
    def test_availability_filter(self):
        B = synthetic_bundle()
        asof = pd.Timestamp("2015-06-30")
        k = F.known(B, "CPIAUCSL", asof)
        self.assertTrue((B.series["CPIAUCSL"].df.loc[k.index, "avail"] <= asof).all())
        self.assertLess(k.index[-1], pd.Timestamp("2015-06-01"))

    def test_features_ignore_target_and_future(self):
        B = synthetic_bundle()
        p = pd.Timestamp("2010-05-01")
        for kind in SPECS:
            base = F.features_at(B, kind, p, F.fc_time(kind, p))
            B2 = synthetic_bundle()
            for sid, sd in B2.series.items():   # corrupt EVERYTHING not yet available at forecast time
                m = sd.df["avail"] > F.fc_time(kind, p)
                sd.df.loc[m, "value"] = 1e9
            alt = F.features_at(B2, kind, p, F.fc_time(kind, p))
            for k in base:
                if base[k] == base[k]:
                    self.assertAlmostEqual(base[k], alt[k], msg=f"{kind}:{k} leaked future data")

    def test_target_never_available_at_forecast_time(self):
        B = synthetic_bundle()
        for kind, spec in SPECS.items():
            df = B.series[spec["target"]].df
            for p in df.index[200:260]:
                self.assertGreater(df.loc[p, "avail"], F.fc_time(kind, p), kind)

    def test_leakage_detected_if_target_included(self):
        """Control: if the target were visible the model would look unrealistically good, proving the test has teeth."""
        B = synthetic_bundle()
        X, y = F.build_dataset(B, "nfp")
        X = X.copy()
        X["l1"] = y   # deliberately leaky
        rep = M.run_backtest(X, y, SPECS["nfp"])
        self.assertLess(rep["metrics"]["naive"]["mae"], 1e-6)


class TestModels(unittest.TestCase):
    def test_ridge_recovers_structure(self):
        rng = np.random.default_rng(1)
        X = pd.DataFrame(rng.normal(size=(500, 2)), columns=["a", "b"])
        y = 3 * X.a - 2 * X.b + 5 + rng.normal(0, 0.1, 500)
        m = M.ridge_fit(X, y, lam=1.0)
        self.assertAlmostEqual(M.ridge_predict(m, pd.Series({"a": 1.0, "b": 1.0})), 6.0, delta=0.2)

    def test_walk_forward_uses_only_past(self):
        B = synthetic_bundle()
        X, y = F.build_dataset(B, "core_cpi_mm")
        spec = SPECS["core_cpi_mm"]
        cols = M.AR + spec["extra"]
        fr, yy = X[cols].dropna(), y.reindex(X[cols].dropna().index)
        base = M.walk_forward(fr, yy, spec["extra"], 120)
        k = 250
        y2 = yy.copy()
        y2.iloc[k:] = y2.iloc[k:] + 999.0      # change FUTURE targets only
        alt = M.walk_forward(fr, y2, spec["extra"], 120)
        pd.testing.assert_series_equal(base["ridge_full"].iloc[:k - 120], alt["ridge_full"].iloc[:k - 120])

    def test_backtest_finds_real_signal(self):
        B = synthetic_bundle()
        X, y = F.build_dataset(B, "nfp")
        rep = M.run_backtest(X, y, SPECS["nfp"])
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["selected"], "ridge_full")
        self.assertLess(rep["metrics"]["ridge_full"]["mae"], rep["metrics"]["naive"]["mae"])
        self.assertEqual(rep["validation"]["code"], "validated")
        self.assertLess(rep["ablation"]["claims_chg"], rep["ar_mae"])  # known-useful indicator detected as useful

    def test_noise_indicator_not_credited(self):
        B = synthetic_bundle()
        X, y = F.build_dataset(B, "nfp")
        X = X.copy()
        X["cclaims_chg"] = np.random.default_rng(5).normal(size=len(X))
        rep = M.run_backtest(X, y, SPECS["nfp"])
        self.assertGreaterEqual(rep["ablation"]["cclaims_chg"], rep["ar_mae"] * 0.98)

    def test_covid_window_excluded(self):
        B = synthetic_bundle()
        X, y = F.build_dataset(B, "nfp")
        rep = M.run_backtest(X, y, SPECS["nfp"])
        self.assertFalse(((rep["preds"].index >= "2020-03-01") & (rep["preds"].index <= "2021-06-01")).any())

    def test_interval_and_coverage(self):
        B = synthetic_bundle()
        X, y = F.build_dataset(B, "nfp")
        rep = M.run_backtest(X, y, SPECS["nfp"])
        self.assertGreater(rep["coverage"]["n"], 30)
        self.assertAlmostEqual(rep["coverage"]["coverage"], 0.8, delta=0.12)

    def test_insufficient_history(self):
        B = synthetic_bundle(end="1999-12-01")
        X, y = F.build_dataset(B, "nfp")
        rep = M.run_backtest(X, y, SPECS["nfp"])
        self.assertFalse(rep["ok"])
        self.assertIn("usable", rep["reason"])

    def test_unvalidated_label_when_few_oos(self):
        rep = {"n_oos": 20, "selected": "ridge_full", "sign_test_p": 0.01, "mae_ratio": 0.5}
        self.assertEqual(M.validation_status(rep)["code"], "unvalidated")

    def test_baseline_selected_is_labelled(self):
        rep = {"n_oos": 100, "selected": "naive", "sign_test_p": None, "mae_ratio": 1.0}
        self.assertEqual(M.validation_status(rep)["code"], "baseline_only")


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.B = synthetic_bundle()
        cls.reports = R.compute_reports(cls.B)

    def ev(self, title, fc="", act="", t="2026-10-09 12:30:00+00:00"):
        return dict(key=f"{title}|x", title=title, time=pd.Timestamp(t), forecast=fc, actual=act)

    def test_missing_consensus_not_invented(self):
        r = R.evaluate_event(self.ev("Core CPI m/m", ""), self.B, self.reports, pd.Timestamp("2026-10-05", tz="UTC"))
        self.assertIsNone(r["consensus"])
        self.assertIsNone(r["diff"])
        self.assertEqual(r["status"], "waiting_consensus")
        self.assertIsNotNone(r["estimate"])

    def test_consensus_diff_units_mm(self):
        r = R.evaluate_event(self.ev("Core CPI m/m", "0.3%"), self.B, self.reports, pd.Timestamp("2026-10-05", tz="UTC"))
        self.assertEqual(r["status"], "forecast_ready")
        self.assertAlmostEqual(r["diff"], r["estimate"] - 0.3)

    def test_nfp_units_thousands(self):
        r = R.evaluate_event(self.ev("Non-Farm Employment Change", "150K", t="2026-10-02 12:30:00+00:00"), self.B, self.reports,
                             pd.Timestamp("2026-09-30", tz="UTC"))
        self.assertEqual(r["consensus"], 150.0)
        self.assertEqual(r["unit"], "K jobs")
        self.assertAlmostEqual(r["diff"], r["estimate"] - 150.0)

    def test_yoy_derived_and_flagged(self):
        r = R.evaluate_event(self.ev("Core CPI y/y", "3.0%"), self.B, self.reports, pd.Timestamp("2026-10-05", tz="UTC"))
        self.assertEqual(r["measure"], "yy")
        self.assertTrue(any("DERIVED" in n for n in r["notes"]))
        lv = F.known(self.B, "CPILFESL", pd.Timestamp("2262-01-01"))
        exp = 100 * (lv[pd.Timestamp("2026-08-01")] * (1 + r["mm_estimate"] / 100) / lv[pd.Timestamp("2025-09-01")] - 1)
        self.assertAlmostEqual(r["estimate"], exp, places=6)

    def test_provider_outage_status(self):
        B = synthetic_bundle(drop=("CPILFESL",))
        reps = R.compute_reports(B)
        r = R.evaluate_event(self.ev("Core CPI m/m", "0.3%"), B, reps, pd.Timestamp("2026-10-05", tz="UTC"))
        self.assertEqual(r["status"], "provider_unavailable")
        self.assertIsNone(r["estimate"])

    def test_missing_indicator_degrades_gracefully(self):
        B = synthetic_bundle(drop=("ICSA", "CCSA"))
        reps = R.compute_reports(B)
        self.assertTrue(reps["nfp"]["ok"])
        self.assertEqual(reps["nfp"]["extras"], [])
        self.assertEqual(set(reps["nfp"]["dropped_extras"]), {"claims_chg", "cclaims_chg"})

    def test_actual_released_status(self):
        r = R.evaluate_event(self.ev("Core CPI m/m", "0.3%", "0.4%"), self.B, self.reports, pd.Timestamp("2026-10-10", tz="UTC"))
        self.assertEqual(r["status"], "actual_released")
        self.assertEqual(r["actual"], 0.4)

    def test_unsupported_title(self):
        self.assertIsNone(R.evaluate_event(self.ev("ISM Manufacturing PMI"), self.B, self.reports))
        self.assertIsNone(R.classify_title("ADP Non-Farm Employment Change"))

    def test_parse_values(self):
        self.assertEqual(R.parse_value("-20K", "nfp"), -20.0)
        self.assertEqual(R.parse_value("1.2M", "nfp"), 1200.0)
        self.assertIsNone(R.parse_value("150", "nfp"))
        self.assertEqual(R.parse_value("0.3%", "cpi"), 0.3)
        for bad in ["", None, "n/a", "abc"]:
            self.assertIsNone(R.parse_value(bad, "cpi"))

    def test_timezone_reference_period(self):
        self.assertEqual(R.ref_period("2026-11-06 13:30:00+00:00"), pd.Timestamp("2026-10-01"))
        # 03:00 UTC on 1 Dec is still 30 Nov in New York -> reference month stays October
        self.assertEqual(R.ref_period("2026-12-01 03:00:00+00:00"), pd.Timestamp("2026-10-01"))
        self.assertEqual(R.ref_period(pd.Timestamp("2026-12-10 13:30", tz="UTC")), pd.Timestamp("2026-11-01"))

    def test_incomplete_month_waits_for_gasoline(self):
        B = synthetic_bundle()
        x = F.features_at(B, "cpi_mm", pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-20"))
        self.assertTrue(np.isnan(x["gas_mom"]))


class TestScenarios(unittest.TestCase):
    def mk(self, diff, tier="meaningful"):
        return {"release": "cpi", "consensus": 0.3, "estimate": 0.3 + diff, "tier": tier, "diff": diff, "sigma": 0.1, "unit": "%"}

    def test_upside_hawkish(self):
        t = SC.pre_release(self.mk(0.2))["headline"]
        self.assertIn("above consensus", t)
        self.assertIn("gold-negative", t)

    def test_downside_dovish(self):
        self.assertIn("below consensus", SC.pre_release(self.mk(-0.2))["headline"])

    def test_negligible(self):
        self.assertIn("within model noise", SC.pre_release(self.mk(0.01, "negligible"))["headline"])

    def test_no_consensus(self):
        r = self.mk(0.2)
        r.update(consensus=None, diff=None, tier=None)
        self.assertIn("No official consensus", SC.pre_release(r)["headline"])

    def test_scenarios_always_have_invalidators(self):
        for s in SC.pre_release(self.mk(0.2))["scenarios"]:
            self.assertTrue(s["invalidates"])

    def test_post_release_lines(self):
        r = {"actual": 0.5, "consensus": 0.3, "estimate": 0.4, "sigma": 0.1, "unit": "%"}
        t = " ".join(SC.post_release(r, move5=-2.0))
        self.assertIn("closer to the actual", t)
        self.assertIn("matched", t)
        self.assertIn("proves nothing", t)
        self.assertIn("did not match", " ".join(SC.post_release(r, move5=+2.0)))


def _row(key="CPI|2026-11-10", est=0.31, cons=0.3, when="2026-11-10 13:30:00+00:00"):
    return {"event_key": key, "title": "CPI m/m", "release": "cpi", "measure": "mm", "unit": "%", "estimate": est,
            "consensus": cons, "scheduled_utc": pd.Timestamp(when), "method": "ridge_full", "baselines": {"naive": 0.2},
            "interval": (0.1, 0.5), "interval_basis": "x", "model_version": "hs-test", "validation": {"code": "preliminary"},
            "n_train": 300, "freshness": {"CPIAUCSL": {"last_obs": "2026-09-01"}}}


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = ST.Store(self.tmp.name, origin="synthetic")

    def tearDown(self):
        self.tmp.cleanup()

    def test_synthetic_cannot_touch_production_dir(self):
        with self.assertRaises(ValueError):
            ST.Store(ST.DEFAULT_DIR, origin="synthetic")

    def test_append_only_and_dedupe(self):
        t0 = pd.Timestamp("2026-11-01", tz="UTC")
        self.assertTrue(self.s.log_forecast(_row(), t0))
        self.assertFalse(self.s.log_forecast(_row(), t0 + pd.Timedelta(hours=1)))          # unchanged -> no new row
        self.assertTrue(self.s.log_forecast(_row(cons=0.4), t0 + pd.Timedelta(hours=2)))   # consensus changed -> new row
        f = self.s.forecasts()
        self.assertEqual(len(f), 2)
        self.assertEqual(f.sort_values("generated_at_utc").iloc[0].consensus, "0.3")       # original preserved

    def test_no_logging_after_release(self):
        self.assertFalse(self.s.log_forecast(_row(), pd.Timestamp("2026-11-10 14:00", tz="UTC")))
        self.assertEqual(len(self.s.forecasts()), 0)

    def test_outcome_never_overwrites_forecast_or_first_print(self):
        t0 = pd.Timestamp("2026-11-01", tz="UTC")
        self.s.log_forecast(_row(), t0)
        before = self.s.forecasts().copy()
        self.assertTrue(self.s.record_outcome("CPI|2026-11-10", "cpi", "mm", "%", 0.4, t0 + pd.Timedelta(days=10)))
        self.assertFalse(self.s.record_outcome("CPI|2026-11-10", "cpi", "mm", "%", 0.9, t0 + pd.Timedelta(days=11), latest=0.5))
        o = self.s.outcomes().iloc[0]
        self.assertEqual(float(o.actual_initial), 0.4)
        self.assertEqual(float(o.actual_latest), 0.5)
        pd.testing.assert_frame_equal(before, self.s.forecasts())

    def test_evaluation_uses_last_pre_release_forecast(self):
        t0 = pd.Timestamp("2026-11-01", tz="UTC")
        self.s.log_forecast(_row(est=0.31), t0)
        self.s.log_forecast(_row(est=0.35), t0 + pd.Timedelta(days=2))
        self.s.record_outcome("CPI|2026-11-10", "cpi", "mm", "%", 0.4, t0 + pd.Timedelta(days=10))
        e = self.s.evaluation().iloc[0]
        self.assertAlmostEqual(e.hs, 0.35)
        self.assertAlmostEqual(e.hs_error, 0.05)
        self.assertAlmostEqual(e.cons_error, 0.1)
        self.assertTrue(e.dir_correct)
        self.assertTrue(e.in_interval)

    def test_no_evaluation_without_forecast_made_before_release(self):
        self.s.record_outcome("CPI|2026-11-10", "cpi", "mm", "%", 0.4, pd.Timestamp("2026-11-10", tz="UTC"))
        self.assertTrue(self.s.evaluation().empty)

    def test_performance_withheld_until_enough(self):
        ev = pd.DataFrame([{"release": "cpi", "hs_error": 0.1, "cons_error": 0.1, "dir_correct": True, "in_interval": True}] * 5)
        p = ST.performance(ev)
        self.assertEqual(p.iloc[0].status, "collecting")
        self.assertNotIn("hs_mae", p.columns)
        p2 = ST.performance(pd.concat([ev] * 2))
        self.assertEqual(p2.iloc[0].status, "reported")

    def test_supabase_failure_does_not_break_logging(self):
        s = ST.Store(self.tmp.name, supabase={"url": "http://invalid.invalid", "key": "k"}, origin="synthetic")
        with mock.patch("requests.post", side_effect=requests.ConnectionError()), mock.patch("requests.get", side_effect=requests.ConnectionError()):
            self.assertTrue(s.log_forecast(_row(), pd.Timestamp("2026-11-01", tz="UTC")))
        self.assertEqual(len(s.forecasts()), 1)
        self.assertIsNotNone(ST.last_error["msg"])


class _Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __getattr__(self, n): return lambda *a, **k: None


class _FakeSt:
    secrets = {}
    def __getattr__(self, n): return lambda *a, **k: None
    def columns(self, n): return [_Ctx() for _ in range(n)]
    def expander(self, *a, **k): return _Ctx()


class TestUiAndCompat(unittest.TestCase):
    def test_ui_and_main_view_render_with_synthetic_bundle(self):
        from hs_engine import ui, dashboard, runtime as RT
        B = synthetic_bundle()
        cal = pd.DataFrame([{"key": "Core CPI m/m|2026-11-10", "title": "Core CPI m/m", "time": "2026-11-10 13:30:00+00:00",
                             "impact": "High", "forecast": "0.3%", "previous": "0.2%", "actual": "", "status": "scheduled", "updated_at": ""},
                            {"key": "FOMC Rate Decision|2026-11-12", "title": "FOMC Rate Decision", "time": "2026-11-12 19:00:00+00:00",
                             "impact": "High", "forecast": "4.5%", "previous": "4.5%", "actual": "", "status": "scheduled", "updated_at": ""}])
        h = pd.DataFrame(columns=["key", "title", "pre_release", "move30", "move5", "move15", "reversal"])
        now = pd.Timestamp("2026-11-03", tz="UTC")
        with tempfile.TemporaryDirectory() as d:
            RT._S.update(bundle=B, reports=R.compute_reports(B), running=False, finished=time.time(), error=None)
            RT._DONE.clear()
            HS = RT.results(cal, now=now, store_factory=lambda sb: ST.Store(d, origin="synthetic"))
            self.assertEqual(HS["status"], "ready")
            self.assertEqual(len(HS["results"]), 1)       # FOMC is not an HS-supported release
            ui.render(_FakeSt(), cal, h, None, HS, now=now)
            md = []
            fake = _FakeSt()
            fake.markdown = lambda t, **k: md.append(t)
            tag = lambda t: "CPI" if "CPI" in t else "FOMC"
            dashboard.render_main(fake, now, [], cal, HS, h, tag)
            page = " ".join(md)
            for word in ["NEXT EVENT", "CONSENSUS", "HS ESTIMATE", "INITIAL SPIKE", "REVERSAL TENDENCY", "UPCOMING", "FOMC Rate Decision"]:
                self.assertIn(word, page)
            self.assertEqual(len(ST.Store(d, origin="synthetic").forecasts()), 1)
            RT.results(cal, now=now, store_factory=lambda sb: ST.Store(d, origin="synthetic"))   # rerun: no duplicate row
            self.assertEqual(len(ST.Store(d, origin="synthetic").forecasts()), 1)

    def test_app_py_keeps_original_functionality(self):
        src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        original = {"num", "lean", "load_cal", "schedule_events", "fetch_feed", "surprise_lean", "sync_calendar", "events",
                    "bls_dates", "gold_series", "oanda_cfg", "live_price", "prices", "market", "macro", "market_lean", "sb",
                    "sb_load", "sb_save", "load_hist", "save_hist", "update_history", "tag", "gate", "fomc_engine", "build_card",
                    "card_html", "cards_view", "top_row", "watcher", "validation", "validation_text", "expected_move",
                    "post_line", "countdown", "resolve", "confidence"}
        self.assertEqual(original - names, set())
        for token in ['"Upcoming", "Calendar", "Track record"', "HIST = \"history.csv\"", "with tab3:", "with tab2:", "with tab1:"]:
            self.assertIn(token, src)
        self.assertIn("hs_engine", src)
        self.assertIn("HS Expectation", src)
        self.assertIn("def main_view", src)
        self.assertIn("Detailed analyst cards (original view)", src)
        self.assertIn("cards_view()", src)


class TestRuntimeAndDashboard(unittest.TestCase):
    def setUp(self):
        from hs_engine import runtime as RT
        self.RT = RT
        RT._S.update(bundle=None, reports=None, running=False, finished=0.0, error=None)

    def test_never_blocks_while_data_loads(self):
        def slow():
            time.sleep(1.5)
            return synthetic_bundle()
        t0 = time.time()
        HS = self.RT.results(pd.DataFrame(), loader=slow)
        self.assertLess(time.time() - t0, 0.5)
        self.assertEqual(HS["status"], "loading")
        for _ in range(100):
            time.sleep(0.5)
            if self.RT.snapshot()["status"] == "ready":
                break
        self.assertEqual(self.RT.snapshot()["status"], "ready")

    def test_total_failure_is_reported_and_retry_is_throttled(self):
        calls = []
        def fail():
            calls.append(1)
            return P.Bundle(errors={"PAYEMS": "network error (ReadTimeout)"}, fetched_at=pd.Timestamp("2026-10-30"))
        self.RT.results(pd.DataFrame(), loader=fail)
        for _ in range(50):
            time.sleep(0.1)
            if not self.RT.snapshot()["running"]:
                break
        HS = self.RT.results(pd.DataFrame(), loader=fail)
        self.RT.results(pd.DataFrame(), loader=fail)
        self.assertEqual(HS["status"], "unavailable")
        self.assertIn("ReadTimeout", HS["error"])
        self.assertEqual(len(calls), 1)      # not hammered on every page run

    def test_failed_refresh_keeps_last_good_data(self):
        B = synthetic_bundle()
        self.RT._S.update(bundle=B, reports=R.compute_reports(B), running=False, finished=0.0, error=None)
        self.RT.ensure_started(loader=lambda: P.Bundle(errors={"PAYEMS": "down"}, fetched_at=pd.Timestamp("2026-10-30")), force=True)
        for _ in range(50):
            time.sleep(0.1)
            if not self.RT.snapshot()["running"]:
                break
        self.assertEqual(self.RT.snapshot()["status"], "ready")
        self.assertIn("PAYEMS", self.RT.snapshot()["bundle"].series)

    def test_dashboard_spike_lean_rules(self):
        from hs_engine import dashboard as D
        base = {"estimate": 0.4, "consensus": 0.3, "diff": 0.1, "tier": "meaningful"}
        self.assertEqual(D.spike_lean(None, base)[0], "Bearish gold")
        self.assertEqual(D.spike_lean(None, {**base, "diff": -0.1})[0], "Bullish gold")
        self.assertEqual(D.spike_lean(None, {**base, "tier": "negligible"})[0], "No edge")
        self.assertEqual(D.spike_lean(None, {**base, "consensus": None})[0], "No edge")
        self.assertEqual(D.spike_lean(None, None)[0], "No edge")
        self.assertEqual(D.spike_lean({"base": "FOMC", "spike": "Bullish gold"}, None)[0], "Bullish gold")

    def test_reaction_stats_only_from_tracked_history(self):
        from hs_engine import dashboard as D
        h = pd.DataFrame({"title": ["CPI m/m"] * 4, "pre_release": [1] * 4, "move30": [1, 2, 3, None], "move5": [1, -2, 3, 9],
                          "move15": [2, -4, 6, 9], "reversal": [0, 1, 0, None]})
        st_ = D.reaction_stats(h, "CPI", lambda t: "CPI")
        self.assertEqual(st_["n"], 3)
        self.assertAlmostEqual(st_["m15"], 4.0)
        self.assertEqual(D.reaction_stats(h.iloc[0:0], "CPI", lambda t: "CPI")["n"], 0)

    def test_countdown_text(self):
        from hs_engine import dashboard as D
        now = pd.Timestamp("2026-11-01", tz="UTC")
        self.assertEqual(D.countdown(now + pd.Timedelta(hours=1, seconds=5), now), "IN 01:00:05")
        self.assertIn("RELEASED", D.countdown(now - pd.Timedelta(minutes=2), now))


if __name__ == "__main__":
    unittest.main()
