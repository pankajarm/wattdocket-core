#!/usr/bin/env bash
# Rebuild every artifact in dependency order.
#
# This exists because the order matters and I got it wrong: `wattdocket.cards`
# rewrites the card files from the CSV, and `wattdocket.hero` *adds* the hourly
# series to one of those files afterwards. Re-running the card builder on its own
# therefore silently deletes the hero's 288 intervals — which is exactly what
# happened, and the live site served a card claiming "no hourly series" while the
# README advertised the chart.
#
# So: one command, one order, and the last step asserts the hero survived.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PY:-.venv/bin/python}"
export PYTHONPATH="core"

echo "== 1/5 cards"
$PY -m wattdocket.cards --csv data/echo_datacenters_naics518210.csv \
    --out artifacts/cards --index artifacts/cards.json

echo "== 2/5 hero hourly (must run after cards, or it is wiped)"
# ERCOT's dashboard serves only the last couple of days, so the hero's day
# usually cannot be re-fetched. Without a cache, re-apply the series already
# published on the committed hero card (snapshotted before step 4 overwrites it).
HERO=echo_110072155814
if [ -f "${ERCOT_CACHE:-/tmp/ercot.json}" ]; then
  HERO_SRC=(--ercot "${ERCOT_CACHE:-/tmp/ercot.json}")
else
  SNAP="$(mktemp)"
  cp "site/data/cards/$HERO.json" "$SNAP"
  HERO_SRC=(--from-card "$SNAP")
  echo "   no ERCOT cache: re-using the published hourly series from site/data/cards/$HERO.json" >&2
fi
$PY -m wattdocket.hero --cards artifacts/cards --out artifacts/cards \
    "${HERO_SRC[@]}" --facility "$HERO"

echo "== 3/5 permit facts"
$PY -m wattdocket.permit_records --cards artifacts/cards.json \
    --permits data/permits --out artifacts/permits.json >/dev/null
# permit_records feeds cards, so rebuild cards once more with permits attached,
# then re-apply the hero series that the rebuild just wiped.
$PY -m wattdocket.cards --csv data/echo_datacenters_naics518210.csv \
    --out artifacts/cards --index artifacts/cards.json >/dev/null
$PY -m wattdocket.hero --cards artifacts/cards --out artifacts/cards \
    "${HERO_SRC[@]}" --facility "$HERO" >/dev/null

echo "== 4/5 copy to site + static API"
cp artifacts/cards.json site/data/cards.json
mkdir -p site/data/cards
cp artifacts/cards/*.json site/data/cards/
python3 scripts/build_static_api.py
python3 scripts/build_operator.py
python3 scripts/build_resolve_fallback.py

echo "== 5/5 verify the hero survived"
python3 - <<'PY'
import json, pathlib, sys
card = json.loads(pathlib.Path("site/data/cards/echo_110072155814.json").read_text())
h = card.get("hourly")
if not h:
    sys.exit("FAIL: hero card has no hourly series — the chart will be empty")
n = h.get("intervals")
print(f"   hero hourly: {n} intervals, {h.get('min')}-{h.get('max')} lb/MWh "
      f"({h.get('ratio')}x)")
if not isinstance(n, int) or n < 200:
    sys.exit(f"FAIL: hero hourly has {n} intervals, expected 288")
PY
echo "== build ok"
