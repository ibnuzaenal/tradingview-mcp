#!/usr/bin/env node
/**
 * watchlist-annotate.mjs
 *
 * Runs the TradingView-side half of the "watchlist-daily-report" skill
 * (see skills/watchlist-daily-report/SKILL.md, Steps 0-6) as a single
 * deterministic script instead of ~300 individual tool calls.
 *
 * For every symbol in the TradingView watchlist (plus any extra symbols
 * passed via --extra), this script:
 *   1. Pulls OHLCV on a 2-hour timeframe by default (last 200 candles —
 *      deliberately NOT capped at a small fixed window: a genuine Double
 *      Top/Bottom or Head & Shoulders can span far more than 40 bars, and
 *      truncating the window risks cutting off the real extreme pivot) and
 *      computes support/resistance, the candlestick pattern on the most
 *      recent bar(s), a volume verdict, a 3-factor score, and — from the
 *      full swing structure of that window — checks for a genuine Double
 *      Top, Double Bottom, Head & Shoulders, or Inverse Head & Shoulders
 *      (the same chart patterns professional analysts reference, not just
 *      single-candle shapes). A candidate pattern is only accepted when
 *      the PRIOR TREND actually supports a reversal reading (e.g. a
 *      Double Top needs a real uptrend beforehand — it can't reverse a
 *      trend that never happened) and is flagged with a volume-confirmed
 *      / needs-confirmation read based on the classic divergence tell
 *      (e.g. a Double Bottom is stronger when the second low prints on
 *      higher volume than the first — accumulation, not exhaustion).
 *      Point --timeframe/--count at Daily/90 instead if you want the
 *      swing-trading-scale read used earlier in this project.
 *   2. Wipes that symbol's existing drawings and redraws fresh ones: a
 *      thin real-time ZIGZAG line through every swing high/low across the
 *      whole window (start to end, so an "M", "W", or head & shoulders
 *      shape is visually obvious, not just implied by a couple of
 *      labels), a Moving Average (MA) line, S/R lines, a buy/sell zone, a
 *      measured-move TARGET line, and text notations anchored EXACTLY on
 *      the signal candle (uniform small font, 60% opacity on every line
 *      and notation) — including an MA trend read, a swing-structure (SW)
 *      trend read (Higher-High/Higher-Low vs Lower-High/Lower-Low), and a
 *      volume-validity read (is this move backed by real volume, or does
 *      it look like a thin-volume anomaly/trader trap?). When a chart
 *      pattern is found, its neckline and labeled pivot points are
 *      highlighted on top of the zigzag. Every run reflects the CURRENT
 *      price/pattern/volume, never stale overlays from a previous run
 *      (see --no-clear to opt out) — pass --watch to keep re-running on a
 *      loop so the chart tracks live price movement instead of a single
 *      one-shot pass.
 *   3. Prints one JSON object per symbol to stdout (newline-delimited),
 *      so this can feed a separate report-building step.
 *
 * What this script deliberately does NOT do (still needs Claude/human
 * judgment, per SKILL.md): cross-checking a detected chart pattern
 * against the chart's own "Chart Patterns [FEELS]" indicator, writing
 * dated news catalysts, building the HTML report, exporting the PDF,
 * or emailing it. Those stay a separate step.
 *
 * Usage:
 *   node scripts/watchlist-annotate.mjs
 *   node scripts/watchlist-annotate.mjs --extra=INTP,SMGR,INCO,TINS,JPFA,GGRM,MIKA,IDX:SILO
 *   node scripts/watchlist-annotate.mjs --symbols=BBCA,BMRI --timeframe=D --count=90
 *   node scripts/watchlist-annotate.mjs --json > /tmp/watchlist-analysis.jsonl
 *
 * Flags:
 *   --symbols=A,B,C   Override the watchlist with an explicit symbol list.
 *   --extra=A,B,C     Additional symbols to analyze/annotate (e.g. the
 *                     big-cap screening set), appended after the watchlist.
 *   --timeframe=120   Chart timeframe to analyze (default "120" = 2h bars).
 *   --count=200       How many bars back to read (default 200 — not a hard
 *                     40-candle cap; a real chart pattern can span more).
 *   --wing=3          Pivot sensitivity for swing detection — a bar must
 *                     be the extreme within this many bars each side to
 *                     count as a swing point (default 3; try 4-5 on Daily/90).
 *   --separation=4    Minimum bars between a chart pattern's key pivots
 *                     (default 4; try 12+ on Daily/90 — genuine multi-
 *                     week structures need far more separation).
 *   --depth=0.015     Minimum neckline retracement (fraction of price) for
 *                     a chart pattern to count as genuine (default 0.015;
 *                     try 0.06 on Daily/90 — daily/weekly swings are
 *                     proportionally deeper than 30-min ones).
 *   --no-clear        Keep existing drawings and add on top instead of
 *                     replacing them (off by default — every run redraws
 *                     from scratch so the chart always matches the
 *                     latest price/pattern/volume, never a stale mix).
 *   --ma=20           Moving-average period for the MA trend read (default 20).
 *   --ma-lookback=80  How many of the most recent MA points to actually
 *                     draw as a line (default 80 — keeps draw-call count
 *                     sane; the trend read itself always uses full data).
 *   --watch           Keep re-fetching + redrawing on a loop instead of a
 *                     single pass, so the chart tracks live price
 *                     movement. Never exits on its own — stop with Ctrl+C.
 *                     Best pointed at a small --symbols set, not the full
 *                     watchlist, so each cycle stays close to real-time.
 *   --interval=60     Seconds between --watch cycles (default 60).
 *   --json            Suppress human-readable progress lines; only emit
 *                     the final JSON Lines summary (one object per line).
 *
 * Exit codes: 0 success, 1 a symbol failed, 2 could not connect to
 * TradingView at all.
 */

import { chart, data, drawing, watchlist, health } from '../src/core/index.js';

// ---------------------------------------------------------------------
// CLI args
// ---------------------------------------------------------------------

