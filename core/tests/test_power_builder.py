from copy import deepcopy
import json
from pathlib import Path
import pytest
from wattdocket.production.facility_power import build_facility_power


def source_state():
    objects=json.loads((Path(__file__).parent/'fixtures/facility_power_publication.json').read_text())
    index=objects['facility-power.json']
    desc=next(x for x in index['series'] if x['source_id']=='nrel_esif_power')
    series=deepcopy(objects[desc['readings_path']])
    site=deepcopy(next(x for x in index['sites'] if x['site_id']==series['site_id']))
    receipts=[series['source_receipt'],*[p['receipt'] for p in site['identity_evidence']],*[p['receipt'] for p in series['method_evidence']],series['license']['receipt']]
    receipts=list({(r['url'],r['sha256']):r for r in receipts}.values())
    state={'result':{'data':{'power_sites':[site],'power_series':[series]}},'receipts':receipts,'last_success_at':series['checked_at'],'status':'current'}
    return state,series


def test_reviewed_esif_capture_passes_builder_and_omits_signed_redirect_urls():
    state,series=source_state()
    for receipt in state['receipts']:receipt['final_url']='https://example.gov/archive?X-Amz-Signature=private-transport'
    files,coverage=build_facility_power({'nrel_esif_power':state},'test','2026-11-04T23:00:00Z')
    assert coverage['power_interval_series']==1
    assert all(b'X-Amz-Signature' not in raw for raw in files.values())


@pytest.mark.parametrize('damage',['method_relabel','whole_facility','interval','mean','count','gap','duplicates','invented_value'])
def test_esif_source_identity_cannot_bypass_aggregation_and_boundary_gate(damage):
    state,series=source_state()
    row=next(r for r in series['readings'] if r[1] is not None)
    if damage=='method_relabel':
        series['measurement_basis']='publisher_reported_meter_power';row[1]*=2
    elif damage=='whole_facility':series['whole_facility']=True
    elif damage=='interval':series['interval_minutes']=30
    elif damage=='mean':row[1]*=2
    elif damage=='count':row[5]=44
    elif damage=='gap':row[6]=301
    elif damage=='duplicates':row[8]=1
    elif damage=='invented_value':
        row[1]=123;row[2:5]=[None,None,None];row[5:]=[0,3600,0,0]
    with pytest.raises(ValueError):build_facility_power({'nrel_esif_power':state},'test','2026-11-04T23:00:00Z')
