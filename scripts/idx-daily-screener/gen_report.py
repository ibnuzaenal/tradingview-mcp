#!/usr/bin/env python3
"""
Generates the IDX Daily Screener HTML report from a single JSONL data file
(one JSON object per line, produced by ../watchlist-read-idxa.mjs).

Usage:
    python3 gen_report.py [input.jsonl] [output.html]

Defaults: input=/tmp/idxa-data.jsonl, output=<script_dir>/idxa_screening_report.html
categories.json / macro_facts.json / macro_reads.json are always read from
this script's own directory, so the whole idx-daily-screener/ folder is
self-contained and can be copied/reused as-is.
"""
import json, html, datetime, sys, os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IN_PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/idxa-data.jsonl"
OUT_PATH = sys.argv[2] if len(sys.argv) > 2 else os.path.join(SCRIPT_DIR, "idxa_screening_report.html")

rows = [json.loads(l) for l in open(IN_PATH) if l.strip()]

def bare(sym):
    return sym.split(':')[-1]

def fmt_price(p):
    if p is None:
        return "—"
    if p == int(p):
        return f"{int(p):,}".replace(",", ".")
    return f"{p:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

import re

# DECISION codes come straight from the "IDX Analyzer Dev" indicator's own
# dashboard table (see scripts/watchlist-read-idxdev.mjs) — this maps them
# to the same 6-tier bull/bear coloring used everywhere else in the report.
def action_class(decision):
    d = (decision or "").upper()
    if d.startswith("BUY-") or d in ("TP1", "TP2", "TP-FINAL"):
        return "bull-strong"
    if d in ("PRE-BUY", "HOLD"):
        return "bull"
    if d in ("CL", "TSTOP-X", "BOS-X", "FBO-X", "TIME-X"):
        return "bear-strong"
    if d in ("RED50", "RED25"):
        return "bear"
    if d in ("NO-BUY", "EXT", "NO-T", "HOLD?"):
        return "neutral-warn"
    return "neutral"  # WAIT-*, NO-RR, and anything unrecognized

ARROW = {"up": "&#9650;", "down": "&#9660;", "flat": "&#8596;", "warn": "&#9888;"}
RECO_RANK = {"bull-strong": 0, "bull": 1, "neutral": 2, "neutral-warn": 3, "bear": 4, "bear-strong": 5}
RECO_LABEL = {"bull-strong": "BUY", "bull": "BUY", "neutral": "WAIT", "neutral-warn": "WAIT", "bear": "SELL", "bear-strong": "SELL"}

def parse_score(score_str):
    """'6/10' -> 6, or None."""
    if not score_str:
        return None
    m = re.match(r"(\d+)\s*/\s*10", str(score_str))
    return int(m.group(1)) if m else None

# No native A+/B/C grade in the Dev V2.4-lineage indicator (that was a PRO v6
# concept) — a straightforward tier derived from its own 0-10 score, not a
# re-analysis. The DG2.7+ family DOES ship its own letter grade (gradePro,
# read straight from the dashboard) — grade_display() below prefers that.
def grade_from_score(score):
    if score is None:
        return "—", "neutral"
    if score >= 8:
        return "A+ — PRIME", "bull-strong"
    if score >= 6:
        return "A — GOOD", "bull"
    if score >= 4:
        return "B — WAIT", "neutral"
    return "C — LOW", "neutral-warn"

def grade_display(d, score):
    """Prefer the indicator's OWN grade (DG2.7's gradePro) when present;
    fall back to the report-side score bucket for the older Dev V2.4 schema."""
    g = d.get("grade")
    if not g:
        return grade_from_score(score)
    gu = g.upper()
    if gu.startswith("A+"):
        return g, "bull-strong"
    if gu.startswith("A"):
        return g, "bull"
    if gu.startswith("B"):
        return g, "neutral"
    return g, "neutral-warn"

def bias_cls(label):
    l = (label or "").upper()
    if "BULL" in l or "BUY" in l or "AKUMULASI" in l:
        return "bull"
    if "BEAR" in l or "SELL" in l or "AVOID" in l or "DISTRIBUSI" in l:
        return "bear"
    return "neutral"

def key_params_html(d):
    """'Parameter Kunci' panel — the model-internal drivers behind the
    DECISION/score above (Decision Gate 2.0/3.0, AI Forecast 2.0, MTF/Market
    alignment, Bandarmologi, EWS Detection, volatility regime). Only
    rendered for the DG2.7+ schema (absent entirely on older Dev V2.4-sourced
    rows, detected via the presence of dg3_dir); the EWS Detection chip
    itself is only shown when the JSONL row carries ews_state (DG3.4+ rows)."""
    if not d.get("dg3_dir"):
        return ""
    items = []
    items.append(("Gate 2.0", f'{d.get("gate2_dir","—")} &middot; {d.get("gate2_tier","—")} ({d.get("gate2_conviction","—")})', bias_cls(d.get("gate2_dir"))))
    items.append(("Decision Gate 3.0", f'{d.get("dg3_dir","—")} p={d.get("dg3_prob","—")}%', bias_cls(d.get("dg3_dir"))))
    items.append(("AI Forecast 2.0", f'{d.get("af2_dir","—")} p={d.get("af2_prob","—")}%', bias_cls(d.get("af2_dir"))))
    items.append(("MTF/Market", f'{d.get("mtf_bias","—")} / {d.get("market_bias","—")}', bias_cls(d.get("mtf_bias"))))
    bandar = d.get("bandar_flow", "—")
    if d.get("bandar_anomaly"):
        bandar += " &#9888; anomali"
    items.append(("Bandarmologi", bandar, bias_cls(d.get("bandar_flow"))))
    if d.get("ews_state") is not None:
        ews_val = f'{d.get("ews_state","—")} &middot; Bull {d.get("ews_bull_risk","—")}% Bear {d.get("ews_bear_risk","—")}%'
        if (d.get("ews_panic_index") or 0) >= 65:
            ews_val += " &#9888; Panic"
        items.append(("EWS Detection", ews_val, bias_cls(d.get("ews_state"))))
    vol_bits = []
    if d.get("hurst_regime"):
        vol_bits.append(str(d["hurst_regime"]))
    if d.get("ewma_vol_pctile") is not None:
        vol_bits.append(f'Vol p{int(d["ewma_vol_pctile"])}')
    items.append(("Volatilitas", " &middot; ".join(vol_bits) or "—", "neutral"))
    chips = "".join(
        f'<span class="kp-chip {cls}"><em>{html.escape(lbl)}</em>{val}</span>' for lbl, val, cls in items
    )
    return f'<div class="key-params">{chips}</div>'

def parse_rvol(rvol_str):
    """'0.58x / NEUTRAL' -> (0.58, 'NEUTRAL')."""
    if not rvol_str:
        return None, None
    m = re.match(r"([\d.]+)x\s*/\s*(\w+)", str(rvol_str))
    if not m:
        return None, None
    return float(m.group(1)), m.group(2)

def parse_pct_field(s):
    """'3.55%' -> 3.55, or None."""
    if not s:
        return None
    try:
        return float(str(s).replace("%", "").replace(",", "."))
    except ValueError:
        return None

def fcst_dir_cls(direction):
    d = (direction or "").upper()
    if d == "UP":
        return "naik", "up"
    if d == "DOWN":
        return "turun", "down"
    return None, "flat"

def win_rate_chips(dsh):
    """Reads the 4 chart-pattern win-rate fields, each formatted 'hits/total pct%'."""
    parts = []
    for name, key in (("Double Top", "double_top"), ("Double Bottom", "double_bottom"),
                       ("Head & Shoulders", "head_shoulders"), ("Inverse H&S", "inverse_hs")):
        v = dsh.get(key)
        if not v:
            continue
        m = re.match(r"(\d+)/(\d+)\s+(\d+)%", v)
        if m:
            parts.append({"name": name, "hits": m.group(1), "total": m.group(2), "pct": int(m.group(3))})
    return parts

composite = next((r for r in rows if bare(r["symbol"]) == "COMPOSITE"), None)
stocks = [r for r in rows if bare(r["symbol"]) not in ("COMPOSITE", "XAUUSD") and r.get("dashboard")]

# This indicator's dashboard has a single 0-10 BUY-conviction score, not a
# symmetric bull-vs-bear pair — so "which tier is this stock in" (from its
# own DECISION field) is the primary sort/grouping key, and the score is
# only a tie-breaker within a tier. Precomputed once per stock, reused by
# sorting, category grouping, and the summary table below.
for r in stocks:
    d = r.get("dashboard") or {}
    r["_score"] = parse_score(d.get("score")) or 0
    r["_acls"] = action_class(d.get("decision"))
    r["_rank"] = RECO_RANK[r["_acls"]]

stocks.sort(key=lambda r: (r["_rank"], -r["_score"]))

categories = json.load(open(os.path.join(SCRIPT_DIR, "categories.json")))
STOCK_INDEX_STATUS = json.load(open(os.path.join(SCRIPT_DIR, "macro_facts.json"))).get("stock_index_status", {})