const args = Object.fromEntries(
  process.argv.slice(2)
    .filter((a) => a.startsWith('--'))
    .map((a) => {
      const [k, v] = a.slice(2).split('=');
      return [k, v === undefined ? true : v];
    }),
);
const jsonOnly = Boolean(args.json);
const clearFirst = !args['no-clear']; // always redraw fresh unless explicitly opted out
const timeframe = args.timeframe || '120'; // 2-hour bars by default — coarse enough that S/R and patterns track real structure, not noise
const barCount = args.count ? Number(args.count) : 200; // deliberately NOT capped at ~40 — a real chart pattern can span far more bars
const explicitSymbols = args.symbols ? args.symbols.split(',').map((s) => s.trim()).filter(Boolean) : null;
const extraSymbols = args.extra ? args.extra.split(',').map((s) => s.trim()).filter(Boolean) : [];
const watch = Boolean(args.watch); // keep re-fetching + redrawing on a loop instead of a single one-shot pass
const intervalSec = args.interval ? Number(args.interval) : 60; // seconds between --watch cycles
const maLength = args.ma ? Number(args.ma) : 20; // moving-average period
const maLookback = args['ma-lookback'] ? Number(args['ma-lookback']) : 80; // how many MA points to actually draw (keeps draw-call count sane)

function log(...msg) {
  if (!jsonOnly) console.error(...msg); // progress goes to stderr so stdout stays pure JSONL
}

// Every text notation AND every line drawn by this script uses the same
// alpha + font, per spec: uniform small font/size for all notations, 60%
// transparency across notations and lines alike, placed at the actual
// candle each one refers to.
const NOTE_ALPHA = 0.6;
const NOTE_FONTSIZE = 10;
const rgba = (rgbTriplet) => `rgba(${rgbTriplet},${NOTE_ALPHA})`;
const COLOR = {
  bullish: rgba('22,163,74'),
  bearish: rgba('220,38,38'),
  neutral: rgba('138,109,31'),
};
// Named line colors per spec: support = red, resistance = green, target = blue.
const LINE_COLOR = {
  support: rgba('220,38,38'),
  resistance: rgba('22,163,74'),
  target: rgba('37,99,235'),
  patternBullish: rgba('168,85,247'),
  patternBearish: rgba('249,115,22'),
  ma: rgba('217,119,6'),
};

// ---------------------------------------------------------------------
// Step 0 — connect
// ---------------------------------------------------------------------

async function ensureConnected() {
  try {
    await health.healthCheck();
    log('[0] TradingView already connected.');
    return;
  } catch {
    log('[0] Not connected — launching TradingView with CDP...');
  }
  await health.launch({ kill_existing: true });
  for (let i = 0; i < 15; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      await health.healthCheck();
      log('[0] Connected.');
      return;
    } catch {
      /* keep retrying */
    }
  }
  throw new Error('Could not connect to TradingView after launch (waited 30s).');
}

// ---------------------------------------------------------------------
// Candlestick pattern classification (single/double-candle, professional
// reference set: Marubozu, Engulfing, Hammer/Hanging Man, Shooting Star,
// Doji, Spinning Top, Piercing Line, Dark Cloud Cover)
// ---------------------------------------------------------------------

/** @param {{open:number,high:number,low:number,close:number}} b */
function shapeOf(b) {
  const range = b.high - b.low || 1e-9;
  const body = Math.abs(b.close - b.open);
  const bodyPct = body / range;
  const upperWick = b.high - Math.max(b.open, b.close);
  const lowerWick = Math.min(b.open, b.close) - b.low;
  const bullish = b.close > b.open;
  return { range, body, bodyPct, upperWick, lowerWick, bullish };
}

/**
 * Classify the most recent 1-2 bars into a named candlestick pattern.
 * @param {Array<{open:number,high:number,low:number,close:number}>} bars last 5 daily bars, oldest first
 */
function classifyPattern(bars) {
  const last = bars[bars.length - 1];
  const prev = bars[bars.length - 2];
  const s = shapeOf(last);

  // Two-candle patterns take priority when clearly present.
  if (prev) {
    const ps = shapeOf(prev);
    const engulfsUp = !ps.bullish && s.bullish && last.open <= prev.close && last.close >= prev.open;
    const engulfsDown = ps.bullish && !s.bullish && last.open >= prev.close && last.close <= prev.open;
    if (engulfsUp) return { name: 'Bullish Engulfing', bias: 'bullish' };
    if (engulfsDown) return { name: 'Bearish Engulfing', bias: 'bearish' };

    const midPrev = (prev.open + prev.close) / 2;
    if (!ps.bullish && s.bullish && last.open < prev.low && last.close > midPrev && last.close < prev.open) {
      return { name: 'Piercing Line', bias: 'bullish' };
    }
    if (ps.bullish && !s.bullish && last.open > prev.high && last.close < midPrev && last.close > prev.open) {
      return { name: 'Dark Cloud Cover', bias: 'bearish' };
    }
  }

  // Single-candle patterns.
  if (s.bodyPct < 0.08) return { name: 'Doji', bias: 'neutral' };
  if (s.bodyPct > 0.7 && s.upperWick / s.range < 0.12 && s.lowerWick / s.range < 0.12) {
    return { name: s.bullish ? 'Bullish Marubozu' : 'Bearish Marubozu', bias: s.bullish ? 'bullish' : 'bearish' };
  }
  if (s.lowerWick > 2 * s.body && s.upperWick < s.body && s.bodyPct < 0.4) {
    // Direction depends on the preceding trend; without it we call it a Hammer (bullish reading)
    // and flag Hanging Man as the alternative reading for the human/report step to disambiguate.
    return { name: 'Hammer / Hanging Man (cek arah tren sebelumnya)', bias: s.bullish ? 'bullish' : 'mixed' };
  }
  if (s.upperWick > 2 * s.body && s.lowerWick < s.body && s.bodyPct < 0.4) {
    return { name: 'Shooting Star / Inverted Hammer (cek arah tren sebelumnya)', bias: s.bullish ? 'mixed' : 'bearish' };
  }
  if (s.bodyPct >= 0.08 && s.bodyPct < 0.35) return { name: 'Spinning Top', bias: 'neutral' };
  return { name: s.bullish ? 'Bullish' : 'Bearish', bias: s.bullish ? 'bullish' : 'bearish' };
}

// ---------------------------------------------------------------------
// Chart-pattern detection: Double Top, Double Bottom, Head & Shoulders,
// Inverse Head & Shoulders — from swing pivots over the full bar window.
// This is the multi-bar structure professional technical analysts read,
// as opposed to the single/double-candle shapes above.
// ---------------------------------------------------------------------

