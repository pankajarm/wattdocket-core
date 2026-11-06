"""Independent release checks reject cross-site permit assignment and invented status."""
import copy
import importlib.util
from pathlib import Path
import pytest

SPEC=importlib.util.spec_from_file_location('currentness_public_gate',Path(__file__).resolve().parents[2]/'scripts/verify_permit_recovery.py')
gate=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(gate)


def fixture():
    def evidence(url,digest):
        return {'url':url,'sha256':digest,'locator':'Reviewed publisher row',
                'receipt':{'url':url,'sha256':digest,'retrieved_at':'2026-11-05T03:00:00Z','raw_key':'raw/'+digest+'.gz'}}
    gis=evidence('https://gisdata.deq.virginia.gov/arcgis/rest/services/public/EDMA/MapServer/294/query','a'*64)
    gis['record']={'PLA_REG_NUM':73567,'PLA_ID':40582,'PLA_ICIS_ID':'VA0000005105900936','FAC_L_STATE':'VA'}
    air={'RegistryID':'110031222451','SourceID':'VA0000005105900936','OtherPermitIDs':'73567','AIRStreet':'Physical location','AIRCity':'Fairfax','AIRState':'VA','AIRFIPSCode':'51059'}
    link={'record_id':'echo:110031222451','relationship':'same_regulator_record','verification_status':'verified','method':gate.REVIEWED_IDENTITY_METHOD,
          'expected_record':{'id':'echo:110031222451','air_record':air},'evidence':[gis,evidence('https://echodata.epa.gov/echo/air-row','b'*64)]}
    site={'status':'regulator_site_verified','registration_root':'73567','evidence':[gis],'inventory_links':[link]}
    permit={'registration':'73567-1','county_fips':'059','site_identity_review':site}
    facility={'id':'echo:110031222451','identity_match':{'reviewed_links':[link]}}
    return facility,permit


def test_legacy_plant_id_requires_reciprocal_regulator_cross_reference():
    facility,permit=fixture()
    assert gate.reviewed_identity_supported(facility,permit)


@pytest.mark.parametrize('damage',['county','registration','icis','frs','receipt','candidate','stale','absent_card_proof'])
def test_forged_site_link_fails_independent_gate(damage):
    facility,permit=fixture();site=permit['site_identity_review'];link=site['inventory_links'][0]
    air=link['expected_record']['air_record']
    if damage=='county':air['AIRFIPSCode']='51107'
    elif damage=='registration':air['OtherPermitIDs']='173567'
    elif damage=='icis':air['SourceID']='VA0000005105900001'
    elif damage=='frs':air['RegistryID']='110000000000'
    elif damage=='receipt':link['evidence'][1]['receipt']['sha256']='c'*64
    elif damage=='candidate':link['relationship']='candidate'
    elif damage=='stale':site['status']='review_required'
    else:facility['identity_match']['reviewed_links']=[]
    assert not gate.reviewed_identity_supported(facility,permit)


class Checks:
    def __init__(self):self.errors=[]
    def require(self,condition,message):
        if not condition:self.errors.append(message)


def test_active_site_is_not_evidence_of_current_legal_conditions():
    row={'registration':'73567-1','url':'https://example.gov/permit.pdf','content_sha256':'d'*64,
         'permit_history_review':{'review_version':'va-history-1','status':'verified','registration':'73567-1','publisher_url':'https://example.gov/permit.pdf','document_sha256':'d'*64,
                                 'legal_effectiveness':{'status':'source_confirmed_current','scope':'entire_document','evidence':[]}}}
    checks=Checks();gate.validate_permit_context(row,checks)
    assert any('explicit scoped original evidence' in error for error in checks.errors)
