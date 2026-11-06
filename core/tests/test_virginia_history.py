"""Reviewed physical identity is separate from issued-document legal effect."""
import copy
import hashlib
import json

import pytest

from wattdocket.production.permit_history import apply_permit_history, reviewed_history, METHOD
from wattdocket.production.identity import match_virginia_permits
from wattdocket.production.sources_virginia_sites import parse_virginia_sites, REGISTRATION_ROOTS, QUERY_URL
from wattdocket.production.pipeline import POLICIES, adapters


def context(registration='81609-5'):
    rule=copy.deepcopy(next(r for r in reviewed_history() if r['registration']==registration))
    permits=[]
    for row in rule['registry_rows']:
        guard=rule['collected_document_guards'][row['registration']]
        permits.append({'registration':row['registration'],'permit_url':row['url'],'issued_iso':row['issued_at'],
            'program':row['program'],'county':'Harrisonburg City' if registration=='81609-5' else 'Loudoun Co.',
            'document':{'content_sha256':guard['sha256'],'representation':guard['representation']}})
    # Actual county names come from the reviewed source, never guessed by a join.
    from wattdocket.classify import VA_COUNTY_FIPS
    county=rule['expected_air_fields']['AIRFIPSCode'][2:]
    for p in permits:p['county']=next(k for k,v in VA_COUNTY_FIPS.items() if v==county)
    air=rule['expected_air_fields'];fid='echo:'+air['RegistryID']
    record={'id':fid,'state':'VA','address_line':'139 TYCO STREET','air_records':[copy.deepcopy(air)],'name':'Source record'}
    a=copy.deepcopy(rule['expected_gis_fields']);raw=json.dumps({'features':[{'attributes':a}]}).encode();sha=hashlib.sha256(raw).hexdigest()
    site={'registration_root':registration.split('-')[0],'regulator_site_id':'va-deq:'+str(a['PLA_ID']),
        'attributes':a,'physical_address':{'street':a['FAC_L_ADDR_1'],'city':a['FAC_L_CITY'],'state':'VA'},
        'site_status':{'air_operating_status':a['AIR_OP_STATUS']}}
    receipt={'url':QUERY_URL,'sha256':sha,'raw_key':'raw/'+sha+'.gz','retrieved_at':'2026-11-05T03:00:00Z'}
    states={'virginia_site_registry':{'status':'current','error':None,'last_success_at':'2026-11-05T03:30:00Z',
        'result':{'data':{'sites':[site],'query_url':QUERY_URL,'source_sha256':sha,'source_receipt':receipt}}},
        'virginia_permits':{'status':'current','error':None,'last_success_at':'2026-11-05T03:30:00Z',
            'result':{'data':{'registry_receipt':rule['registry_receipt'],'registry_as_of':'2026-11-02'}}}}
    return rule,permits,record,states


def reviewed(registration='81609-5', change=None):
    rule,permits,record,states=context(registration)
    if change:change(rule,permits,record,states)
    rows=apply_permit_history(permits,[record],states,built_at='2026-11-05T04:00:00Z',rules=[rule])
    return next(p for p in rows if p['registration']==registration),record,rows


def test_legacy_icis_crossref_links_without_changing_conflicting_frs_address():
    permit,record,rows=reviewed()
    match=match_virginia_permits(record,rows)
    assert match['identity_match']['method']==METHOD
    assert permit['site_identity_review']['status']=='regulator_site_verified'
    assert permit['site_identity_review']['inventory_links'][0]['combined_address_conflict']
    assert record['address_line']=='139 TYCO STREET'
    assert permit['site_identity_review']['physical_address']['street']=='1175 North Main Street'
    assert permit['permit_history_review']['legal_effectiveness']['status']=='unknown'


@pytest.mark.parametrize('field,value',[('SourceID','VA0000005166000999'),('OtherPermitIDs','81610'),
    ('AIRStreet','139 TYCO STREET'),('AIRCity','RESTON'),('AIRFIPSCode','51059'),('RegistryID','110000000001')])
def test_changed_program_identity_withholds_link(field,value):
    permit,record,rows=reviewed(change=lambda r,p,f,s:f['air_records'][0].update({field:value}))
    assert permit['site_identity_review']['inventory_links']==[]
    assert not match_virginia_permits(record,rows)['matches']


@pytest.mark.parametrize('field,value',[('PLA_ICIS_ID','VA0000005166000999'),('FAC_L_ADDR_1','139 Tyco Street'),('PLA_ID',999)])
def test_changed_deq_site_requires_review(field,value):
    permit,_,_=reviewed(change=lambda r,p,f,s:s['virginia_site_registry']['result']['data']['sites'][0]['attributes'].update({field:value}))
    assert permit['site_identity_review']['status']=='review_required'
    assert not permit['site_identity_review']['inventory_links']


@pytest.mark.parametrize('status',['failed','not_configured'])
def test_failed_without_usable_state_does_not_renew_site_link(status):
    permit,_,_=reviewed(change=lambda r,p,f,s:s['virginia_site_registry'].update(status=status))
    assert permit['site_identity_review']['source_freshness']==status
    assert permit['site_identity_review']['status']=='review_required'
    assert permit['permit_history_review']['status']=='verified'


