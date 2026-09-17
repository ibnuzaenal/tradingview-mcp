"""EWS Detector (Bullish-Risk / Bearish-Risk / FOMO Index / Panic Index),
merged into the live indicator at DG3.0 and wired into the Conviction Score
as a contrarian vote at DG3.4 — ported directly against
pine/IDX_Analyzer_DG3.4.pine's "EWS DETECTOR" block (search `grpEws`), using
that script's own input defaults (all diagnostic lengths/windows below are
those defaults, not exposed as options here — same convention as every
other dg_engine module).

Reuses this repo's own rsi/macdHist (from engine.base()) and the
build_market_proxy market-index series already used for market_bias —
exactly like the live script reuses its own rsi14/macdHist/mCloseP instead
of a second computation (see the .pine header's own redundancy-audit note).
"""
import numpy as np
import pandas as pd

from engine import lag, roll  # noqa: E402
from engine_dg26 import percentrank  # noqa: E402

EWS_LEN_MA = 125
EWS_LEN_RV = 20
EWS_LEN_BB = 20
EWS_BB_MULT = 2.0
EWS_LEN_EXTREME = 60
EWS_LEN_PART = 20
EWS_PCTILE_WIN = 504
EWS_LEN_CURV = 20
EWS_LEN_SKEW = 20
EWS_LEN_CHASE = 60
EWS_LEN_CORR_SPIKE = 20
EWS_LEN_CORR_BASE = 120
EWS_BULL_THRESHOLD = 68.0
EWS_BEAR_THRESHOLD = 68.0
EWS_BULL_VOTE_WEIGHT = 8.0
EWS_BEAR_VOTE_WEIGHT = 8.0
EWS_PANIC_VOTE_WEIGHT = 6.0
EWS_PANIC_VOTE_THRESHOLD = 65.0


def _rank(src):
    return np.nan_to_num(percentrank(src, EWS_PCTILE_WIN), nan=50.0)


def _rank_inv(src):
    return 100.0 - _rank(src)


def _skew(src, n):
    """f_ewsSkew(src, n) — population skew over a trailing n-bar window:
    mean(cube deviation from the window's own SMA) / stdev^3."""
    s = pd.Series(src)
    m = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)

    def _sumcube(w):
        return np.sum((w - w.mean()) ** 3)

    sumcube = s.rolling(n).apply(_sumcube, raw=True)
    sd3 = sd ** 3
    out = np.where(sd3.to_numpy() != 0, (sumcube.to_numpy() / n) / sd3.to_numpy(), 0.0)
    return out


def ews_detector(a, mkt_close_aligned):
    """Returns dict of full-series arrays: ewsBullRisk, ewsBearRisk,
    ewsFomoIndex, ewsPanicIndex, ewsState (1/-1/0)."""
    c = a['c']
    v = a['v']
    rsi14 = a['rsi']
    macd_hist = a['macdHist']
    n = len(c)

    log_ret = np.zeros(n)
    with np.errstate(divide='ignore', invalid='ignore'):
        log_ret[1:] = np.log(c[1:] / c[:-1])
    log_ret[~np.isfinite(log_ret)] = 0.0

    trend_ma = roll(c, EWS_LEN_MA, 'mean')
    with np.errstate(divide='ignore', invalid='ignore'):
        mkt_mom = (c - trend_ma) / trend_ma
    rv = pd.Series(log_ret).rolling(EWS_LEN_RV).std(ddof=0).to_numpy() * np.sqrt(252)

    bb_basis = roll(c, EWS_LEN_BB, 'mean')
    bb_dev = EWS_BB_MULT * pd.Series(c).rolling(EWS_LEN_BB).std(ddof=0).to_numpy()
    bb_upper = bb_basis + bb_dev
    bb_lower = bb_basis - bb_dev
    with np.errstate(divide='ignore', invalid='ignore'):
        bb_pctb = (c - bb_lower) / (bb_upper - bb_lower)

    hh60 = roll(a['h'], EWS_LEN_EXTREME, 'max')
    with np.errstate(divide='ignore', invalid='ignore'):
        close_to_high = 1 - np.abs(c / hh60 - 1)

    up_day = (c > lag(c)).astype(float)
    participation = roll(up_day, EWS_LEN_PART, 'mean')
    breadth_high_expansion = participation - lag(participation, 10)
    breadth_collapse_rate = -(participation - lag(participation, 10))

    with np.errstate(divide='ignore', invalid='ignore'):
        mom5 = np.log(c) - np.log(lag(c, 5))
    price_accel = mom5 - lag(mom5, 5)

    vol_ma20 = roll(v, 20, 'mean')
    vol_sd20 = roll(v, 20, 'std')
    with np.errstate(divide='ignore', invalid='ignore'):
        vol_z = np.where(vol_sd20 != 0, (v - vol_ma20) / vol_sd20, 0.0)
    vol_accel = vol_z - lag(vol_z, 5)

    half_curv = max(2, EWS_LEN_CURV // 2)
    with np.errstate(divide='ignore', invalid='ignore'):
        mom_recent = np.log(c) - np.log(lag(c, half_curv))
        mom_prior = np.log(lag(c, half_curv)) - np.log(lag(c, EWS_LEN_CURV))
    parabolic_curv = mom_recent - mom_prior

    chasing_corr = pd.Series(lag(log_ret, 1)).rolling(EWS_LEN_CHASE).corr(pd.Series(vol_z)).to_numpy()

    down_vol_contribution = np.where(c < lag(c), vol_z, 0.0)
    down_vol_shock = roll(down_vol_contribution, 5, 'mean')

    ret_skew = _skew(log_ret, EWS_LEN_SKEW)

    with np.errstate(divide='ignore', invalid='ignore'):
        bench_ret = np.diff(np.log(mkt_close_aligned), prepend=np.nan)
    corr_to_bench = pd.Series(log_ret).rolling(EWS_LEN_CORR_SPIKE).corr(pd.Series(bench_ret)).to_numpy()
    corr_baseline = roll(corr_to_bench, EWS_LEN_CORR_BASE, 'mean')
    corr_spike = corr_to_bench - corr_baseline

    fomo_index = (_rank(price_accel) + _rank(vol_accel) + _rank(parabolic_curv)
                  + _rank(breadth_high_expansion) + _rank(np.nan_to_num(chasing_corr, nan=0.0))) / 5.0
    panic_index = (_rank(down_vol_shock) + _rank(np.nan_to_num(corr_spike, nan=0.0)) + _rank(-ret_skew)
                   + _rank(breadth_collapse_rate)) / 4.0
    bull_risk = (0.25 * _rank_inv(rsi14) + 0.20 * _rank_inv(mkt_mom) + 0.15 * _rank(rv)
                 + 0.15 * _rank_inv(macd_hist) + 0.15 * panic_index + 0.10 * _rank_inv(bb_pctb))
    bear_risk = (0.25 * _rank(mkt_mom) + 0.20 * _rank(participation) + 0.15 * _rank(close_to_high)
                 + 0.15 * _rank(rsi14) + 0.15 * fomo_index + 0.10 * _rank(np.nan_to_num(corr_spike, nan=0.0)))

    state = np.where((bull_risk >= EWS_BULL_THRESHOLD) & (bull_risk > bear_risk), 1,
             np.where((bear_risk >= EWS_BEAR_THRESHOLD) & (bear_risk > bull_risk), -1, 0))

    return dict(ewsBullRisk=bull_risk, ewsBearRisk=bear_risk, ewsFomoIndex=fomo_index,
                ewsPanicIndex=panic_index, ewsState=state)
