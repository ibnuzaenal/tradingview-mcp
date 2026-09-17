---
name: idx-daily-screen
description: Generate the IDX Daily Screen(er) PDF report (IHSG gauge, Top Gainer/Loser with volume-trap check, macro/sentiment panel, sector-categorized watchlist cards with Decision Gate 2.0/3.0) purely from CSV price data — no TradingView Desktop required, runnable from a phone/cloud session. Trigger phrases: "IDX Daily Screen", "jalankan IDX Daily Screening", "buat laporan screening IDX hari ini". Includes emailing the finished PDF to a confirmed recipient (see Step 7) whenever an email tool/connector is available in the session.
---

# IDX Daily Screener — Report Generation (CSV-based, no TradingView Desktop)

## Why this pipeline exists (read before touching the old one)

The original version of this skill drove TradingView Desktop over Chrome
DevTools Protocol (CDP) and read a Pine indicator's own dashboard table
straight off the live chart. That was abandoned outright, not just
de-prioritized — CDP connections were unreliable (`tv_health_check` failing
after relaunch, an indicator that silently stopped producing output after
long runtime), and it could only run on this one Mac with TradingView
Desktop open, never unattended or from a phone. **This pipeline never
touches TradingView Desktop, CDP, or any `tv_*`/chart tool at any step** —
the two old CDP-based readers (`watchlist-read-idxa.mjs`,
`watchlist-read-idxdev.mjs`) have been deleted from this repo, not just
superseded, so there is no legacy path left to accidentally fall back to.

**Everything the pipeline needs now lives inside THIS repo** — no step reads
from or requires opening the separate `Claude-Tradeview AI/IDX Project`
folder on the laptop:
- `pine/IDX_Analyzer_DG2.7.pine` (plus every prior DG version, `DG2.0`
  through `DG2.7`) — a checked-in copy of the actual indicator source, kept
  in this repo's own git history. When the user ships a newer version, copy
  the new file into `pine/` and commit it here too — that copy, not the one
  on the laptop's separate project folder, is what this skill's formulas are
  read against and cited from.
- `scripts/idx-daily-screener/dg_engine/` — a checked-in copy of the
  validated research engine (`engine.py`, `engine_dg21.py`, `engine_dg26.py`,
  `dg27_sparse_model.json`) that `dg_snapshot.py` imports locally
  (`from engine import ...`, `sys.path` pointed at this folder, not an
  external one). This is a snapshot, not a live symlink — if the user's
  research folder produces new/updated engine code, that needs a deliberate
  re-copy + re-verify here, same as the .pine sync above.
- `.venv/` at the repo root — a self-contained Python virtualenv
  (pandas/numpy/scikit-learn) so `dg_snapshot.py` never needs the other
  project's `research_v24/.venv` either.

**This still never re-analyzes or re-designs anything.** Every field in the
output is either read straight out of a validated engine function, or a
mechanical, line-by-line reproduction of a ternary/formula copied directly
from `pine/IDX_Analyzer_DG2.7.pine` — match whatever is the highest-numbered
`IDX_Analyzer_DG*.pine` file, version-agnostic, same never-hardcode-a-version
discipline the old TradingView-based pipeline had for "IDX Analyzer Dev". If
a future DG version changes a formula this pipeline replicates, that formula
needs re-reading from the new .pine file and re-porting into
`dg_snapshot.py`/`dg_engine/` — don't assume it's still identical.

**One-directional, no callbacks:** the six steps below run strictly in
order, each one producing a file the next reads — fetch → compute → render
→ export → verify → deliver. None of them loop back to re-trigger an earlier
step automatically, and none of them retry-on-failure beyond the single
pass already built into `dg_snapshot.py` (a failed ticker is skipped and
logged, not retried). If a step needs to be redone, redo it and everything
after it — never re-enter a step that already finished.

## Known, disclosed scope limits

Read these once — they're also printed in the generated report's footnote,
so the user always sees them:

- **Market Index Regime** uses an equal-weighted proxy built from the cached
  tickers' own returns, not the real IDX:COMPOSITE index constituents — the
  same disclosed approximation `research_v24` itself uses.
- **Sector RS** is off by default in the real indicator too, so this isn't a
  new gap.
- **Gate 2.0's Risk Overlay** (Kelly 1/4 sizing, PSR, MinTRL, Circuit
  Breaker, CUSUM) and the **Lead Quality Tracker** both need a maintained
  rolling trade/signal history across many days on a live chart — they
  cannot be produced from one stateless CSV snapshot, so `dg_snapshot.py`
  leaves them out entirely rather than fabricate numbers. (If ever wanted,
  this would need a persistent state file updated on every run — a
  meaningfully bigger project, not a tweak.)
- **Chart-pattern win-rate chips** (Double Top/Bottom/H&S/Inverse H&S %) are
  also omitted — mapping the research engine's internal pattern `kind` codes
  to those four named types needs more verification than was done; shipping
  a wrong number would be worse than shipping none.
