"""Python research port of the NEW logic added in IDX_Analyzer_DG2.1.pine
(the ML1.3 "PRO" diagnostics + the ML1.4/DG2.0 "Decision Gate 2.0" synthesis)
on top of the ALREADY-VALIDATED legacy engine in engine.py.

engine.py's base()/chart_patterns()/elite()/forecast() port the UNCHANGED
legacy V2.4/ML1.0-ML1.2 logic that DG2.1 explicitly does not touch (buySignal,
hardGates, autoStop, decision) — those numbers are already known from prior
research_v24 runs and are NOT re-derived here. This file only ports the parts
of DG2.1 that are actually NEW relative to ML1.2, so this backtest measures
what is actually new, not what was already validated.

Known, disclosed deviations from the real Pine script (same honesty standard
as engine_v6.py's docstring):
  - Market Index Regime (marketBias) needs IDX:COMPOSITE, which is not in
    idx_stocks_10y/. Approximated with an equal-weighted proxy composite built
    from the 91 tickers' own daily returns (see `build_market_proxy` in
    experiment_dg21.py) — disclosed as an approximation, not the real index.
  - Sector/Benchmark RS (rsBias) defaults OFF in the real script and is not
    tested here (would need a real sector benchmark, which isn't available).
  - Decision Gate 2.0's forecast-direction terms (direction/aiForecastDirection,
    worth 6+4=10 points out of ~100) are approximated from engine.forecast()'s
    TR/AI return outputs (shrunk by trShrink/aiShrink=0.25, thresholded at
    +-1%) rather than reproducing confidence-gating exactly — this term is
    explicitly LOW weight and already proven to have no directional edge
    (research_v24/results/summary.csv), so an approximation here doesn't
    change what's being tested (whether Decision Gate 2.0's conviction score
    as a WHOLE correlates with better outcomes).
  - The Chart Pattern trend-gate (useTrendGatePattern) is tested as a genuine
    variant of engine.chart_patterns() below (chart_patterns_gated), not an
    approximation — it reimplements the exact same pivot/matching loop with
    the trend-gate condition added, mirroring the Pine f_pivotTrendPro/
    trendOk logic bar-for-bar.
"""
import numpy as np
import pandas as pd
from engine import ema, rma, lag, roll, rsi, pivots, bars_since, ROOT, MINTICK, base, elite, forecast

# ---------------------------------------------------------------------------
# 1. Auto-MTF (mtfBias) — EMA20/EMA50 stack on the weekly-resampled close,
#    shifted by [1] to read the LAST CLOSED weekly bar only (matches the
#    lookahead-bug fix applied to the real Pine script in this same pass:
#    the original unshifted + lookahead_on combination was a real repainting
#    bug, fixed by shifting every field by [1], exactly as ported here).
# ---------------------------------------------------------------------------
def mtf_bias(a, d):
    periods = d.Date.dt.to_period('W-FRI')
    wc = d.groupby(periods, sort=True).Close.last()
    we20 = ema(wc.to_numpy(), 20)
    we50 = ema(wc.to_numpy(), 50)
    weekly = pd.DataFrame({'wc': wc.to_numpy(), 'we20': we20, 'we50': we50}, index=wc.index).shift(1)
    w = weekly.reindex(periods).reset_index(drop=True)
    htfClose, htfEma20, htfEma50 = w.wc.to_numpy(), w.we20.to_numpy(), w.we50.to_numpy()
    bias = np.where((htfClose > htfEma20) & (htfEma20 > htfEma50), 1,
            np.where((htfClose < htfEma20) & (htfEma20 < htfEma50), -1, 0))
    return bias


# ---------------------------------------------------------------------------
# 2. Market Index Regime (marketBias) — same EMA20/EMA50 stack test applied
#    to a market proxy series (see experiment_dg21.py's build_market_proxy).
#    No [1] shift needed: the proxy is a same-timeframe (daily), same-bar
#    series (like the real script's timeframe.period request), not a higher
#    timeframe one, so there's no repaint risk to guard against.
# ---------------------------------------------------------------------------
def market_bias(market_close, dates, own_dates):
    s = pd.Series(market_close, index=pd.to_datetime(dates))
    s = s.reindex(pd.to_datetime(own_dates)).ffill()
    mc = s.to_numpy()
    me20 = ema(mc, 20)
    me50 = ema(mc, 50)
    bias = np.where((mc > me20) & (me20 > me50), 1, np.where((mc < me20) & (me20 < me50), -1, 0))
    bias = np.where(np.isfinite(mc), bias, 0)
    return bias


