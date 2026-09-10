---
name: watchlist-daily-report
description: Produce the full IDX watchlist analysis package — annotate every chart in TradingView (S/R, buy/sell zones, candle-pattern labels, entry markers, validated Double Top/Bottom shapes), publish one consolidated HTML report artifact, export it as a print-ready PDF, and (optionally) email it via Gmail through Claude in Chrome. Use when the user wants a daily/end-of-day technical screening report for their TradingView watchlist.
---

# Watchlist Daily Report

End-to-end procedure to turn a TradingView watchlist into an annotated chart set + one HTML report artifact + a clean PDF export. Follow the steps in order — each step's output feeds the next.

**Fast path for Steps 0-6:** `scripts/watchlist-annotate.mjs` in this repo implements the mechanical part of Steps 0-6 (connect, fetch OHLCV, classify the candlestick pattern, score volume/trend, compute S/R + entry zone, draw everything) as one deterministic Node script, reusing this repo's own `src/core` modules directly — no per-symbol tool-call orchestration needed. Prefer it over manually repeating Steps 0-6 per symbol; it's faster and far less prone to the CDP/race-condition issues that come from hundreds of sequential tool calls.

```bash
node scripts/watchlist-annotate.mjs                                   # full watchlist
node scripts/watchlist-annotate.mjs --extra=INTP,SMGR,INCO,TINS,JPFA,GGRM,MIKA,IDX:SILO   # + Step 5 screening set
node scripts/watchlist-annotate.mjs --symbols=BBCA,BMRI --clear       # explicit symbols, wipe old drawings first
node scripts/watchlist-annotate.mjs --json > /tmp/watchlist.jsonl     # machine-readable only, for scripting
```

It prints one JSON object per symbol to stdout (`{symbol, price, change_pct, support, resistance, pattern, volume, entry_type, entry_low, entry_high, score}`) — use this as the data source for Step 7's report cards instead of re-deriving everything by hand. It does **not** attempt genuine Double Top/Bottom/H&S validation (still a manual/Claude judgment call per Step 2.2) and does not touch the HTML report, PDF, or email — those stay separate steps below. Read the file's own header comment for full flag documentation.

## Step 0 — Connect

`tv_health_check`. If it fails: `tv_launch` (kills any CDP-less instance and relaunches with debugging enabled). Confirm `chart_resolution` is `"D"` via `chart_get_state`; if not, `chart_set_timeframe("D")`.

## Step 1 — Get the universe and today's closes

`watchlist_get` → gives every symbol's final closing price + change%. This is the authoritative price source for the whole report; don't hand-type prices from memory.

## Step 2 — Per-symbol technical data

For **every** symbol (loop `chart_set_symbol` → fetch):

1. `data_get_ohlcv(summary:true, count:90)` on Daily → gives period high/low (for support/resistance), open/close/change, and the **last 5 daily bars** (for candlestick-pattern reading).
2. `data_get_ohlcv(count:14-16)` on **Weekly** (temporarily `chart_set_timeframe("W")`) → only for symbols where the daily shape suggests a possible double top/bottom (a clean two-touch W or M in the recent swing). Identify: two comparable extremes (within ~5%) separated by an interim swing of the opposite kind. Cross-check candidates against the chart's built-in `Chart Patterns [FEELS]` indicator via `data_get_pine_labels(study_filter:"Chart Patterns")` — if its own Double Top/Bottom/H&S label sits near the same price, that's confirmation. Switch back to `chart_set_timeframe("D")` when done.
3. `data_get_ohlcv(count:10-15)` on **30-min** (`chart_set_timeframe("30")`) for a same-day intraday read — this refines the entry price to the current session's actual structure (e.g. "closed at session high, ascending staircase" vs "broke down through support intraday"). Switch back to Daily afterward.