def index_status_badges(sym):
    """Small MSCI/FTSE badge(s) next to a stock card's ticker — ONLY for
    tickers with a verified status in macro_facts.json's stock_index_status
    (see its own _note key). in=green (masuk/masih terdaftar), watch=red
    (dalam evaluasi/downgrade), out=black (dikeluarkan)."""
    statuses = STOCK_INDEX_STATUS.get(sym)
    if not statuses:
        return ""
    cls_map = {"in": "idxbadge-in", "watch": "idxbadge-watch", "out": "idxbadge-out"}
    badges = "".join(
        f'<span class="idxbadge {cls_map.get(state, "idxbadge-watch")}" title="{html.escape(index_name)}: '
        f'{"masuk/masih terdaftar" if state == "in" else "dalam evaluasi" if state == "watch" else "dikeluarkan"}">'
        f'{html.escape(index_name)}</span>'
        for index_name, state in statuses.items()
    )
    return badges

category_of = {}
for cat, tickers in categories.items():
    for t in tickers:
        category_of[t] = cat

def category_resume(members):
    n = len(members)
    avg_score = sum(m["_score"] for m in members) / n if n else 0
    n_buy = sum(1 for m in members if m["_acls"] in ("bull", "bull-strong"))
    n_sell = sum(1 for m in members if m["_acls"] in ("bear", "bear-strong"))
    n_wait = n - n_buy - n_sell
    if n_buy - n_sell >= max(2, n * 0.3):
        tone, tone_cls = "cenderung BULLISH", "up"
    elif n_sell - n_buy >= max(2, n * 0.3):
        tone, tone_cls = "cenderung BEARISH", "down"
    else:
        tone, tone_cls = "cenderung NETRAL/campuran", "flat"
    return {"n": n, "avg_score": avg_score, "n_buy": n_buy, "n_wait": n_wait, "n_sell": n_sell, "tone": tone, "tone_cls": tone_cls}

grouped = {}
for r in stocks:
    cat = category_of.get(bare(r["symbol"]), "Lainnya")
    grouped.setdefault(cat, []).append(r)
for cat in grouped:
    grouped[cat].sort(key=lambda r: (r["_rank"], -r["_score"]))

category_order = [c for c in categories.keys() if c in grouped] + ([c for c in grouped if c not in categories])

# ---------------------------------------------------------------------
# Top Gainers / Top Losers — with a volume-trap read so a stock isn't
# flagged BUY/SELL just because it moved; the move needs volume behind it.
# ---------------------------------------------------------------------

for r in stocks:
    r["_chg"] = parse_pct_field(r.get("change_pct"))

rankable = [r for r in stocks if r["_chg"] is not None]
top_gainers = sorted(rankable, key=lambda r: r["_chg"], reverse=True)[:10]
top_losers = sorted(rankable, key=lambda r: r["_chg"])[:10]

def volume_reco(list_type, trap, rvol, acls):
    is_bull = acls in ("bull", "bull-strong")
    is_bear = acls in ("bear", "bear-strong")
    if trap and trap != "NONE":
        return ("TRAP", "warn", f"Indikator mendeteksi trap/anomali: {trap}")
    if rvol is not None and rvol <= 0.6:
        return ("TRAP", "warn", "Volume tipis — waspada jebakan")
    if list_type == "gainer":
        if is_bull and (rvol is None or rvol >= 1.0):
            return ("BUY", "buy", "Kenaikan didukung volume & skor bullish")
        return ("WASPADA", "warn", "Naik tapi skor/volume belum meyakinkan")
    else:
        if is_bear and (rvol is None or rvol >= 1.0):
            return ("SELL", "sell", "Penurunan didukung volume & skor bearish")
        return ("WASPADA", "warn", "Turun tapi bisa jadi shakeout, cermati")

def ews_dominant(d):
    """Dominant EWS Detection signal for one stock — the triggered Bull/Bear
    state when ewsState fired, else Panic when it clears its own (higher)
    vote threshold, else whichever of Bull-Risk/Bear-Risk currently reads
    higher (still sub-threshold, shown as informational-only)."""
    state = d.get("ews_state")
    bull, bear, panic = d.get("ews_bull_risk"), d.get("ews_bear_risk"), d.get("ews_panic_index")
    if bull is None or bear is None:
        return None
    if state == "BULL":
        return (f"EWS BULL {bull:.0f}%", "bull")
    if state == "BEAR":
        return (f"EWS BEAR {bear:.0f}%", "bear")
    if panic is not None and panic >= 65:
        return (f"EWS PANIC {panic:.0f}%", "warn")
    dom, val = ("BULL", bull) if bull >= bear else ("BEAR", bear)
    return (f"EWS {dom} {val:.0f}% (netral)", "neutral")

def gainer_loser_row(r, list_type):
    d = r.get("dashboard") or {}
    acls = r["_acls"]
    rvol, _flow = parse_rvol(d.get("rvol"))
    trap = d.get("trap")
    reco_label, reco_cls, reco_note = volume_reco(list_type, trap, rvol, acls)
    price = r.get("price")
    target = d.get("target2") if list_type == "gainer" else d.get("stop")
    proj = None
    if price and target:
        if list_type == "gainer" and target > price:
            proj = (target - price) / price * 100
        elif list_type == "loser" and target < price:
            proj = (target - price) / price * 100
    rvol_txt = f"{rvol:.1f}x" if rvol is not None else "—"
    proj_txt = f"{proj:+.1f}%" if proj is not None else "—"
    ews = ews_dominant(d)
    ews_html = f'<span class="gl-ews {ews[1]}">{html.escape(ews[0])}</span> ' if ews else ""
    return f'''<div class="gl-row gl-{reco_cls}">
      <span class="gl-reco {reco_cls}">{reco_label}</span>
      <span class="gl-ticker">{bare(r["symbol"])}</span>
      <span class="gl-chg {"up" if r["_chg"] >= 0 else "down"}"><em>Hari Ini</em> {r["_chg"]:+.2f}%</span>
      <span class="gl-vol">Vol {rvol_txt}</span>
      <span class="gl-proj"><em>Besok</em> Proyeksi {proj_txt}</span>
      <span class="gl-note">{ews_html}{html.escape(reco_note)}</span>
    </div>'''

gainers_html = "\n".join(gainer_loser_row(r, "gainer") for r in top_gainers)
losers_html = "\n".join(gainer_loser_row(r, "loser") for r in top_losers)

gainers_losers_section = f'''<section class="gl-section">
  <h2 class="section-title">Top Gainer &amp; Loser — Sinyal Besok</h2>
  <p class="section-sub">10 saham penguatan &amp; pelemahan terbesar hari ini, disaring dengan volume relatif (RVOL) untuk menghindari jebakan/trap broker sebelum dijadikan acuan transaksi besok. <b>Hari Ini</b> = pergerakan harga aktual hari ini (dasar perankingan top gainer/loser). <b>Besok</b> = proyeksi arah untuk sesi berikutnya (Target 2 untuk gainer, level Stop untuk loser) berdasarkan sinyal DECISION/volume saat ini — bukan jaminan, sesi berikutnya bisa berbeda.</p>
  <div class="gl-legend">
    <span><span class="gl-dot buy"></span>BUY — didukung volume</span>
    <span><span class="gl-dot sell"></span>SELL — didukung volume</span>
    <span><span class="gl-dot warn"></span>WASPADA/TRAP — volume tidak meyakinkan</span>
  </div>
  <div class="gl-two-col">
    <div class="gl-block">
      <h3 class="gl-block-title up">&#9650; Top 10 Gainer</h3>
      {gainers_html}
    </div>
    <div class="gl-block">
      <h3 class="gl-block-title down">&#9660; Top 10 Loser</h3>
      {losers_html}
    </div>
  </div>
</section>'''

# ---------------------------------------------------------------------
# Akumulasi & Distribusi Bertahap — Top 10 (3 hari terakhir) — ranks the
# whole watchlist by the Bandarmologi accScore/distScore averaged over the
# trailing 3 bars (acc_score_3d/dist_score_3d from dg_snapshot.py), rather
# than the single-bar accClass/distClass tag already shown on each stock's
# own card, so a sustained 3-day flow doesn't get lost next to a single
# outlier day.
# ---------------------------------------------------------------------
def acc_dist_row(r, list_type):
    d = r.get("dashboard") or {}
    score = d.get("acc_score_3d") if list_type == "acc" else d.get("dist_score_3d")
    days = d.get("acc_days_3d") if list_type == "acc" else d.get("dist_days_3d")
    rvol_avg = d.get("rvol_avg_3d")
    chg3d = d.get("chg_3d_pct")
    cls = "buy" if list_type == "acc" else "sell"
    score_txt = f"{score:.1f}" if score is not None else "—"
    days_txt = f"{days}/3 hari" if days is not None else "—"
    rvol_txt = f"{rvol_avg:.2f}x" if rvol_avg is not None else "—"
    chg_txt = f"{chg3d:+.2f}%" if chg3d is not None else "—"
    return f'''<div class="gl-row gl-{cls}">
      <span class="gl-reco {cls}">{score_txt}</span>
      <span class="gl-ticker">{bare(r["symbol"])}</span>
      <span class="gl-chg {"up" if (chg3d or 0) >= 0 else "down"}">{chg_txt}</span>
      <span class="gl-vol">RVOL {rvol_txt}</span>
      <span class="gl-proj">{days_txt}</span>
      <span class="gl-note">Skor {"akumulasi" if list_type == "acc" else "distribusi"} rata-rata 3 hari &middot; perubahan harga 3 hari &middot; jumlah hari tertandai {"AK" if list_type == "acc" else "DS"}&ge;1</span>
    </div>'''