/** Local pivot highs/lows: a bar is a swing point if it's the extreme within `wing` bars each side. */
function findSwingPoints(bars, wing = 3) {
  const highs = [];
  const lows = [];
  for (let i = wing; i < bars.length - wing; i++) {
    const windowBars = bars.slice(i - wing, i + wing + 1);
    if (bars[i].high === Math.max(...windowBars.map((b) => b.high))) highs.push({ i, time: bars[i].time, price: bars[i].high, volume: bars[i].volume });
    if (bars[i].low === Math.min(...windowBars.map((b) => b.low))) lows.push({ i, time: bars[i].time, price: bars[i].low, volume: bars[i].volume });
  }
  return { highs, lows };
}

/** % price change from the start of the window up to (and including) bar index `uptoIndex` — used to confirm a chart pattern actually has a trend to reverse. */
function priorTrendPct(bars, uptoIndex) {
  const slice = bars.slice(0, uptoIndex + 1);
  if (slice.length < 2) return 0;
  return (slice[slice.length - 1].close - slice[0].close) / slice[0].close;
}

const closeEnough = (a, b, pct = 0.045) => Math.abs(a - b) / Math.min(a, b) <= pct;

// Genuine Double Top/Bottom/H&S structures span weeks, not a handful of
// bars — without these two guards, any two vaguely-similar local pivots
// in a choppy series get misread as a "pattern" (verified: an earlier,
// looser version of this function flagged 4 of 5 test symbols as Double
// Top simultaneously, which is not plausible). Both guards must hold:
// Tuned for the default 30-min/40-bar window (~2-3 trading days): looser
// than the multi-week Daily case, since intraday swings are naturally
// smaller and closer together. Override with --separation / --depth if
// you point --timeframe/--count at a longer window (e.g. Daily/90).
const MIN_SEPARATION = args.separation ? Number(args.separation) : 4; // bars between the two/three main pivots (~2h on 30-min)
const MIN_DEPTH = args.depth ? Number(args.depth) : 0.015; // neckline must retrace at least 1.5%

const deepestLow = (pts) => pts.reduce((a, b) => (b.price < a.price ? b : a));
const highestHigh = (pts) => pts.reduce((a, b) => (b.price > a.price ? b : a));
const byIndex = (a, b) => a.i - b.i;

/**
 * @param {Array<{time:number,open:number,high:number,low:number,close:number}>} bars full window, oldest first
 *
 * Candidate tops/bottoms/heads are chosen by GLOBAL significance (the
 * most extreme swing points in the whole window), not merely "the last
 * two/three pivots chronologically" — otherwise any pair of similarly-
 * sized recent wiggles gets misread as a pattern even while price is
 * mid-uptrend making routine resistance retests (verified: an earlier
 * version flagged AALI, mid-breakout at a fresh high, as a bearish
 * "Double Top" against its own prior swing — wrong). A pattern is also
 * discarded once price has already moved past it in the continuation
 * direction (broken above a Double Top's tops, or below a Double
 * Bottom's bottoms) since it's no longer acting as resistance/support.
 */
// A reversal pattern can only reverse a trend that actually happened —
// require at least this much prior move in the right direction leading
// into the pattern's first key pivot, otherwise it's just noise.
const TREND_GATE = 0.02;

/** Classic volume-divergence tell for a bearish top-type pattern: momentum fading = 2nd/head area printing on lighter volume than the 1st. */
const isBearishVolumeConfirmed = (first, second) => second.volume <= first.volume;
/** Classic volume-divergence tell for a bullish bottom-type pattern: accumulation building = 2nd/head area printing on heavier volume than the 1st. */
const isBullishVolumeConfirmed = (first, second) => second.volume >= first.volume;

/**
 * A pattern whose own measured-move target has ALREADY been reached (or
 * passed) has already played out — it's history, not an actionable setup.
 * Without this, a wide bar window (needed to avoid truncating genuine
 * structures) can surface a real-but-STALE pattern from far earlier in
 * the window, with a neckline/entry/target nowhere near the current
 * price (verified: an early test flagged a "Double Bottom" whose target
 * the price had already run 20%+ past).
 */
function isPatternStale(bias, points, neckline, lastClose) {
  const prices = points.map((p) => p.price);
  const extreme = bias === 'bearish' ? Math.max(...prices) : Math.min(...prices);
  const height = Math.abs(extreme - neckline);
  const target = bias === 'bearish' ? neckline - height : neckline + height;
  return bias === 'bearish' ? lastClose <= target : lastClose >= target;
}

