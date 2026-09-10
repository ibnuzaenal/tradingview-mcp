"""Python research port of the NEW logic added in IDX_Analyzer_DG2.6.pine
(Risk Overlay + the six new Decision Gate 2.0 component votes + candlestick/
RVOL/MTF/market/weekly explicit wiring), on top of the ALREADY-VALIDATED
engine.py/engine_dg21.py ports.

Only the parts that are actually NEW relative to DG2.1 are ported here —
bullScorePro/bearScorePro, chart patterns, bandarmologi, mtf/market bias all
come from engine.py/engine_dg21.py unchanged (this backtest measures what
DG2.6 actually changed, not what was already validated in prior rounds).

Known, disclosed deviations from the real Pine script:
  - VWAP vote is DAILY-only data here (idx_stocks_10y is daily OHLCV) and the
    real Pine script itself only applies the VWAP vote when
    timeframe.isintraday — so on this dataset the vote is always 0 on both
    sides, exactly matching what the real script would do on a Daily chart.
    Not implemented as a numeric feature; documented as structurally absent.
  - AI Forecast 2.0 (af2Direction) uses the EXACT frozen coefficients shipped
    in the Pine script (forecast2_simple_model.py's C=0.1 logistic fit) — not
    re-trained here, just evaluated, matching what the Pine script actually
    computes on the chart.
  - Hurst exponent uses the Lo-MacKinlay variance-ratio approximation exactly
    as ported to Pine (VR(q)=q^(2H-1)); EWMA volatility uses lambda=0.94
    exactly as in Pine. Both operate on log-returns from the (possibly
    unadjusted) daily Close series, matching the Pine chart's own `close`.
"""
import numpy as np
import pandas as pd
from engine import lag


# ---------------------------------------------------------------------------
# AI Forecast 2.0 (af2) — frozen logistic-regression coefficients, IDENTICAL
# to the constants hardcoded into IDX_Analyzer_DG2.x.pine (af2_z1..z6 block).
# ---------------------------------------------------------------------------
AF2_MEAN = dict(dist_sma200_atr=-4.207273, mom20=0.015898, atr_pct=3.794796,
                adx=26.184254, market_bias=0.171254, rsi=49.934481)
AF2_SCALE = dict(dist_sma200_atr=311.415115, mom20=0.179317, atr_pct=2.106997,
                  adx=12.210904, market_bias=0.806202, rsi=12.955396)
AF2_COEF = dict(dist_sma200_atr=-1.094955, mom20=-0.042770, atr_pct=0.097402,
                 adx=0.086939, market_bias=-0.066760, rsi=0.027976)
AF2_INTERCEPT = -0.045571


def af2_direction(a, marketBias):
    """Returns (af2Direction: +1/-1 array, af2_prob: array in [0,1])."""
    c, sma200, atr, adx, rsi_ = [a[k] for k in ['c', 'sma200', 'atr', 'adx', 'rsi']]
    dist_sma200_atr = np.divide(c - sma200, atr, out=np.zeros(len(c)), where=atr > 0)
    c20 = lag(c, 20)
    mom20 = np.divide(c, c20, out=np.ones(len(c)), where=(c20 != 0) & np.isfinite(c20)) - 1
    atr_pct = np.divide(atr, c, out=np.zeros(len(c)), where=c != 0) * 100
    feats = dict(dist_sma200_atr=dist_sma200_atr, mom20=mom20, atr_pct=atr_pct,
                 adx=adx, market_bias=marketBias.astype(float), rsi=rsi_)
    score = np.full(len(c), AF2_INTERCEPT)
    for k in AF2_MEAN:
        z = (feats[k] - AF2_MEAN[k]) / AF2_SCALE[k]
        score = score + AF2_COEF[k] * z
    prob = 1.0 / (1.0 + np.exp(-score))
    direction = np.where(prob > 0.5, 1, -1)
    return direction, prob


# ---------------------------------------------------------------------------
# EWMA volatility (lambda=0.94) — RiskMetrics-style GARCH(1,1) proxy, exact
# recursion ported from the Pine script (ewmaVar := lambda*ewmaVar + (1-lambda)*ret^2).
# ---------------------------------------------------------------------------
def ewma_vol(c, lam=0.94):
    logret = np.zeros(len(c))
    logret[1:] = np.where((c[1:] > 0) & (c[:-1] > 0), np.log(c[1:] / c[:-1]), 0.0)
    var = np.zeros(len(c))
    var[0] = logret[0] ** 2
    for t in range(1, len(c)):
        var[t] = lam * var[t - 1] + (1 - lam) * logret[t] ** 2
    vol_pct = np.sqrt(var) * 100
    return vol_pct


def percentrank(x, window):
    """Matches Pine's ta.percentrank: % of the trailing `window` bars
    (current bar included) that are <= the current value."""
    s = pd.Series(x)

    def _rank(w):
        cur = w[-1]
        if not np.isfinite(cur):
            return np.nan
        finite = w[np.isfinite(w)]
        if len(finite) == 0:
            return np.nan
        return (finite <= cur).sum() / len(finite) * 100

    return s.rolling(window, min_periods=1).apply(lambda w: _rank(w.to_numpy()), raw=False).to_numpy()


