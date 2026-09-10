#!/usr/bin/env python3
"""
dg_snapshot.py — CSV-based, TradingView-Desktop-free daily snapshot generator
for the "IDX Analyzer DG" indicator family (DG2.7 and newer).

Replaces the old CDP/watchlist-read-idxdev.mjs pipeline entirely — this
script never opens TradingView Desktop and never reads from the separate
"Claude-Tradeview AI/IDX Project" folder on disk. It reads local OHLCV CSV
files (refreshed from Yahoo Finance ahead of time — see Step 1 in SKILL.md)
and reproduces, in Python, the SAME technical-analysis pipeline already
validated by the user's own research pass — `dg_engine/engine.py` /
`engine_dg21.py` / `engine_dg26.py` (a faithful, disclosed-deviations
research port of the live Pine script, copied into THIS repo so the whole
pipeline is self-contained and portable — no external folder needed), plus
the DG2.7-specific additions (Decision Gate 3.0 composite probability,
DG2.7's 3 Decision-Gate-2.0 weight tweaks) ported here directly against the
actual pine/IDX_Analyzer_DG2.7.pine source (also copied into this repo's
`pine/` directory — dashboard table + decision-engine sections cited inline
below by line number).

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

CACHE_DIR = SCRIPT_DIR / "data_cache"
SPARSE_MODEL_PATH = ENGINE_DIR / "dg27_sparse_model.json"

# ---------------------------------------------------------------------------
# Pine input defaults (pine/IDX_Analyzer_DG2.7.pine) needed by the decision engine
# and grade function, read directly from the script's `input.*()` lines.
# ---------------------------------------------------------------------------
PAT_BLOCK_BARS = 5          # input.int(5, "Blokir BUY N bar setelah pola bearish")
MIN_RR = 2.0                # input.float(2.0, "Minimum risk-reward")
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
# Decision Gate 2.0, DG2.7 weights (pine/IDX_Analyzer_DG2.7.pine header items 2-4):
# candle vote REMOVED (wrong sign per the walk-forward fit), breakout vote
# 24->16 (precedes big moves of either sign, not specifically bullish),
# rvol vote 4->6 (robustly positive across every L1 sparsity level tried).
# Structure otherwise identical to engine_dg26.decision_gate_2_6.
# ---------------------------------------------------------------------------
def decision_gate_2_7(a, bullScorePro, bearScorePro, patBias, isAnomalyTrap, direction, aiDirection,
                       af2Dir, wBull, wBear, mtfBias, marketBias, hurstDampen):
    dailyBullStrong, dailyBullEarly, dailyBear = a['strong'], a['early'], a['bear']
    breakout = a['breakout']
    rvol = a['rvol']

    componentBull = np.where(dailyBullStrong, 30, np.where(breakout, 16, np.where(dailyBullEarly, 10, 0))).astype(float)
    componentBear = np.where(dailyBear, 24, 0.0)
    componentBull += np.where(patBias == 1, 26, 0)
    componentBear += np.where(patBias == -1, 26, 0)
    componentBull += (bullScorePro - 50) * 0.15
    componentBear += (bearScorePro - 50) * 0.15
    componentBull += np.where(direction == 1, 6, 0)
    componentBear += np.where(direction == -1, 6, 0)
    componentBull += np.where(aiDirection == 1, 4, 0)
    componentBear += np.where(aiDirection == -1, 4, 0)
    componentBull += np.where(af2Dir == 1, 8, 0)
    componentBear += np.where(af2Dir == -1, 8, 0)
    componentBull += np.where(wBull, 10, 0)
    componentBear += np.where(wBear, 10, 0)
    componentBull += np.where(mtfBias == 1, 6, 0)
    componentBear += np.where(mtfBias == -1, 6, 0)
    componentBull += np.where(marketBias == 1, 6, 0)
    componentBear += np.where(marketBias == -1, 6, 0)
    # candle vote removed (DG2.7 item 2)
    rvol_medium = 1.5
    componentBull += np.where(rvol >= rvol_medium, 6, 0)
    componentBear += np.where(rvol >= rvol_medium, 6, 0)
    # VWAP vote: structurally 0/0 on this daily dataset (real script only
    # applies it intraday) — same disclosed omission as engine_dg26.

    trapDampen = np.where(isAnomalyTrap, 0.5, 1.0)
    hd = np.nan_to_num(hurstDampen, nan=1.0)
    convictionRaw = (componentBull - componentBear) * trapDampen * hd
    convictionScore = np.clip(50 + convictionRaw, 0, 100)
    convictionAbs = np.minimum(100.0, np.abs(convictionScore - 50) * 2)
    gate2Dir = np.where(convictionScore > 54, 1, np.where(convictionScore < 46, -1, 0))
    gate2Tier = np.select([convictionAbs >= 50, convictionAbs >= 30, convictionAbs >= 14], ['A+', 'A', 'B'], 'C')
    return convictionScore, convictionAbs, gate2Dir, gate2Tier


def grade_pro(score, rr, liquid, confirmed):
    """f_gradePro(score, rr, liq, confirmed) — pine/IDX_Analyzer_DG2.7.pine:1707."""
    if not confirmed:
        return "B — WAIT" if score >= SETUP_THRESHOLD_PRO else "D — NO TRADE"
    if score >= PRIME_THRESHOLD_PRO and rr >= 2.0 and liquid:
        return "A+ — PRIME"
    if score >= CONFIRM_THRESHOLD_PRO and rr >= MIN_RR:
        return "A — HIGH QUALITY"
    if score >= SETUP_THRESHOLD_PRO:
        return "B — TRADABLE"
    return "C — SPECULATIVE"


def decision_flat(liquid, buySignalReversal, rrOK, stopOK, setupCode, wBear, dailyBear,
                   bullTrap, upthrust, gapTrap, overextended, structureBull, reversalConfluence,
                   setupValid, preBuy, buySignal):
    """Flat-position branch of `decision` — pine/IDX_Analyzer_DG2.7.pine:1806.
    (The hasPosition branch, line 1797, is not ported: this project's
    indicator is always run with hasPosition=false, per standing convention.)
    """
    if not liquid:
        return "NO-T"
    if buySignalReversal and rrOK and stopOK:
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
    if not rrOK or not stopOK:
        return "NO-RR"
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
    trDirection = np.where(trRet > 0.01, 1, np.where(trRet < -0.01, -1, 0))
    aiKnnDirection = np.where(np.isfinite(aiRet) & (aiRet > 0.01), 1,
                               np.where(np.isfinite(aiRet) & (aiRet < -0.01), -1, 0))

    af2Dir, af2Prob = af2_direction(a, mktB)
    hurst, hregime, hdampen = hurst_exponent(a['c'])
    vol = ewma_vol(a['c'])
    volPctile = percentrank(vol, 252)

    conviction, convictionAbs, gate2Dir, gate2Tier = decision_gate_2_7(
        a, bull, bear, a['patBias'], bandar['isAnomalyTrap'], trDirection, aiKnnDirection,
        af2Dir, a['wBull'], a['wBear'], mtfB, mktB, hdampen,
    )

    # ---- last-bar scalars ----
    i = len(a['c']) - 1
    c = a['c'][i]
    atr = a['atr'][i]
    rvol = a['rvol'][i]
    dist_sma200_atr = (c - a['sma200'][i]) / atr if atr > 0 else 0.0
    atr_pct = atr / c * 100 if c else 0.0

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

    # ---- flat-branch decision string (pine/IDX_Analyzer_DG2.7.pine:1795-1806) ----
    liquid = bool(a['liquid'][i])
    rr = a['rrAvailable'][i]
    rrOK = bool(rr >= 2.0) if np.isfinite(rr) else False
    stopOK = bool((a['autoStop'][i] < c) and (a['stopPct'][i] <= 8))
    setupValid = bool(a['breakout'][i] or a['retest'][i] or a['pullback'][i] or a['reversal'][i])
    accCluster = bool(pd.Series(a['accClass'][:i + 1] > 0).tail(5).sum() >= 2 and not a['bosBear'][i])
    patternBearBlock = bool(a['patBias'][i] == -1 and a['patAge'][i] <= PAT_BLOCK_BARS)
    weeklyAllowed = not bool(a['wBear'][i])
    trendAllowed = bool(a['strong'][i] or a['early'][i])
    buySignalReversal = bool(
        (not a['buyCore'][i]) and a['reversalConfluence'][i] and rrOK and stopOK
        and not a['upthrust'][i] and not a['gapTrap'][i] and not patternBearBlock
    )
    preBuy = bool(
        (not a['buySignal'][i]) and liquid and weeklyAllowed and trendAllowed and a['structureBull'][i]
        and (a['accClass'][i] >= 2 or accCluster) and not a['overextended'][i] and not a['bullTrap'][i]
    )
    decision = decision_flat(
        liquid, buySignalReversal, rrOK, stopOK, a['setup'][i], bool(a['wBear'][i]), bool(a['bear'][i]),
        bool(a['bullTrap'][i]), bool(a['upthrust'][i]), bool(a['gapTrap'][i]), bool(a['overextended'][i]),
        bool(a['structureBull'][i]), bool(a['reversalConfluence'][i]), setupValid, preBuy, bool(a['buySignal'][i]),
    )

    grade_active_score = max(bull[i], bear[i])
    grade = grade_pro(grade_active_score, rr if np.isfinite(rr) else 0.0, liquid, bool(a['buySignal'][i]))

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
        gate2_dir={1: "BUY BIAS", -1: "SELL/AVOID BIAS", 0: "NETRAL"}[int(gate2Dir[i])],
        gate2_tier=str(gate2Tier[i]),
        gate2_conviction=round(float(convictionAbs[i]), 1),
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