top_acc = sorted(stocks, key=lambda r: (r.get("dashboard") or {}).get("acc_score_3d", float("-inf")), reverse=True)[:10]
top_dist = sorted(stocks, key=lambda r: (r.get("dashboard") or {}).get("dist_score_3d", float("-inf")), reverse=True)[:10]
acc_html = "\n".join(acc_dist_row(r, "acc") for r in top_acc)
dist_html = "\n".join(acc_dist_row(r, "dist") for r in top_dist)

acc_dist_section = f'''<div class="summary">
    <h2>Akumulasi &amp; Distribusi Bertahap — 3 Hari Terakhir</h2>
    <p class="section-sub">10 saham dengan skor akumulasi/distribusi rata-rata tertinggi dalam 3 hari transaksi terakhir (Bandarmologi CMF + RVOL) — sinyal aliran dana bertahap, bukan lonjakan satu hari.</p>
    <div class="gl-two-col">
      <div class="gl-block">
        <h3 class="gl-block-title up">&#9650; Top 10 Akumulasi</h3>
        {acc_html}
      </div>
      <div class="gl-block">
        <h3 class="gl-block-title down">&#9660; Top 10 Distribusi</h3>
        {dist_html}
      </div>
    </div>
  </div>'''

now = datetime.datetime.now()
gen_time = now.strftime("%d %B %Y, %H:%M WIB")

def card_html(r, is_index=False):
    sym = bare(r["symbol"])
    d = r.get("dashboard") or {}
    price = fmt_price(r.get("price"))
    chg = r.get("change_pct") or "—"
    chg_cls = "up" if (isinstance(chg, str) and not chg.startswith("-")) else "down"
    acls = r.get("_acls") or action_class(d.get("decision"))
    score = r.get("_score")
    if score is None:
        score = parse_score(d.get("score"))  # composite/IHSG never goes through the `stocks` precompute loop
    grade_txt, gcls = grade_display(d, score)
    candle = d.get("candle")
    trap = d.get("trap")
    chips = win_rate_chips(d)

    pattern_html = ""
    if candle and candle != "-":
        pattern_html = f'<div class="pattern-tag"><span>{html.escape(candle)}</span></div>'

    trap_html = ""
    if trap and trap != "NONE":
        trap_html = f'<div class="pattern-tag confirmed"><span>&#9888; Trap/Anomali: {html.escape(trap)}</span></div>'

    forecast_html = ""
    tr_dir, tr_cls = fcst_dir_cls(d.get("tr_dir"))
    ai_dir_, ai_cls = fcst_dir_cls(d.get("ai_dir"))
    lines = []
    if tr_dir:
        lines.append(f'<div class="ai-forecast {tr_dir}"><i class="dir">{ARROW[tr_cls]}</i>TR Forecast (20D): <b>{tr_dir.upper()}</b> {html.escape(d.get("tr_fcst_pct") or "")}<span class="conf">keyakinan {html.escape(d.get("tr_conf") or "—")}</span></div>')
    if ai_dir_:
        lines.append(f'<div class="ai-forecast {ai_dir_}"><i class="dir">{ARROW[ai_cls]}</i>AI Forecast: <b>{ai_dir_.upper()}</b> {html.escape(d.get("ai_fcst_pct") or "")}<span class="conf">keyakinan {html.escape(d.get("ai_conf") or "—")}</span></div>')
    forecast_html = "\n".join(lines)

    chips_html = ""
    if chips:
        chips_html = '<div class="winrate">' + "".join(
            f'<span class="chip" title="{c["name"]}"><b>{c["hits"]}/{c["total"]}</b> {html.escape(c["name"])} <em>{c["pct"]}%</em></span>' for c in chips
        ) + "</div>"

    levels_html = ""
    lv_items = []
    if d.get("support") is not None:
        lv_items.append(("Support", fmt_price(d["support"]), "support"))
    if d.get("resistance") is not None:
        lv_items.append(("Resistance", fmt_price(d["resistance"]), "resistance"))
    if d.get("entry") is not None:
        lv_items.append(("Entry", fmt_price(d["entry"]), "tp1"))
    if d.get("stop") is not None:
        lv_items.append(("Stop", fmt_price(d["stop"]), "cutloss"))
    if d.get("target2") is not None:
        lv_items.append(("Target 2", fmt_price(d["target2"]), "tp2"))
    if lv_items:
        levels_html = '<div class="levels">' + "".join(
            f'<div class="lvl {cls}"><span class="lvl-label">{lbl}</span><span class="lvl-val">{val}</span></div>' for lbl, val, cls in lv_items
        ) + "</div>"

    rvol, flow = parse_rvol(d.get("rvol"))
    score_disp = score if score is not None else 0
    bar_html = f'''<div class="score-bar" role="img" aria-label="Skor {score_disp} dari 10">
        <div class="seg bull" style="width:{score_disp/10*100:.1f}%"></div>
        <div class="seg neutral" style="width:{100-score_disp/10*100:.1f}%"></div>
    </div>
    <div class="score-nums"><span class="bull">Skor {score_disp}/10</span><span class="neutral">RVOL {html.escape(d.get("rvol") or "—")}</span></div>'''

    card_class = "card index-card" if is_index else "card"
    header_label = "IHSG — Kondisi Pasar" if is_index else sym
    badges_html = "" if is_index else index_status_badges(sym)

    return f'''<article class="{card_class}">
      <header class="card-head">
        <div class="ticker-row">
          <span class="ticker">{header_label}</span>{badges_html}
          <span class="price">{price}</span>
          <span class="chg {chg_cls}">{html.escape(str(chg))}</span>
        </div>
        <div class="pill {acls}">{html.escape(d.get("decision") or "—")}</div>
      </header>
      <div class="meta-row">
        <span class="meta"><em>Weekly</em>{html.escape(d.get("weekly") or "—")}</span>
        <span class="meta"><em>Daily</em>{html.escape(d.get("daily_trend") or "—")}</span>
        <span class="meta"><em>Struktur</em>{html.escape(d.get("structure") or "—")}</span>
        <span class="meta"><em>Setup</em>{html.escape(d.get("setup") or "—")}</span>
      </div>
      {pattern_html}
      {trap_html}
      {bar_html}
      <div class="grade-row"><span class="pill {gcls}">{html.escape(grade_txt)}</span></div>
      {key_params_html(d)}
      {levels_html}
      {forecast_html}
      {chips_html}
    </article>'''

index_card = card_html(composite, is_index=True) if composite else ""
def category_section_html(cat, members):
    res = category_resume(members)
    cards = "\n".join(card_html(r) for r in members)
    return f'''<div class="category-block">
      <div class="category-head">
        <h3>{html.escape(cat)} <span class="category-count">({res["n"]} saham)</span></h3>
        <span class="macro-trend {res["tone_cls"]}">{ARROW.get(res["tone_cls"], "&#8596;")} <b>{html.escape(res["tone"])}</b></span>
      </div>
      <div class="category-resume">Rata-rata skor <b class="{res["tone_cls"]}">{res["avg_score"]:.1f}/10</b> &middot; <span class="bull-strong">{res["n_buy"]} Buy</span> / <span class="neutral">{res["n_wait"]} Wait</span> / <span class="bear-strong">{res["n_sell"]} Sell</span></div>
      <div class="grid category-grid">
        {cards}
      </div>
    </div>'''

stock_sections = "\n".join(category_section_html(cat, grouped[cat]) for cat in category_order)

macro_reads = json.load(open(os.path.join(SCRIPT_DIR, "macro_reads.json")))
macro_facts = json.load(open(os.path.join(SCRIPT_DIR, "macro_facts.json")))

def trend_arrow(trend):
    t = (trend or "").upper()
    if "BULLISH" in t:
        return "up"
    if "BEARISH" in t:
        return "down"
    return "flat"

def macro_card_html(m):
    cls = trend_arrow(m["trend"])
    edge = m["score_bull"] - m["score_bear"]
    edge_cls = "bull" if edge > 8 else ("bear" if edge < -8 else "neutral")
    return f'''<div class="macro-card">
      <div class="macro-head">
        <span class="macro-label">{html.escape(m["label"])}</span>
        <span class="macro-trend {cls}">{ARROW[cls]} <b>{html.escape(m["trend"])}</b></span>
      </div>
      <div class="macro-sub"><span class="{edge_cls}">&#9679; Bull <b>{m["score_bull"]}</b> / Bear <b>{m["score_bear"]}</b></span></div>
      <p class="macro-key">{html.escape(m["key"])}</p>
    </div>'''

def policy_card_html(p):
    tone = "up" if p["arrow"] == "down" else "down"  # dovish(rate arrow down)=green tone; hawkish(rate arrow up)=red tone
    facts_html = "".join(f'<li>{html.escape(f)}</li>' for f in p["facts"])
    return f'''<div class="policy-card">
      <div class="policy-head">
        <span class="policy-label">{html.escape(p["label"])}</span>
        <span class="macro-trend {tone}">{ARROW[p["arrow"]]} <b>{html.escape(p["tag"])}</b></span>
      </div>
      <div class="policy-value">{html.escape(p["value"])}</div>
      <ul class="fact-list">{facts_html}</ul>
    </div>'''

macro_cards_html = "\n".join(macro_card_html(m) for m in macro_reads)
policy_cards_html = "\n".join(policy_card_html(p) for p in macro_facts["policy"])