function detectChartPattern(bars, highs, lows) {
  const lastClose = bars[bars.length - 1].close;

  // Head & Shoulders (bearish): the single highest swing high in the window is
  // the head; its nearest comparable flanking highs (one strictly before, one
  // strictly after) are the shoulders, with two similar troughs as the neckline.
  if (highs.length >= 3) {
    const head = highestHigh(highs);
    const before = highs.filter((h) => h.i < head.i).sort(byIndex).at(-1);
    const after = highs.filter((h) => h.i > head.i).sort(byIndex)[0];
    if (before && after && head.i - before.i >= MIN_SEPARATION && after.i - head.i >= MIN_SEPARATION) {
      const between1 = lows.filter((l) => l.i > before.i && l.i < head.i);
      const between2 = lows.filter((l) => l.i > head.i && l.i < after.i);
      if (between1.length && between2.length) {
      const neck1 = deepestLow(between1);
      const neck2 = deepestLow(between2);
        const avgNeck = (neck1.price + neck2.price) / 2;
        const headDepth = (head.price - avgNeck) / avgNeck;
        if (
          head.price > before.price * 1.02 && head.price > after.price * 1.02 &&
          closeEnough(before.price, after.price, 0.05) && closeEnough(neck1.price, neck2.price, 0.05) &&
          headDepth >= MIN_DEPTH && lastClose <= head.price * 1.01 &&
          priorTrendPct(bars, before.i) >= TREND_GATE &&
          !isPatternStale('bearish', [before, head, after], avgNeck, lastClose)
        ) {
          return {
            type: 'Head & Shoulders',
            bias: 'bearish',
            neckline: avgNeck,
            volumeConfirmed: isBearishVolumeConfirmed(before, after),
            points: [
              { label: 'Left Shoulder', ...before },
              { label: 'Head', ...head },
              { label: 'Right Shoulder', ...after },
            ],
            necklinePoints: [neck1, neck2],
          };
        }
      }
    }
  }

  // Inverse Head & Shoulders (bullish): mirror image using the single lowest swing low as the head.
  if (lows.length >= 3) {
    const head = deepestLow(lows);
    const before = lows.filter((l) => l.i < head.i).sort(byIndex).at(-1);
    const after = lows.filter((l) => l.i > head.i).sort(byIndex)[0];
    if (before && after && head.i - before.i >= MIN_SEPARATION && after.i - head.i >= MIN_SEPARATION) {
      const between1 = highs.filter((h) => h.i > before.i && h.i < head.i);
      const between2 = highs.filter((h) => h.i > head.i && h.i < after.i);
      if (between1.length && between2.length) {
      const neck1 = highestHigh(between1);
      const neck2 = highestHigh(between2);
        const avgNeck = (neck1.price + neck2.price) / 2;
        const headDepth = (avgNeck - head.price) / avgNeck;
        if (
          head.price < before.price * 0.98 && head.price < after.price * 0.98 &&
          closeEnough(before.price, after.price, 0.05) && closeEnough(neck1.price, neck2.price, 0.05) &&
          headDepth >= MIN_DEPTH && lastClose >= head.price * 0.99 &&
          priorTrendPct(bars, before.i) <= -TREND_GATE &&
          !isPatternStale('bullish', [before, head, after], avgNeck, lastClose)
        ) {
          return {
            type: 'Inverse Head & Shoulders',
            bias: 'bullish',
            neckline: avgNeck,
            volumeConfirmed: isBullishVolumeConfirmed(before, after),
            points: [
              { label: 'Left Shoulder', ...before },
              { label: 'Head', ...head },
              { label: 'Right Shoulder', ...after },
            ],
            necklinePoints: [neck1, neck2],
          };
        }
      }
    }
  }

  // Double Top (bearish): the two HIGHEST swing highs in the whole window
  // (most significant, not just most recent), well separated in time, with
  // a meaningfully deep trough between them — discarded if price has since
  // pushed past both tops (that reads as trend continuation, not a top).
  if (highs.length >= 2) {
    const [t1, t2] = [...highs].sort((a, b) => b.price - a.price).slice(0, 2).sort(byIndex);
    if (t2.i - t1.i >= MIN_SEPARATION) {
      const between = lows.filter((l) => l.i > t1.i && l.i < t2.i);
      if (between.length) {
        const neck = deepestLow(between);
        const avgTop = (t1.price + t2.price) / 2;
        const depth = (avgTop - neck.price) / avgTop;
        if (
          closeEnough(t1.price, t2.price, 0.035) && depth >= MIN_DEPTH &&
          lastClose <= Math.max(t1.price, t2.price) * 1.01 &&
          priorTrendPct(bars, t1.i) >= TREND_GATE &&
          !isPatternStale('bearish', [t1, t2], neck.price, lastClose)
        ) {
          return {
            type: 'Double Top',
            bias: 'bearish',
            neckline: neck.price,
            volumeConfirmed: isBearishVolumeConfirmed(t1, t2),
            points: [
              { label: 'Top 1', ...t1 },
              { label: 'Top 2', ...t2 },
            ],
            necklinePoints: [neck],
          };
        }
      }
    }
  }

  // Double Bottom (bullish): the two LOWEST swing lows in the whole window,
  // well separated, with a meaningfully tall peak between them — discarded
  // if price has since broken below both bottoms.
  if (lows.length >= 2) {
    const [b1, b2] = [...lows].sort((a, b) => a.price - b.price).slice(0, 2).sort(byIndex);
    if (b2.i - b1.i >= MIN_SEPARATION) {
      const between = highs.filter((h) => h.i > b1.i && h.i < b2.i);
      if (between.length) {
        const neck = highestHigh(between);
        const avgBottom = (b1.price + b2.price) / 2;
        const depth = (neck.price - avgBottom) / avgBottom;
        if (
          closeEnough(b1.price, b2.price, 0.035) && depth >= MIN_DEPTH &&
          lastClose >= Math.min(b1.price, b2.price) * 0.99 &&
          priorTrendPct(bars, b1.i) <= -TREND_GATE &&
          !isPatternStale('bullish', [b1, b2], neck.price, lastClose)
        ) {
          return {
            type: 'Double Bottom',
            bias: 'bullish',
            neckline: neck.price,
            volumeConfirmed: isBullishVolumeConfirmed(b1, b2),
            points: [
              { label: 'Bottom 1', ...b1 },
              { label: 'Bottom 2', ...b2 },
            ],
            necklinePoints: [neck],
          };
        }
      }
    }
  }

  return null;
}

// ---------------------------------------------------------------------
// Volume verdict
// ---------------------------------------------------------------------

function volumeVerdict(lastBar, avgVolume) {
  const ratio = avgVolume ? lastBar.volume / avgVolume : 1;
  const priceUp = lastBar.close >= lastBar.open;
  if (ratio >= 1.4) {
    return priceUp
      ? { label: 'Akumulasi', bias: 'bullish', note: `Volume ${ratio.toFixed(1)}x rata-rata pada candle naik.`, ratio }
      : { label: 'Distribusi', bias: 'bearish', note: `Volume ${ratio.toFixed(1)}x rata-rata pada candle turun.`, ratio };
  }
  if (ratio <= 0.6) {
    return { label: 'Netral (volume mengering)', bias: 'neutral', note: `Volume hanya ${ratio.toFixed(1)}x rata-rata.`, ratio };
  }
  return { label: 'Netral', bias: 'neutral', note: `Volume mendekati rata-rata (${ratio.toFixed(1)}x).`, ratio };
}

// ---------------------------------------------------------------------
// Support / resistance (near-term: recent bar cluster, not full period extreme)
// ---------------------------------------------------------------------

function nearTermSR(bars, lookback = 6) {
  const recent = bars.slice(-lookback);
  const support = Math.min(...recent.map((b) => b.low));
  const resistance = Math.max(...recent.map((b) => b.high));
  return { support, resistance };
}

// ---------------------------------------------------------------------
// Moving Average trend read (MA) — a lagging, smoothed confirmation that
// sits alongside the swing-structure (SW) read below.
// ---------------------------------------------------------------------