- Everything else — DECISION, Setup, 0-10 Score, Support/Resistance/Entry/
  Stop/Target2, the indicator's own Bull/Bear score + letter Grade, TR
  Forecast, AI Forecast (kNN), AI Forecast 2.0, Decision Gate 2.0, and the
  new **Decision Gate 3.0** composite-probability model — is fully ported
  and live in every report this pipeline generates.

## Step 0 — Refresh the ticker watchlist definition (rarely needed)

`scripts/idx-daily-screener/categories.json` is now also the **source of
truth for which tickers get screened** (there is no live "watchlist" to read
from TradingView anymore). Add/remove a ticker by editing this file — it'll
be picked up automatically next run (Step 1 fetches whatever's missing from
cache, Step 2 computes whatever's in the cache directory). Only touch this
when the user actually asks to add/drop a stock, not on every run.

## Step 1 — Refresh the local OHLCV cache (network, no TradingView)

Cache location: `scripts/idx-daily-screener/data_cache/{TICKER}.csv` (one
file per ticker, `Ticker,Date,Open,High,Low,Close,Adj Close,Volume,
Dividends,Stock Splits` — `Dividends`/`Stock Splits` are always `0.0`,
downstream code never reads them). Also needs a `COMPOSITE.csv` (the IHSG
index, Yahoo symbol `^JKSE`, no `.JK` suffix) for the IHSG gauge card.

**Important, confirmed by testing:** direct `curl`/Python `requests` calls to
Yahoo Finance from this machine's Bash tool get HTTP 429 (rate-limited) —
every time, even with a browser User-Agent. The **Browser pane tool**
(`mcp__Claude_Browser__navigate` + `get_page_text`, or `javascript_tool`
running a `fetch()` inside the page) reaches Yahoo Finance successfully.
Always fetch this way, never via Bash/curl/requests.

URL pattern: `https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}.JK?range={RANGE}&interval=1d`
(index tickers like `^JKSE` skip the `.JK` suffix). Response shape:
`chart.result[0].timestamp[]` paired with `.indicators.quote[0].{open,high,
low,close,volume}[]` and `.indicators.adjclose[0].adjclose[]`; convert each
unix timestamp to a UTC `YYYY-MM-DD` date; skip any index where `open`/
`high`/`low`/`close` is `null`.

Two fetch modes:
- **A ticker with no existing cache file** (a brand-new addition to
  `categories.json`): fetch `range=2y` once to build full history from
  scratch.
- **A ticker that already has a cache file** (the normal daily case): fetch
  a small `range=10d`, keep only rows dated strictly after the file's
  current last date, and append them — never refetch or rewrite the whole
  history every run, that's the "ringan/cepat" part.

**Context-budget note:** a single `range=2y` fetch for one ticker is
~25-30K characters of raw JSON — manageable for one-off use, but fetching
many tickers this way one at a time in the MAIN conversation burns a lot of
context fast. For a full-watchlist refresh (60+ tickers), **delegate this
step to a background Agent** with explicit instructions to (a) fetch via the
Browser pane exactly as described above, one or two tickers per
`browser_batch` call to avoid the ~100K-character tool-output-size error,
and (b) report back only a compact per-ticker status table, never paste raw
JSON into its final response. This keeps the large payloads inside the
subagent's own isolated context instead of the main conversation's.

## Step 2 — Compute the DG2.7 snapshot (pure Python, no network, fully local)

```bash
"/Users/ibnu.zaenal/tradingview-mcp/.venv/bin/python3" \
  scripts/idx-daily-screener/dg_snapshot.py /tmp/dg-snapshot.jsonl
```

`dg_snapshot.py` does the actual indicator-formula replication, importing
only from this repo's own `dg_engine/` (no `sys.path` entry outside this
repo):
1. Loads every `data_cache/*.csv`, builds the equal-weighted market proxy
   from all of them (`build_market_proxy`), loads the frozen Decision Gate
   3.0 coefficients from `dg_engine/dg27_sparse_model.json`.
