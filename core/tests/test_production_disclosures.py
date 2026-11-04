"""Cross-source observations never become demand or an unreviewed site join."""
import copy

import pytest

from wattdocket.production.disclosures import build_disclosures, validate_observation


def report():
    location = {'disclosure_location_id': 'meta:example', 'location_label': 'Example', 'reporting_scope': 'campus'}
    row = {'observation_id': 'meta:example:2024:electricity', 'disclosure_location_id': 'meta:example',
           'metric': 'electricity_consumption', 'value': 1200, 'unit': 'MWh', 'data_year': 2024,
           'period_start': '2024-01-01T00:00:00Z', 'period_end': '2025-01-01T00:00:00Z',
           'reporting_scope': 'campus', 'reporting_basis': 'operator_reported',
           'methodology_note': 'Campus total may include multiple buildings.', 'source_sha256': 'a' * 64,
           'source_url': 'https://example.org/report.pdf', 'source_page': 6, 'publisher': 'Operator',
           'facility_binding_verified': False}
    return location, row


def test_reported_campus_energy_without_identity_never_fills_inventory_gaps():
    location, row = report()
    states = {'meta_actuals2024': {'status': 'stale', 'retrieved_at': '2026-11-03T00:00:00Z',
              'last_success_at': '2026-11-03T01:00:00Z',
              'receipts': [{'url':row['source_url'], 'sha256':row['source_sha256'], 'retrieved_at':'2026-10-20T00:00:00Z'}],
              'result': {'data': {'locations': [location], 'observations': [row]}}}}
    payload, links, coverage, sources = build_disclosures(states, [], 'release', '2026-11-03T01:00:00Z')
    assert links == {} and coverage['actual_power_records'] == 0
    assert coverage['reported_electricity_locations'] == 1 and coverage['disclosure_observations'] == 1
    actual = payload['locations'][0]['observations'][0]
    assert actual['value'] == 1200 and actual['unit'] == 'MWh'
    assert actual['inventory_allocation'] is False and actual['source_status'] == 'stale'
    assert actual['data_year'] == 2024 and actual['retrieved_at'] == '2026-10-20T00:00:00Z'
    assert actual['checked_at'] == '2026-11-03T01:00:00Z'


@pytest.mark.parametrize('key,value', [('unit', 'MW'), ('value', None), ('value', True), ('value', float('nan')),
    ('value', -1), ('data_year', 2030), ('period_end', '2026-01-01T00:00:00Z'), ('reporting_scope', 'building'),
    ('source_sha256', 'no'), ('facility_binding_verified', True), ('metric', 'power_capacity')])
def test_rejects_unit_scope_period_and_receipt_corruption(key, value):
    location, row = report(); row[key] = value
    with pytest.raises(ValueError): validate_observation(row, location)


def test_duplicate_observation_and_cross_source_entity_rejected():
    location, row = report()
    source = {'result': {'data': {'locations': [location], 'observations': [row, copy.deepcopy(row)]}}}
    with pytest.raises(ValueError, match='Duplicate observation'): build_disclosures({'s': source}, [], 'r', 't')
    source['result']['data']['observations'].pop()
    with pytest.raises(ValueError, match='duplicate disclosure entity'):
        build_disclosures({'s': source, 't': copy.deepcopy(source)}, [], 'r', 't')