# ---------------------------------------------------------------------------
# Hurst exponent approximation — Lo-MacKinlay variance ratio, exact formula
# ported to Pine: VR(q) = Var(logret over q bars) / (q * Var(1-bar logret)),
# H = 0.5 + 0.5*ln(VR)/ln(q), both variances over the same rolling window.
# ---------------------------------------------------------------------------
def hurst_exponent(c, q=10, win=120):
    logret = np.zeros(len(c))
    logret[1:] = np.where((c[1:] > 0) & (c[:-1] > 0), np.log(c[1:] / c[:-1]), 0.0)
    logretq = np.zeros(len(c))
    logretq[q:] = np.where((c[q:] > 0) & (c[:-q] > 0), np.log(c[q:] / c[:-q]), 0.0)
    var1 = pd.Series(logret).rolling(win).var(ddof=0).to_numpy()
    varq = pd.Series(logretq).rolling(win).var(ddof=0).to_numpy()
    with np.errstate(divide='ignore', invalid='ignore'):
        vr = varq / (q * var1)
    hurst = np.where((var1 > 0) & (vr > 0), 0.5 + 0.5 * np.log(np.where(vr > 0, vr, np.nan)) / np.log(q), np.nan)
    regime = np.where(~np.isfinite(hurst), 'NA', np.where(hurst > 0.55, 'TRENDING', np.where(hurst < 0.45, 'MEAN-REV', 'RANDOM')))
    dampen = np.where(regime == 'MEAN-REV', 0.85, np.where(regime == 'RANDOM', 0.80, 1.0))
    return hurst, regime, dampen


# ---------------------------------------------------------------------------
# Decision Gate 2.0 — DG2.6 extended conviction score. Same structure as
# engine_dg21.decision_gate_2(), with the DG2.6 header's six new explicit
# votes added and the bullScorePro/bearScorePro blend weight cut 0.30->0.15
# (see IDX_Analyzer_DG2.6.pine header item #2 for the full rationale).
# ---------------------------------------------------------------------------
def decision_gate_2_6(a, bullScorePro, bearScorePro, patBias, isAnomalyTrap, direction, aiDirection,
                       af2Dir, wBull, wBear, mtfBias, marketBias, hurstDampen):
    dailyBullStrong, dailyBullEarly, dailyBear = a['strong'], a['early'], a['bear']
    breakout = a['breakout']
    candleBull, candleBear = a['candleBullScore'], a['candleBearScore']
    rvol = a['rvol']

    componentBull = np.where(dailyBullStrong, 30, np.where(breakout, 24, np.where(dailyBullEarly, 10, 0))).astype(float)
    componentBear = np.where(dailyBear, 24, 0.0)
    componentBull += np.where(patBias == 1, 26, 0)
    componentBear += np.where(patBias == -1, 26, 0)
    componentBull += (bullScorePro - 50) * 0.15
    componentBear += (bearScorePro - 50) * 0.15
    componentBull += np.where(direction == 1, 6, 0)
    componentBear += np.where(direction == -1, 6, 0)
    componentBull += np.where(aiDirection == 1, 4, 0)
    componentBear += np.where(aiDirection == -1, 4, 0)
    # --- DG2.6 new explicit votes ---
    componentBull += np.where(af2Dir == 1, 8, 0)
    componentBear += np.where(af2Dir == -1, 8, 0)
    componentBull += np.where(wBull, 10, 0)
    componentBear += np.where(wBear, 10, 0)
    componentBull += np.where(mtfBias == 1, 6, 0)
    componentBear += np.where(mtfBias == -1, 6, 0)
    componentBull += np.where(marketBias == 1, 6, 0)
    componentBear += np.where(marketBias == -1, 6, 0)
    componentBull += np.where(candleBull >= 2, 6, 0)
    componentBear += np.where(candleBear >= 2, 6, 0)
    rvol_medium = 1.5
    componentBull += np.where(rvol >= rvol_medium, 4, 0)
    componentBear += np.where(rvol >= rvol_medium, 4, 0)
    # VWAP vote: structurally 0/0 on this daily dataset (see module docstring).

    trapDampen = np.where(isAnomalyTrap, 0.5, 1.0)
    hd = np.nan_to_num(hurstDampen, nan=1.0)
    convictionRaw = (componentBull - componentBear) * trapDampen * hd
    convictionScore = np.clip(50 + convictionRaw, 0, 100)
    convictionAbs = np.minimum(100.0, np.abs(convictionScore - 50) * 2)
    gate2Dir = np.where(convictionScore > 54, 1, np.where(convictionScore < 46, -1, 0))
    gate2Tier = np.select([convictionAbs >= 50, convictionAbs >= 30, convictionAbs >= 14], ['A+', 'A', 'B'], 'C')
    return convictionScore, convictionAbs, gate2Dir, gate2Tier
