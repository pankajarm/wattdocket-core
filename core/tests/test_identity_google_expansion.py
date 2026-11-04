"""Google coverage requires the reviewed physical address and all capture proofs."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import pytest

from wattdocket.production import identity_google_expansion as google
from wattdocket.production.facility_disclosure_identity import (
    DisclosureIdentityError, resolve_disclosure_links,
)

NOW = datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / 'fixtures/google_identity_inventory_2026_11_04.json'


class Captures:
    def __init__(self, monkeypatch):
        self.values, self.receipts = {}, {}
        specs = copy.deepcopy(google.EVIDENCE)
        for spec in specs:
            if spec['kind'] == 'pdf':
                document = pymupdf.open()
                for _ in range(spec['page']):
                    document.new_page(width=1500, height=842)
                document[-1].insert_text((20, 30), '\n'.join(spec['excerpts']), fontsize=9)
                raw = document.tobytes()
                document.close()
                spec['expected_sha256'] = hashlib.sha256(raw).hexdigest()
            elif spec['kind'] == 'json':
                raw = json.dumps([spec['row_selector']]).encode()
            else:
                raw = ('<html><body>' + '<p>'.join(spec['excerpts']) + '</body></html>').encode()
            self.set(spec['url'], raw)
        monkeypatch.setattr(google, 'EVIDENCE', specs)

    def set(self, url, raw):
        sha = hashlib.sha256(raw).hexdigest()
        self.values[url] = raw
        self.receipts[url] = {'url': url, 'sha256': sha, 'raw_key': f'raw/{sha}.gz',
                              'retrieved_at': '2026-11-04T18:00:00Z', 'cache_hit': True}

    def get_bytes(self, url):
        return self.values[url]

    def receipt(self, url):
        return self.receipts.get(url)


def inventory():
    return json.loads(FIXTURE.read_text())['facilities']


def collect(monkeypatch):
    fetcher = Captures(monkeypatch)
    return google.collect_google_identity(fetcher, now=NOW), fetcher


def test_reviewed_google_expansion_has_eight_locations_and_no_numeric_allocation(monkeypatch):
    result, _ = collect(monkeypatch)
    assert result.source_id == 'google_disclosure_identity'
    assert result.record_count == 11
    assert result.data['validation']['primary_documents'] == 22
    assert result.data['validation']['disclosure_locations'] == 8
    assert result.data['documents_failed'] == 0
    links = resolve_disclosure_links(inventory(), result.data)
    assert len(links) == 11
    assert all(x['verification_status'] == 'verified' for x in links)
    assert all(x['relationship'] == 'within_disclosure_location' for x in links)
    assert all(x['allocation_allowed'] is False and x['same_physical_scope_verified'] is False for x in links)
    assert all(x['coverage_is_exhaustive'] is False for x in links)
    assert sum(x['disclosure_location_id'] == 'google:mayes-county-ok' for x in links) == 2
    assert sum(x['disclosure_location_id'] == 'google:berkeley-county-sc' for x in links) == 2


@pytest.mark.parametrize('field', ['address_line', 'city', 'state', 'name', 'registry_id', 'program_ids', 'duplicate', 'missing'])
def test_mesa_current_record_identity_drift_withholds_link(monkeypatch, field):
    result, _ = collect(monkeypatch)
    records = inventory()
    record = next(x for x in records if x['id'] == 'echo:110072011959')
    if field == 'duplicate':
        records.append(copy.deepcopy(record))
    elif field == 'missing':
        records.remove(record)
    else:
        record[field] = {} if field == 'program_ids' else 'changed'
    link = next(x for x in resolve_disclosure_links(records, result.data) if x['record_id'] == record['id'])
    assert link['verification_status'] == 'candidate'
    assert link['relationship'] == 'candidate'
    assert link['allocation_allowed'] is False


@pytest.mark.parametrize('problem', ['wrong_parcel', 'mailing_address', 'duplicate_row', 'receipt_hash', 'receipt_raw_key', 'no_receipt'])
def test_mesa_parcel_or_receipt_failure_withholds_only_dependent_location(monkeypatch, problem):
    fetcher = Captures(monkeypatch)
    spec = next(x for x in google.EVIDENCE if x['id'] == 'google-expansion-mesa-address')
    if problem in ('wrong_parcel', 'mailing_address', 'duplicate_row'):
        row = copy.deepcopy(spec['row_selector'])
        if problem == 'wrong_parcel':
            row['parcel_number'] = 'APPLE-PLATYPUS-PARCEL'
        elif problem == 'mailing_address':
            row['mailing_address'] = row.pop('property_address')
        fetcher.set(spec['url'], json.dumps([row, row] if problem == 'duplicate_row' else [row]).encode())
    elif problem == 'no_receipt':
        del fetcher.receipts[spec['url']]
    elif problem == 'receipt_hash':
        fetcher.receipts[spec['url']]['sha256'] = '0' * 64
    else:
        fetcher.receipts[spec['url']]['raw_key'] = 'raw/unrelated.gz'
    result = google.collect_google_identity(fetcher, now=NOW)
    assert result.record_count == 10
    assert result.data['documents_failed'] == 1
    links = resolve_disclosure_links(inventory(), result.data)
    assert sum(x['verification_status'] == 'verified' for x in links) == 10
    mesa = next(x for x in links if x['disclosure_location_id'] == 'google:mesa-az')
    assert mesa['verification_status'] == 'candidate'


@pytest.mark.parametrize('problem', ['missing_passage', 'script_only', 'changed_pdf', 'wrong_pdf_page'])
def test_changed_primary_evidence_does_not_reuse_previous_verified_link(monkeypatch, problem):
    fetcher = Captures(monkeypatch)
    spec = next(x for x in google.EVIDENCE if x['id'] == ('google-expansion-lenoir-county' if problem in ('missing_passage', 'script_only') else 'google-expansion-lenoir-permit'))
    if problem == 'missing_passage':
        fetcher.set(spec['url'], b'<html>Google campus, address not supplied</html>')
    elif problem == 'script_only':
        fetcher.set(spec['url'], ('<html><script>' + ' '.join(spec['excerpts']) + '</script></html>').encode())
    elif problem == 'changed_pdf':
        fetcher.set(spec['url'], fetcher.values[spec['url']] + b'changed')
    else:
        spec['page'] -= 1
    result = google.collect_google_identity(fetcher, now=NOW)
    assert result.record_count == 10
    assert result.data['documents_failed'] == 1
    link = next(x for x in resolve_disclosure_links(inventory(), result.data) if x['disclosure_location_id'] == 'google:lenoir-nc')
    assert link['verification_status'] == 'candidate'


def test_apple_other_addresses_and_old_semiconductor_are_not_linked(monkeypatch):
    result, _ = collect(monkeypatch)
    records = inventory()
    # A same-address new registration is also unreviewed, even if every label matches.
    sibling = copy.deepcopy(records[0])
    sibling.update(id='echo:199999999999', registry_id='199999999999')
    records.append(sibling)
    linked = {x['record_id'] for x in resolve_disclosure_links(records, result.data)}
    assert 'echo:199999999999' not in linked
    assert not linked.intersection({'echo:110062853416', 'echo:110043192586', 'echo:110070251348',
                                    'echo:110070866661', 'echo:110044930778', 'echo:110070595478'})


def test_identity_document_dates_do_not_become_observation_or_retrieval_dates(monkeypatch):
    result, _ = collect(monkeypatch)
    evidence = {x['id']: x for x in result.data['evidence']}
    assert evidence['google-expansion-iowa-alias-state']['document_period_end'] == '2015-06-30'
    assert evidence['google-expansion-montgomery']['published_at'] is None
    assert evidence['google-expansion-mesa-parcel']['document_date'] == '2023-05-31'
    assert all(x['retrieved_at'] == '2026-11-04T18:00:00Z' for x in evidence.values())
    assert all(x['checked_at'] == '2026-11-04T22:00:00Z' for x in evidence.values())
    assert not any('value' in x or 'data_year' in x for x in result.data['links'])


def test_no_valid_evidence_fails_source_instead_of_publishing_empty_success(monkeypatch):
    fetcher = Captures(monkeypatch)
    fetcher.receipts.clear()
    with pytest.raises(DisclosureIdentityError, match='No reviewed link'):
        google.collect_google_identity(fetcher, now=NOW)
