#!/usr/bin/env bash
# Render a resume authored as HTML into PDF and DOCX.
#
#   ./jobs/render.sh path/to/name-resume.html
#
# PDF comes from Chromium because it honors @page (size and margins), which is
# how the one-page US Letter layout is actually enforced. LibreOffice's HTML
# import ignores @page and defaults to A4, so it is used only for the DOCX,
# which is the editable copy rather than the one sent.
#
# Fails loudly if the result is not exactly one page.

set -euo pipefail

SRC="${1:?usage: render.sh <resume.html>}"
[ -f "$SRC" ] || { echo "no such file: $SRC" >&2; exit 1; }

DIR="$(cd "$(dirname "$SRC")" && pwd)"
BASE="$(basename "$SRC" .html)"

CHROME="$(ls -d /opt/pw-browsers/chromium-*/chrome-linux/chrome 2>/dev/null | head -1 || true)"
[ -n "$CHROME" ] || CHROME="$(command -v chromium || command -v chromium-browser || command -v google-chrome || true)"
[ -n "$CHROME" ] || { echo "chromium not found; cannot render PDF" >&2; exit 1; }

"$CHROME" --headless --disable-gpu --no-sandbox --no-pdf-header-footer \
  --print-to-pdf="$DIR/$BASE.pdf" "file://$DIR/$BASE.html" >/dev/null 2>&1

# en_US locale makes LibreOffice default to Letter rather than A4.
LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 soffice \
  -env:UserInstallation=file:///tmp/lo-render \
  --headless --norestore --infilter="HTML (StarWriter)" \
  --convert-to docx --outdir "$DIR" "$DIR/$BASE.html" >/dev/null 2>&1 || true

PAGES="$(pdfinfo "$DIR/$BASE.pdf" | awk '/^Pages:/{print $2}')"
SIZE="$(pdfinfo "$DIR/$BASE.pdf" | awk -F'[()]' '/^Page size:/{print $2}')"

if [ "$PAGES" != "1" ]; then
  echo "FAIL: $BASE.pdf is $PAGES pages, must be 1. Trim content or reduce font size." >&2
  exit 1
fi

echo "$BASE.pdf  ${PAGES}p  $SIZE"
[ -f "$DIR/$BASE.docx" ] && echo "$BASE.docx written" || echo "warning: docx not produced" >&2
