#!/usr/bin/env python3
"""
dg_snapshot.py — CSV-based, TradingView-Desktop-free daily snapshot generator
for the "IDX Analyzer DG" indicator family (DG3.4 and newer).

Replaces the old CDP/watchlist-read-idxdev.mjs pipeline entirely — this
script never opens TradingView Desktop and never reads from the separate
"Claude-Tradeview AI/IDX Project" folder on disk. It reads local OHLCV CSV
files (refreshed from Yahoo Finance ahead of time — see Step 1 in SKILL.md)
and reproduces, in Python, the SAME technical-analysis pipeline already
validated by the user's own research pass — `dg_engine/engine.py` /
`engine_dg21.py` / `engine_dg26.py` / `engine_dg34.py` (a faithful,
disclosed-deviations research port of the live Pine script, copied into
THIS repo so the whole pipeline is self-contained and portable — no
external folder needed), plus the DG3.4-specific additions (Decision Gate
3.0 composite probability, the EWS Detector's four composite scores now
voting on the Conviction Score, and every Decision-Gate-2.0 weight/gate
change between DG2.7 and DG3.4) ported here directly against the actual
pine/IDX_Analyzer_DG3.4.pine source (also copied into this repo's `pine/`
directory, alongside every intermediate DG2.8-DG3.3 version for the revert
trail — dashboard table + decision-engine sections cited inline below by
line number).

Re-sync trail (DG2.7 -> DG3.4, what actually changed formula-wise; every
DG2.8/DG3.2/DG3.3 round in between was visual/notation-only, no scoring
change):
  - DG2.7's OWN conviction-score vote block already replaced the 3 separate
    hand-tuned forecast-direction votes (TR/kNN-AI/AF2, weights 6/4/8) with
    ONE weight-20 vote from dg3Prob (pine/IDX_Analyzer_DG2.7.pine:2229) —
    this file's prior version still had the pre-DG2.7 3-vote formula, a real
    mismatch against the very DG2.7 source it cited; fixed here as part of
    this sync, not a DG3.4-specific change.
  - DG3.1 removed the "01. Mode & Position" and "02. Filter" (liquid/rrOK/
    stopOK/minRR/maxStopPct) groups entirely — hardGates/buyScore/
    buySignalReversal/decision/f_gradePro no longer reference them
    (research_v24/dg31_filter_removal_research.py: removing the filter did
    NOT degrade held-out signal quality). buyScore's rrOK term (max 1pt)
    dropped, buySignalCore threshold rescaled 8->7 (same slack). `decision`
    reordered to check buySignalReversal FIRST (fixes 47/160 walk-forward
    cases where the panel disagreed with which signal fired).
  - DG3.0 merged the standalone "EWS Detector" script's four composite
    scores (Bullish-Risk/Bearish-Risk/FOMO/Panic) in as diagnostic-only
    dashboard rows; DG3.4 reversed that and wired Bull-Risk/Bear-Risk/Panic
    into the Conviction Score as a low-weight, threshold-gated contrarian
    vote (`dg_engine/engine_dg34.py`) — FOMO stays diagnostic-only (no
    measured standalone edge).
  - DG3.4 itself: patBiasDir vote cut 26->14, dailyBullStrong vote cut
    30->20, and a real RVOL vote bug fix — it used to add +6 to BOTH
    componentBullPro AND componentBearPro under the identical condition
    (net-zero, silently inert since introduced), now paired with candle
    direction (close vs open) so it actually confirms whichever side the
    volume showed up on.

If a newer "IDX_Analyzer_DG*.pine" version ships, re-sync manually: copy the
new .pine into `pine/`, re-check whether any formula this file replicates
changed, and update `dg_engine/*.py` / this file accordingly — there is no
automatic sync, by design (never assume a newer version kept an identical
formula).

This does NOT re-derive or re-tune the indicator's own analysis — every
number here is either read straight from an already-validated engine
function, or a mechanical reproduction of a Pine ternary/formula that was
read directly from the .pine file (cited inline where non-obvious).

Known, disclosed scope limits (see SKILL.md for the full explanation):
  - Market Index Regime uses an equal-weighted proxy of the cached tickers'
    own returns, not the real IDX:COMPOSITE (same approximation research_v24
    already uses and discloses).
  - Sector RS defaults off in the real script too, so this is not a gap.
  - Gate 2.0's Risk Overlay (Kelly 1/4, PSR, MinTRL, Circuit Breaker, CUSUM)
    and the Lead Quality Tracker both need a maintained rolling trade/signal
    history across many days — not reproducible from a single stateless CSV
    snapshot, so they are omitted here (fields left null) rather than faked.
  - Chart-pattern win-rate chips (Double Top/Bottom/H&S/Inverse H&S %) are
    omitted for the same reason as above scope discipline: mapping this
    script's `chart_patterns_gated` event/kind codes to the four named
    pattern types needs more verification than time allowed; shipping wrong
    numbers would be worse than shipping none.
  - dg3LiveAccuracy (DG3.4's new live rolling hit-rate tracker for
    dg3Direction) and the standalone "BUY : harga"/"SELL : harga" final
    recommendation callout are NOT ported — both are chart-side, real-time
    constructs (a self-updating tracker across days, a specific-bar entry
    callout) with no clean equivalent in a single stateless CSV snapshot.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
ENGINE_DIR = SCRIPT_DIR / "dg_engine"
sys.path.insert(0, str(ENGINE_DIR))

from engine import base, elite, forecast, lag  # noqa: E402
from engine_dg21 import mtf_bias, market_bias, bandarmologi, bull_bear_score, chart_patterns_gated  # noqa: E402
from engine_dg26 import af2_direction, ewma_vol, percentrank, hurst_exponent  # noqa: E402
from engine_dg34 import (  # noqa: E402
    ews_detector, EWS_BULL_VOTE_WEIGHT, EWS_BEAR_VOTE_WEIGHT,
    EWS_PANIC_VOTE_WEIGHT, EWS_PANIC_VOTE_THRESHOLD,
)

CACHE_DIR = SCRIPT_DIR / "data_cache"
SPARSE_MODEL_PATH = ENGINE_DIR / "dg27_sparse_model.json"

# ---------------------------------------------------------------------------
# Pine input defaults (pine/IDX_Analyzer_DG3.4.pine) needed by the decision engine
# and grade function, read directly from the script's `input.*()` lines.
# (minRR was dropped along with "02. Filter" at DG3.1 — no longer read here.)
# ---------------------------------------------------------------------------
PAT_BLOCK_BARS = 5          # input.int(5, "Blokir BUY N bar setelah pola bearish")
SETUP_THRESHOLD_PRO = 60.0  # input.float(60, "Setup Score")
CONFIRM_THRESHOLD_PRO = 72.0  # input.float(72, "Confirmed Score")
PRIME_THRESHOLD_PRO = 82.0  # input.float(82, "A+ Prime Score")


def load_from_cache(ticker):
    p = CACHE_DIR / f"{ticker}.csv"
    d = pd.read_csv(p, parse_dates=["Date"])
    d = d.dropna(subset=["Open", "High", "Low", "Close"]).reset_index(drop=True)
    return d


def build_market_proxy(tickers):
    """Equal-weighted proxy for IDX:COMPOSITE built from the cached universe's
    own daily returns — same disclosed-approximation method as
    research_v24/experiment_dg21.py's build_market_proxy(), just pointed at
    our refreshed data_cache instead of the static idx_stocks_10y bundle."""
    frames = []
    for sym in tickers:
        if sym == "COMPOSITE":
            continue
        d = load_from_cache(sym)
        s = pd.Series(np.log(d.Close.to_numpy() / np.roll(d.Close.to_numpy(), 1)), index=d.Date)
        s.iloc[0] = np.nan
        frames.append(s)
    panel = pd.concat(frames, axis=1)
    mean_ret = panel.mean(axis=1, skipna=True)
    mean_ret.iloc[0] = 0.0
    level = 1000.0 * np.exp(mean_ret.fillna(0).cumsum())
    return level.index.to_numpy(), level.to_numpy()


# ---------------------------------------------------------------------------
# Decision Gate 2.0/3.0, DG3.4 weights (pine/IDX_Analyzer_DG3.4.pine:2190-2316).
# Operates on LAST-BAR SCALARS, not full arrays — every caller only ever
# reads this function's result at the final bar (same as the live indicator,
# which evaluates one bar realtime), so there is no need to vectorize the
# whole history just to throw away everything but the last value.
# ---------------------------------------------------------------------------
def decision_gate_3_4(dailyBullStrong, dailyBullEarly, dailyBear, breakout, rvol, candleUp,
                       bullScorePro, bearScorePro, patBias, isAnomalyTrap, dg3Prob,
                       wBull, wBear, mtfBias, marketBias, hurstDampen, ewsState, ewsPanicIndex):
    componentBull = 20.0 if dailyBullStrong else (16.0 if breakout else (10.0 if dailyBullEarly else 0.0))
    componentBear = 24.0 if dailyBear else 0.0
    componentBull += 14.0 if patBias == 1 else 0.0
    componentBear += 14.0 if patBias == -1 else 0.0
    componentBull += (bullScorePro - 50) * 0.15
    componentBear += (bearScorePro - 50) * 0.15
    # the three separate low-weight forecast-direction votes DG2.6 had (TR
    # direction 6, kNN AI Forecast 4, AI Forecast 2.0 8) are replaced by ONE
    # vote from dg3Prob, weight 20, scaled continuously — already true as of
    # DG2.7 itself (pine/IDX_Analyzer_DG2.7.pine:2229), not a DG3.4 change.
    componentBull += (dg3Prob - 0.5) * 2 * 20
    componentBear += (0.5 - dg3Prob) * 2 * 20
    componentBull += 10.0 if wBull else 0.0
    componentBear += 10.0 if wBear else 0.0
    componentBull += 6.0 if mtfBias == 1 else 0.0
    componentBear += 6.0 if mtfBias == -1 else 0.0
    componentBull += 6.0 if marketBias == 1 else 0.0
    componentBear += 6.0 if marketBias == -1 else 0.0
    # RVOL vote FIXED (DG3.4): used to add +6 to BOTH sides under the
    # identical condition (net-zero, silently inert since introduced) — now
    # paired with candle direction so it confirms whichever side volume
    # showed up on.
    rvol_medium = 1.5
    componentBull += 6.0 if (rvol >= rvol_medium and candleUp) else 0.0
    componentBear += 6.0 if (rvol >= rvol_medium and not candleUp) else 0.0
    # VWAP vote: structurally 0 on this daily dataset (real script only
    # applies it intraday) — same disclosed omission as before.
    # EWS Bull-Risk/Bear-Risk/Panic votes (DG3.4 UPDATE, reverses DG3.0's
    # diagnostic-only decision) — gated on the discrete threshold-gated
    # ewsState so it stays silent during ordinary trends.
    componentBull += EWS_BULL_VOTE_WEIGHT if ewsState == 1 else 0.0
    componentBear += EWS_BEAR_VOTE_WEIGHT if ewsState == -1 else 0.0
    componentBear += EWS_PANIC_VOTE_WEIGHT if ewsPanicIndex >= EWS_PANIC_VOTE_THRESHOLD else 0.0

    trapDampen = 0.5 if isAnomalyTrap else 1.0
    hd = hurstDampen if np.isfinite(hurstDampen) else 1.0
    convictionRaw = (componentBull - componentBear) * trapDampen * hd
    convictionScore = min(100.0, max(0.0, 50.0 + convictionRaw))
    convictionAbs = min(100.0, abs(convictionScore - 50.0) * 2)
    gate2Dir = 1 if convictionScore > 54 else (-1 if convictionScore < 46 else 0)
    gate2Tier = ('A+' if convictionAbs >= 50 else
                 'A' if convictionAbs >= 30 else
                 'B' if convictionAbs >= 14 else 'C')
    return convictionScore, convictionAbs, gate2Dir, gate2Tier


def grade_pro(score, confirmed):
    """f_gradePro(score, confirmed) — pine/IDX_Analyzer_DG3.4.pine (rr/liquid
    params dropped at DG3.1 along with the "02. Filter" group; grade is now
    driven purely by score vs the three PRO thresholds)."""
    if not confirmed:
        return "B — WAIT" if score >= SETUP_THRESHOLD_PRO else "D — NO TRADE"
    if score >= PRIME_THRESHOLD_PRO:
        return "A+ — PRIME"
    if score >= CONFIRM_THRESHOLD_PRO:
        return "A — HIGH QUALITY"
    if score >= SETUP_THRESHOLD_PRO:
        return "B — TRADABLE"
    return "C — SPECULATIVE"


def decision_flat_dg34(buySignalReversal, setupCode, wBear, dailyBear,
                        bullTrap, upthrust, gapTrap, overextended, structureBull, reversalConfluence,
                        setupValid, preBuy, buySignal):
    """Flat-position branch of `decision` — pine/IDX_Analyzer_DG3.4.pine:1804.
    DG3.1 removed liquid/rrOK/stopOK from this ternary entirely (no more
    "NO-T"/"NO-RR" codes) and reordered buySignalReversal to check FIRST
    (fixes 47/160 walk-forward cases where the panel disagreed with which
    signal actually fired). The hasPosition branch is still not ported —
    this project's indicator is always run flat, per standing convention.
    """
    if buySignalReversal:
        return "BUY-" + setupCode
    if wBear or dailyBear:
        return "WAIT-T"
    if bullTrap or upthrust or gapTrap:
        return "NO-BUY"
    if overextended:
        return "EXT"
    if not structureBull and not reversalConfluence:
        return "WAIT-S"
    if not setupValid and not reversalConfluence:
        return "PRE-BUY" if preBuy else "WAIT-Z"
    if buySignal:
        return "BUY-" + setupCode
    return "WAIT-V"


def candle_name(a, i):
    """Best-effort candle-pattern label from engine.py's individually-exported
    pattern booleans (bullEngulf/bearEngulf/hammer/shootingStar/morningStar/
    eveningStar) — engine.py folds piercing/dark-cloud into the candleBull/
    BearScore tally only, without exporting them by name, so those two don't
    get a distinct label here (they fall through to the score-based label)."""
    if a['morningStar'][i]:
        return "Morning Star"
    if a['eveningStar'][i]:
        return "Evening Star"
    if a['bullEngulf'][i]:
        return "Bullish Engulfing"
    if a['bearEngulf'][i]:
        return "Bearish Engulfing"
    if a['hammer'][i]:
        return "Hammer"
    if a['shootingStar'][i]:
        return "Shooting Star"
    if a['candleBullScore'][i] >= 2:
        return "Bullish"
    if a['candleBearScore'][i] >= 2:
        return "Bearish"
    return "Netral"


def num_or_none(x):
    x = float(x)
    return None if not np.isfinite(x) else round(x, 4)


def smart_signals(a, bandar, i, mkt_close_aligned):
    """Three volume/price-based read-at-a-glance signals for the summary
    table, averaged over the last 2 bars (today + previous, i.e. back to
    roughly H-2 given this pipeline's own data lag) rather than a single
    noisy bar:
      - Smart Money: the existing Bandarmologi CMF phase (AKUMULASI/
        DISTRIBUSI/NETRAL) — already computed per bar, just read over a
        2-bar window here instead of the single last bar.
      - Aggression: candle-close-location (CLV) direction confirmed by
        RVOL — a genuine OHLCV-derived buy/sell-pressure read.
      - Foreign Flow: NOT real KSEI/IDX broker-summary foreign net-buy/sell
        data (this pipeline only has Yahoo Finance OHLCV, no such feed) —
        a disclosed PROXY only, reading whether the stock's own 2-day
        price direction moves WITH or AGAINST the market-proxy index over
        the same window, RVOL-confirmed. Big-cap foreign flow often shows
        up as "moves with the index on volume"; a stock diverging from the
        index on volume is more likely domestic/idiosyncratic-driven. This
        is a heuristic, not measured foreign ownership data.
    """
    j = max(0, i - 1)
    phase = bandar['phaseBias'][j:i + 1]
    if np.mean(phase) > 0.15:
        smart_money, sm_cls = "AKUMULASI", "bull"
    elif np.mean(phase) < -0.15:
        smart_money, sm_cls = "DISTRIBUSI", "bear"
    else:
        smart_money, sm_cls = "NETRAL", "neutral"

    clv_avg = float(np.mean(a['clv'][j:i + 1]))
    rvol_avg = float(np.mean(a['rvol'][j:i + 1]))
    if clv_avg >= 0.65 and rvol_avg >= 1.2:
        aggression, ag_cls = "AGRESIF BELI", "bull"
    elif clv_avg <= 0.35 and rvol_avg >= 1.2:
        aggression, ag_cls = "AGRESIF JUAL", "bear"
    else:
        aggression, ag_cls = "NETRAL", "neutral"

    k = max(0, i - 2)
    own_chg = a['c'][i] - a['c'][k]
    mkt_chg = mkt_close_aligned[i] - mkt_close_aligned[k] if np.isfinite(mkt_close_aligned[i]) and np.isfinite(mkt_close_aligned[k]) else np.nan
    if rvol_avg >= 1.1 and np.isfinite(mkt_chg) and own_chg != 0 and mkt_chg != 0:
        if (own_chg > 0) == (mkt_chg > 0):
            foreign_flow, ff_cls = "SEJALAN PASAR", "bull"
        else:
            foreign_flow, ff_cls = "BERLAWANAN PASAR", "bear"
    else:
        foreign_flow, ff_cls = "NETRAL", "neutral"

    return dict(smart_money=smart_money, smart_money_cls=sm_cls,
                aggression=aggression, aggression_cls=ag_cls,
                foreign_flow_proxy=foreign_flow, foreign_flow_cls=ff_cls)


def ak3_ds3_signal(a, i, target2, autostop):
    """AK3/DS3 — the STRONGEST Bandarmologi accumulation/distribution tier
    (accClass/distClass == 3, elite()'s own top tier) seen in the last 3
    bars, whichever is more recent if both fired. Price projection is the
    same target2 (candidateEntry + candidateRisk*2.0, the "Target 2" already
    shown on every card) for an AK3 BUY read, or autoStop (the same stop
    level already shown) for a DS3 SELL/avoid read — no new price model,
    just labeling the existing ones for whichever signal fired.

    Also reports BOS+/BOS- (bosBull/bosBear, elite()'s own break-of-structure
    flags) on that SAME bar, if any — a same-day structural confirmation of
    the volume signal, not a separate lookback."""
    lo = max(0, i - 2)
    acc3 = [j for j in range(lo, i + 1) if a['accClass'][j] == 3]
    dist3 = [j for j in range(lo, i + 1) if a['distClass'][j] == 3]
    last_acc3 = acc3[-1] if acc3 else None
    last_dist3 = dist3[-1] if dist3 else None
    if last_acc3 is None and last_dist3 is None:
        return None

    def bos_at(j):
        if a['bosBull'][j]:
            return "BOS+"
        if a['bosBear'][j]:
            return "BOS-"
        return None

    if last_dist3 is None or (last_acc3 is not None and last_acc3 >= last_dist3):
        j = last_acc3
        return dict(type="AK3", days_ago=i - j, proj_label="BUY", proj_price=num_or_none(target2), bos=bos_at(j))
    j = last_dist3
    return dict(type="DS3", days_ago=i - j, proj_label="SELL", proj_price=num_or_none(autostop), bos=bos_at(j))


def ak3_ds3_candidate(a, i):
    """Disclosed PROXIMITY heuristic, not a forecast model: elite()'s own
    tier-3 threshold is (accScore>=8 and rvol>=2) vs tier-2's (accScore>=5
    and rvol>=1.5) — pine/... engine.py:215-216. A stock already at tier 2
    with a score already >=6 (closer to 8 than to 5) is flagged as a
    candidate that could reach AK3/DS3 the next bar on continued volume,
    nothing more than restating how close it already is to that threshold."""
    acc_score, dist_score = a['accScore'][i], a['distScore'][i]
    acc_class, dist_class = a['accClass'][i], a['distClass'][i]
    rvol = a['rvol'][i]
    if acc_class == 2 and acc_score >= 6:
        return dict(type="AK3", note=f"Skor akumulasi {acc_score:.0f} (ambang tier-3: 8), RVOL {rvol:.2f}x (ambang: 2x)")
    if dist_class == 2 and dist_score >= 6:
        return dict(type="DS3", note=f"Skor distribusi {dist_score:.0f} (ambang tier-3: 8), RVOL {rvol:.2f}x (ambang: 2x)")
    return None


def compute_symbol(ticker, proxy_dates, proxy_level, sparse_model):
    d = load_from_cache(ticker)
    a = base(d)
    events, dropped, patBias, patAge = chart_patterns_gated(a, use_gate=True)
    a['patBias'] = patBias
    a['patAge'] = patAge
    elite(a)  # mutates a in place with buySignal/setup/autoStop/buyScore/etc.

    mtfB = mtf_bias(a, d)
    mktB = market_bias(proxy_level, proxy_dates, a['dates'])
    bandar = bandarmologi(a)
    bull, bear = bull_bear_score(a, mtfB, mktB, bandar, a['patBias'], a['patAge'])

    fc = forecast(a, H=20, ai_options=dict(D=100, K=5))
    trRet = fc['TR'] * 0.25
    aiRet = fc['AI'] * 0.25
    # trDirection is still needed below as one of dg3Prob's own 9 fitted
    # features — the pre-DG2.7 kNN-AI-Forecast/TR/AF2 3-vote it used to also
    # feed into the Conviction Score was already replaced by a single
    # dg3Prob vote as of DG2.7 itself (see decision_gate_3_4).
    trDirection = np.where(trRet > 0.01, 1, np.where(trRet < -0.01, -1, 0))

    af2Dir, af2Prob = af2_direction(a, mktB)
    hurst, hregime, hdampen = hurst_exponent(a['c'])
    vol = ewma_vol(a['c'])
    volPctile = percentrank(vol, 252)

    # ---- EWS Detector — Bullish-Risk/Bearish-Risk/FOMO/Panic (merged at
    # DG3.0, wired into the Conviction Score at DG3.4) ----
    mkt_close_aligned = pd.Series(proxy_level, index=pd.to_datetime(proxy_dates)) \
        .reindex(pd.to_datetime(a['dates'])).ffill().to_numpy()
    ews = ews_detector(a, mkt_close_aligned)

    # ---- last-bar scalars ----
    i = len(a['c']) - 1
    c = a['c'][i]
    o = a['o'][i]
    atr = a['atr'][i]
    rvol = a['rvol'][i]
    dist_sma200_atr = (c - a['sma200'][i]) / atr if atr > 0 else 0.0
    atr_pct = atr / c * 100 if c else 0.0
    signals = smart_signals(a, bandar, i, mkt_close_aligned)
    ak3ds3 = ak3_ds3_signal(a, i, c + a['risk'][i] * 2.0, a['autoStop'][i])
    ak3ds3_cand = ak3_ds3_candidate(a, i)
    ews_bull_risk = float(ews['ewsBullRisk'][i])
    ews_bear_risk = float(ews['ewsBearRisk'][i])
    ews_fomo_index = float(ews['ewsFomoIndex'][i])
    ews_panic_index = float(ews['ewsPanicIndex'][i]) if np.isfinite(ews['ewsPanicIndex'][i]) else 0.0
    ews_state = int(ews['ewsState'][i])

    # ---- Decision Gate 3.0 composite probability (frozen sparse model) ----
    feat_values = dict(
        dailyBullStrong=float(a['strong'][i]), dailyBullEarly=float(a['early'][i]),
        dailyBear=float(a['bear'][i]), retest=float(a['retest'][i]), reversal=float(a['reversal'][i]),
        patBiasDir=float(a['patBias'][i]), bullScorePro=float(bull[i]), bearScorePro=float(bear[i]),
        trDirection=float(trDirection[i]), af2_prob=float(af2Prob[i]), wBull=float(a['wBull'][i]),
        mtfBias=float(mtfB[i]), marketBias=float(mktB[i]), candleBullScore=float(a['candleBullScore'][i]),
        rvol=float(rvol), hurstExp=float(hurst[i]) if np.isfinite(hurst[i]) else 0.5,
        ewmaVolPctile=float(volPctile[i]) if np.isfinite(volPctile[i]) else 50.0,
        atr_pct=float(atr_pct), dist_sma200_atr=float(dist_sma200_atr),
    )
    m = sparse_model
    z = np.array([(feat_values[f] - mean) / scale for f, mean, scale in zip(m['features'], m['mean'], m['scale'])])
    dg3_score = m['intercept'] + float(np.dot(z, m['coef']))
    dg3_prob = 1.0 / (1.0 + np.exp(-dg3_score))
    dg3_dir = "UP" if dg3_prob > 0.5 else "DOWN"
    dg3_conf_pct = round(abs(dg3_prob - 0.5) * 200, 1)

    # ---- Decision Gate 2.0 conviction score (pine/IDX_Analyzer_DG3.4.pine:2190-2335) ----
    conviction, convictionAbs, gate2Dir, gate2Tier = decision_gate_3_4(
        bool(a['strong'][i]), bool(a['early'][i]), bool(a['bear'][i]), bool(a['breakout'][i]), rvol, bool(c > o),
        float(bull[i]), float(bear[i]), int(a['patBias'][i]), bool(bandar['isAnomalyTrap'][i]), dg3_prob,
        bool(a['wBull'][i]), bool(a['wBear'][i]), int(mtfB[i]), int(mktB[i]),
        float(hdampen[i]) if np.isfinite(hdampen[i]) else 1.0, ews_state, ews_panic_index,
    )

    # ---- flat-branch decision string (pine/IDX_Analyzer_DG3.4.pine:1751-1804) ----
    setupValid = bool(a['breakout'][i] or a['retest'][i] or a['pullback'][i] or a['reversal'][i])
    accCluster = bool(pd.Series(a['accClass'][:i + 1] > 0).tail(5).sum() >= 2 and not a['bosBear'][i])
    patternBearBlock = bool(a['patBias'][i] == -1 and a['patAge'][i] <= PAT_BLOCK_BARS)
    weeklyAllowed = not bool(a['wBear'][i])
    trendAllowed = bool(a['strong'][i] or a['early'][i])
    # DG3.1 removed liquid/rrOK/stopOK from every gate below.
    buySignalReversal = bool(
        (not a['buyCore'][i]) and a['reversalConfluence'][i]
        and not a['upthrust'][i] and not a['gapTrap'][i] and not patternBearBlock
    )
    preBuy = bool(
        (not a['buySignal'][i]) and weeklyAllowed and trendAllowed and a['structureBull'][i]
        and (a['accClass'][i] >= 2 or accCluster) and not a['overextended'][i] and not a['bullTrap'][i]
    )
    decision = decision_flat_dg34(
        buySignalReversal, a['setup'][i], bool(a['wBear'][i]), bool(a['bear'][i]),
        bool(a['bullTrap'][i]), bool(a['upthrust'][i]), bool(a['gapTrap'][i]), bool(a['overextended'][i]),
        bool(a['structureBull'][i]), bool(a['reversalConfluence'][i]), setupValid, preBuy, bool(a['buySignal'][i]),
    )

    grade_active_score = max(bull[i], bear[i])
    grade = grade_pro(grade_active_score, bool(a['buySignal'][i]))

    price = c
    prev_close = a['c'][i - 1] if i > 0 else c
    change_pct = f"{(price - prev_close) / prev_close * 100:.2f}%" if prev_close else "0.00%"

    dashboard = dict(
        # legacy-schema fields (compatible with the old Dev V2.4 report template)
        weekly="BULL" if a['wBull'][i] else ("BEAR" if a['wBear'][i] else "NEUTRAL"),
        daily_trend="BULL STRONG" if a['strong'][i] else ("BULL EARLY" if a['early'][i] else ("BEAR" if a['bear'][i] else "NEUTRAL")),
        structure="HH-HL" if a['structureBull'][i] else ("LH-LL" if a['structureBear'][i] else "MIXED"),
        rvol=f"{rvol:.2f}x / {'BUY' if bandar['phaseBias'][i] > 0 else ('SELL' if bandar['phaseBias'][i] < 0 else 'NEUTRAL')}",
        accumulation={3: "AK3", 2: "AK2", 1: "AK1"}.get(int(a['accClass'][i]), "NONE"),
        distribution={3: "DS3", 2: "DS2", 1: "DS1"}.get(int(a['distClass'][i]), "NONE"),
        candle=candle_name(a, i),
        trap="TRAP" if bandar['isAnomalyTrap'][i] else "NONE",
        setup=str(a['setup'][i]),
        score=f"{int(a['buyScore'][i])}/10",
        lots=num_or_none(a['lots'][i]),
        support=num_or_none(a['lastPL'][i]) if 'lastPL' in a else None,
        resistance=num_or_none(a['lastPH'][i]) if 'lastPH' in a else None,
        entry=num_or_none(price),
        stop=num_or_none(a['autoStop'][i]),
        target2=num_or_none(price + a['risk'][i] * 2.0),  # candidateTP2 = candidateEntry + candidateRisk*2.0 (pine:1629)
        decision=decision,
        atr_pct=f"{atr_pct:.2f}%",
        tr_dir="UP" if trRet[i] > 0 else ("DOWN" if trRet[i] < 0 else "FLAT"),
        tr_conf=f"{fc['TR_CONF'][i] * 100:.0f}%",
        tr_fcst_pct=f"{trRet[i] * 100:.2f}%",
        tr_trend="POSITIVE" if trRet[i] > 0 else ("NEGATIVE" if trRet[i] < 0 else "FLAT"),
        tr_status="VALID" if fc['TR_CONF'][i] * 100 >= 55 else "LOW CONF",
        ai_dir="UP" if aiRet[i] > 0 else ("DOWN" if aiRet[i] < 0 else "FLAT"),
        ai_fcst_pct=f"{aiRet[i] * 100:.2f}%",
        ai_conf=f"{fc['AI_CONF'][i] * 100:.0f}%" if np.isfinite(fc['AI_CONF'][i]) else "N/A",
        ai_status="VALID" if np.isfinite(fc['AI_CONF'][i]) and fc['AI_CONF'][i] * 100 >= 55 else "LOW CONF",
        double_top=None, double_bottom=None, head_shoulders=None, inverse_hs=None,  # scope limit, see module docstring
        # DG-specific fields (new)
        bull_score=round(float(bull[i]), 1),
        bear_score=round(float(bear[i]), 1),
        grade=grade,
        af2_dir="UP" if af2Dir[i] == 1 else "DOWN",
        af2_prob=round(float(af2Prob[i]) * 100, 1),
        gate2_dir={1: "BUY BIAS", -1: "SELL/AVOID BIAS", 0: "NETRAL"}[int(gate2Dir)],
        gate2_tier=str(gate2Tier),
        gate2_conviction=round(float(convictionAbs), 1),
        dg3_dir=dg3_dir,
        dg3_prob=round(dg3_prob * 100, 1),
        dg3_conf=dg3_conf_pct,
        mtf_bias={1: "BULL", -1: "BEAR", 0: "NEUTRAL"}[int(mtfB[i])],
        market_bias={1: "BULL", -1: "BEAR", 0: "NEUTRAL"}[int(mktB[i])],
        bandar_flow="AKUMULASI" if bandar['phaseBias'][i] > 0 else ("DISTRIBUSI" if bandar['phaseBias'][i] < 0 else "NETRAL"),
        bandar_anomaly=bool(bandar['isAnomalyTrap'][i]),
        hurst_regime=str(hregime[i]),
        ewma_vol_pct=round(float(vol[i]), 2) if np.isfinite(vol[i]) else None,
        ewma_vol_pctile=round(float(volPctile[i]), 0) if np.isfinite(volPctile[i]) else None,
        # EWS Detector (DG3.0 merge, DG3.4 vote) — Bullish-Risk/Bearish-Risk
        # are contrarian/reversal-risk reads (oversold->bullish reversal odds,
        # overbought->bearish reversal odds), NOT trend confirmation; FOMO
        # stays diagnostic-only (no measured standalone edge), Panic votes
        # bearish above its own higher percentile threshold.
        ews_bull_risk=round(ews_bull_risk, 1),
        ews_bear_risk=round(ews_bear_risk, 1),
        ews_fomo_index=round(ews_fomo_index, 1),
        ews_panic_index=round(ews_panic_index, 1),
        ews_state={1: "BULL", -1: "BEAR", 0: "NETRAL"}[ews_state],
        # Akumulasi/Distribusi Bertahap over the last 3 trading days — the
        # same accScore/distScore/accClass/rvol Bandarmologi already computes
        # per bar (elite()), averaged/counted over a trailing 3-bar window
        # rather than read at the single last bar, for a "3 hari terakhir"
        # read on top of the existing single-bar accumulation/distribusi tag.
        acc_score_3d=round(float(np.mean(a['accScore'][max(0, i - 2):i + 1])), 2),
        dist_score_3d=round(float(np.mean(a['distScore'][max(0, i - 2):i + 1])), 2),
        acc_days_3d=int(np.sum(a['accClass'][max(0, i - 2):i + 1] >= 1)),
        dist_days_3d=int(np.sum(a['distClass'][max(0, i - 2):i + 1] >= 1)),
        rvol_avg_3d=round(float(np.mean(a['rvol'][max(0, i - 2):i + 1])), 2),
        chg_3d_pct=round(float((c - a['c'][max(0, i - 3)]) / a['c'][max(0, i - 3)] * 100), 2) if i >= 3 and a['c'][i - 3] else None,
        # Ringkasan Entry table signals (H-2 window) — see smart_signals() docstring
        **signals,
        # AK3/DS3 — strongest Bandarmologi tier in the last 3 bars, if any
        ak3_ds3_type=ak3ds3["type"] if ak3ds3 else None,
        ak3_ds3_days_ago=ak3ds3["days_ago"] if ak3ds3 else None,
        ak3_ds3_proj_label=ak3ds3["proj_label"] if ak3ds3 else None,
        ak3_ds3_proj_price=ak3ds3["proj_price"] if ak3ds3 else None,
        ak3_ds3_bos=ak3ds3["bos"] if ak3ds3 else None,
        ak3_ds3_candidate_type=ak3ds3_cand["type"] if ak3ds3_cand else None,
        ak3_ds3_candidate_note=ak3ds3_cand["note"] if ak3ds3_cand else None,
    )
    return dict(symbol=ticker, price=num_or_none(price), change_pct=change_pct, dashboard=dashboard)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dg-snapshot.jsonl"
    tickers = sorted(p.stem for p in CACHE_DIR.glob("*.csv"))
    print(f"[1] Building market proxy from {len(tickers)} cached tickers...", file=sys.stderr)
    proxy_dates, proxy_level = build_market_proxy(tickers)
    sparse_model = json.loads(SPARSE_MODEL_PATH.read_text())["model"]

    results = []
    failed = []
    for t in tickers:
        try:
            results.append(compute_symbol(t, proxy_dates, proxy_level, sparse_model))
            print(f"      -> {t}: {results[-1]['dashboard']['decision']} | score {results[-1]['dashboard']['score']} | DG3 {results[-1]['dashboard']['dg3_dir']} {results[-1]['dashboard']['dg3_prob']}%", file=sys.stderr)
        except Exception as e:
            failed.append((t, str(e)))
            print(f"      !! {t} FAILED: {e}", file=sys.stderr)

    with open(out_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"\nDone. {len(results)} ok, {len(failed)} failed.", file=sys.stderr)
    if failed:
        print("Failed tickers:", [t for t, _ in failed], file=sys.stderr)


if __name__ == "__main__":
    main()
