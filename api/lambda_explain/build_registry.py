"""Build the explain service's trusted inputs from the shipped public cards.

The generated file is packaged with the handler, not accepted from a request.
Keep it in step with the exact card snapshot published by the static release.
"""
from __future__ import annotations

import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def number(value: object, places: int = 0) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    quant = Decimal('1').scaleb(-places)
    rounded = Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)
    return f'{rounded:,.{places}f}'


def record(card: dict) -> tuple[str, dict]:
    facility = card['facility']
    facility_id = facility['id']
    if not isinstance(facility_id, str) or not facility_id.startswith('echo_') or len(facility_id) > 64:
        raise ValueError('Invalid facility identity')
    verdict = card['verdict']['sentence']
    if not isinstance(verdict, str) or not verdict.strip() or len(verdict) > 2000:
        raise ValueError(f'Invalid fallback for {facility_id}')
    claims = card['claims']
    if not isinstance(claims, list):
        raise ValueError(f'Invalid claims for {facility_id}')
    by_metric = {}
    for claim in claims:
        if isinstance(claim, dict) and isinstance(claim.get('metric'), str):
            metric = claim['metric']
            if metric in by_metric:
                raise ValueError(f'Duplicate {metric} for {facility_id}')
            by_metric[metric] = claim

    facts = []
    power = by_metric.get('power_mw', {})
    power_value = number(power.get('value'), 1)
    if power_value is not None and power.get('unit') == 'MW' and power.get('badge') == 'ESTIMATED' and power.get('renderable') is True:
        facts.append(f'The calculation uses a reference electrical load of {power_value} megawatts; it is not a facility measurement.')
    carbon = by_metric.get('carbon_t', {})
    carbon_value = number(carbon.get('value'))
    if carbon_value is not None and carbon.get('unit') == 't CO2e/yr' and carbon.get('badge') == 'MODELED' and carbon.get('renderable') is True:
        fact = f'At that reference load, modeled annual emissions are {carbon_value} metric tonnes of carbon dioxide equivalent'
        band = carbon.get('range')
        if isinstance(band, dict):
            low, high = number(band.get('p10')), number(band.get('p90'))
            if low is not None and high is not None:
                fact += f', with a range of {low} to {high} metric tonnes'
        facts.append(fact + '; this is an estimate, not a facility measurement.')
    grid = card.get('grid', {})
    if isinstance(grid, dict):
        rate = number(grid.get('rate_lb_per_mwh'), 1)
        if rate is not None and grid.get('intensity_state') in ('PROVISIONAL', 'SETTLED'):
            facts.append(f'The regional grid average is {rate} pounds of carbon dioxide equivalent per megawatt-hour, marked {grid["intensity_state"].lower()}.')
    water = by_metric.get('water_gal', {})
    if water.get('value') == 'NOT_PUBLISHED' and water.get('renderable') is True:
        facts.append('No facility-level operational water figure was found in the public sources used.')
    diesel = by_metric.get('diesel_mw', {})
    if diesel.get('value') == 'NOT_PUBLISHED' and diesel.get('renderable') is True:
        facts.append('Backup diesel generator capacity was not published in the public sources used.')
    else:
        diesel_value = number(diesel.get('value'), 1)
        if diesel_value is not None and diesel.get('unit') == 'MW' and diesel.get('badge') == 'REPORTED' and diesel.get('renderable') is True and any(c.get('verified') is True for c in diesel.get('citations', []) if isinstance(c, dict)):
            facts.append(f'The permit reports {diesel_value} megawatts of installed backup generator nameplate capacity, not operating hours or actual emissions.')
    if not facts:
        raise ValueError(f'No supported facts for {facility_id}')
    return facility_id, {'facts': facts, 'template': verdict.strip()}


def build_registry(card_dir: Path, catalogue_path: Path) -> dict:
    catalogue = json.loads(catalogue_path.read_text(encoding='utf-8'))
    if not isinstance(catalogue, list) or len(set(catalogue)) != len(catalogue):
        raise ValueError('Catalogue must contain distinct IDs')
    result = {}
    for path in sorted(p for p in card_dir.iterdir() if p.is_file()):
        facility_id, entry = record(json.loads(path.read_text(encoding='utf-8')))
        if facility_id in result:
            raise ValueError(f'Duplicate facility identity: {facility_id}')
        result[facility_id] = entry
    if set(result) != set(catalogue):
        raise ValueError('Shipped cards do not match the explain catalogue')
    return dict(sorted(result.items()))


def main() -> None:
    import sys
    if len(sys.argv) != 2:
        raise SystemExit('Usage: build_registry.py OUTPUT')
    registry = build_registry(ROOT / 'site/api/card', ROOT / 'api/lambda_explain/catalogue.json')
    Path(sys.argv[1]).write_text(json.dumps(registry, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(f'Built trusted explain registry for {len(registry)} cards')


if __name__ == '__main__':
    main()
