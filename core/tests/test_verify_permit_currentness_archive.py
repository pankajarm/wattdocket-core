"""Adversarial offline checks for the independent review/archive verifier."""
import copy
import importlib.util
import json
import io
from pathlib import Path
import zipfile

import pymupdf
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/verify_permit_currentness_archive.py'
spec = importlib.util.spec_from_file_location('currentness_archive_audit', SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def fixture():
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((50, 70), 'Registration No. 73567\nFairfax County\n1807 Michael Faraday Court\nThis amendment replaces page 6 only.')
    raw_pdf = pdf.tobytes()
    gs = {'PLA_ID': 40582, 'PLA_REG_NUM': 73567, 'PLA_ICIS_ID': 'VA0000005105900936',
          'FAC_L_ADDR_1': '1807 Michael Faraday Ct', 'FAC_L_CITY': 'Reston', 'FAC_L_STATE': 'VA',
          'AIR_OP_STATUS': 'Operating', 'STATUS_GROUP': 'Active', 'PLA_DATA_CENTER_YN': 'Y'}
    ar = {'RegistryID': '110031222451', 'SourceID': 'VA0000005105900936', 'OtherPermitIDs': '73567',
          'AIRStreet': '1807 MICHAEL FARADAY CT', 'AIRCity': 'RESTON', 'AIRFIPSCode': '51059'}
    blobs = {}
    receipts = []

    def add(url, raw):
        digest = audit.sha(raw)
        blobs[digest] = raw
        receipt = {'url': url, 'sha256': digest, 'retrieved_at': '2026-11-05T03:00:00Z',
                   'bytes': len(raw), 'http_status': 200}
        receipts.append(receipt)
        return copy.deepcopy(receipt)

    pr = add('https://www.deq.virginia.gov/home/showpublisheddocument/25731/638628562585630000', raw_pdf)
    gr = add('https://gisdata.deq.virginia.gov/arcgis/rest/services/public/EDMA/MapServer/294/query?f=json',
             json.dumps({'features': [{'attributes': gs}]}).encode())
    er = add('https://echodata.epa.gov/echo/air_rest_services.get_qid?qid=1',
             json.dumps({'Results': {'Facilities': [ar]}}).encode())
    doc = {'registration': '73567-1', 'source_url': pr['url'], 'pdf_sha256': pr['sha256'],
           'fresh_receipt': pr, 'gis_evidence': {'receipt': gr}, 'air_evidence': {'receipt': er},
           'expected_gis_fields': copy.deepcopy(gs), 'expected_air_fields': copy.deepcopy(ar),
           'identity_review': {'registration': {'pdf_page': 1, 'quote': 'Registration No. 73567'}},
           'inventory_match': {'record_id': 'echo:110031222451'},
           'permit_history_review': {'current_legal_effectiveness': 'unknown', 'indexed_chain': [],
              'explicit_relationships': [{'relationship': 'replaces_selected_pages',
                  'evidence': {**pr, 'pdf_page': 1, 'quote': 'This amendment replaces page 6 only.'}}]}}
    return {'documents': [doc]}, blobs, receipts


def check(report, blobs, receipts):
    return audit.verify_reports([report], blobs.__getitem__, receipts,
                                expected_registrations={'73567-1'}).report()


def test_complete_exact_primary_bridge_passes():
    r, b, receipts = fixture()
    result = check(r, b, receipts)
    assert result['passed'], result['errors']
    assert result['verified_links'][0]['registry_id'] == '110031222451'


@pytest.mark.parametrize('mutate,reason', [
    (lambda d: d['inventory_match'].update(record_id='echo:110070563090'), 'FRS identity'),
    (lambda d: d['expected_air_fields'].update(OtherPermitIDs='12345'), 'native field mismatch'),
    (lambda d: d['expected_air_fields'].update(AIRFIPSCode='51107'), 'native field mismatch'),
    (lambda d: d['expected_gis_fields'].update(FAC_L_ADDR_1='44664 Guilford Drive'), 'native field mismatch'),
    (lambda d: d['expected_gis_fields'].update(PLA_ID=12345), 'native field mismatch'),
    (lambda d: d['permit_history_review'].update(current_legal_effectiveness='current'), 'legal-effectiveness'),
    (lambda d: d['identity_review']['registration'].update(pdf_page=20), 'page out of range'),
    (lambda d: d['permit_history_review']['explicit_relationships'][0]['evidence'].update(quote='This amendment replaces the entire permit.'), 'quote absent'),
    (lambda d: d['fresh_receipt'].update(retrieved_at='2026-11-06T03:00:00Z'), 'retrieval date'),
])
def test_claim_mutations_fail_closed(mutate, reason):
    r, b, receipts = fixture()
    mutate(r['documents'][0])
    result = check(r, b, receipts)
    assert not result['passed']
    assert any(reason in e for e in result['errors']), result['errors']


def test_same_name_cannot_replace_exact_icis_bridge_even_if_report_updated():
    r, b, receipts = fixture()
    d = r['documents'][0]
    old = d['air_evidence']['receipt']
    payload = json.loads(b[old['sha256']])
    payload['Results']['Facilities'][0]['SourceID'] = 'VA0000005110774094'
    raw = json.dumps(payload).encode()
    new = {**old, 'sha256': audit.sha(raw), 'bytes': len(raw)}
    b[new['sha256']] = raw
    receipts.append(new)
    d['air_evidence']['receipt'] = new
    d['expected_air_fields']['SourceID'] = 'VA0000005110774094'
    result = check(r, b, receipts)
    assert not result['passed']
    assert any('ICIS missing' in e for e in result['errors'])


def test_receipt_required_and_pdf_hash_is_original_bytes():
    r, b, receipts = fixture()
    without_pdf = [x for x in receipts if x['sha256'] != r['documents'][0]['pdf_sha256']]
    assert any('no exact successful original receipt' in x for x in check(r, b, without_pdf)['errors'])
    b[r['documents'][0]['pdf_sha256']] = b'%PDF- not original content'
    assert any('bytes/hash mismatch' in x for x in check(r, b, receipts)['errors'])


def test_unreviewed_visual_quote_cannot_bypass_page_text():
    r, b, receipts = fixture()
    q = r['documents'][0]['identity_review']['registration']
    q.update(quote='Registration No. 583600', quote_method='reviewed_pdf_transcription')
    result = check(r, b, receipts)
    assert not result['passed']
    assert any('pinned visual transcription' in e for e in result['errors'])


def test_pinned_transcription_requires_same_quote_hash_and_original_image():
    r, b, receipts = fixture()
    q = r['documents'][0]['identity_review']['registration']
    # The receipt/PDF/page and exact reviewed transcription remain independently pinned.
    image_pdf = pymupdf.open()
    image_pdf.new_page().insert_text((50, 70), 'Reviewed source page')
    image = image_pdf[0].get_pixmap().tobytes('png')
    b[audit.sha(image)] = image
    q.update(quote='Registration No. 73567 (reviewed visual wording)',
             quote_method='reviewed_pdf_transcription', visual_review_sha256=audit.sha(image))
    q['reviewed_quote_sha256'] = audit.sha(audit.flat(q['quote']).encode())
    assert check(r, b, receipts)['passed']
    q['quote'] = 'Registration No. 99999 (unapproved text)'
    assert not check(r, b, receipts)['passed']


def test_cookie_metadata_is_rejected_without_changing_evidence_bytes():
    r, b, receipts = fixture()
    d = r['documents'][0]
    digest = d['pdf_sha256']
    d['fresh_receipt']['response_headers'] = {'Content-Type': 'application/pdf', 'Set-Cookie': 'not-needed'}
    assert not check(r, b, receipts)['passed']
    sanitized = audit.sanitize(r)
    assert check(sanitized, b, receipts)['passed']
    assert digest == audit.sha(b[digest])


def test_complete_expected_set_cannot_pass_with_missing_site():
    r, b, receipts = fixture()
    result = audit.verify_reports([r], b.__getitem__, receipts,
        expected_registrations={'73567-1', '73977-2'}).report()
    assert not result['passed']
    assert 'Verified link set incomplete' in result['errors']


def workbook_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Applications" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="4"><c r="B4"><v>73567</v></c><c r="C4"><v>2</v></c><c r="G4"><v>46191</v></c></row></sheetData></worksheet>')
    return buffer.getvalue()


def test_pending_application_cells_and_excel_dates_are_reconciled():
    r, b, receipts = fixture()
    raw = workbook_bytes()
    receipt = {'url': 'https://www.deq.virginia.gov/applications.xlsx', 'sha256': audit.sha(raw),
               'bytes': len(raw), 'retrieved_at': '2026-11-05T03:00:00Z', 'http_status': 200}
    receipts.append(receipt)
    b[receipt['sha256']] = raw
    app = {'worksheet': 'Applications', 'row': 4, 'raw_cells': {'B4': '73567', 'C4': '2', 'G4': '46191'},
           'received_date': '2026-06-18', 'evidence': receipt}
    r['documents'][0]['active_applications'] = [app]
    assert check(r, b, receipts)['passed']
    app['received_date'] = '2026-11-05'
    assert any('receipt date changed' in x for x in check(r, b, receipts)['errors'])
    app['received_date'] = '2026-06-18'
    app['raw_cells']['C4'] = '3'
    assert any('cell changed C4' in x for x in check(r, b, receipts)['errors'])


def test_html_table_claim_cannot_combine_values_from_different_records():
    r, b, receipts = fixture()
    raw = b'<html><table><tr><td>Site A</td><td>111</td></tr><tr><td>Site B</td><td>222</td></tr></table></html>'
    receipt = {'url': 'https://www.epa.gov/proposals', 'sha256': audit.sha(raw), 'bytes': len(raw),
               'retrieved_at': '2026-11-05T03:00:00Z', 'http_status': 200}
    receipts.append(receipt)
    b[receipt['sha256']] = raw
    r['shared_evidence'] = [{**receipt, 'quote': 'Site A | 111', 'quote_representation': 'Exact cell strings joined with |'}]
    assert check(r, b, receipts)['passed']
    r['shared_evidence'][0]['quote'] = 'Site A | 222'
    assert any('single native row' in x for x in check(r, b, receipts)['errors'])


def test_archive_receipts_cannot_retain_session_headers():
    r, b, receipts = fixture()
    receipts[0]['response_headers'] = {'Set-Cookie': 'unneeded'}
    assert any('Archive receipts' in x for x in check(r, b, receipts)['errors'])