def test_no_inventory_record_still_verifies_regulator_physical_site():
    rule,permits,record,states=context()
    row=apply_permit_history(permits,[],states,built_at='2026-11-05T04:00:00Z',rules=[rule])[0]
    assert row['site_identity_review']['status']=='regulator_site_verified'
    assert row['site_identity_review']['inventory_links']==[]


def test_new_registry_document_invalidates_history_not_site_identity():
    def mutate(r,p,f,s):p.append({**p[0],'registration':'81609-99','issued_iso':'2026-11-05'})
    permit,_,_=reviewed(change=mutate)
    assert permit['permit_history_review']['status']=='review_required'
    assert permit['permit_history_review']['registry_listing']['status']=='listed_with_later_document'
    assert permit['site_identity_review']['status']=='regulator_site_verified'


@pytest.mark.parametrize('field,value',[('permit_url','https://example.org/replaced.pdf')])
def test_changed_original_url_withholds_review_and_identity(field,value):
    permit,_,_=reviewed(change=lambda r,p,f,s:next(x for x in p if x['registration']==r['registration']).update({field:value}))
    assert permit['permit_history_review']['status']=='review_required'
    assert permit['site_identity_review']['status']=='review_required'


def test_changed_original_hash_withholds_review():
    permit,_,_=reviewed(change=lambda r,p,f,s:next(x for x in p if x['registration']==r['registration'])['document'].update(content_sha256='a'*64))
    assert permit['permit_history_review']['status']=='review_required'
    assert permit['site_identity_review']['status']=='review_required'


def test_active_site_latest_index_does_not_establish_legal_effect():
    permit,_,_=reviewed()
    assert permit['site_identity_review']['site_status']['air_operating_status']=='Operating'
    assert permit['permit_history_review']['legal_effectiveness']['status']=='unknown'
    assert permit['permit_history_review']['checked_at']==next(r for r in reviewed_history() if r['registration']=='81609-5')['registry_receipt']['retrieved_at']


def test_current_assertion_requires_exact_scoped_affirmative_evidence():
    def mutate(r,p,f,s):r['legal_effectiveness']={'status':'source_confirmed_current','scope':'entire_document','evidence':[]}
    with pytest.raises(ValueError,match='affirmative evidence'):reviewed(change=mutate)


def test_exact_scoped_current_assertion_is_supported_but_withdrawn_on_changed_chain():
    def mutate(r,p,f,s):
        evidence=copy.deepcopy(r['air_evidence']);evidence['quote']='Test-only affirmative exact-document status assertion'
        r['legal_effectiveness']={'status':'source_confirmed_current','scope':'specified_conditions','condition_refs':['condition-1'],
            'evidence':[evidence],'explicit_exact_document_or_condition_assertion':True}
    permit,_,_=reviewed(change=mutate)
    assert permit['permit_history_review']['legal_effectiveness']['status']=='source_confirmed_current'
    def changed(r,p,f,s):mutate(r,p,f,s);p.append({**p[0],'registration':'81609-99'})
    permit,_,_=reviewed(change=changed)
    assert permit['permit_history_review']['legal_effectiveness']['status']=='unknown'


def test_identity_receipt_hash_tamper_is_rejected():
    def mutate(r,p,f,s):r['air_evidence']['receipt']['sha256']='a'*64
    with pytest.raises(ValueError,match='archived evidence'):reviewed(change=mutate)


def test_pending_and_proposed_actions_do_not_become_issued_replacements():
    rules={r['registration']:r for r in reviewed_history()}
    assert any(e['type']=='pending_application' and 'not an issued replacement' in e['scope'] for e in rules['73200-3']['events'])
    assert any(e['type']=='proposed_title_v_action' and 'not final issuance' in e['scope'] for e in rules['73370-12']['events'])
    assert all(r['legal_effectiveness']['status']=='unknown' for r in rules.values())


def test_shutdown_status_is_preserved():
    rule=next(r for r in reviewed_history() if r['registration']=='11541-3')
    assert rule['expected_gis_fields']['AIR_OP_STATUS']=='Temporarily Shutdown'


def test_daily_bounded_lane_is_registered_without_changing_inventory_budget():
    policy=POLICIES['virginia_site_registry']
    assert policy['minimum']==policy['maximum']==13
    assert policy['fetcher']=={'source_budget_seconds':30,'max_bytes':1_000_000}
    assert policy['interval']==86400 and not policy['required']
    assert 'virginia_site_registry' in adapters()


def test_gis_parser_rejects_error_payload_duplicates_and_excess_roots():
    a=reviewed_history()[0]['expected_gis_fields'];payload={'features':[{'attributes':a}]}
    sites,missing=parse_virginia_sites(json.dumps(payload).encode())
    assert len(sites)==1 and len(missing)==12
    for bad in ({'error':{'message':'Service not found'}},{**payload,'exceededTransferLimit':True},
                {'features':payload['features']*2},{'features':[]}):
        with pytest.raises(ValueError):parse_virginia_sites(json.dumps(bad).encode())