function computeMA(bars, length) {
  const ma = [];
  for (let i = length - 1; i < bars.length; i++) {
    const slice = bars.slice(i - length + 1, i + 1);
    const avg = slice.reduce((a, b) => a + b.close, 0) / length;
    ma.push({ i, time: bars[i].time, price: avg });
  }
  return ma;
}

function maTrendVerdict(ma, lastClose, length) {
  if (ma.length < 6) return { bias: 'neutral', label: `MA${length}: data belum cukup`, value: null };
  const last = ma[ma.length - 1];
  const prior = ma[ma.length - 6]; // slope over the last ~6 bars
  const rising = last.price > prior.price;
  const priceAbove = lastClose > last.price;
  if (priceAbove && rising) return { bias: 'bullish', label: `MA${length}: Uptrend (harga > MA, MA naik)`, value: last.price };
  if (!priceAbove && !rising) return { bias: 'bearish', label: `MA${length}: Downtrend (harga < MA, MA turun)`, value: last.price };
  return { bias: 'neutral', label: `MA${length}: Sideways/Transisi (harga & MA berlawanan)`, value: last.price };
}

// ---------------------------------------------------------------------
// Swing-structure trend read (SW) — classic Dow-theory read off the same
// swing highs/lows already used for chart-pattern detection: Higher-High
// + Higher-Low = uptrend structure, Lower-High + Lower-Low = downtrend.
// ---------------------------------------------------------------------

function swingTrendVerdict(highs, lows) {
  if (highs.length < 2 || lows.length < 2) return { bias: 'neutral', label: 'SW: struktur belum cukup data' };
  const [h1, h2] = highs.slice(-2);
  const [l1, l2] = lows.slice(-2);
  const higherHigh = h2.price > h1.price;
  const higherLow = l2.price > l1.price;
  if (higherHigh && higherLow) return { bias: 'bullish', label: 'SW: Higher-High & Higher-Low (struktur bullish)' };
  if (!higherHigh && !higherLow) return { bias: 'bearish', label: 'SW: Lower-High & Lower-Low (struktur bearish)' };
  return { bias: 'neutral', label: 'SW: struktur campuran/sideways' };
}

// ---------------------------------------------------------------------
// Volume validity — is the entry signal backed by real participation, or
// is it a thin-volume move that reads more like an anomaly/trader trap
// (a classic bull/bear-trap tell: price moves but volume doesn't)?
// ---------------------------------------------------------------------

function volumeValidity(volumeInfo, entryType) {
  if (entryType === 'WAIT') return { valid: null, label: 'Volume: netral, belum ada sinyal untuk divalidasi' };
  if (volumeInfo.ratio >= 1.4) return { valid: true, label: '✓ VALID — didukung volume tinggi, bukan anomali' };
  if (volumeInfo.ratio <= 0.6) return { valid: false, label: '⚠ WASPADA — volume tipis, rawan jebakan (trap)' };
  return { valid: null, label: '~ Volume rata-rata — konfirmasi belum kuat' };
}

// ---------------------------------------------------------------------
// 3-factor score (SKILL.md Step 3) — chart pattern (if any) reinforces
// or can override the single-candle read, since it's the stronger signal.
// ---------------------------------------------------------------------

function biasScore(bias) {
  return bias === 'bullish' ? 2 : bias === 'bearish' ? -2 : 0;
}

function scoreSymbol({ pattern, chartPattern, volume, changePct }) {
  const candleBias = chartPattern ? chartPattern.bias : pattern.bias;
  // A chart pattern without its classic volume tell (see isBearishVolumeConfirmed/
  // isBullishVolumeConfirmed) is a weaker signal — count it, but at half strength.
  const patternStrength = chartPattern && !chartPattern.volumeConfirmed ? 0.5 : 1;
  const candleScore = candleBias === 'mixed' ? 1 : biasScore(candleBias) * patternStrength;
  const volumeScore = biasScore(volume.bias);
  const trendScore = changePct > 3 ? 2 : changePct > 0.5 ? 1 : changePct < -3 ? -2 : changePct < -0.5 ? -1 : 0;
  const total = candleScore + volumeScore + trendScore;

  const allPositive = candleScore > 0 && volumeScore >= 0 && trendScore >= 0;
  const allNegative = candleScore < 0 && volumeScore <= 0 && trendScore <= 0;
  const conflict = (candleScore > 0 && volumeScore < 0) || (candleScore < 0 && volumeScore > 0);

  let verdict;
  if (conflict) verdict = 'Netral (konflik!)';
  else if (allPositive && total >= 5) verdict = 'Strong Buy';
  else if (allPositive) verdict = 'Buy';
  else if (allNegative && total <= -5) verdict = 'Strong Sell';
  else if (allNegative) verdict = 'Sell';
  else verdict = 'Netral';

  return { candleScore, volumeScore, trendScore, total, verdict, conflict };
}

// ---------------------------------------------------------------------
// Entry zone (a narrow range tied to the pattern, not just raw S/R) —
// a confirmed chart pattern's neckline takes priority over the
// single-candle read when both are present.
// ---------------------------------------------------------------------

function entryZone({ pattern, chartPattern, support, resistance, lastClose }) {
  // When a chart pattern is confirmed, the entry HAS to be anchored to its
  // own neckline — using the generic near-term support/resistance instead
  // (as before) could put the entry band nowhere near the level the
  // pattern/target math is actually built on.
  if (chartPattern) {
    const neckline = chartPattern.neckline;
    if (chartPattern.bias === 'bullish') {
      const lo = Math.round(neckline * 0.995);
      const hi = Math.round(neckline * 1.02);
      return { type: 'BUY', low: lo, high: Math.max(hi, lo + 1) };
    }
    const hi = Math.round(neckline * 1.005);
    const lo = Math.round(neckline * 0.98);
    return { type: 'SELL', low: Math.min(lo, hi - 1), high: hi };
  }

  const bias = pattern.bias;
  if (bias === 'bullish') {
    const lo = Math.round(support);
    const hi = Math.round(support + (lastClose - support) * 0.35);
    return { type: 'BUY', low: lo, high: Math.max(hi, lo + 1) };
  }
  if (bias === 'bearish') {
    const hi = Math.round(resistance);
    const lo = Math.round(resistance - (resistance - lastClose) * 0.35);
    return { type: 'SELL', low: Math.min(lo, hi - 1), high: hi };
  }
  return { type: 'WAIT', low: Math.round(support), high: Math.round(support * 1.01) };
}