# ---------------------------------------------------------------------------
# 3. Bandarmologi — CMF-style money flow phase + velocity + Anomali-Trap +
#    Akumulasi/Distribusi Bertahap. Faithful port of the Pine block (same
#    formulas, same default thresholds).
# ---------------------------------------------------------------------------
def bandarmologi(a, cmf_len=20, velo_len=10, anomaly_lookback=3, anomaly_move_mult=2.0,
                  anomaly_max_rvol=0.70, gradual_min_rvol=0.90, gradual_sedang_rvol=1.10,
                  gradual_besar_rvol=1.50):
    h, l, c, v, atr, rvol = [a[k] for k in ['h', 'l', 'c', 'v', 'atr', 'rvol']]
    rng = np.where((h - l) != 0, h - l, np.nan)
    mfm = np.where(np.isfinite(rng), ((c - l) - (h - c)) / rng, 0.0)
    mfv = mfm * v
    cmf = roll(mfv, cmf_len, 'sum') / roll(v, cmf_len, 'sum')
    velocity = (c / lag(c, velo_len) - 1) * 100
    volHot = rvol >= 1.5  # rvolMedium default
    veloHot = np.abs(velocity) >= (atr / c * 100) * 1.5
    phaseBias = np.where(cmf >= 0.05, 1, np.where(cmf <= -0.05, -1, 0))
    anomalyMove = np.abs(c - lag(c, anomaly_lookback))
    isAnomalyTrap = (anomalyMove > atr * anomaly_move_mult) & (rvol < anomaly_max_rvol)
    gradualAccum = (phaseBias == 1) & (rvol >= gradual_min_rvol) & ~veloHot
    gradualDist = (phaseBias == -1) & (rvol >= gradual_min_rvol) & ~veloHot
    gradualTier = np.select([rvol >= gradual_besar_rvol, rvol >= gradual_sedang_rvol], ['BESAR', 'SEDANG'], 'KECIL')
    return dict(cmf=cmf, velocity=velocity, volHot=volHot, veloHot=veloHot, phaseBias=phaseBias,
                isAnomalyTrap=isAnomalyTrap, gradualAccum=gradualAccum, gradualDist=gradualDist,
                gradualTier=gradualTier)


