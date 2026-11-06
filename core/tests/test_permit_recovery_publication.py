"""Reviewed PDF scope must survive publication and model input generation."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from wattdocket.production.build import _permit_review
from wattdocket.production.explain_registry import build_explain_registry

FIXTURES = Path(__file__).parent / 'fixtures/explain_registry'
META = json.loads((FIXTURES / 'fixture.json').read_text())
KEY = 'echo_va0000005110774287'


def reviewed_files(*, extent='full_permit', transcription=False):
    files = {str(p.relative_to(FIXTURES)): p.read_bytes() for p in FIXTURES.rglob('*.json') if p.name != 'fixture.json'}
    cards = json.loads(files['cards.json'])
    facility = cards[KEY]['facility']
    limit = facility['permit_limits'][0]
    review = {'status': 'verified', 'review_version': 'va-reviewed-pdf-1', 'document_extent': extent,
              'registration': limit['registration'], 'publisher_url': limit['url'],
              'document_sha256': limit['content_sha256'],
              'eligible_condition_pages': sorted({p['page'] for p in facility['permit_limits']}),
              'eligible_equipment_pages': [],
              'registry_listing': {'county_fips': limit['county_fips'], 'issued_at': limit['issued_at'], 'checked_at': META['built_at']},
              'supersession_review': {'current_legal_effectiveness': 'unknown'}}
    for row in facility['permit_limits']:
        row.update(extraction_version='va-reviewed-pdf-1', recovery_review=copy.deepcopy(review),
                   representation='reviewed_pdf_transcription' if transcription else 'publisher_pdf',
                   retrieved_at=META['built_at'], archived_by_at=None, freshness='current', page_derivation='PDF page index')
    facility['permit_reviews'] = [{k: copy.deepcopy(v) for k, v in facility['permit_limits'][0].items()
                                 if k not in {'metric', 'value', 'unit', 'quote', 'page'}}]
    files['cards.json'] = json.dumps(cards).encode()
    return files, cards


def facts(files):
    registry, _ = build_explain_registry(files, release_id=META['release_id'], built_at=META['built_at'])
    return registry['subjects']['facility:echo:VA0000005110774287']['facts']


def test_reviewed_pdf_facts_preserve_partial_amendment_boundary():
    files, _ = reviewed_files(extent='partial_amendment')
    found = [f for f in facts(files) if f['kind'] in {'permit_condition', 'permit_document_review'}]
    assert len(found) == 4
    assert all('partial amendment' in f['required_qualifiers'] and 'not a complete permit' in f['required_qualifiers'] for f in found)
    assert all('current effectiveness is unknown' in f['required_qualifiers'] for f in found)


def test_transcribed_quantity_requires_visible_correction_disclosure():
    files, _ = reviewed_files(transcription=True)
    found = [f for f in facts(files) if f['kind'] == 'permit_condition']
    assert found
    assert all('visually checked transcription' in f['required_qualifiers'] for f in found)


@pytest.mark.parametrize('mutation', ['hash', 'registration', 'url', 'status', 'pages', 'current'])
def test_unbound_review_cannot_authorize_numeric_muse_facts(mutation):
    files, cards = reviewed_files()
    for p in cards[KEY]['facility']['permit_limits']:
        r = p['recovery_review']
        if mutation == 'hash': r['document_sha256'] = '0' * 64
        elif mutation == 'registration': r['registration'] = '99999-1'
        elif mutation == 'url': r['publisher_url'] = 'https://example.org/wrong.pdf'
        elif mutation == 'status': r['status'] = 'review_required'
        elif mutation == 'pages': r['eligible_condition_pages'] = []
        else: r['supersession_review']['current_legal_effectiveness'] = 'current'
    files['cards.json'] = json.dumps(cards).encode()
    assert not [f for f in facts(files) if f['kind'] == 'permit_condition']


def test_amendment_review_remains_explainable_without_numeric_conditions():
    files, cards = reviewed_files(extent='partial_amendment')
    cards[KEY]['facility']['permit_limits'] = []
    files['cards.json'] = json.dumps(cards).encode()
    assert any(f['kind'] == 'permit_document_review' and 'not a complete permit' in f['text'] for f in facts(files))


def test_reviewed_publication_rejects_changed_original_pdf():
    _, cards = reviewed_files()
    row = cards[KEY]['facility']['permit_reviews'][0]
    permit = {'registration': row['registration'], 'permit_url': row['url'], 'issued_iso': row['issued_at'],
              'county': 'Loudoun Co.', 'program': 'Article 6',
              'document': {**row, 'status': 'parsed'}}
    assert _permit_review(permit)['recovery_review'] == row['recovery_review']
    permit['document']['content_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='does not identify'):
        _permit_review(permit)


def test_independent_public_gate_rejects_condition_outside_reviewed_pages():
    spec = importlib.util.spec_from_file_location('verify_recovery', Path(__file__).resolve().parents[2] / 'scripts/verify_permit_recovery.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, cards = reviewed_files()
    row = copy.deepcopy(cards[KEY]['facility']['permit_reviews'][0])
    row['inventory_record_ids'] = [cards[KEY]['facility']['id']]
    objects = {'cards.json': {KEY: cards[KEY]}, 'data-status.json': {'permit_recovery': {'documents': [row]}}}
    class Checks:
        def __init__(self): self.errors = []
        def require(self, value, message):
            if not value: self.errors.append(message)
    checks = Checks()
    module.validate_permit_recovery(objects, {'reviewed_permit_documents': 1, 'records_with_reviewed_permits': 1}, checks)
    assert not checks.errors
    cards[KEY]['facility']['permit_limits'][0]['page'] = 999
    module.validate_permit_recovery(objects, {'reviewed_permit_documents': 1, 'records_with_reviewed_permits': 1}, checks)
    assert any('outside reviewed PDF/pages' in e for e in checks.errors)