// ---------------------------------------------------------------------
// Price target — classic measured-move projection. A confirmed chart
// pattern uses ITS OWN neckline + pattern height (the textbook way to
// project a Double Top/Bottom or H&S target); otherwise fall back to
// projecting the plain S/R range beyond the breakout side.
// ---------------------------------------------------------------------

function priceTarget({ entry, chartPattern, support, resistance }) {
  if (chartPattern) {
    // Height must come from the pattern's own defining extreme — the
    // TOP/HEAD above the neckline for a bearish pattern, the BOTTOM/HEAD
    // below it for a bullish one. A generic Math.max() picks the wrong
    // point for Inverse H&S (its head is the LOWEST price, not the
    // highest) and for Double Bottom (picks the shallower of the two
    // bottoms instead of the true extreme), understating the height.
    const patternPrices = chartPattern.points.map((p) => p.price);
    const extreme = chartPattern.bias === 'bearish' ? Math.max(...patternPrices) : Math.min(...patternPrices);
    const height = Math.abs(extreme - chartPattern.neckline);
    return entry.type === 'BUY'
      ? Math.round(chartPattern.neckline + height)
      : entry.type === 'SELL'
        ? Math.round(chartPattern.neckline - height)
        : null;
  }
  const range = resistance - support;
  if (entry.type === 'BUY') return Math.round(resistance + range);
  if (entry.type === 'SELL') return Math.round(support - range);
  return null;
}

// ---------------------------------------------------------------------
// Drawing — every text notation is anchored to the actual candle it
// describes (small font, 60% opacity, per spec), not offset off to the
// side. Buy/sell area sits right around the signal candle.
// ---------------------------------------------------------------------

/**
 * Trace a thin real-time zigzag through EVERY swing high/low across the
 * whole analyzed window, start to end — not just the 2-3 points of a
 * detected pattern. This is what makes an "M", "W", or head & shoulders
 * shape actually readable on the chart: the eye follows one continuous
 * line through the candles instead of a few isolated labels.
 */
async function drawFullZigzag(highs, lows) {
  const ordered = [...highs, ...lows].sort((a, b) => a.i - b.i);
  for (let k = 0; k < ordered.length - 1; k++) {
    await drawing.drawShape({
      shape: 'trend_line',
      point: { time: ordered[k].time, price: ordered[k].price },
      point2: { time: ordered[k + 1].time, price: ordered[k + 1].price },
      overrides: { linecolor: 'rgba(120,120,120,0.6)', linewidth: 1, linestyle: 0 },
    });
  }
}

/** Draw the moving-average line over its most recent `maLookback` points (capped so draw-call count stays sane). */
async function drawMA(ma) {
  const points = ma.slice(-maLookback);
  for (let k = 0; k < points.length - 1; k++) {
    await drawing.drawShape({
      shape: 'trend_line',
      point: { time: points[k].time, price: points[k].price },
      point2: { time: points[k + 1].time, price: points[k + 1].price },
      overrides: { linecolor: LINE_COLOR.ma, linewidth: 2, linestyle: 0 },
    });
  }
}

async function drawChartPattern(chartPattern, barInterval) {
  if (!chartPattern) return;
  const color = chartPattern.bias === 'bullish' ? COLOR.bullish : COLOR.bearish;
  const lineColor = chartPattern.bias === 'bullish' ? LINE_COLOR.patternBullish : LINE_COLOR.patternBearish;

  // Re-trace the pattern's own pivots on top of the full zigzag, thicker
  // and colored, so the specific M/W/H&S shape stands out from the rest.
  const ordered = [...chartPattern.points, ...chartPattern.necklinePoints].sort((a, b) => a.i - b.i);
  for (let k = 0; k < ordered.length - 1; k++) {
    await drawing.drawShape({
      shape: 'trend_line',
      point: { time: ordered[k].time, price: ordered[k].price },
      point2: { time: ordered[k + 1].time, price: ordered[k + 1].price },
      overrides: { linecolor: lineColor, linewidth: 2, linestyle: 0 },
    });
  }
  // Neckline, extended toward the present.
  const lastNeck = chartPattern.necklinePoints.at(-1);
  const lastPivot = chartPattern.points.at(-1);
  await drawing.drawShape({
    shape: 'trend_line',
    point: { time: lastNeck.time, price: chartPattern.neckline },
    point2: { time: Math.max(lastPivot.time, lastNeck.time) + 20 * barInterval, price: chartPattern.neckline },
    overrides: { linecolor: lineColor, linewidth: 1, linestyle: 2 },
  });

  // Label each pivot exactly at its candle.
  for (const p of chartPattern.points) {
    await drawing.drawShape({
      shape: 'text',
      point: { time: p.time, price: p.price },
      text: `${p.label} (${Math.round(p.price).toLocaleString('id-ID')})`,
      overrides: { color, fontsize: NOTE_FONTSIZE, bold: true },
    });
  }

  // Pattern name at the most recent pivot — flagged with the volume tell
  // (see isBearishVolumeConfirmed/isBullishVolumeConfirmed) so a pattern
  // lacking it reads as needing confirmation, not equally reliable.
  const confirmSuffix = chartPattern.volumeConfirmed ? ' [vol. confirmed]' : ' [need confirmation]';
  await drawing.drawShape({
    shape: 'text',
    point: { time: lastPivot.time, price: chartPattern.neckline },
    text: `${chartPattern.type.toUpperCase()} — Neckline ${Math.round(chartPattern.neckline).toLocaleString('id-ID')}${confirmSuffix}`,
    overrides: { color, fontsize: NOTE_FONTSIZE, bold: true },
  });
}

