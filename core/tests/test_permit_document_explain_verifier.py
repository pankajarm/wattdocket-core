"""Standalone permit evidence must reconcile without an invented inventory join."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('permit_explain_verifier', ROOT / 'scripts/verify_explain_registry.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class Checks:
    def __init__(self):
        self.errors = []

    def require(self, condition, message):
        if not condition:
            self.errors.append(message)


@pytest.fixture
def release():
    release_id, built = 'permit-review-fixture', '2026-11-05T03:00:00Z'
    rows, subjects = [], {}
    for reg, partial in [('11790-1', False), ('73200-3', True)]:
        sha, url = ('a' if not partial else 'b') * 64, 'https://www.deq.virginia.gov/fixture/' + reg
        date = '2016-08-23' if not partial else '2007-10-18'
        review = {'status': 'verified', 'document_sha256': sha, 'registration': reg, 'publisher_url': url,
                  'document_extent': 'partial_amendment' if partial else 'full_permit'}
        row = {'registration': reg, 'content_sha256': sha, 'url': url, 'issued_at': date,
               'recovery_review': review, 'conditions': [], 'equipment_claims': [], 'inventory_record_ids': []}
        evidence = {'sha256': sha, 'url': url, 'locator': 'Original PDF page 1', 'quote': 'Registration ' + reg}
        review_text = 'This reviewed separate permit document has current effectiveness is unknown; no facility measurement or inventory allocation is inferred.'
        if partial:
            review_text += ' This is a partial amendment, not a complete permit.'
        facts = [{'id': 'review', 'kind': 'permit_document_review', 'scope': 'document_review', 'text': review_text,
                  'numbers': [], 'required_qualifiers': ['current effectiveness is unknown'], 'evidence': [evidence],
                  'period': {'issued_at': date, 'current_effectiveness_verified': False},
                  'source_selector': {'path': 'data-status.json', 'registration': reg, 'content_sha256': sha}}]
        if not partial:
            quote = 'Each engine shall not operate more than 500 hours per year.'
            condition = {'registration': reg, 'content_sha256': sha, 'url': url, 'issued_at': date,
                         'metric': 'generator_hours_limit', 'value': 500, 'unit': 'hours/year', 'quote': quote,
                         'is_document_condition': True, 'applies_to_entire_site': False, 'recovery_review': review}
            row['conditions'] = [condition]
            facts.append({'id': 'condition', 'kind': 'permit_condition', 'scope': 'quoted_condition',
                          'text': 'The 500 hours/year document condition covers only the equipment; not actual use; current effectiveness is unknown.',
                          'required_qualifiers': ['not actual use'],
                          'numbers': [{'value': 500, 'unit': 'hours/year', 'display': '500', 'label': 'generator_hours_limit'}],
                          'evidence': [{**evidence, 'quote': quote}],
                          'period': {'issued_at': date, 'current_effectiveness_verified': False},
                          'source_selector': {'path': 'data-status.json', 'registration': reg, 'metric': 'generator_hours_limit', 'content_sha256': sha, 'value': 500}})
            equipment_quote = 'Six listed generators each 2500 kW and one 600 kW.'
            claim = {'id': 'diesel-mw', 'value': 15.6, 'unit': 'MW nameplate', 'evidence_scope': 'equipment_table_page',
                     'is_complete_fleet': False, 'permit_metadata': {'registration': reg},
                     'citations': [{'doc_sha256': sha, 'quote': equipment_quote, 'url': url}]}
            row['equipment_claims'] = [claim]
            facts.append({'id': 'equipment', 'kind': 'equipment_nameplate', 'scope': 'equipment_table_page',
                          'text': 'The listed equipment is 15.6 MW nameplate, not a whole-site total; current effectiveness is unknown.',
                          'required_qualifiers': ['not a whole-site total'],
                          'numbers': [{'value': 15.6, 'unit': 'MW nameplate', 'display': '15.6', 'label': 'generator table capacity'}],
                          'evidence': [{**evidence, 'quote': equipment_quote}],
                          'period': {'issued_at': date, 'current_effectiveness_verified': False},
                          'source_selector': {'path': 'data-status.json', 'registration': reg, 'claim_id': 'diesel-mw'}})
        subjects['permit_document:' + reg] = {'kind': 'permit_document', 'id': reg, 'facts': facts,
            'fallback': {'text': ' '.join(f['text'] for f in facts), 'fact_ids': [f['id'] for f in facts]}}
        rows.append(row)
    coverage = {'explain_subjects': 2, 'explain_facts': 4, 'explain_subject_kinds': {'permit_document': 2}}
    registry = {'schema_version': '1', 'release_id': release_id, 'built_at': built, 'subjects': subjects, 'coverage': coverage, 'aliases': {}}
    objects = {'cards.json': {}, 'data-status.json': {'permit_recovery': {'documents': rows}}, 'explain-registry.json': registry}
    manifest = {'release_id': release_id, 'built_at': built, 'coverage': coverage}
    return objects, manifest


def check(release):
    objects, manifest = release
    checks = Checks()
    verifier.validate_explain_registry(objects, {k: json.dumps(v).encode() for k, v in objects.items()}, manifest, checks)
    return checks.errors


def test_unjoined_numeric_and_partial_permit_documents_are_explainable(release):
    assert check(release) == []
    assert release[0]['cards.json'] == {}


@pytest.mark.parametrize('damage', ['missing_document_subject', 'wrong_kind', 'selector_path', 'selector_registration',
                                  'quantity', 'unit', 'issue_date', 'source_quote', 'source_url', 'scope',
                                  'actual_use', 'current_effective', 'equipment_value', 'equipment_registration',
                                  'partial_document_scope', 'missing_review_fact'])
def test_standalone_permit_mismatches_are_rejected(release, damage):
    objects, manifest = release
    subjects = objects['explain-registry.json']['subjects']
    full = subjects['permit_document:11790-1']; condition = full['facts'][1]
    if damage == 'missing_document_subject':
        del subjects['permit_document:73200-3']
    elif damage == 'wrong_kind': full['kind'] = 'facility'
    elif damage == 'selector_path': condition['source_selector']['path'] = 'cards.json'
    elif damage == 'selector_registration': condition['source_selector']['registration'] = '73200-3'
    elif damage == 'quantity': condition['numbers'][0]['value'] = 300
    elif damage == 'unit': condition['numbers'][0]['unit'] = 'MW'
    elif damage == 'issue_date': condition['period']['issued_at'] = '2026-11-05'
    elif damage == 'source_quote': condition['evidence'][0]['quote'] = 'The whole campus consumes 500.'
    elif damage == 'source_url': condition['evidence'][0]['url'] += '/different-permit'
    elif damage == 'scope': condition['scope'] = 'whole_facility'
    elif damage == 'actual_use': objects['data-status.json']['permit_recovery']['documents'][0]['conditions'][0]['is_document_condition'] = False
    elif damage == 'current_effective': condition['period']['current_effectiveness_verified'] = True
    elif damage == 'equipment_value': full['facts'][2]['numbers'][0]['value'] = 15600
    elif damage == 'equipment_registration': objects['data-status.json']['permit_recovery']['documents'][0]['equipment_claims'][0]['permit_metadata']['registration'] = '73200-3'
    elif damage == 'partial_document_scope': subjects['permit_document:73200-3']['facts'][0]['text'] = 'Current effectiveness is unknown for this complete permit.'
    elif damage == 'missing_review_fact': full['facts'] = full['facts'][1:]
    assert check(release)