def stat_chip(item):
    cls = item["arrow"]
    note = f' <span class="stat-note">{html.escape(item["note"])}</span>' if item.get("note") else ""
    return f'<div class="stat-chip"><span class="macro-trend {cls}">{ARROW[cls]}</span><b class="stat-value {cls}">{html.escape(item["value"])}</b><span class="stat-name">{html.escape(item["name"])}</span>{note}</div>'

domestic_items_html = "\n".join(stat_chip(it) for it in macro_facts["domestic"]["items"])

sentiment_stats_html = "\n".join(stat_chip(it) for it in macro_facts["sentiment"]["stats"])

def index_events_html(sentiment):
    ie = sentiment.get("index_events")
    if not ie:
        return ""
    status_cls = lambda s: "neutral-warn" if "akan datang" in s else "neutral"
    rows_html = "\n".join(
        f'''<tr>
          <td class="mono">{html.escape(r["symbol"])}</td>
          <td>{html.escape(r["index"])}</td>
          <td>{html.escape(r["action"])}</td>
          <td class="mono">{html.escape(r["effective"])}</td>
          <td><span class="pill small {status_cls(r["status"])}">{html.escape(r["status"])}</span></td>
          <td class="idx-impact">{html.escape(r["impact"])}</td>
        </tr>''' for r in ie["rows"]
    )
    other_html = "\n".join(
        f'<li><b>{html.escape(o["name"])}</b> — {o["timeline"]} <span class="idx-other-note">({o["note"]})</span></li>'
        for o in ie.get("other_ratings", [])
    )
    fl = ie.get("full_list")
    fl_html = ""
    if fl:
        fl_groups = "\n".join(
            f'''<li><span class="macro-trend {g["arrow"]}">{ARROW[g["arrow"]]}</span>
              <b>{html.escape(g["index"])}</b> — {html.escape(g["action"])}:
              {" ".join(f'<span class="pill small neutral">{html.escape(s)}</span>' for s in g["symbols"])}
            </li>''' for g in fl["groups"]
        )
        fl_html = f'''<div class="idx-full-list">
          <h4 class="idx-events-title">{fl["label"]}</h4>
          <ul class="idx-full-list-groups">{fl_groups}</ul>
          <p class="idx-note"><b>{html.escape(fl["no_additions_note"])}</b></p>
          <p class="idx-note">{html.escape(fl["incomplete_note"])}</p>
        </div>'''
    cw = ie.get("correction_watch")
    cw_html = ""
    if cw:
        cw_items = "\n".join(
            f'<li><span class="pill small {"bear" if it["in_watchlist"] else "neutral"}">{html.escape(it["symbol"])}</span>'
            f'{" <em>(watchlist)</em>" if it["in_watchlist"] else " <em>(luar watchlist)</em>"} — {html.escape(it["reason"])}</li>'
            for it in cw["items"]
        )
        cw_html = f'''<div class="idx-correction-watch">
          <h4 class="idx-events-title">{html.escape(cw["label"])}</h4>
          <p class="idx-note">{html.escape(cw["note"])}</p>
          <ul class="idx-correction-list">{cw_items}</ul>
          <p class="idx-note">{html.escape(cw["rotation_note"])}</p>
        </div>'''
    trend_html = f'<p class="idx-trend">{ie["trend"]}</p>' if ie.get("trend") else ""
    return f'''<div class="idx-events">
      <h4 class="idx-events-title">{html.escape(ie["label"])}</h4>
      <div class="table-wrap"><table class="idx-events-table">
        <thead><tr><th>Kode</th><th>Indeks</th><th>Aksi</th><th>Efektif</th><th>Status</th><th>Estimasi Dampak</th></tr></thead>
        <tbody>{rows_html}</tbody>
      </table></div>
      {trend_html}
      {fl_html}
      {cw_html}
      <ul class="idx-other-ratings">{other_html}</ul>
    </div>'''

sentiment_index_events_html = index_events_html(macro_facts["sentiment"])

def fomo_panic_html(fp, stock_list):
    if not fp:
        return ""
    fomo_vals = [(r.get("dashboard") or {}).get("ews_fomo_index") for r in stock_list]
    panic_vals = [(r.get("dashboard") or {}).get("ews_panic_index") for r in stock_list]
    fomo_vals = [v for v in fomo_vals if v is not None]
    panic_vals = [v for v in panic_vals if v is not None]
    states = [(r.get("dashboard") or {}).get("ews_state") for r in stock_list]
    n = len(fomo_vals) or 1
    avg_fomo = sum(fomo_vals) / n
    avg_panic = sum(panic_vals) / (len(panic_vals) or 1)
    n_fomo_hot = sum(1 for v in fomo_vals if v >= 65)
    n_panic_hot = sum(1 for v in panic_vals if v >= 65)
    n_bull = states.count("BULL")
    n_bear = states.count("BEAR")
    n_neutral = states.count("NETRAL")

    def level(avg, hot_n, total):
        if avg >= 60 or hot_n / total >= 0.25:
            return "TINGGI", "bear"
        if avg >= 45 or hot_n / total >= 0.10:
            return "MODERAT", "neutral-warn"
        return "RENDAH", "bull"

    fomo_level, fomo_cls = level(avg_fomo, n_fomo_hot, n)
    panic_level, panic_cls = level(avg_panic, n_panic_hot, n)
    skew = "BEARISH" if n_bear > n_bull else ("BULLISH" if n_bull > n_bear else "NETRAL")
    skew_cls = "bear" if skew == "BEARISH" else ("bull" if skew == "BULLISH" else "neutral")

    catalysts_html = "\n".join(
        f'<li><span class="macro-trend {c["arrow"]}">{ARROW[c["arrow"]]}</span> {c["text"]}</li>' for c in fp["catalysts"]
    )

    def top_n_syms(key, n=6):
        ranked = sorted(
            (r for r in stock_list if (r.get("dashboard") or {}).get(key) is not None),
            key=lambda r: r["dashboard"][key], reverse=True,
        )
        return [(bare(r["symbol"]), r["dashboard"][key]) for r in ranked[:n]]

    def sym_pills(pairs, cls, arrow):
        return " ".join(
            f'<span class="pill small {cls}">{ARROW[arrow]} {html.escape(s)} {v:.0f}%</span>' for s, v in pairs
        )

    hawkish_syms = sym_pills(top_n_syms("ews_panic_index"), "bear", "down")
    dovish_syms = sym_pills(top_n_syms("ews_fomo_index"), "bull", "up")

    return f'''<div class="macro-block wide">
      <h3>{html.escape(fp["label"])}</h3>
      <div class="stat-row">
        <div class="stat-chip"><span class="macro-trend {panic_cls}">{ARROW["warn"]}</span><b class="stat-value {panic_cls}">PANIC: {panic_level}</b><span class="stat-name">rata-rata {avg_panic:.1f}, {n_panic_hot}/{n} saham &ge;65</span></div>
        <div class="stat-chip"><span class="macro-trend {fomo_cls}">{ARROW["warn"] if fomo_cls=="bear" else ARROW["up"] if fomo_cls=="bull" else ARROW["flat"]}</span><b class="stat-value {fomo_cls}">FOMO: {fomo_level}</b><span class="stat-name">rata-rata {avg_fomo:.1f}, {n_fomo_hot}/{n} saham &ge;65</span></div>
        <div class="stat-chip"><span class="macro-trend {skew_cls}">{ARROW["down"] if skew_cls=="bear" else ARROW["up"] if skew_cls=="bull" else ARROW["flat"]}</span><b class="stat-value {skew_cls}">Bias EWS State: {skew}</b><span class="stat-name">{n_bear} Bear / {n_bull} Bull / {n_neutral} Netral dari {n} saham</span></div>
      </div>
      <ul class="geo-list">{catalysts_html}</ul>
      <p class="block-resume"><span class="pill small bear">Skenario Hawkish</span> {fp["scenario_hawkish"]}</p>
      <p class="idx-note"><b>Saham dengan risiko Panic Index tertinggi saat ini (paling rentan tertekan lanjut):</b><br>{hawkish_syms}</p>
      <p><span class="pill small bull">Skenario Dovish</span> {fp["scenario_dovish"]}</p>
      <p class="idx-note"><b>Saham dengan FOMO Index tertinggi saat ini (paling berpotensi lanjut momentum naik):</b><br>{dovish_syms}</p>
      <p class="idx-note">{html.escape(fp["note"])}</p>
    </div>'''

sentiment_fomo_panic_html = fomo_panic_html(macro_facts.get("fomo_panic"), stocks)

def market_review_html(mr):
    if not mr:
        return ""
    items_html = "\n".join(
        f'<li><span class="macro-trend {it["arrow"]}">{ARROW[it["arrow"]]}</span> '
        f'<a href="{html.escape(it["url"])}" target="_blank" rel="noopener">{html.escape(it["headline"])}</a> '
        f'<span class="idx-other-note">({html.escape(it["source"])})</span></li>'
        for it in mr["items"]
    )
    return f'''<div class="macro-block wide">
      <h3>{html.escape(mr["label"])}</h3>
      <ul class="geo-list market-review-list">{items_html}</ul>
      <p class="idx-note">{html.escape(mr["sources_note"])}</p>
    </div>'''

market_review_section_html = market_review_html(macro_facts.get("market_review"))