async function annotate(rawSymbol, { support, resistance, pattern, chartPattern, entry, target, lastBar, bars, highs, lows, ma, maInfo, swInfo, validity }) {
  if (clearFirst) await drawing.clearAll();

  // Derive the extension unit from the actual bar spacing so this works
  // correctly whatever --timeframe is in use (2h by default, or Daily).
  const barInterval = bars.length >= 2 ? bars[bars.length - 1].time - bars[bars.length - 2].time : 7200;

  // The candle where the SIGNAL actually sits: when a chart pattern is
  // confirmed, that's its last key pivot (second top/bottom, right
  // shoulder) — the bar where the M/W/H&S shape actually completed — not
  // necessarily the latest candle on the chart. Falls back to the last
  // bar for a plain single-candle read.
  const signalBar = chartPattern ? bars[chartPattern.points.at(-1).i] : lastBar;

  // The full swing zigzag first (thin, neutral), then the MA line — both
  // are the "structural" layers so the S/R/zone/label layers drawn after
  // them sit visually on top.
  await drawFullZigzag(highs, lows);
  if (ma.length) await drawMA(ma);

  await drawing.drawShape({
    shape: 'horizontal_line',
    point: { time: lastBar.time, price: support },
    overrides: { linecolor: LINE_COLOR.support, linewidth: 2, linestyle: 2 },
  });
  await drawing.drawShape({
    shape: 'horizontal_line',
    point: { time: lastBar.time, price: resistance },
    overrides: { linecolor: LINE_COLOR.resistance, linewidth: 2, linestyle: 2 },
  });

  if (target !== null) {
    await drawing.drawShape({
      shape: 'trend_line',
      point: { time: lastBar.time, price: target },
      point2: { time: lastBar.time + 20 * barInterval, price: target },
      overrides: { linecolor: LINE_COLOR.target, linewidth: 2, linestyle: 0 },
    });
    await drawing.drawShape({
      shape: 'text',
      point: { time: lastBar.time + 3 * barInterval, price: target },
      text: `TARGET ${target.toLocaleString('id-ID')}`,
      overrides: { color: LINE_COLOR.target, fontsize: NOTE_FONTSIZE, bold: true },
    });
  }

  // Buy/sell area wraps the SIGNAL candle (where the pattern actually
  // formed), not always the latest bar — buy = green, sell = red, both
  // 60% transparent.
  const zoneFrom = signalBar.time - 2 * barInterval;
  const zoneTo = signalBar.time + 4 * barInterval;
  if (entry.type === 'BUY') {
    await drawing.drawShape({
      shape: 'rectangle',
      point: { time: zoneFrom, price: entry.low },
      point2: { time: zoneTo, price: entry.high },
      overrides: { color: '#22c55e', backgroundColor: '#22c55e', transparency: 60, linewidth: 1 },
    });
  } else if (entry.type === 'SELL') {
    await drawing.drawShape({
      shape: 'rectangle',
      point: { time: zoneFrom, price: entry.low },
      point2: { time: zoneTo, price: entry.high },
      overrides: { color: '#dc2626', backgroundColor: '#dc2626', transparency: 60, linewidth: 1 },
    });
  }

  // Candle-pattern label anchored EXACTLY on the current candle: just
  // above its high if bullish/neutral, just below its low if bearish —
  // this describes the LATEST bar's own shape (Doji, Marubozu, ...),
  // which is a separate read from any multi-bar chart pattern below.
  const patternColor = pattern.bias === 'bullish' ? COLOR.bullish : pattern.bias === 'bearish' ? COLOR.bearish : COLOR.neutral;
  const padding = (resistance - support) * 0.05;
  const patternAnchorPrice = pattern.bias === 'bearish' ? lastBar.low - padding : lastBar.high + padding;
  await drawing.drawShape({
    shape: 'text',
    point: { time: lastBar.time, price: patternAnchorPrice },
    text: pattern.name,
    overrides: { color: patternColor, fontsize: NOTE_FONTSIZE, bold: true },
  });

  // Entry marker on the SIGNAL candle — the bar the pattern/entry price
  // actually belongs to — bracketing it on the side matching the trade
  // direction (below for BUY, above for SELL) so it never overlaps price.
  const entryColor = entry.type === 'BUY' ? COLOR.bullish : entry.type === 'SELL' ? COLOR.bearish : COLOR.neutral;
  const targetSuffix = target !== null ? ` (target ${target.toLocaleString('id-ID')})` : '';
  const entryText =
    entry.type === 'BUY'
      ? `● ENTRY BUY ▲ ${entry.low.toLocaleString('id-ID')}-${entry.high.toLocaleString('id-ID')}${targetSuffix}`
      : entry.type === 'SELL'
        ? `● ENTRY SELL ▼ ${entry.low.toLocaleString('id-ID')}-${entry.high.toLocaleString('id-ID')}${targetSuffix}`
        : '● NETRAL — TUNGGU';
  const entryPadding = (resistance - support) * 0.05 || signalBar.close * 0.01;
  const entryAnchorPrice = entry.type === 'SELL' ? signalBar.high + entryPadding : signalBar.low - entryPadding;
  await drawing.drawShape({
    shape: 'text',
    point: { time: signalBar.time, price: entryAnchorPrice },
    text: entryText,
    overrides: { color: entryColor, fontsize: NOTE_FONTSIZE, bold: true },
  });

  // Volume-validity read stacked further out from the entry marker on the
  // same side, so the three signal-candle notations read top-to-bottom
  // (or bottom-to-top) without overlapping. A detected trap (thin-volume
  // move) gets an eye-catching white-on-red callout instead of the plain
  // colored text used for the other verdicts, so it can't be missed.
  const validityAnchorPrice = entry.type === 'SELL' ? signalBar.high + entryPadding * 2.2 : signalBar.low - entryPadding * 2.2;
  if (validity.valid === false) {
    await drawing.drawShape({
      shape: 'text',
      point: { time: signalBar.time, price: validityAnchorPrice },
      text: validity.label,
      overrides: {
        color: '#ffffff',
        backgroundColor: '#dc2626',
        fillBackground: true,
        backgroundTransparency: 60,
        fontsize: NOTE_FONTSIZE,
        bold: true,
      },
    });
  } else {
    const validityColor = validity.valid === true ? COLOR.bullish : COLOR.neutral;
    await drawing.drawShape({
      shape: 'text',
      point: { time: signalBar.time, price: validityAnchorPrice },
      text: validity.label,
      overrides: { color: validityColor, fontsize: NOTE_FONTSIZE, bold: true },
    });
  }

  // MA trend notation sits right on the end of the MA line, like the
  // target label sits on the target line.
  if (ma.length) {
    const lastMa = ma[ma.length - 1];
    const maColor = maInfo.bias === 'bullish' ? COLOR.bullish : maInfo.bias === 'bearish' ? COLOR.bearish : COLOR.neutral;
    await drawing.drawShape({
      shape: 'text',
      point: { time: lastMa.time + 3 * barInterval, price: lastMa.price },
      text: maInfo.label,
      overrides: { color: maColor, fontsize: NOTE_FONTSIZE, bold: true },
    });
  }

  // SW (swing-structure) trend notation sits on the most recent swing
  // pivot — the zigzag point it's actually describing.
  const lastSwingPoint = [...highs, ...lows].sort((a, b) => b.i - a.i)[0];
  if (lastSwingPoint) {
    const swColor = swInfo.bias === 'bullish' ? COLOR.bullish : swInfo.bias === 'bearish' ? COLOR.bearish : COLOR.neutral;
    await drawing.drawShape({
      shape: 'text',
      point: { time: lastSwingPoint.time, price: lastSwingPoint.price },
      text: swInfo.label,
      overrides: { color: swColor, fontsize: NOTE_FONTSIZE, bold: true },
    });
  }

  await drawChartPattern(chartPattern, barInterval);
}