# ---------------------------------------------------------------------------
# 4. Chart Pattern Engine with optional prior-trend gate — a copy of
#    engine.chart_patterns() with the trend-gate condition added at the exact
#    point (only-just-appended left-shoulder / first-peak-or-bottom pivot)
#    the real Pine f_matchHigh/f_matchLow check it, so this measures the
#    gate's real effect, not an approximation of it.
# ---------------------------------------------------------------------------
def chart_patterns_gated(a, max_keep=12, use_gate=True, lookback=20, gate_pct=0.02):
    h, l, c, atr = [a[k] for k in ['h', 'l', 'c', 'atr']]
    length = len(c)
    ph = pivots(h, 3)
    pl = -pivots(-l, 3)
    zz = []  # (bar, price, dir, trendpct)
    pats = []
    events = []
    dropped = []
    bias = np.zeros(length, int)
    age = np.full(length, 999999, int)
    bullconf = np.zeros(length, bool)
    bearconf = bullconf.copy()

    def neck(p, t):
        return p['neck'] + p['slope'] * (t - p['nx'])

    def trend_pct(bar):
        ref = bar - lookback
        if ref < 0 or c[ref] == 0:
            return 0.0
        return (c[bar] - c[ref]) / c[ref]

    for t in range(length):
        tol = atr[t] * .5
        buf = atr[t] * .15
        for val, di in [(ph[t], 1), (pl[t], -1)]:
            if not np.isfinite(val):
                continue
            pivot_bar = t - 3
            tp = trend_pct(pivot_bar)
            appended = False
            if not zz or zz[-1][2] != di:
                zz.append((pivot_bar, val, di, tp))
                appended = True
            elif (di == 1 and val > zz[-1][1]) or (di == -1 and val < zz[-1][1]):
                zz[-1] = (pivot_bar, val, di, tp)
            zz = zz[-20:]
            if not appended:
                continue
            n = len(zz)
            candidates = []
            if n >= 5:
                lsT = zz[-5][3]
                ls, t1, hd, t2, rs = [z[1] for z in zz[-5:]]
                valid = (hd > ls + tol and hd > rs + tol and abs(ls - rs) <= 2 * tol and abs(t1 - t2) <= .35 * (hd - min(t1, t2))) if di == 1 else \
                        (hd < ls - tol and hd < rs - tol and abs(ls - rs) <= 2 * tol and abs(t1 - t2) <= .35 * (max(t1, t2) - hd))
                gateOk = (not use_gate) or (lsT >= gate_pct if di == 1 else lsT <= -gate_pct)
                candidates.append((5, valid and gateOk, 3 if di == 1 else 4))
            if n >= 3:
                firstT = zz[-3][3]
                x, y, z = [q[1] for q in zz[-3:]]
                valid = abs(x - z) <= tol and ((min(x, z) - y > 1.5 * tol) if di == 1 else (y - max(x, z) > 1.5 * tol))
                gateOk = (not use_gate) or (firstT >= gate_pct if di == 1 else firstT <= -gate_pct)
                candidates.append((3, valid and gateOk, 1 if di == 1 else 2))
            for sz, valid, kind in candidates:
                if not valid:
                    continue
                pts = zz[-sz:]
                start = pts[0][0]
                leg = max(3, int((pts[-1][0] - start) / (sz - 1)))
                nx = pts[1][0]
                nv = pts[1][1]
                sl = (pts[3][1] - nv) / (pts[3][0] - nx) if sz == 5 else 0.
                before = zz[n - sz - 1][0] if n >= sz + 1 else start
                lim = max(1, min(2 * leg, start - before))
                cross = -1
                for k in range(1, lim + 1):
                    b = start - k
                    lv = nv + sl * (b - nx)
                    if b >= 0 and ((di == 1 and l[b] <= lv) or (di == -1 and h[b] >= lv)):
                        cross = b
                        break
                sb = cross if cross >= 0 else before
                end = pts[-1][0] + leg
                if any(min(end, p['pts'][-1][0]) - max(sb, p['pts'][0][0]) > .15 * min(end - sb, p['pts'][-1][0] - p['pts'][0][0]) for p in pats):
                    continue
                points = [(z[0], z[1]) for z in pts]
                if cross >= 0:
                    points.insert(0, (cross, nv + sl * (cross - nx)))
                elif n >= sz + 1:
                    points.insert(0, (zz[n - sz - 1][0], zz[n - sz - 1][1]))
                ex = max(pts[0][1], pts[-1][1]) if di == 1 else min(pts[0][1], pts[-1][1])
                apex = pts[2][1] if sz == 5 else (pts[0][1] + pts[-1][1]) / 2
                pats.append(dict(kind=kind, dir=-di, neck=nv, slope=sl, nx=nx, apex=apex,
                    apexbar=pts[2][0] if sz == 5 else pts[-1][0], invalid=pts[-1][1] if sz == 5 else ex,
                    ext=apex if sz == 5 else ex, born=t, conf=None, result=0, pts=points))
                break
        keep = []
        for p in pats:
            gone = False
            if p['conf'] is None:
                invalid = (h[t] > p['ext'] + tol) if p['dir'] == -1 else (l[t] < p['ext'] - tol)
                confirm = (c[t] < neck(p, t) - buf) if p['dir'] == -1 else (c[t] > neck(p, t) + buf)
                if invalid:
                    gone = True
                elif confirm:
                    p['conf'] = t
                    height = (p['apex'] - neck(p, p['apexbar'])) if p['dir'] == -1 else (neck(p, p['apexbar']) - p['apex'])
                    p['target'] = neck(p, t) + p['dir'] * height
                    tb = t
                    for b in range(p['pts'][-1][0] + 1, t + 1):
                        if (p['dir'] == -1 and l[b] <= neck(p, b)) or (p['dir'] == 1 and h[b] >= neck(p, b)):
                            tb = b
                            break
                    p['pts'].append((tb, neck(p, tb)))
                    bullconf[t] |= p['dir'] == 1
                    bearconf[t] |= p['dir'] == -1
                elif t - p['born'] > 30:
                    gone = True
            elif p['result'] == 0:
                hit = (l[t] <= p['target']) if p['dir'] == -1 else (h[t] >= p['target'])
                fail = (c[t] >= p['invalid']) if p['dir'] == -1 else (c[t] <= p['invalid'])
                if hit:
                    p['result'] = 1
                elif fail:
                    p['result'] = 2
                elif t - p['conf'] > 60:
                    p['result'] = 3
                if p['result']:
                    events.append({k: v for k, v in p.items() if k != 'pts'} | {'end': t, 'samebar_hit_fail': bool(hit and fail)})
            if not gone:
                keep.append(p)
        pats = keep
        while sum(p['conf'] is not None for p in pats) > max_keep:
            j = next(j for j, p in enumerate(pats) if p['conf'] is not None)
            p = pats.pop(j)
            if p['result'] == 0:
                dropped.append(p)
        active = [p for p in pats if p['conf'] is not None and p['result'] == 0]
        if active:
            p = max(active, key=lambda x: x['conf'])
            bias[t] = p['dir']
            age[t] = t - p['conf']
    return events, dropped, bias, age