From this data, determine for each symbol:
- **Support / Resistance**: nearest genuine swing low/high (not the full 90-day extreme unless price is still near it).
- **Candlestick pattern**: name it precisely (Bullish/Bearish Marubozu, Bullish/Bearish Engulfing, Hammer, Hanging Man, Shooting Star, Doji, Spinning Top, Piercing Line) from the last 1-2 daily bars.
- **Volume verdict**: compare volume on the signal candle(s) to the recent average — Akumulasi (high vol supporting the move) / Distribusi (high vol against it) / Netral (no signal, or volume drying up in a range).
- **Buy/sell zone**: a narrow price range tied to the pattern (retest of a marubozu's open, the low of an engulfing candle, a consolidation floor/ceiling) — not just the raw S/R level.

## Step 3 — Score each symbol (3-factor confirmation model)

Score three independent factors, each **-2 to +2**: **Candlestick**, **Volume**, **Trend**. Sum = -6..+6.

- Verdict is **Strong Buy/Strong Sell** only when all three factors agree in direction.
- Verdict is plain **Buy/Sell** when two agree and the third is neutral (not opposing).
- Any time factors conflict (e.g. strong bullish candle but volume does NOT confirm) — mark it **"Netral (konflik!)"** or **"Waspada"** explicitly and say so in the note, regardless of what the raw sum looks like. Never let one flashy factor override a disagreeing one.

## Step 4 — Annotate every chart

Per symbol, on the Daily chart, draw (`draw_shape`):

| Element | Shape | Style |
|---|---|---|
| Support | `horizontal_line` | `#2196F3`, width 2, `linestyle:2` (dashed) |
| Resistance | `horizontal_line` | `#F44336`, width 2, `linestyle:2` |
| Buy zone | `rectangle` | `color/backgroundColor:"#22c55e"`, `transparency:80` — span ~12 days back to ~5 days forward of the last bar so it's visible |
| Sell zone | `rectangle` | `color/backgroundColor:"#f97316"`, `transparency:80`, same time span |
| Candlestick pattern label | `text` | `fontsize:10`, `bold:true`, color as `rgba(...)` at **0.5–0.7 alpha** (green `22,163,74` bullish / red `220,38,38` bearish-alert / brown `138,109,31` neutral/doji / dark-orange `194,65,12` caution) — placed just above/below the signal candle |
| Entry marker | `text` | Same style family; text reads `● ENTRY BUY ▲ <zone>`, `● ENTRY SELL ▼ <zone>`, `● NETRAL — TUNGGU`, or `⚠ WASPADA — <why>` |

**For a genuinely validated Double Top/Bottom** (from Step 2.2): draw it as a **complete W/M**, not just the middle V — four segments as separate `trend_line` calls: lead-in (a point before the first extreme) → extreme 1 → neckline extreme → extreme 2 → lead-out (a point after, showing the breakout/continuation or the current price if unconfirmed). Add a dashed neckline (`horizontal_line`/`trend_line`, extended to current bar) and point-labels ("Bottom 1 (price)", "Bottom 2 (price)", "Neckline ~X") plus one bold summary label ("DOUBLE BOTTOM ✓ Breakout — Bullish" or "Double Top (belum konfirmasi) — Waspada"). Purple `#a855f7` for bottoms, orange `#f97316` for tops.

Always verify the chart is on the intended symbol (`chart_get_state`) right before drawing if there's any chance the shared session moved — re-set the symbol if not.

## Step 5 — Screen additional big-cap candidates (outside the watchlist)

Filter: large-cap only, share price strictly between ~Rp1.000 and ~Rp30.000 (drop anything outside that band). Pick names from sectors not already covered by the watchlist. For each survivor, repeat Steps 2–4 exactly (same data pull, same scoring, same chart annotations) so they get identical treatment to the core watchlist. Use `chart_set_symbol("IDX:<TICKER>")` (explicit exchange prefix) for any ticker that risks resolving to a foreign listing of the same code.

## Step 6 — IHSG gets a market-condition gauge, not a stock card

Don't analyze the composite index as if it were a stock. Instead:
- Give it a dedicated **market condition gauge** at the very top of the report: current level + change, support/resistance, one trend/pattern sentence, and two short bullet lists — **Faktor Positif** / **Faktor Negatif** — covering foreign fund flow, BI Rate / Fed policy, domestic policy news, geopolitical risk, and MSCI/FTSE index-weight changes (pull these via a background research agent with WebSearch if current data is needed; keep each bullet dated).
- In the sector-level "Indeks & Valas" analysis card slot, use **USD/IDR** instead (fetch/score/annotate it exactly like any other instrument in Steps 2–4).

## Step 7 — Build the HTML report artifact

One HTML file, this section order:
1. Masthead (title, date, timeframe note)
2. IHSG market-condition gauge (Step 6)
3. "Ringkasan Entry — Sekilas" — a chip grid, one per symbol: ticker **+ last close price**, colored Buy/Sell/Waspada/Netral tag, entry zone, one-line reason. Must match the chart markers exactly.
4. Short method box explaining the 3-factor model in plain language.
5. Scoreboard table: Symbol | Candle | Volume | Trend | Total score | Verdict | Conflict note.
6. Sector-grouped full cards (one per symbol): ticker/name/price/change, trend badge, support/resistance row, candle-pattern line, a bordered sub-block with Volume verdict / Pattern name / Entry price, and a one-line dated catalyst.
7. Screening section (Step 5) in the same card format.
8. Footer: methodology note + a clear disclaimer that this is technical analysis for personal reference, not personalized financial advice.

Design: warm paper palette (`#f6f3ec` light / `#14120e` dark, full `prefers-color-scheme` + `data-theme` support), a display serif for headings (e.g. Fraunces) + a plain sans for body + a mono face for tickers/numbers/labels, tabular-nums on all price columns. Publish via the Artifact tool.

## Step 8 — Export to PDF

1. Wrap the artifact's HTML file in a full document: `<!doctype html><html data-theme="light"><head>...</head><body>{content}</body></html>` — forcing light theme regardless of system setting.
2. Inject print CSS: `*{print-color-adjust:exact;} body{zoom:0.7;} @page{size:A4; margin:5mm;} .wrap{max-width:none!important;}` plus `break-inside:avoid` on individual cards/chips/gauge box (**not** on the long scoreboard table — let it paginate naturally, just put `break-inside:avoid` on its `<tr>` so no row splits) and reduce the table's `min-width` to `0` so it doesn't force overflow at the smaller zoom.
3. Render with a local Chromium-family browser in headless mode:
   `"<browser binary>" --headless --disable-gpu --no-sandbox --print-to-pdf="<output.pdf>" --print-to-pdf-no-header --no-pdf-header-footer --virtual-time-budget=15000 "file://<wrapped.html>"`
4. **Verify before delivering**: render 3–4 pages to PNG (`pdftoppm -png -r 100 -f <n> -l <n>`) and actually look at them — check for large blank gaps (usually a `break-inside:avoid` fight on something too tall) and for content bleeding into the page margin. Fix and re-render rather than shipping unseen.
5. Name the file so it's self-identifying by generation time: `IDX Daily Screener : {DD Month YYYY}, {HH.MM}.pdf` (regenerate this timestamp fresh each run — don't reuse an old one).
6. Deliver with `SendUserFile`.

