-- Run once in the Supabase SQL editor (same project the app already uses). Append-only logs for HS EXPECTATION ENGINE.
create table if not exists hs_forecasts (
  forecast_id text primary key, event_key text, release text, measure text, title text, scheduled_utc text,
  consensus text, hs_estimate text, method text, baseline_estimate text, interval_lo text, interval_hi text,
  interval_basis text, unit text, model_version text, validation_status text, n_train text,
  inputs_last_obs text, generated_at_utc text, data_origin text);
create table if not exists hs_outcomes (
  event_key text primary key, release text, measure text, unit text, actual_initial text, actual_source text,
  recorded_at_utc text, actual_latest text, latest_recorded_at_utc text);
-- The app uses the same key as its existing tables. If row-level security is enabled on your project, add insert/select
-- policies for that key on both tables (mirror whatever you configured for "calls" and "calendar").