// ---------------------------------------------------------------------
// Per-symbol pipeline
// ---------------------------------------------------------------------

async function processSymbol(rawSymbol) {
  log(`[symbol] ${rawSymbol} ...`);

  await chart.setSymbol({ symbol: rawSymbol });
  await new Promise((r) => setTimeout(r, 400)); // let the symbol switch settle
  await chart.setTimeframe({ timeframe });

  const full = await data.getOhlcv({ count: barCount });
  const bars = full.bars;
  const last5 = bars.slice(-5);
  const lastBar = bars[bars.length - 1];
  const firstBar = bars[0];
  const avgVolume = Math.round(bars.reduce((a, b) => a + b.volume, 0) / bars.length);
  const changePct = ((lastBar.close - firstBar.open) / firstBar.open) * 100;

  // A meaningful recent range, not just the last 5 bars (which produced a
  // support/resistance pair unrelated to the wider structure a chart
  // pattern might already be describing via its own neckline).
  const { support, resistance } = nearTermSR(bars.slice(-20), 20);
  const pattern = classifyPattern(last5);
  const { highs, lows } = findSwingPoints(bars, args.wing ? Number(args.wing) : 3);
  const chartPattern = detectChartPattern(bars, highs, lows);
  const volume = volumeVerdict(lastBar, avgVolume);
  const score = scoreSymbol({ pattern, chartPattern, volume, changePct });
  const entry = entryZone({ pattern, chartPattern, support, resistance, lastClose: lastBar.close });
  const target = priceTarget({ entry, chartPattern, support, resistance });
  const validity = volumeValidity(volume, entry.type);

  const ma = computeMA(bars, maLength);
  const maInfo = maTrendVerdict(ma, lastBar.close, maLength);
  const swInfo = swingTrendVerdict(highs, lows);

  await annotate(rawSymbol, { support, resistance, pattern, chartPattern, entry, target, lastBar, bars, highs, lows, ma, maInfo, swInfo, validity });

  return {
    symbol: rawSymbol,
    price: lastBar.close,
    change_pct: `${changePct.toFixed(2)}%`,
    support: Math.round(support),
    resistance: Math.round(resistance),
    target,
    ma: { length: maLength, value: maInfo.value !== null ? Math.round(maInfo.value) : null, trend: maInfo.bias },
    sw_trend: swInfo.bias,
    volume_validity: validity.label,
    pattern: pattern.name,
    chart_pattern: chartPattern
      ? { type: chartPattern.type, bias: chartPattern.bias, neckline: Math.round(chartPattern.neckline), volume_confirmed: chartPattern.volumeConfirmed }
      : null,
    volume: volume.label,
    volume_note: volume.note,
    entry_type: entry.type,
    entry_low: entry.low,
    entry_high: entry.high,
    score,
  };
}

// ---------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------

async function resolveSymbols() {
  let symbols;
  if (explicitSymbols) {
    symbols = explicitSymbols;
  } else {
    const wl = await watchlist.get();
    symbols = (wl.symbols || []).map((s) => s.symbol);
    log(`[1] Watchlist: ${symbols.length} symbols.`);
  }
  symbols = symbols.concat(extraSymbols);
  if (extraSymbols.length) log(`[5] Plus ${extraSymbols.length} extra screening symbol(s).`);
  return symbols;
}

async function runOnce(symbols) {
  const results = [];
  let hadFailure = false;
  for (const sym of symbols) {
    try {
      const result = await processSymbol(sym);
      results.push(result);
      const targetNote = result.target !== null ? ` | target ${result.target}` : '';
      const cpNote = result.chart_pattern ? ` | CHART PATTERN: ${result.chart_pattern.type}` : '';
      log(`      -> ${result.pattern} | ${result.volume} | ${result.ma.trend.toUpperCase()} | ${result.sw_trend} | score ${result.score.total} (${result.score.verdict})${targetNote}${cpNote}`);
    } catch (err) {
      hadFailure = true;
      log(`      !! FAILED: ${err.message}`);
      results.push({ symbol: sym, error: err.message });
    }
  }
  for (const r of results) process.stdout.write(JSON.stringify(r) + '\n');
  return { results, hadFailure };
}

async function main() {
  await ensureConnected();
  const symbols = await resolveSymbols();

  if (!watch) {
    const { results, hadFailure } = await runOnce(symbols);
    log(`\nDone. ${results.length} symbols processed${hadFailure ? ' (some failed — see above)' : ''}.`);
    process.exit(hadFailure ? 1 : 0);
  }

  // --watch: keep re-fetching + redrawing on a loop so the chart always
  // reflects the latest price action, instead of a single one-shot pass.
  // Each cycle clears and redraws from scratch (same as a normal run) —
  // this never exits on its own; stop it with Ctrl+C.
  log(`[watch] Auto-refresh every ${intervalSec}s for ${symbols.length} symbol(s). Press Ctrl+C to stop.`);
  // eslint-disable-next-line no-constant-condition
  while (true) {
    log(`\n[watch] Cycle start ${new Date().toISOString()}`);
    await runOnce(symbols);
    log(`[watch] Cycle done — sleeping ${intervalSec}s...`);
    await new Promise((r) => setTimeout(r, intervalSec * 1000));
  }
}

main().catch((err) => {
  console.error('[fatal]', err.message);
  process.exit(2);
});
