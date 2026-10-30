"""Static configuration. No secrets live here; no API key is required anywhere in this package."""

MODEL_VERSION = "hs-1.0.0"

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
USER_AGENT = {"User-Agent": "HighSocietyGoldBias/1.0 (HS Expectation Engine)"}
HTTP_TIMEOUT = 15

# FRED series used. "lag" = conservative days after the end of the observation period (monthly series) or after
# the observation date (weekly series) before the value can be treated as public. These are RULES OF THUMB, not
# vintage timestamps: FRED's CSV download carries no publication dates.
SERIES = {
    "PAYEMS":       {"freq": "M", "lag": 9,  "label": "Total nonfarm payrolls (BLS)"},
    "ICSA":         {"freq": "W", "lag": 5,  "label": "Initial jobless claims (DOL)"},
    "CCSA":         {"freq": "W", "lag": 12, "label": "Continuing claims (DOL)"},
    "CPIAUCSL":     {"freq": "M", "lag": 16, "label": "CPI all items, SA (BLS)"},
    "CPILFESL":     {"freq": "M", "lag": 16, "label": "Core CPI, SA (BLS)"},
    "CUSR0000SAH1": {"freq": "M", "lag": 16, "label": "CPI shelter, SA (BLS)"},
    "PPIFIS":       {"freq": "M", "lag": 16, "label": "PPI final demand (BLS)"},
    "PCEPILFE":     {"freq": "M", "lag": 31, "label": "Core PCE price index (BEA)"},
    "GASREGW":      {"freq": "W", "lag": 2,  "label": "US regular gasoline price, weekly (EIA)"},
}

# Forecast time for target month p = end of month p + offset days. Chosen EARLIER than any real release
# so that nothing published with the release itself can be used.
FC_OFFSET_DAYS = {"nfp": 0, "cpi_mm": 7, "core_cpi_mm": 7, "core_pce_mm": 24}

SPECS = {
    "nfp": {"label": "US Non-Farm Payrolls", "unit": "K jobs", "target": "PAYEMS", "start": "1995-01-01",
            "extra": ["claims_chg", "cclaims_chg"], "min_train": 120,
            # pandemic collapse and rebound: unusable as training or scoring data for a payroll-change model
            "exclude": [("2020-03-01", "2021-06-01")]},
    "cpi_mm": {"label": "US CPI m/m", "unit": "%", "target": "CPIAUCSL", "start": "1995-01-01",
               "extra": ["gas_mom", "shelter_l1", "ppi_l1"], "min_train": 120, "exclude": []},
    "core_cpi_mm": {"label": "US Core CPI m/m", "unit": "%", "target": "CPILFESL", "start": "1995-01-01",
                    "extra": ["shelter_l1", "ppi_l1"], "min_train": 120, "exclude": []},
    "core_pce_mm": {"label": "US Core PCE m/m", "unit": "%", "target": "PCEPILFE", "start": "1995-01-01",
                    "extra": ["core_cpi_p", "ppi_l1"], "min_train": 120, "exclude": []},
}

FEATURE_SERIES = {  # feature -> series it needs (so a missing provider drops only what depends on it)
    "claims_chg": "ICSA", "cclaims_chg": "CCSA", "gas_mom": "GASREGW", "shelter_l1": "CUSR0000SAH1",
    "ppi_l1": "PPIFIS", "core_cpi_p": "CPILFESL",
}
FEATURE_LABEL = {
    "l1": "last month's value", "m3": "3-month average", "m12": "12-month average",
    "claims_chg": "change in 4-wk avg initial claims (survey week vs a month earlier, log)",
    "cclaims_chg": "change in 4-wk avg continuing claims (log)",
    "gas_mom": "gasoline price, month avg vs prior month avg (%)",
    "shelter_l1": "last month's CPI shelter change (%)", "ppi_l1": "last month's PPI final-demand change (%)",
    "core_cpi_p": "same-month core CPI change (%)",
}

RIDGE_LAMBDA = 10.0
INTERVAL_LEVEL = 0.80
MIN_OOS_FOR_VALIDATION = 36
MIN_OOS_FOR_INTERVAL = 30
MIN_LIVE_FOR_PERFORMANCE = 10
CACHE_OK_SECONDS = 6 * 3600
CACHE_FAIL_SECONDS = 600