def test_explanation_context_preserves_pending_and_boundary_qualifiers():
    from wattdocket.production.explain_registry import _permit_context_facts
    for reg in ('73200-3','73370-12'):
        rule,permits,record,states=context(reg)
        # This fixture needs its own correctly derived county; context sets it from AIR.
        permit=next(p for p in apply_permit_history(permits,[record],states,built_at='2026-11-05T04:00:00Z',rules=[rule]) if p['registration']==reg)
        public={**permit,'content_sha256':rule['document_sha256']}
        facts=_permit_context_facts({'id':reg},public,standalone=True)
        history=next(f for f in facts if f['kind']=='permit_history_review')
        assert len(history['text']) <= 900
        assert 'not final issuance' in history['text']
        assert 'not an issued replacement' in history['text']
        assert 'Current legal effectiveness is unknown' in history['required_qualifiers']
        site=next(f for f in facts if f['kind']=='regulator_site_identity')
        assert 'not a whole-campus or meter boundary' in site['required_qualifiers']
        assert site['source_selector']['field']=='site_identity_review'


def test_packaged_reviews_contain_no_request_secrets_or_local_paths():
    raw=json.dumps(reviewed_history())
    for secret in ('Set-Cookie','set-cookie','response_headers','request_headers','/Users/','/tmp/'):
        assert secret not in raw


def test_recent_failed_refresh_retains_exact_dated_identity_with_visible_error():
    def mutate(r,p,f,s):
        s['virginia_site_registry'].update(status='stale', error='DEQ HTTP200 JSON404: Service not found', last_attempt_at='2026-11-05T03:50:00Z')
    permit,record,rows=reviewed(change=mutate)
    site=permit['site_identity_review']
    assert site['status']=='regulator_site_verified' and site['inventory_links']
    assert site['source_freshness']=='stale' and site['retained_last_good'] is True
    assert site['retrieved_at']=='2026-11-05T03:00:00Z'
    assert site['checked_at']=='2026-11-05T03:30:00Z'
    assert site['latest_fetch_at']=='2026-11-05T03:50:00Z'
    assert site['latest_fetch_status']=='failed' and 'Service not found' in site['latest_fetch_error']
    assert site['capture_age_seconds']==3600
    assert match_virginia_permits(record,rows)['identity_match']['method']==METHOD


@pytest.mark.parametrize('status',['current','stale'])
@pytest.mark.parametrize('captured',['2026-11-02T03:59:59Z','2026-11-05T04:00:01Z',None,'invalid'])
def test_expired_future_or_invalid_original_capture_withdraws_identity(status,captured):
    def mutate(r,p,f,s):
        s['virginia_site_registry']['status']=status
        s['virginia_site_registry']['result']['data']['source_receipt']['retrieved_at']=captured
    permit,record,rows=reviewed(change=mutate)
    assert permit['site_identity_review']['status']=='review_required'
    assert not permit['site_identity_review']['inventory_links']
    assert not match_virginia_permits(record,rows)['matches']


def test_exact_three_day_boundary_is_admitted_without_renewing_original_date():
    def mutate(r,p,f,s):
        s['virginia_site_registry'].update(status='stale',error='refresh failed')
        s['virginia_site_registry']['result']['data']['source_receipt']['retrieved_at']='2026-11-02T04:00:00Z'
    permit,_,_=reviewed(change=mutate)
    assert permit['site_identity_review']['status']=='regulator_site_verified'
    assert permit['site_identity_review']['capture_age_seconds']==3*86400
    assert permit['site_identity_review']['retrieved_at']=='2026-11-02T04:00:00Z'


def test_stale_capture_does_not_override_changed_positive_identity_evidence():
    def mutate(r,p,f,s):
        source=s['virginia_site_registry'];source.update(status='stale',error='refresh failed')
        source['result']['data']['sites'][0]['attributes']['FAC_L_ADDR_1']='Different physical address'
    permit,record,rows=reviewed(change=mutate)
    assert permit['site_identity_review']['status']=='review_required'
    assert not permit['site_identity_review']['inventory_links']
    assert not match_virginia_permits(record,rows)['matches']


def test_stale_site_explanation_requires_original_capture_date_and_delayed_refresh():
    from wattdocket.production.explain_registry import _permit_context_facts
    rule,permits,record,states=context()
    states['virginia_site_registry'].update(status='stale',error='Service not found',last_success_at='2026-11-06T03:00:00Z',last_attempt_at='2026-11-06T03:30:00Z')
    rows=apply_permit_history(permits,[record],states,built_at='2026-11-06T04:00:00Z',rules=[rule])
    permit=next(p for p in rows if p['registration']==rule['registration'])
    facts=_permit_context_facts({'id':rule['registration']},{**permit,'content_sha256':rule['document_sha256']},standalone=True)
    site=next(f for f in facts if f['kind']=='regulator_site_identity')
    assert 'source captured on 2026-11-05' in site['text']
    assert '2026-11-06' not in site['text']
    assert '2026-11-05' in site['required_qualifiers']
    assert 'last-known site record; refresh delayed' in site['text']
    assert 'last-known site record; refresh delayed' in site['required_qualifiers']