def recommendations_html(rec, stock_list):
    if not rec:
        return ""
    by_symbol = {bare(r["symbol"]): r for r in stock_list}

    def own_read(sym):
        r = by_symbol.get(sym)
        if not r:
            return ""
        d = r.get("dashboard") or {}
        acls = r.get("_acls") or action_class(d.get("decision"))
        return f' &middot; analisa sendiri: <span class="pill small {acls}">{html.escape(d.get("decision") or "—")}</span> {html.escape(str(r.get("change_pct") or ""))}'

    def rec_row(it, consistent):
        sym = it["symbol"]
        src_badges = " ".join(f'<span class="idx-other-note">{html.escape(s)}</span>' for s in it["sources"])
        cls = "bull-strong" if consistent else "neutral"
        note = f' — {html.escape(it["note"])}' if it.get("note") else ""
        return (f'<li><span class="pill small {cls}">{"KONSISTEN" if consistent else "1 sumber"}</span> '
                f'<b class="mono">{html.escape(sym)}</b>{note} — {src_badges}{own_read(sym)}</li>')

    consistent_html = "\n".join(rec_row(it, True) for it in rec["consistent"])
    single_html = "\n".join(rec_row(it, False) for it in rec.get("single_source", []))
    return f'''<div class="macro-block wide">
      <h3>{html.escape(rec["label"])}</h3>
      <p class="idx-note">{html.escape(rec["note"])}</p>
      <ul class="rec-list">{consistent_html}</ul>
      {f'<p class="idx-events-title">Rekomendasi Satu Sumber</p><ul class="rec-list">{single_html}</ul>' if single_html else ''}
      <p class="idx-note">{html.escape(rec["sources_note"])}</p>
    </div>'''

recommendations_section_html = recommendations_html(macro_facts.get("recommendations"), stocks)

sources_html = " &middot; ".join(html.escape(s) for s in macro_facts["sources"])

macro_section = f'''<section class="macro-section">
  <h2 class="section-title">Faktor Makro &amp; Sentimen Pasar</h2>
  <p class="section-sub">Kurs, komoditas &amp; bursa global — snapshot berkala, bukan data real-time; kebijakan &amp; sentimen dirangkum dari sumber publik per {html.escape(macro_facts["as_of"])}.</p>

  <div class="macro-grid">
    {macro_cards_html}
  </div>

  <div class="policy-grid">
    {policy_cards_html}
  </div>

  <div class="macro-block wide">
    <h3>{html.escape(macro_facts["domestic"]["label"])}</h3>
    <div class="stat-row">{domestic_items_html}</div>
  </div>

  {market_review_section_html}

  <div class="macro-block wide">
    <h3>{html.escape(macro_facts["sentiment"]["label"])}</h3>
    <div class="stat-row">{sentiment_stats_html}</div>
    <p class="macro-resume block-resume"><b>{html.escape(macro_facts["sentiment"]["resume"])}</b></p>
    {sentiment_index_events_html}
  </div>

  {sentiment_fomo_panic_html}

  <p class="macro-sources">Sumber: {sources_html}</p>
</section>'''

summary_stocks = sorted(stocks, key=lambda r: (r["_rank"], -r["_score"]))

def ak3_ds3_box_html(stock_list):
    """AK3 (akumulasi tier tertinggi) / DS3 (distribusi tier tertinggi) yang
    muncul di salah satu dari 3 bar terakhir — hanya saham yang benar-benar
    kena sinyal ini yang ditampilkan (bukan seluruh watchlist)."""
    hits = [r for r in stock_list if (r.get("dashboard") or {}).get("ak3_ds3_type")]
    if not hits:
        return ""
    ak3 = [r for r in hits if r["dashboard"]["ak3_ds3_type"] == "AK3"]
    ds3 = [r for r in hits if r["dashboard"]["ak3_ds3_type"] == "DS3"]

    def row(r):
        d = r["dashboard"]
        is_ak3 = d["ak3_ds3_type"] == "AK3"
        cls = "bull-strong" if is_ak3 else "bear-strong"
        days = d["ak3_ds3_days_ago"]
        when = "hari ini" if days == 0 else f"{days} hari lalu"
        proj = d.get("ak3_ds3_proj_price")
        proj_txt = fmt_price(proj) if proj is not None else "—"
        bos = d.get("ak3_ds3_bos")
        bos_cls = "bull" if bos == "BOS+" else ("bear" if bos == "BOS-" else None)
        bos_html = f' <span class="pill small {bos_cls}">{bos}</span>' if bos else ''
        return (f'<li><span class="pill small {cls}">{d["ak3_ds3_type"]}</span>{bos_html} '
                f'<b class="mono">{html.escape(bare(r["symbol"]))}</b> ({when}) &rarr; '
                f'Proyeksi <b>{d["ak3_ds3_proj_label"]}</b> @ <span class="mono">{proj_txt}</span></li>')

    ak3_html = "\n".join(row(r) for r in ak3) or '<li class="idx-note">Tidak ada.</li>'
    ds3_html = "\n".join(row(r) for r in ds3) or '<li class="idx-note">Tidak ada.</li>'

    candidates = [r for r in stock_list if (r.get("dashboard") or {}).get("ak3_ds3_candidate_type")]
    cand_ak3 = [r for r in candidates if r["dashboard"]["ak3_ds3_candidate_type"] == "AK3"]
    cand_ds3 = [r for r in candidates if r["dashboard"]["ak3_ds3_candidate_type"] == "DS3"]

    def cand_row(r):
        d = r["dashboard"]
        cls = "bull" if d["ak3_ds3_candidate_type"] == "AK3" else "bear"
        return (f'<li><span class="pill small {cls}">{d["ak3_ds3_candidate_type"]}?</span> '
                f'<b class="mono">{html.escape(bare(r["symbol"]))}</b> — {html.escape(d["ak3_ds3_candidate_note"])}</li>')

    cand_ak3_html = "\n".join(cand_row(r) for r in cand_ak3) or '<li class="idx-note">Tidak ada.</li>'
    cand_ds3_html = "\n".join(cand_row(r) for r in cand_ds3) or '<li class="idx-note">Tidak ada.</li>'

    return f'''<div class="ak3ds3-box">
      <h3 class="idx-events-title">Sinyal AK3/DS3 — 3 Hari Terakhir</h3>
      <p class="idx-note">AK3/DS3 adalah tier akumulasi/distribusi TERTINGGI Bandarmologi (accClass/distClass = 3, elite()) — sinyal volume+struktur paling kuat, bukan sekadar CMF positif/negatif biasa. Proyeksi BUY memakai Target 2 kartu saham terkait, proyeksi SELL memakai level Stop-nya — angka yang sama, bukan model baru. BOS+/BOS- (Break of Structure bullish/bearish) ditampilkan kalau terjadi PADA HARI YANG SAMA dengan sinyal AK3/DS3-nya, sebagai konfirmasi struktur harga di atas konfirmasi volume.</p>
      <div class="gl-two-col">
        <div class="gl-block">
          <h3 class="gl-block-title up">&#9650; AK3 — Akumulasi Kuat</h3>
          <ul class="idx-correction-list">{ak3_html}</ul>
        </div>
        <div class="gl-block">
          <h3 class="gl-block-title down">&#9660; DS3 — Distribusi Kuat</h3>
          <ul class="idx-correction-list">{ds3_html}</ul>
        </div>
      </div>
      <h3 class="idx-events-title" style="margin-top:12px">Prediksi Kandidat AK3/DS3 Besok</h3>
      <p class="idx-note">Heuristik kedekatan ke ambang tier-3 (skor sudah &ge;6 dari kebutuhan &ge;8, RVOL tinggal butuh tembus 2x) — BUKAN model prediksi tervalidasi, sekadar menandai siapa yang paling dekat ke ambang AK3/DS3 kalau volume berlanjut besok.</p>
      <div class="gl-two-col">
        <div class="gl-block">
          <h3 class="gl-block-title up">&#9650; Kandidat AK3</h3>
          <ul class="idx-correction-list">{cand_ak3_html}</ul>
        </div>
        <div class="gl-block">
          <h3 class="gl-block-title down">&#9660; Kandidat DS3</h3>
          <ul class="idx-correction-list">{cand_ds3_html}</ul>
        </div>
      </div>
    </div>'''

ak3_ds3_section_html = ak3_ds3_box_html(stocks)

def signal_pill(d, key, cls_key):
    val = d.get(key)
    if not val:
        return '<span class="pill small neutral">—</span>'
    return f'<span class="pill small {html.escape(d.get(cls_key, "neutral"))}">{html.escape(val)}</span>'

summary_rows = "\n".join(
    (lambda acls, d, grade_txt, gcls: f'''<tr class="reco-{acls}">
      <td><span class="reco-dot {acls}"></span><span class="reco-label {acls}">{RECO_LABEL[acls]}</span></td>
      <td class="mono">{bare(r["symbol"])}</td>
      <td class="mono num">{fmt_price(r.get("price"))}</td>
      <td class="mono num {"up" if isinstance(r.get("change_pct"),str) and not r["change_pct"].startswith("-") else "down"}">{html.escape(str(r.get("change_pct") or "—"))}</td>
      <td>{html.escape(d.get("decision") or "—")}</td>
      <td><span class="pill small {gcls}">{html.escape(grade_txt)}</span></td>
      <td class="mono num">{r["_score"]}/10</td>
      <td>{signal_pill(d, "smart_money", "smart_money_cls")}</td>
      <td>{signal_pill(d, "foreign_flow_proxy", "foreign_flow_cls")}</td>
      <td>{signal_pill(d, "aggression", "aggression_cls")}</td>
    </tr>''')(r["_acls"], r.get("dashboard") or {}, *grade_from_score(r["_score"])) for r in summary_stocks
)

