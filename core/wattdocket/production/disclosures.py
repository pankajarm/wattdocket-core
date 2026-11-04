"""Reported annual observations retain their entity, period, unit and boundary.

Campus relationships never allocate totals to a building or regulatory record.
The inventory and reporting-entity namespaces remain separate and auditable.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse


METRICS = {
    'electricity_consumption': 'Reported annual electricity',
    'water_withdrawal': 'Water withdrawal',
    'water_consumption': 'Water consumption',
    'water_discharge': 'Water discharge',
    'emissions_scope1': 'Scope 1 emissions',
    'emissions_scope2_location': 'Scope 2 emissions · location-based',
    'emissions_scope2_market': 'Scope 2 emissions · market-based',
    'emissions_scope1_and2_market': 'Scope 1 + 2 emissions · market-based',
    'reported_ghg_total': 'Reported emissions · source-defined boundary',
}
SCOPES = {'campus', 'building', 'regulatory_facility', 'operator_location'}
RELATIONSHIPS = {'same_physical_scope', 'within_disclosure_location', 'candidate'}


def safe_id(value):
    return bool(isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9:._-]{0,180}', value))


def https_url(value):
    if not isinstance(value, str):
        return False
    p = urlparse(value)
    return p.scheme == 'https' and bool(p.hostname) and not p.username and not p.password


def validate_observation(row, location):
    """Fail closed before a reported value enters the common public contract."""
    def require(ok, message):
        if not ok:
            raise ValueError('Disclosure ' + str(row.get('observation_id')) + ': ' + message)
    require(safe_id(row.get('observation_id')), 'invalid observation identity')
    require(row.get('disclosure_location_id') == location['disclosure_location_id'], 'entity identity differs')
    require(row.get('metric') in METRICS, 'unsupported metric')
    value = row.get('value')
    require(isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0,
            'missing, nonfinite, negative or nonnumeric quantity')
    require(isinstance(row.get('unit'), str) and 0 < len(row['unit']) < 100, 'unit missing')
    unit = row['unit'].lower()
    metric = row['metric']
    if metric == 'electricity_consumption':
        require(unit in {'mwh', 'kwh', 'gwh', 'kbtu'}, 'electricity consumption must retain an energy unit, not demand')
    elif metric.startswith('water_'):
        require(unit in {'million gallons', 'million liters', 'million litres', 'megaliters', 'megalitres', 'ml',
                         'm3', 'm³', 'cubic meters', 'cubic metres', 'us gallons', 'gallons', 'kgal', 'thousand gallons'},
                'unsupported water volume unit')
    else:
        require('co2e' in unit or 'co₂e' in unit or unit in {'metric tons', 'metric tonnes'}, 'emissions unit lacks CO2e boundary')
    require(row.get('reporting_scope') == location.get('reporting_scope') and row['reporting_scope'] in SCOPES,
            'observation and entity boundaries differ')
    require(row.get('reporting_basis') and row.get('methodology_note'), 'reporting basis/limitations missing')
    year = row.get('data_year')
    require(isinstance(year, int) and not isinstance(year, bool) and 1990 <= year <= datetime.now(timezone.utc).year,
            'invalid reporting year')
    require(row.get('period_start') == f'{year}-01-01T00:00:00Z' and
            row.get('period_end') == f'{year + 1}-01-01T00:00:00Z', 'annual reporting period differs from year')
    sha = row.get('source_sha256') or row.get('pdf_sha256')
    require(isinstance(sha, str) and re.fullmatch('[a-f0-9]{64}', sha), 'source hash missing')
    require(https_url(row.get('source_url')) and bool(row.get('publisher')), 'primary source attribution missing')
    require(row.get('source_page') or row.get('source_field') or row.get('source_quote'), 'source locator missing')
    require(row.get('facility_binding_verified') is False, 'source adapter cannot independently assert an inventory join')


def google_locations(result):
    data = result['data']
    locations, observations = [], []
    for row in data.get('observations', []):
        key = 'google:' + re.sub('[^a-z0-9]+', '-', row['location_label'].lower()).strip('-')
        locations.append({'disclosure_location_id': key, 'operator': 'Google', 'location_label': row['location_label'],
                          'reporting_scope': 'operator_location', 'source_identifiers': {},
                          'scope_note': 'Named Google reporting location; may include multiple buildings.'})
        for metric, field in [('water_withdrawal', 'withdrawal_million_gallons'),
                              ('water_discharge', 'discharge_million_gallons'),
                              ('water_consumption', 'consumption_million_gallons')]:
            observations.append({'observation_id': f'{key}:{row["data_year"]}:{metric}', 'disclosure_location_id': key,
                'metric': metric, 'value': row[field], 'unit': row['unit'], 'data_year': row['data_year'],
                'period_start': row['observation_start'], 'period_end': row['observation_end'],
                'reporting_scope': 'operator_location', 'reporting_basis': row['measurement_basis'],
                'methodology_note': row['methodology_note'] + ' ' + row['unit_note'],
                'source_page': row['source_page'], 'pdf_sha256': row['pdf_sha256'], 'source_url': row['source_url'],
                'publisher': row['publisher'], 'facility_binding_verified': False,
                'assurance': row.get('assurance'), 'published_at': result.get('published_at'),
                'publication_month': data.get('source', {}).get('publication_month'), 'source_field': f'{row["location_label"]} / {metric}'})
    return locations, observations


def build_disclosures(states, records, release_id, built_at):
    locations, observations, source_ids = {}, {}, set()
    for sid, state in sorted(states.items()):
        result = state.get('result') or {}
        data = result.get('data') or {}
        if sid == 'google_water2025' and data:
            raw_locations, raw_observations = google_locations(result)
        elif isinstance(data, dict) and 'locations' in data and 'observations' in data:
            raw_locations, raw_observations = data['locations'], data['observations']
        else:
            continue
        source_ids.add(sid)
        for original in raw_locations:
            location = dict(original)
            key = location.get('disclosure_location_id')
            if not safe_id(key) or key in locations or not location.get('location_label') or location.get('reporting_scope') not in SCOPES:
                raise ValueError('Invalid or duplicate disclosure entity: ' + str(key))
            location.update(source_id=sid, observations=[], record_links=[], source_status=state.get('status'),
                            retrieved_at=state.get('retrieved_at') or state.get('last_success_at'))
            locations[key] = location
        for original in raw_observations:
            row = dict(original)
            key = row.get('disclosure_location_id')
            if key not in locations or locations[key]['source_id'] != sid:
                raise ValueError('Observation lacks its own source entity')
            validate_observation(row, locations[key])
            oid = row['observation_id']
            if oid in observations:
                raise ValueError('Duplicate observation ID: ' + oid)
            sha = row.get('source_sha256') or row.get('pdf_sha256')
            receipt = next((r for r in state.get('receipts', [])
                            if r.get('url') == row['source_url'] and r.get('sha256') == sha), None)
            row.update(source_id=sid, label=METRICS[row['metric']], source_status=state.get('status'),
                       retrieved_at=receipt.get('retrieved_at') if receipt else None,
                       checked_at=state.get('last_success_at'), source_receipt=receipt,
                       source_sha256=row.get('source_sha256') or row.get('pdf_sha256'),
                       inventory_allocation=False, value_origin='reported_disclosure')
            observations[oid] = row
            locations[key]['observations'].append(row)
    identity = (states.get('disclosure_identity', {}).get('result') or {}).get('data') or {}
    links = []
    if identity:
        from .facility_disclosure_identity import resolve_disclosure_links
        links = resolve_disclosure_links(records, identity)
    by_record = defaultdict(list)
    for link in links:
        key = link.get('disclosure_location_id') or link.get('disclosure_entity_id')
        if key not in locations:
            continue
        relation = link.get('relationship')
        if relation not in RELATIONSHIPS:
            raise ValueError('Unknown disclosure relationship')
        normalized = {**link, 'disclosure_location_id': key,
                      'source_status': states.get('disclosure_identity', {}).get('status'),
                      'identity_last_success_at': states.get('disclosure_identity', {}).get('last_success_at'),
                      'inventory_allocation': relation == 'same_physical_scope' and link.get('verification_status') == 'verified'
                      and link.get('allocation_allowed') is True and link.get('same_physical_scope_verified') is True}
        locations[key]['record_links'].append(normalized)
        if link.get('verification_status') == 'verified' and relation != 'candidate':
            by_record[link['record_id']].append({'location': locations[key], 'link': normalized})
    # Evidence stays on reporting entities. References in inventory cards do not
    # create more source observations, a physical-site merge or additive totals.
    metric_counts = Counter(row['metric'] for row in observations.values())
    exact = {group: set() for group in ('power', 'water', 'carbon')}
    for fid, groups in by_record.items():
        for group in groups:
            if not group['link']['inventory_allocation']:
                continue
            for row in group['location']['observations']:
                family = 'power' if row['metric'] == 'electricity_consumption' else 'water' if row['metric'].startswith('water_') else 'carbon'
                exact[family].add(fid)
    coverage = {'disclosure_locations': len(locations), 'disclosure_observations': len(observations),
                'records_with_linked_disclosures': len(by_record),
                'reviewed_disclosure_links': sum(l.get('verification_status') == 'verified' for loc in locations.values() for l in loc['record_links']),
                **{'actual_' + k + '_records': len(v) for k, v in exact.items()},
                **{'reported_' + family + '_locations': sum(any((o['metric'] == 'electricity_consumption' if family == 'electricity'
                    else o['metric'].startswith('water_') if family == 'water' else o['metric'].startswith('emissions_') or o['metric'] == 'reported_ghg_total')
                    for o in loc['observations']) for loc in locations.values()) for family in ('electricity', 'water', 'emissions')}}
    payload = {'release_id': release_id, 'built_at': built_at, 'schema_version': '1',
               'locations': list(locations.values()), 'observation_count': len(observations), 'metric_counts': dict(metric_counts),
               'coverage': coverage, 'identity_evidence': identity.get('evidence', []),
               'note': 'Reported annual values retain their source boundary. Related campus totals are not allocated to individual records; overlapping sources must not be added.'}
    return payload, dict(by_record), coverage, source_ids