# ---------------------------------------------------------------------------
# 5. Independent Bull/Bear score (ML1.3, "SkorSinyal & Grading") — faithful
#    port of the ~20-term additive score. Deliberately excludes the AI
#    forecast term (same omission the real Pine script documents, for the
#    same reason: computed later in the file / low weight).
# ---------------------------------------------------------------------------
def bull_bear_score(a, mtfBias, marketBias, bandar, patBias, patAge,
                     adx_trend_th=20.0, adx_strong_th=28.0, rvol_medium=1.5, min_rvol=1.0):
    c, e20, atr, adx, rvol, rsi_ = [a[k] for k in ['c', 'e20', 'atr', 'adx', 'rvol', 'rsi']]
    diPlus, diMinus = a['diPlus'], a['diMinus']
    dailyBullStrong = a['strong']
    dailyBullEarly = a['early']
    dailyBear = a['bear']
    # Exact hh/hl/lh/ll (not engine.py's derived structureBull/Bear, which OR
    # in an extra "hl and close>ema20" clause the real bullScorePro term
    # doesn't have) — recomputed from the same pivot levels elite() exposes.
    lastPH, prevPH, lastPL, prevPL = a['lastPH'], a['prevPH'], a['lastPL'], a['prevPL']
    hh = lastPH > prevPH
    hl = lastPL > prevPL
    lh = lastPH < prevPH
    ll = lastPL < prevPL
    breakout, retest = a['breakout'], a['retest']
    bosBear = a['bosBear']
    bearTrap = a['bearTrap']
    candleBull, candleBear = a['candleBullScore'], a['candleBearScore']
    bullTrap, upthrust, gapTrap = a['bullTrap'], a['upthrust'], a['gapTrap']
    liquid = a['liquid']
    phaseBias, volHot, veloHot, isAnomalyTrap = bandar['phaseBias'], bandar['volHot'], bandar['veloHot'], bandar['isAnomalyTrap']

    n = len(c)
    bullForming = np.zeros(n, bool)  # not modeled (would need live forming-pattern tracking); negligible weight (4 pts, only when patBias==0)
    bearForming = np.zeros(n, bool)

    bull = np.full(n, 5.0)
    bear = np.full(n, 5.0)
    bull += np.where(dailyBullStrong, 20, np.where(dailyBullEarly, 12, 0))
    bear += np.where(dailyBear, 20, 0)
    bull += np.where(hh & hl, 15, np.where(~hh & hl, 7, 0))
    bear += np.where(lh & ll, 15, np.where(lh & ~ll, 7, 0))
    bull += np.where(mtfBias == 1, 10, 0)
    bear += np.where(mtfBias == -1, 10, 0)
    bull += np.where(marketBias == 1, 8, np.where(marketBias == 0, 3, 0))
    bear += np.where(marketBias == -1, 8, np.where(marketBias == 0, 3, 0))
    bull += 3  # useSectorRS assumed off (real default) -> not-useSectorRS branch always +3
    bear += 3
    bull += np.where(phaseBias == 1, np.where(volHot & veloHot, 8, 5), 0)
    bear += np.where(phaseBias == -1, np.where(volHot & veloHot, 8, 5), 0)
    bull += np.where(patBias == 1, 12, np.where(bullForming, 4, 0))
    bear += np.where(patBias == -1, 12, np.where(bearForming, 4, 0))
    bull += np.where(breakout, 12, np.where(retest, 10, 0))
    bear += np.where(bosBear, 12, np.where(bearTrap, 6, 0))
    bull += np.where(candleBull >= 2, 5, 0)
    bear += np.where(candleBear >= 2, 5, 0)
    bull += np.where((rsi_ >= 50) & (rsi_ <= 72), 5, np.where((rsi_ > 72) & (rsi_ < 80), 2, 0))
    bear += np.where((rsi_ <= 50) & (rsi_ >= 28), 5, np.where((rsi_ < 28) & (rsi_ > 20), 2, 0))
    bull += np.where((adx >= adx_trend_th) & (diPlus > diMinus), np.where(adx >= adx_strong_th, 7, 5), np.where((adx < adx_trend_th) & (c > e20), 2, 0))
    bear += np.where((adx >= adx_trend_th) & (diMinus > diPlus), np.where(adx >= adx_strong_th, 7, 5), np.where((adx < adx_trend_th) & (c < e20), 2, 0))
    bull += np.where(rvol >= rvol_medium, 3, np.where(rvol >= min_rvol, 1, 0))
    bear += np.where(rvol >= rvol_medium, 3, np.where(rvol >= min_rvol, 1, 0))
    bull += np.where(liquid, 2, 0)
    bear += np.where(liquid, 2, 0)
    bull -= np.where(bullTrap | upthrust | gapTrap, 18, np.where(rsi_ >= 82, 6, 0))
    bear -= np.where(bearTrap, 18, np.where(rsi_ <= 18, 6, 0))
    bull = np.clip(bull, 0, 100)
    bear = np.clip(bear, 0, 100)
    return bull, bear


