"""Conditional USD / XAUUSD scenarios. Deterministic text built from the size of the HS-vs-consensus gap and
observed market context. Decision support only: nothing here is a trade signal or a prediction of price."""

HAWKISH = {"nfp": "stronger payrolls", "cpi": "hotter CPI", "core_cpi": "hotter core CPI", "core_pce": "hotter core PCE"}


def _ctx(mk):
    """Observed facts (not interpretation) from the app's existing market() dict."""
    if not mk:
        return [], None
    facts, hawk = [], 0
    try:
        d, y = mk["Dollar (DXY)"], mk["US 10Y yield"]
        facts.append(f"DXY {d[0]:.1f} ({d[2]:+.1f}% over 5 sessions); US 10Y {y[0]:.2f}% ({y[1]:+.2f} over 5 sessions); "
                     f"gold {mk['Gold'][2]:+.1f}% over 5 sessions")
        hawk = (d[2] > 0.5) + (y[1] > 0.1) - (d[2] < -0.5) - (y[1] < -0.1)
    except Exception:
        return [], None
    return facts, hawk


def pre_release(r, mk=None):
    """r = releases.evaluate_event result. Returns dict(headline, facts, scenarios[list of dict]) ."""
    rel, name = r["release"], {"nfp": "payrolls", "cpi": "CPI", "core_cpi": "core CPI", "core_pce": "core PCE"}[r["release"]]
    cons, est, tier_, diff = r["consensus"], r["estimate"], r["tier"], r["diff"]
    facts, hawk = _ctx(mk)
    out = {"facts": facts, "scenarios": []}
    if est is None:
        out["headline"] = "No HS estimate, so no HS-based scenario. The generic scenarios below do not depend on it."
    elif cons is None:
        out["headline"] = "No official consensus yet: HS cannot say whether it expects an upside or downside surprise."
    elif tier_ == "negligible":
        out["headline"] = (f"HS is within model noise of consensus ({diff:+.3g} {r['unit']} vs an HS error scale of "
                           f"{r['sigma']:.3g}): no meaningful HS-implied surprise.")
    else:
        d = "above" if diff > 0 else "below"
        hk = "hawkish (USD-supportive, usually gold-negative)" if diff > 0 else "dovish (USD-negative, usually gold-supportive)"
        out["headline"] = (f"HS expects {name} {d} consensus by {abs(diff):.3g} {r['unit']} ({tier_} relative to the model's "
                           f"typical error). A {d}-consensus print would read as {hk}.")
    if hawk is not None and facts and cons is not None and est is not None and tier_ != "negligible":
        pos = diff > 0
        if (hawk > 0 and pos) or (hawk < 0 and not pos):
            facts.append("Observed: dollar/yields have already moved in the direction of this HS-implied surprise, so part of it may be priced.")
    s = out["scenarios"]
    s.append({"name": f"Upside surprise (actual above consensus)", "usd": "USD up, yields up",
              "gold": "Bearish gold is the usual response (rate-expectation channel)",
              "invalidates": ["Yields and dollar fall after the print despite the beat", "Details are weak (e.g. for payrolls: downside "
                              "revisions or higher unemployment; for inflation: strength is energy/volatile only)",
                              "Risk-off or safe-haven demand dominates the rate channel"]})
    s.append({"name": "Downside surprise (actual below consensus)", "usd": "USD down, yields down",
              "gold": "Bullish gold is the usual response",
              "invalidates": ["Weak data read as growth-scare lifting the dollar as a safe haven", "Gold already priced the miss in the prior session",
                              "Offsetting simultaneous release points the other way"]})
    s.append({"name": "In line with consensus", "usd": "Muted; reaction driven by details and other releases",
              "gold": "Neutral or conflicting; direction not determined by this release alone",
              "invalidates": ["A large revision or a component that disagrees with the headline"]})
    out["not_signal"] = ("Analytical decision support, not a trade signal. Stronger US data does not always mean lower gold: "
                         "policy expectations, yields, dollar, risk sentiment and simultaneous releases can dominate.")
    return out


def post_release(r, move5=None, move15=None):
    """Actual vs consensus vs HS, and whether the observed 5/15-minute reaction matched the stated scenario."""
    a, c, e, sg, u = r["actual"], r["consensus"], r["estimate"], r["sigma"], r["unit"]
    lines = []
    if a is None:
        return ["Actual not available yet."]
    if c is not None:
        s = a - c
        lines.append(f"Observed: actual {a:+.3g} vs consensus {c:+.3g} → surprise {s:+.3g} {u}" +
                     (f" ({s / sg:+.1f}× HS typical error)" if sg else ""))
    if e is not None:
        lines.append(f"Observed: actual vs HS estimate {e:+.3g} → HS error {a - e:+.3g} {u}")
        if c is not None:
            lines.append("HS was " + ("closer to the actual than consensus" if abs(a - e) < abs(a - c)
                                      else "further from the actual than consensus" if abs(a - e) > abs(a - c) else "equally close"))
            if (e - c) * (a - c) > 0:
                lines.append("HS called the direction of the surprise correctly (single observation).")
            elif (e - c) != 0 and (a - c) != 0:
                lines.append("HS had the direction of the surprise wrong (single observation).")
    if c is not None and a != c and move5 is not None:
        expect = -1 if a > c else 1  # usual relationship: hawkish surprise -> gold down
        verdict = "matched" if move5 * expect > 0 else "did not match"
        lines.append(f"Observed: gold moved {move5:+.2f} USD in 5 minutes"
                     + (f" and {move15:+.2f} in 15" if move15 is not None else "")
                     + f". Interpretation: this {verdict} the usual hawkish/dovish gold response for a "
                       f"{'upside' if a > c else 'downside'} surprise; one reaction proves nothing about the model.")
    elif c is not None and move5 is None:
        lines.append("Market reaction: not yet recorded by the existing price tracker.")
    return lines
