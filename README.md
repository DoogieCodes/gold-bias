# gold-bias

High Society × TFF@HIGHTABLE gold news-bias dashboard (Streamlit).

## HS EXPECTATION ENGINE (new tab: "HS Expectation")
Independent, deterministic statistical estimates for **NFP, CPI (m/m, y/y), Core CPI (m/m, y/y) and Core PCE (m/m, y/y)**,
compared with the official consensus from the existing calendar feed. No paid APIs, no AI calls, **no API key required**.

* Data: FRED public CSV downloads (same access method the app's `macro()` already uses).
* Models: naive baselines vs ridge regression, chosen by expanding-window walk-forward error. If no model beats a baseline, the
  estimate is labelled as that baseline. Intervals are empirical (walk-forward errors), shown only with >= 30 errors.
* Forecasts are logged **before** release, append-only (`hs_data/*.csv`, mirrored to Supabase tables `hs_forecasts`, `hs_outcomes`).
* Package: `hs_engine/` (providers, features, models, releases, scenarios, store, ui). Tests: `tests/` (synthetic data only).

### Run tests
    python -m unittest discover -s tests -v

### Optional persistence
Run `supabase_schema.sql` in your existing Supabase project. The app reuses the existing `[supabase]` secret. Without it the
forecast log lives in local files that Streamlit Community Cloud wipes on reboot or redeploy.

Not financial advice. Analytical decision support only; no trading automation.