# ---------------------------------------------------------------------------
# 6. Decision Gate 2.0 (ML1.4/DG2.0) — evidence-weighted synthesis. Includes
#    the (low-weight, approximated) forecast-direction terms — see module
#    docstring for the approximation used.
# ---------------------------------------------------------------------------
def decision_gate_2(a, bullScorePro, bearScorePro, patBias, isAnomalyTrap, direction, aiDirection):
    dailyBullStrong, dailyBullEarly, dailyBear = a['strong'], a['early'], a['bear']
    breakout = a['breakout']

    componentBull = np.where(dailyBullStrong, 30, np.where(breakout, 24, np.where(dailyBullEarly, 10, 0))).astype(float)
    componentBear = np.where(dailyBear, 24, 0.0)
    componentBull += np.where(patBias == 1, 26, 0)
    componentBear += np.where(patBias == -1, 26, 0)
    componentBull += (bullScorePro - 50) * 0.30
    componentBear += (bearScorePro - 50) * 0.30
    componentBull += np.where(direction == 1, 6, 0)
    componentBear += np.where(direction == -1, 6, 0)
    componentBull += np.where(aiDirection == 1, 4, 0)
    componentBear += np.where(aiDirection == -1, 4, 0)

    trapDampen = np.where(isAnomalyTrap, 0.5, 1.0)
    convictionRaw = (componentBull - componentBear) * trapDampen
    convictionScore = np.clip(50 + convictionRaw, 0, 100)
    convictionAbs = np.minimum(100.0, np.abs(convictionScore - 50) * 2)
    gate2Dir = np.where(convictionScore > 54, 1, np.where(convictionScore < 46, -1, 0))
    gate2Tier = np.select([convictionAbs >= 50, convictionAbs >= 30, convictionAbs >= 14], ['A+', 'A', 'B'], 'C')
    return convictionScore, convictionAbs, gate2Dir, gate2Tier