html_out = f'''<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<title>IDX Screening Harian</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root {{
  --ink: #14201c;
  --ink-soft: #4b5a54;
  --paper: #f6f5ef;
  --surface: #ffffff;
  --line: rgba(20,32,28,0.12);
  --line-strong: rgba(20,32,28,0.22);
  --accent: #0f4c46;
  --accent-soft: #e2eeec;
  --bull: #0a8a74;
  --bull-strong: #067a63;
  --bull-soft: #e3f3ee;
  --bear: #c8323f;
  --bear-strong: #a92433;
  --bear-soft: #fbe9ea;
  --neutral: #96690a;
  --neutral-soft: #fbf0da;
  --neutral-warn: #a15c1f;
  --neutral-warn-soft: #fbeee0;
  --font-display: "Fraunces", Georgia, serif;
  --font-body: "IBM Plex Sans", -apple-system, sans-serif;
  --font-mono: "IBM Plex Mono", "SFMono-Regular", monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ink: #eef1ee;
    --ink-soft: #a9b4ae;
    --paper: #101512;
    --surface: #17201c;
    --line: rgba(238,241,238,0.12);
    --line-strong: rgba(238,241,238,0.22);
    --accent: #6fc9bd;
    --accent-soft: #1c2f2c;
    --bull: #3fbfa2;
    --bull-strong: #57d4b6;
    --bull-soft: #17322b;
    --bear: #ef6a72;
    --bear-strong: #f4838a;
    --bear-soft: #3a1c1f;
    --neutral: #e0b558;
    --neutral-soft: #35291b;
    --neutral-warn: #eba25a;
    --neutral-warn-soft: #3a2814;
  }}
}}
:root[data-theme="dark"] {{
  --ink: #eef1ee;
  --ink-soft: #a9b4ae;
  --paper: #101512;
  --surface: #17201c;
  --line: rgba(238,241,238,0.12);
  --line-strong: rgba(238,241,238,0.22);
  --accent: #6fc9bd;
  --accent-soft: #1c2f2c;
  --bull: #3fbfa2;
  --bull-strong: #57d4b6;
  --bull-soft: #17322b;
  --bear: #ef6a72;
  --bear-strong: #f4838a;
  --bear-soft: #3a1c1f;
  --neutral: #e0b558;
  --neutral-soft: #35291b;
  --neutral-warn: #eba25a;
  --neutral-warn-soft: #3a2814;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-body);
  font-size: 13.5px;
  line-height: 1.5;
}}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 2rem 1.5rem 3rem; }}
.masthead {{ margin-bottom: 1.5rem; }}
.eyebrow {{
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
  margin: 0 0 0.4rem;
}}
h1 {{
  font-family: var(--font-display);
  font-weight: 600;
  font-size: 30px;
  margin: 0 0 0.35rem;
  text-wrap: balance;
  color: var(--ink);
}}
.subhead {{
  color: var(--ink-soft);
  font-size: 13px;
  margin: 0;
  max-width: 62ch;
}}
.gen-time {{ font-family: var(--font-mono); color: var(--ink-soft); font-size: 12px; margin-top: 0.5rem; }}

.legend-box {{
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px 18px;
  margin-top: 0.85rem; padding: 10px 14px; background: var(--paper-alt, var(--paper));
  border: 0.5px solid var(--line); border-radius: 8px;
}}
.legend-item {{ font-size: 10.5px; line-height: 1.45; color: var(--ink-soft); }}
.legend-item b {{ color: var(--ink); font-weight: 600; }}

.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(270px, 1fr));
  gap: 14px;
  margin-top: 1.5rem;
}}
.category-grid {{ margin-top: 10px; }}

.category-block {{ margin-top: 1.75rem; }}
.category-block:first-child {{ margin-top: 1rem; }}
.category-head {{
  display: flex; align-items: baseline; justify-content: space-between; gap: 10px; flex-wrap: wrap;
  break-after: avoid; page-break-after: avoid;
}}
.category-head h3 {{ font-family: var(--font-display); font-weight: 600; font-size: 16px; margin: 0; }}
.category-count {{ font-family: var(--font-mono); font-weight: 400; font-size: 12px; color: var(--ink-soft); }}
.category-resume {{ font-size: 11.5px; color: var(--ink-soft); margin-top: 2px; break-after: avoid; page-break-after: avoid; }}
.category-resume .bull-strong {{ color: var(--bull-strong); font-weight: 600; }}
.category-resume .bear-strong {{ color: var(--bear-strong); font-weight: 600; }}
.category-resume .neutral {{ color: var(--neutral); font-weight: 600; }}
.category-resume .up {{ color: var(--bull-strong); }}
.category-resume .down {{ color: var(--bear-strong); }}
.category-resume .flat {{ color: var(--neutral); }}

.card {{
  background: var(--surface);
  border: 0.5px solid var(--line);
  border-radius: 12px;
  padding: 14px 16px;
  break-inside: avoid;
  page-break-inside: avoid;
}}
.index-card {{
  grid-column: 1 / -1;
  border: 1px solid var(--accent);
  background: var(--accent-soft);
}}
.index-grid {{ margin-bottom: 0; }}

.section-title {{
  font-family: var(--font-display); font-weight: 600; font-size: 20px;
  margin: 1.75rem 0 0.25rem;
}}
.section-sub {{ color: var(--ink-soft); font-size: 12.5px; margin: 0 0 1rem; max-width: 78ch; }}

.macro-section {{ break-inside: avoid; }}

.gl-section {{ break-inside: avoid; margin-bottom: 0.5rem; }}
.gl-legend {{ display: flex; flex-wrap: wrap; gap: 14px; font-size: 11.5px; color: var(--ink-soft); margin-bottom: 10px; }}
.gl-legend span {{ display: flex; align-items: center; gap: 5px; }}
.gl-dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; }}
.gl-dot.buy {{ background: var(--bull-strong); }}
.gl-dot.sell {{ background: var(--bear-strong); }}
.gl-dot.warn {{ background: var(--neutral-warn); }}
.gl-two-col {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }}
.gl-block {{
  background: var(--surface); border: 0.5px solid var(--line); border-radius: 12px;
  padding: 12px 14px; break-inside: avoid; page-break-inside: avoid;
}}
.gl-block-title {{ font-family: var(--font-display); font-weight: 600; font-size: 15px; margin: 0 0 8px; }}
.gl-block-title.up {{ color: var(--bull-strong); }}
.gl-block-title.down {{ color: var(--bear-strong); }}
.gl-row {{
  display: grid; grid-template-columns: auto auto auto auto 1fr; align-items: baseline; gap: 8px;
  padding: 6px 0; border-bottom: 0.5px solid var(--line); font-size: 11.5px;
}}
.gl-row:last-child {{ border-bottom: none; }}
.gl-reco {{
  font-family: var(--font-mono); font-weight: 700; font-size: 10px; letter-spacing: 0.03em;
  padding: 2px 7px; border-radius: 999px; white-space: nowrap;
}}
.gl-reco.buy {{ background: var(--bull-soft); color: var(--bull-strong); }}
.gl-reco.sell {{ background: var(--bear-soft); color: var(--bear-strong); }}
.gl-reco.warn {{ background: var(--neutral-warn-soft); color: var(--neutral-warn); }}
.gl-ticker {{ font-family: var(--font-mono); font-weight: 600; font-size: 12.5px; }}
.gl-chg {{ font-family: var(--font-mono); font-weight: 600; white-space: nowrap; }}
.gl-chg.up {{ color: var(--bull-strong); }}
.gl-chg.down {{ color: var(--bear-strong); }}
.gl-vol {{ font-family: var(--font-mono); color: var(--ink-soft); white-space: nowrap; }}
.gl-proj {{ font-family: var(--font-mono); font-weight: 600; color: var(--accent); white-space: nowrap; }}
.gl-chg em, .gl-proj em {{ font-style: normal; font-weight: 500; text-transform: uppercase; font-size: 8.5px; letter-spacing: 0.03em; color: var(--ink-soft); opacity: 0.8; margin-right: 2px; }}
.gl-note {{ color: var(--ink-soft); grid-column: 1 / -1; font-size: 10.5px; margin-top: -2px; }}
.gl-ews {{
  font-family: var(--font-mono); font-weight: 700; font-size: 9.5px; letter-spacing: 0.02em;
  padding: 1px 5px; border-radius: 999px; white-space: nowrap; margin-right: 4px;
}}
.gl-ews.bull {{ background: var(--bull-soft); color: var(--bull-strong); }}
.gl-ews.bear {{ background: var(--bear-soft); color: var(--bear-strong); }}
.gl-ews.warn {{ background: var(--neutral-warn-soft); color: var(--neutral-warn); }}
.gl-ews.neutral {{ background: var(--neutral-soft); color: var(--neutral); }}
.gl-row.gl-warn {{ background: color-mix(in srgb, var(--neutral-warn-soft) 40%, transparent); }}

.macro-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}}
.macro-card {{
  background: var(--surface); border: 0.5px solid var(--line); border-radius: 12px;
  padding: 12px 14px; break-inside: avoid; page-break-inside: avoid;
}}
.macro-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 4px; }}
.macro-label {{ font-weight: 500; font-size: 13px; }}
.macro-trend {{ font-family: var(--font-mono); font-size: 11.5px; font-weight: 600; white-space: nowrap; }}
.macro-trend.up {{ color: var(--bull-strong); }}
.macro-trend.down {{ color: var(--bear-strong); }}
.macro-trend.flat {{ color: var(--neutral); }}
.macro-trend.warn {{ color: var(--neutral-warn); }}
.macro-trend b {{ font-weight: 700; }}
.macro-sub {{ font-family: var(--font-mono); font-size: 11px; margin-bottom: 6px; }}
.macro-sub .bull {{ color: var(--bull-strong); }}
.macro-sub .bear {{ color: var(--bear-strong); }}
.macro-sub .neutral {{ color: var(--neutral); }}
.macro-key {{ font-size: 12px; font-weight: 500; color: var(--ink); margin: 0; line-height: 1.5; }}

.policy-grid {{
  display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px; margin-bottom: 14px;
}}
.policy-card {{
  background: var(--surface); border: 0.5px solid var(--line); border-radius: 12px;
  padding: 12px 14px; break-inside: avoid; page-break-inside: avoid;
}}
.policy-head {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px; }}
.policy-label {{ font-weight: 500; font-size: 13px; }}
.policy-value {{ font-family: var(--font-mono); font-weight: 700; font-size: 20px; margin-bottom: 6px; }}
.fact-list {{ margin: 0; padding-left: 1.1em; }}
.fact-list li {{ font-size: 11.5px; color: var(--ink-soft); line-height: 1.6; }}

.macro-block {{
  background: var(--surface); border: 0.5px solid var(--line); border-radius: 12px;
  padding: 14px 16px; break-inside: avoid; page-break-inside: avoid;
}}
.macro-block.wide {{ grid-column: 1 / -1; }}
.macro-block h3 {{ font-family: var(--font-display); font-weight: 600; font-size: 15px; margin: 0 0 8px; }}
.block-resume {{ padding-top: 8px; border-top: 0.5px dashed var(--line); font-size: 12.5px; margin: 8px 0 0; }}

.stat-row {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.stat-chip {{
  display: flex; align-items: baseline; gap: 5px;
  background: var(--paper); border: 0.5px solid var(--line); border-radius: 8px;
  padding: 5px 9px; font-size: 11.5px;
}}
.stat-value {{ font-family: var(--font-mono); font-size: 13px; font-weight: 700; }}
.stat-value.up {{ color: var(--bull-strong); }}
.stat-value.down {{ color: var(--bear-strong); }}
.stat-value.flat {{ color: var(--neutral); }}
.stat-value.warn {{ color: var(--neutral-warn); }}
.stat-name {{ color: var(--ink-soft); }}
.stat-note {{ color: var(--ink-soft); font-size: 10.5px; }}

.geo-list {{ margin: 0; padding-left: 0; list-style: none; }}
.geo-list li {{ display: flex; gap: 6px; font-size: 12px; line-height: 1.55; margin-bottom: 7px; }}
.geo-list li:last-child {{ margin-bottom: 0; }}

.macro-sources {{ font-size: 10.5px; color: var(--ink-soft); margin: 6px 0 0; }}
.card-head {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }}
.ticker-row {{ display: flex; align-items: baseline; gap: 8px; }}
.ticker {{ font-family: var(--font-mono); font-weight: 600; font-size: 15px; letter-spacing: 0.02em; }}
.idxbadge {{
  font-family: var(--font-mono); font-weight: 700; font-size: 8px; letter-spacing: 0.02em;
  padding: 1px 4px; border-radius: 4px; white-space: nowrap; margin-right: -2px;
}}
.idxbadge-in {{ background: var(--bull-soft); color: var(--bull-strong); }}
.idxbadge-watch {{ background: var(--bear-soft); color: var(--bear); }}
.idxbadge-out {{ background: var(--ink); color: var(--paper); }}
.price {{ font-family: var(--font-mono); font-size: 14px; color: var(--ink-soft); }}
.chg {{ font-family: var(--font-mono); font-size: 12.5px; font-weight: 500; padding: 1px 6px; border-radius: 5px; }}
.chg.up {{ color: var(--bull-strong); background: var(--bull-soft); }}
.chg.down {{ color: var(--bear-strong); background: var(--bear-soft); }}

.pill {{
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.02em;
  padding: 3px 9px;
  border-radius: 999px;
  white-space: nowrap;
}}
.pill.small {{ font-size: 10.5px; padding: 2px 7px; }}
.pill.bull-strong {{ background: var(--bull-soft); color: var(--bull-strong); }}
.pill.bull {{ background: var(--bull-soft); color: var(--bull); }}
.pill.bear-strong {{ background: var(--bear-soft); color: var(--bear-strong); }}
.pill.bear {{ background: var(--bear-soft); color: var(--bear); }}
.pill.neutral {{ background: var(--neutral-soft); color: var(--neutral); }}
.pill.neutral-warn {{ background: var(--neutral-warn-soft); color: var(--neutral-warn); }}

.meta-row {{ display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; color: var(--ink-soft); margin-bottom: 8px; }}
.meta em {{ font-style: normal; display: block; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ink-soft); opacity: 0.75; }}
.meta {{ color: var(--ink); }}

.pattern-tag {{
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 500;
  padding: 4px 9px; border-radius: 7px;
  background: var(--neutral-soft); color: var(--neutral);
  margin-bottom: 8px; width: fit-content;
}}
.pattern-tag.confirmed {{ background: var(--bear-soft); color: var(--bear-strong); }}
.pattern-tag em {{ font-style: normal; font-size: 10px; opacity: 0.75; text-transform: uppercase; letter-spacing: 0.04em; }}

.score-bar {{ display: flex; height: 6px; border-radius: 4px; overflow: hidden; background: var(--line); margin-bottom: 4px; }}
.score-bar .seg.bull {{ background: var(--bull); }}
.score-bar .seg.neutral {{ background: var(--line-strong); }}
.score-bar .seg.bear {{ background: var(--bear); }}
.score-nums {{ display: flex; gap: 10px; font-family: var(--font-mono); font-size: 10.5px; color: var(--ink-soft); margin-bottom: 8px; }}
.score-nums .bull {{ color: var(--bull); }}
.score-nums .bear {{ color: var(--bear); }}

.grade-row {{ margin-bottom: 8px; }}

.levels {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px 10px; margin-bottom: 8px; font-family: var(--font-mono); font-size: 11.5px; }}
.lvl {{ display: flex; justify-content: space-between; gap: 6px; padding: 2px 0; border-bottom: 0.5px dashed var(--line); }}
.lvl-label {{ color: var(--ink-soft); }}
.lvl.support .lvl-val {{ color: var(--neutral); }}
.lvl.resistance .lvl-val {{ color: var(--bull); }}
.lvl.tp1 .lvl-val, .lvl.tp2 .lvl-val {{ color: var(--accent); }}
.lvl.cutloss .lvl-val {{ color: var(--bear); }}

.ai-forecast {{ font-size: 12px; display: flex; align-items: center; gap: 5px; margin-bottom: 6px; }}
.ai-forecast .dir {{ font-style: normal; font-size: 10px; }}
.ai-forecast.naik {{ color: var(--bull-strong); }}
.ai-forecast.turun {{ color: var(--bear-strong); }}
.ai-forecast .conf {{ color: var(--ink-soft); margin-left: 2px; }}

.winrate {{ display: flex; flex-wrap: wrap; gap: 5px; }}
.chip {{
  font-size: 10px; font-family: var(--font-mono);
  background: var(--paper); border: 0.5px solid var(--line);
  border-radius: 5px; padding: 2px 6px; color: var(--ink-soft);
}}
.chip b {{ color: var(--ink); }}
.chip em {{ font-style: normal; color: var(--accent); }}

.key-params {{ display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 8px; }}
.kp-chip {{
  font-size: 10.5px; font-family: var(--font-mono);
  border-radius: 5px; padding: 3px 7px; font-weight: 600;
  display: flex; align-items: center; gap: 4px;
}}
.kp-chip em {{ font-style: normal; font-weight: 500; opacity: 0.75; text-transform: uppercase; letter-spacing: 0.02em; font-size: 9px; }}
.kp-chip.bull {{ background: var(--bull-soft); color: var(--bull-strong); }}
.kp-chip.bear {{ background: var(--bear-soft); color: var(--bear-strong); }}
.kp-chip.neutral {{ background: var(--neutral-soft); color: var(--neutral); }}

.summary {{ margin-top: 2rem; }}
.summary h2 {{
  font-family: var(--font-display); font-weight: 600; font-size: 19px;
  margin: 0 0 0.75rem;
}}
table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
thead th {{
  text-align: left; font-family: var(--font-mono); font-size: 10.5px;
  text-transform: uppercase; letter-spacing: 0.04em; color: var(--ink-soft);
  border-bottom: 1px solid var(--line-strong); padding: 6px 8px;
}}
tbody td {{ padding: 6px 8px; border-bottom: 0.5px solid var(--line); }}
tbody tr {{ break-inside: avoid; page-break-inside: avoid; }}
.mono {{ font-family: var(--font-mono); }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
td.up {{ color: var(--bull-strong); }}
td.down {{ color: var(--bear-strong); }}

.reco-legend {{ display: flex; gap: 16px; font-size: 11.5px; color: var(--ink-soft); margin-bottom: 8px; }}
.reco-legend span {{ display: flex; align-items: center; gap: 5px; }}
.reco-dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; }}
.reco-dot.bull-strong, .reco-dot.bull {{ background: var(--bull-strong); }}
.reco-dot.neutral, .reco-dot.neutral-warn {{ background: var(--neutral); }}
.reco-dot.bear-strong, .reco-dot.bear {{ background: var(--bear-strong); }}
.reco-label {{ font-family: var(--font-mono); font-weight: 600; font-size: 11px; letter-spacing: 0.03em; }}
.reco-label.bull-strong, .reco-label.bull {{ color: var(--bull-strong); }}
.reco-label.neutral, .reco-label.neutral-warn {{ color: var(--neutral); }}
.reco-label.bear-strong, .reco-label.bear {{ color: var(--bear-strong); }}
tr.reco-bull-strong, tr.reco-bull {{ background: color-mix(in srgb, var(--bull-soft) 55%, transparent); }}
tr.reco-bear-strong, tr.reco-bear {{ background: color-mix(in srgb, var(--bear-soft) 55%, transparent); }}
tr.reco-neutral-warn {{ background: color-mix(in srgb, var(--neutral-warn-soft) 55%, transparent); }}
thead th:first-child, tbody td:first-child {{ white-space: nowrap; }}

.idx-events {{ margin-top: 12px; padding-top: 10px; border-top: 0.5px dashed var(--line); }}
.idx-events-title {{ font-family: var(--font-mono); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.03em; color: var(--ink-soft); margin: 0 0 6px; }}
.table-wrap {{ overflow-x: auto; }}
.idx-events-table {{ font-size: 11.5px; }}
.idx-impact {{ color: var(--ink-soft); font-size: 10.5px; max-width: 34ch; }}
.idx-note {{ color: var(--ink-soft); font-size: 11.5px; margin: 6px 0; }}
.ak3ds3-box {{ margin: 10px 0 16px; padding: 12px 14px; background: var(--surface); border: 0.5px solid var(--line); border-radius: 12px; }}
.rec-list {{ list-style: none; margin: 6px 0; padding: 0; font-size: 11.5px; display: flex; flex-direction: column; gap: 6px; }}
.rec-list .idx-other-note {{ background: var(--paper); padding: 1px 5px; border-radius: 4px; margin-right: 2px; }}
.market-review-list a {{ color: var(--accent); text-decoration: none; }}
.market-review-list a:hover {{ text-decoration: underline; }}
.idx-other-ratings {{ list-style: none; margin: 8px 0 0; padding: 0; font-size: 11px; color: var(--ink-soft); display: flex; flex-direction: column; gap: 3px; }}
.idx-other-note {{ opacity: 0.75; }}
.idx-trend {{ font-size: 11.5px; color: var(--ink-soft); margin: 8px 0; padding: 8px 10px; background: var(--accent-soft); border-radius: 8px; }}
.idx-full-list {{ margin-top: 10px; }}
.idx-full-list-groups {{ list-style: none; margin: 6px 0; padding: 0; font-size: 11.5px; display: flex; flex-direction: column; gap: 6px; }}
.idx-full-list-groups .pill {{ margin: 1px 2px 1px 0; display: inline-block; }}
.idx-correction-watch {{ margin-top: 10px; }}
.idx-correction-list {{ list-style: none; margin: 6px 0; padding: 0; font-size: 11px; display: flex; flex-direction: column; gap: 4px; }}
.idx-correction-list em {{ font-style: normal; color: var(--ink-soft); font-size: 10px; }}

.footnote {{
  margin-top: 2rem; padding-top: 1rem; border-top: 0.5px solid var(--line);
  font-size: 11px; color: var(--ink-soft); max-width: 72ch;
}}

@media print {{
  @page {{ size: A4; margin: 6mm; }}
  body {{ zoom: 0.72; background: #fff; }}
  .wrap {{ max-width: none; padding: 0; }}
  .card {{ border: 0.5px solid #ccc; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <div class="masthead">
    <p class="eyebrow">IDX Analyzer DG &middot; Screening Harian</p>
    <h1>Ringkasan Teknikal Watchlist IDX</h1>
    <p class="gen-time">Dibuat {gen_time}</p>
    <div class="legend-box">
      <div class="legend-item"><b>Decision Gate 3.0</b> model komposit — probabilitas naik 20 hari, hasil fit statistik 19 sinyal (akurasi test 53,95% vs baseline 52,50%, edge kecil tapi nyata).</div>
      <div class="legend-item"><b>Gate 2.0</b> skor keyakinan gabungan dari arah trend, forecast, volume, dan EWS Detection (DG3.4) — bukan model ter-fit, tapi konsisten dipakai lintas versi.</div>
      <div class="legend-item"><b>AI Forecast 2.0</b> model logistik beku (frozen) memprediksi arah 20 hari dari 6 fitur teknikal — edge tervalidasi ~1pp di atas baseline.</div>
      <div class="legend-item"><b>EWS Detection</b> Bullish-Risk/Bearish-Risk (kontrarian, oversold/overbought) dan FOMO/Panic Index (DG3.0-3.4) — Panic ikut memberi vote SELL di atas ambang, FOMO tetap diagnostik saja (tidak ada edge berdiri sendiri).</div>
      <div class="legend-item"><b>Hurst / Vol Percentile</b> rezim pasar (TRENDING/MEAN-REV/RANDOM) dan posisi volatilitas EWMA saat ini relatif terhadap histori 252 hari.</div>
      <div class="legend-item"><b>Badge MSCI/FTSE</b> di kartu saham (kalau ada): <span class="idxbadge idxbadge-in">MSCI</span> hijau = masuk/masih terdaftar, <span class="idxbadge idxbadge-watch">FTSE</span> merah = dalam evaluasi/downgrade, <span class="idxbadge idxbadge-out">MSCI</span> hitam = dikeluarkan. HANYA ditandai untuk saham yang statusnya terverifikasi dari sumber publik (lihat panel Sentimen Pasar) — tanpa badge bukan berarti pasti tidak termasuk, statusnya belum terverifikasi.</div>
    </div>
  </div>

  <div class="grid index-grid">
    {index_card}
  </div>

  {macro_section}

  <h2 class="section-title">Watchlist Saham</h2>

  {ak3_ds3_section_html}

  {acc_dist_section}

  {gainers_losers_section}

  {recommendations_section_html}

  <div class="summary">
    <h2>Rekomendasi Saham Hari Ini</h2>
    <p class="section-sub">Seluruh kode saham di watchlist — sinyal DECISION dari analisa DG3.4 sendiri (bukan opini analis pihak ketiga, lihat panel "Rekomendasi Saham Hari Ini" di atas untuk itu).</p>
    <div class="reco-legend">
      <span><span class="reco-dot bull-strong"></span>BUY</span>
      <span><span class="reco-dot neutral"></span>WAIT</span>
      <span><span class="reco-dot bear-strong"></span>SELL</span>
    </div>
    <p class="section-sub">Smart Money &amp; Aggression dari data volume/candle OHLCV 2 hari terakhir (Bandarmologi CMF, close-location-value + RVOL) — sinyal riil dari harga &amp; volume. <b>Foreign Flow adalah PROXY</b> (arah harga saham vs arah IHSG pada volume tinggi), <b>bukan data net-buy/sell asing riil</b> (KSEI/broker summary tidak tersedia di pipeline OHLCV ini).</p>

    <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Reko</th><th>Kode</th><th class="num">Harga</th><th class="num">Chg%</th><th>Decision</th><th>Grade</th><th class="num">Skor</th><th>Smart Money</th><th>Foreign Flow*</th><th>Aggression</th></tr></thead>
      <tbody>
        {summary_rows}
      </tbody>
    </table>
    </div>
  </div>

  {stock_sections}

  <p class="footnote">DECISION, skor, Gate 2.0/3.0, EWS Detection, dan grade dihitung ulang di Python mengikuti persis rumus indikator "IDX Analyzer DG3.4" (dibaca dari source Pine-nya), dijalankan atas data harga historis harian — bukan analisa baru. Cakupan yang BELUM disertakan: Risk Overlay Gate 2.0 (Kelly/PSR/Circuit Breaker/CUSUM) dan Lead Quality Tracker (perlu histori trade berjalan), dg3LiveAccuracy dan callout rekomendasi BUY/SELL akhir (keduanya konstruksi real-time di chart, tidak ada padanan bersih di satu snapshot CSV stateless), serta win-rate pola chart (Double Top/Bottom/H&amp;S) — lihat catatan skill untuk detail. Kolom "Foreign Flow" di Ringkasan Entry adalah proxy arah-harga-vs-IHSG-pada-volume-tinggi, BUKAN data net-buy/sell asing riil (KSEI/rekap broker) — pipeline ini hanya berbasis OHLCV Yahoo Finance. Ini adalah alat bantu analisa teknikal, bukan rekomendasi investasi atau jaminan hasil.</p>
</div>
</body>
</html>'''

with open(OUT_PATH, "w") as f:
    f.write(html_out)
print("wrote", OUT_PATH, len(html_out), "bytes")
print("stocks:", len(stocks))