2. For each ticker: runs `engine.base/elite/forecast`,
   `engine_dg21.chart_patterns_gated/mtf_bias/market_bias/bandarmologi/
   bull_bear_score`, `engine_dg26.af2_direction/ewma_vol/percentrank/
   hurst_exponent` (the checked-in engine copy), then layers on top of
   those: the DG2.7-specific Decision Gate 2.0 weight tweaks
   (`decision_gate_2_7` — candle vote removed, breakout 24→16, rvol 4→6,
   ported directly from the DG2.7 .pine header's disclosed changelog), the
   Decision Gate 3.0 composite probability (frozen sparse-model
   coefficients, standardize → dot → sigmoid), the flat-branch `DECISION`
   string ternary (ported verbatim from `pine/IDX_Analyzer_DG2.7.pine:1806`
   — the with-position branch is not ported, since this project always runs
   with `hasPosition=false`), and the `gradePro` letter-grade function
   (`pine/IDX_Analyzer_DG2.7.pine:1707`).
3. Writes one JSONL line per ticker in the SAME shape the old TradingView
   reader produced (`{symbol, price, change_pct, dashboard: {...}}`), so
   `gen_report.py` needs no restructuring — plus new DG-only keys
   (`gate2_dir/tier/conviction`, `dg3_dir/prob/conf`, `af2_dir/prob`,
   `mtf_bias`, `market_bias`, `bandar_flow/anomaly`, `hurst_regime`,
   `ewma_vol_pct/pctile`, `bull_score`, `bear_score`, `grade`) that
   `gen_report.py` renders as a "Parameter Kunci" chip row on every card
   when present (falls back to the old score-only grade derivation when
   they're absent, so it stays compatible with any older Dev-schema JSONL
   too).

Uses this repo's own `.venv` (pandas/numpy/scikit-learn) — the system
`python3` doesn't have those. `.venv/` and `data_cache/` are gitignored
(a virtualenv and a daily-refreshed data cache don't belong in git history);
`pine/` and `dg_engine/` ARE committed — those are the actual source-of-
truth copies "history" refers to.

If a ticker's CSV is missing or empty, `compute_symbol` raises and that
ticker is skipped with a `FAILED` line on stderr, once, no retry — the run
continues for everyone else (same "don't chase 100%" philosophy as the old
pipeline, now also the "no callback" one).

## Step 3 — Generate the HTML report (unchanged from before)

```bash
python3 scripts/idx-daily-screener/gen_report.py /tmp/dg-snapshot.jsonl /tmp/idxa-report.html
```

Same categorization (`categories.json`), same macro panel
(`macro_facts.json`/`macro_reads.json`, static — refresh only every few days,
not every run), same Top Gainer/Loser panel, same "Ringkasan Entry —
Sekilas" summary table sorted Buy-first/Sell-last. The only additions are
the "Parameter Kunci" chip row per card (Gate 2.0, Decision Gate 3.0, AI
Forecast 2.0, MTF/Market, Bandarmologi, volatility regime) and a short
explainer legend under the report's masthead defining those new terms for
the reader.

## Step 4 — Export to PDF (unchanged)

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --no-sandbox \
  --print-to-pdf="/tmp/idxa-report.pdf" --no-pdf-header-footer \
  "file:///tmp/idxa-report.html"
```
Filename convention: `IDX Daily Screener : {DD Month YYYY}, {HH.MM}.pdf`.

## Step 5 — Visual verification (mandatory, single pass)

```bash
pdftoppm -png -r 100 "/tmp/idxa-report.pdf" "/tmp/idxa-pages/page"
```
Read every page image, check nothing is sliced across a page boundary. Fix
any specific offending block's CSS in `gen_report.py` and re-run Steps 3-5
only — never Steps 1-2 for a layout-only issue.

## Step 6 — Copy the finished PDF into the download folder (automatic)

```bash
cp "/tmp/idxa-report.pdf" "/Users/ibnu.zaenal/Documents/Claude-Tradeview AI/IDX_Daily Stock Report/IDX Daily Screener : {DD Month YYYY}, {HH.MM}.pdf"
```
Standing download location for every report this skill produces — do this
automatically, no need to ask. `SendUserFile` in-chat too, for whichever
device the user is on.

## Why this is now phone/unattended-runnable

Nothing in Steps 1-6 touches TradingView Desktop, CDP, or any app that only
runs on this Mac with a GUI session — Step 1 is a network call, Steps 2-6
are pure computation/file I/O against files that all live inside this one
git repo (`pine/`, `dg_engine/`, `data_cache/`, `.venv/`). That means the
whole pipeline can run inside any Claude Code session that has this repo
checked out and internet access — including one triggered remotely (a
scheduled cloud task, or a session started from the Claude mobile app) —
without needing this specific Mac, TradingView Desktop, or the separate
`Claude-Tradeview AI/IDX Project` folder to be present at all. The only
manual step left is Step 0's occasional `pine/`/`dg_engine/` re-sync when
the user ships a new DG version from wherever they're iterating on it.

## Step 7 — Email the finished PDF (enabled 2026-09-18)

Emailing is now a standing step, confirmed by the user once recipient and
subject were fixed — no longer "on hold": send the finished PDF (attached)
to **ibnu.zaenal@gmail.com**, subject **`IDX Daily Screen - {tanggal}`**
(e.g. `IDX Daily Screen - 18 September 2026`), via whichever email-sending
tool/connector is available in the running session (a Gmail-style MCP
connector, if connected). If no email tool is available in a given session,
skip this step and say so plainly in the summary rather than failing the
whole run — the PDF/Artifact delivery from Steps 5-6 already covers the
user regardless. If the user ever names a different recipient or subject
format for a specific run, that overrides these defaults for that run only.
