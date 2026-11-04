"""A failed identity document cannot verify a link or erase unrelated evidence."""
import copy
import hashlib
from datetime import datetime, timezone

import pytest

from wattdocket.production.facility_disclosure_identity import (
    collect_reviewed_identity, resolve_disclosure_links, DisclosureIdentityError)

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def spec(i):
    return {'id': f'county-{i}', 'url': f'https://county.gov/{i}', 'kind': 'html',
            'publisher': 'County', 'excerpts': [f'Operator at {i} Main Street']}


def link(i):
    return {'record_id': f'echo:{i}', 'disclosure_location_id': f'operator:campus-{i}',
            'relationship':'within_disclosure_location', 'allocation_allowed':False,
            'same_physical_scope_verified':False, 'evidence_ids':[f'county-{i}'],
            'expected_record':{'id':f'echo:{i}', 'registry_id':str(i), 'name':'Operator',
                               'address':f'{i} Main Street', 'city':'Town', 'state':'VA'}}


class Fetcher:
    def __init__(self, fail=()):
        self.fail, self.receipts = fail, {}
    def get_bytes(self, url):
        i = int(url.rsplit('/',1)[1])
        if i in self.fail: raise TimeoutError('publisher delayed')
        raw = f'<p>Operator at {i} Main Street</p>'.encode(); sha=hashlib.sha256(raw).hexdigest()
        self.receipts[url]={'url':url,'sha256':sha,'raw_key':f'raw/{sha}.gz','retrieved_at':'2026-10-01T00:00:00Z'}
        return raw


def collect(fetcher, **extra):
    return collect_reviewed_identity(fetcher, now=NOW, source_id='test_disclosure_identity', label='Test',
        evidence_specs=[spec(1),spec(2)], reviewed_links=[link(1),link(2)],
        review_version='test', reviewed_at='2026-11-04', **extra)


def test_document_failure_is_isolated_and_cannot_verify_its_dependent_record():
    result=collect(Fetcher(fail={2}),allow_partial=True)
    assert result.record_count==1 and result.data['documents_failed']==1
    records=[{**link(i)['expected_record']} for i in (1,2)]
    resolved=resolve_disclosure_links(records,result.data)
    assert [r['verification_status'] for r in resolved]==['verified','candidate']
    assert all(r['allocation_allowed'] is False for r in resolved)
    assert result.data['evidence'][0]['retrieved_at']=='2026-10-01T00:00:00Z'
    assert result.data['evidence'][0]['checked_at']=='2026-11-04T00:00:00Z'


def test_all_failed_is_error_never_an_empty_success():
    with pytest.raises(DisclosureIdentityError,match='No reviewed link'):
        collect(Fetcher(fail={1,2}),allow_partial=True)
    with pytest.raises(TimeoutError): collect(Fetcher(fail={2}))


def test_configuration_cannot_duplicate_evidence_or_reference_unreviewed_proofs():
    with pytest.raises(DisclosureIdentityError,match='missing reviewed evidence'):
        collect_reviewed_identity(Fetcher(),now=NOW,source_id='test',label='Test',
            evidence_specs=[spec(1)],reviewed_links=[link(2)],review_version='t',reviewed_at='2026-11-04')


def test_partial_refresh_demotes_only_failed_links_instead_of_retaining_old_verification(tmp_path, monkeypatch):
    from wattdocket.production import pipeline
    from wattdocket.production.storage import LocalStore, json_bytes
    sid = 'test_disclosure_identity'
    monkeypatch.setitem(pipeline.POLICIES, sid, {'minimum': 1, 'maximum': 2, 'interval': 1,
        'stale': 86400, 'allow_partial_evidence': True})
    store = LocalStore(tmp_path)
    old = collect(Fetcher(), allow_partial=True).as_dict()
    store.put(f'state/sources/{sid}.json', json_bytes({'id': sid, 'result': old, 'record_count': 2}))
    class F(Fetcher):
        def __init__(self, *args, **kwargs): super().__init__(fail={2})
    actual = pipeline.collect_one(sid, lambda fetcher, **kw: collect(fetcher, allow_partial=True),
        store, NOW, force=True, fetcher_factory=F)
    assert actual['record_count'] == 1 and actual['error'] is None and actual['partial']
    resolved = resolve_disclosure_links([link(i)['expected_record'] for i in (1, 2)], actual['result']['data'])
    assert [r['verification_status'] for r in resolved] == ['verified', 'candidate']
    altered = copy.deepcopy(actual['result'])
    altered['data']['links'].pop()
    assert not pipeline.explained_identity_drop(altered, {'result': old})
    altered = copy.deepcopy(actual['result'])
    altered['data']['links'][0]['expected_record']['address'] = 'Changed'
    assert not pipeline.explained_identity_drop(altered, {'result': old})


def test_identity_partition_provenance_and_duplicate_evidence_are_checked():
    from wattdocket.production.disclosures import build_disclosures
    from test_disclosure_publication_gate import sample
    payload, _, _ = sample()
    loc = copy.deepcopy(payload['locations'][0])
    row = loc.pop('observations')[0]
    loc.pop('record_links')
    states = {'operator_report': {'result': {'data': {'locations': [loc], 'observations': [row]}},
        'receipts': [row['source_receipt']], 'status': 'current'}}
    part = collect(Fetcher(), allow_partial=True).as_dict()
    part['data']['links'][0]['disclosure_location_id'] = loc['disclosure_location_id']
    states['test_disclosure_identity'] = {'result': part, 'status': 'stale', 'last_success_at': '2026-10-01T00:00:00Z'}
    _, by_record, _, _ = build_disclosures(states, [link(1)['expected_record']], 'release-test', '2026-11-04T00:00:00Z')
    bound = by_record['echo:1'][0]['link']
    assert bound['identity_source_id'] == 'test_disclosure_identity'
    assert bound['source_status'] == 'stale' and bound['identity_last_success_at'] == '2026-10-01T00:00:00Z'
    states['other_disclosure_identity'] = copy.deepcopy(states['test_disclosure_identity'])
    with pytest.raises(ValueError, match='Repeated identity evidence'):
        build_disclosures(states, [link(1)['expected_record']], 'release-test', '2026-11-04T00:00:00Z')
