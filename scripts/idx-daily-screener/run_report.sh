#!/usr/bin/env bash
# run_report.sh — Steps 2-5 of the IDX Daily Screen pipeline as one command:
# compute the DG3.4/EWS snapshot, render the HTML report, export to PDF, and
# render pages to PNG for visual verification. Step 1 (refreshing
# data_cache/*.csv from Yahoo Finance) and Step 6 (copying the PDF to
# Documents, publishing the artifact, sending the file) are NOT part of this
# script — they need the Browser tool / Artifact tool / SendUserFile, which
# only an agent session has, not a plain shell. Run this from the repo root
# or anywhere; it resolves paths relative to its own location.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python3"
SNAPSHOT="/tmp/dg-snapshot.jsonl"
REPORT_HTML="/tmp/idxa-report.html"
REPORT_PDF="/tmp/idxa-report.pdf"
PAGES_DIR="/tmp/idxa-pages"

echo "[2/5] Computing DG3.4 + EWS Detector snapshot..."
"$VENV_PY" "$SCRIPT_DIR/dg_snapshot.py" "$SNAPSHOT"

echo "[3/5] Generating HTML report..."
python3 "$SCRIPT_DIR/gen_report.py" "$SNAPSHOT" "$REPORT_HTML"

echo "[4/5] Exporting PDF (headless Chrome)..."
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ ! -x "$CHROME" ]; then
  CHROME="$(command -v google-chrome || command -v chromium || true)"
fi
if [ -z "$CHROME" ]; then
  echo "No Chrome/Chromium binary found — cannot export PDF." >&2
  exit 1
fi
"$CHROME" --headless --disable-gpu --no-sandbox \
  --print-to-pdf="$REPORT_PDF" --no-pdf-header-footer \
  "file://$REPORT_HTML" 2>/dev/null

echo "[5/5] Rendering pages for visual verification..."
rm -rf "$PAGES_DIR" && mkdir -p "$PAGES_DIR"
pdftoppm -png -r 100 "$REPORT_PDF" "$PAGES_DIR/page"

echo "Done."
echo "  HTML:  $REPORT_HTML"
echo "  PDF:   $REPORT_PDF"
echo "  Pages: $PAGES_DIR ($(ls "$PAGES_DIR" | wc -l | tr -d ' ') files)"