## Step 9 — Email it (optional, only if the user asks for delivery)

There is no standing email/WhatsApp/Telegram connector — the only path is the user's real, logged-in Gmail via the **Claude in Chrome** extension. If it's not connected, `tabs_context_mcp` returns a "not connected" error — tell the user to install the extension and sign in with the same account, then retry. Never attempt this through the sandboxed Browser pane (`mcp__Claude_Browser__*`); that's a different, non-logged-in browser.

1. **Always confirm the recipient with the user first** — never guess or reuse a previous address without asking.
2. `tabs_context_mcp(createIfEmpty:true)` → `navigate` the tab to `https://mail.google.com/mail/u/0/#inbox?compose=new` (this opens straight into a compose window).
3. Fill fields with `find` to locate refs, then `form_input`/`computer`:
   - `find("To recipients field")` → `form_input` the recipient email.
   - Subject `form_input` = the exact PDF filename (no `.pdf` extension needed, matches what the user will see attached).
   - Click the Message Body ref, `computer type` the message — include the artifact's HTML link in the body, labeled with the PDF filename, plus a line noting the PDF is attached.
4. **Attach the PDF — never click the paperclip/"Attach files" button directly**, it opens a native OS file dialog the tools can't see. Instead `find("file input for attaching files")` to get the hidden `<input type=file>` ref, then `file_upload(ref, tabId, paths:["<absolute path to the PDF>"])`.
5. **Before sending**, screenshot the compose window and re-verify with `find("recipient chip in To field")` that there is exactly **one** correct recipient chip — Gmail's To field can visually show a name+email pair that looks like two entries; confirm via `find` rather than trusting the raw screenshot text.
6. Click **Send**, then screenshot to confirm the "Message sent" toast appears.
7. `tabs_close_mcp` the compose tab when done.
