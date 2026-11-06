"""The archive audit checks source bytes independently of production parsing."""
import copy
import importlib.util
import json
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[2]
module_spec = importlib.util.spec_from_file_location('recovered_archive_audit', ROOT / 'scripts/verify_recovered_permit_archive.py')
audit_module = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(audit_module)


@pytest.fixture
def sample():
    # Visible source says 583,600; its bad embedded OCR says 583,500. The fixture
    # models the actual recovery failure, without any production extractor.
    drawing = pymupdf.open()
    drawing.new_page().insert_text((40, 60), 'Fuel for EG01-EG07: no more than 583,600 gallons per year.')
    raster = drawing[0].get_pixmap().tobytes('png')
    pdf = pymupdf.open()
    pdf.new_page().insert_text((40, 60), 'Registration No. 11790. Wise County.\nEquipment: 2 generators, each 1000 kW.')
    page = pdf.new_page()
    page.insert_image(page.rect, stream=raster)
    page.insert_text((40, 60), 'Fuel for EG01-EG07: no more than 583,500 gallons per year.', render_mode=3)
    image = page.get_pixmap().tobytes('png')
    raw = pdf.tobytes()
    native = {i+1: audit_module.flat(page.get_text()) for i, page in enumerate(pdf)}
    corrected = {**native, 2: native[2].replace('583,500', '583,600')}
    sha = audit_module.digest(raw)
    url = 'https://www.deq.virginia.gov/home/showpublisheddocument/25640/638627840699300000'
    condition = {'metric': 'fuel_throughput_limit', 'value': 583600, 'unit': 'gallons/year', 'pdf_page': 2,
                 'quote': corrected[2], 'subject_scope': 'EG01-EG07 collectively', 'purpose': 'diesel throughput', 'period': 'year'}
    row = {'count': 2, 'rated_ekw_each': 1000, 'pdf_page': 1, 'quote': 'Equipment: 2 generators, each 1000 kW.'}
    record = {'registration': '11790-1', 'publisher_url': url, 'document_sha256': sha, 'page_count': 2,
              'document_extent': 'full_permit', 'reviewed_at': '2026-11-05T02:30:00Z',
              'identity_review': {'registration': {'pdf_page': 1, 'value': '11790', 'quote': 'Registration No. 11790.'}},
              'eligible_condition_pages': [2], 'eligible_equipment_pages': [1],
              'supersession_review': {'current_legal_effectiveness': 'unknown', 'statements': []},
              'corrections': [{'pdf_page': 2, 'old_text': '583,500', 'new_text': '583,600',
                               'visual_review_sha256': audit_module.digest(image), 'reason': 'Visible source differs from embedded OCR.'}],
              'conditions': [condition], 'equipment': [{'subject_scope': 'Listed equipment', 'rows': [row]}]}

    def citation(item):
        n = item['pdf_page']
        return {'page': n, 'quote': item['quote'], 'url': url, 'content_sha256': sha,
                'native_page_text_sha256': audit_module.digest(native[n].encode()),
                'reviewed_page_text_sha256': audit_module.digest(corrected[n].encode()),
                'representation': 'reviewed_pdf_transcription' if n == 2 else 'publisher_pdf'}

    cite = citation(condition)
    equipment_cite = citation(row)
    review = {'registration': '11790-1', 'status': 'verified', 'manifest_record_sha256': audit_module.digest(audit_module.canonical(record))}
    doc = {'content_sha256': sha, 'extraction_version': audit_module.VERSION, 'recovery_review': review,
           'obligations': [{**{k: condition[k] for k in ('metric', 'value', 'unit')}, 'citation': cite,
                            'reviewed_condition_scope': condition['subject_scope']}],
           'equipment': [{'rated_ekw': 2000, 'mw_total': 2, 'is_complete_fleet': False,
                          'equipment_rows': [{'count': 2, 'rated_ekw_each': 1000, 'citation': equipment_cite}]}]}
    source = {'permits': [{'registration': '11790-1', 'document': doc}]}
    limit = {**{k: condition[k] for k in ('metric', 'value', 'unit')}, **cite,
             'registration': '11790-1', 'extraction_version': audit_module.VERSION,
             'applies_to_entire_site': False, 'is_document_condition': True,
             'reviewed_condition_scope': condition['subject_scope'], 'reviewed_operating_purpose': condition['purpose'],
             'reviewed_period': condition['period']}
    claim = {'value': 2, 'unit': 'MW nameplate', 'is_complete_fleet': False, 'citations': [equipment_cite],
             'permit_metadata': {'recovery_review': review}}
    return {'raw': raw, 'image': image, 'manifest': {'review_version': audit_module.VERSION, 'documents': {'11790-1': record}},
            'receipt': {'url': url, 'sha256': sha, 'bytes': len(raw), 'retrieved_at': '2026-11-05T02:00:00Z', 'http_status': 200},
            'source': source, 'cards': {'facility': {'facility': {'permit_limits': [limit]}, 'claims': [claim]}}}


def run(sample):
    return audit_module.verify(sample['manifest'], lambda reg, sha: sample['raw'], [sample['receipt']],
                               image_loader=lambda sha: sample['image'], source=sample['source'], cards=sample['cards'])


def test_independent_pdf_capture_transcription_and_public_values_reconcile(sample):
    sample['source']['nested_review'] = {'registration': {'value': '11790', 'pdf_page': 1}}
    result = run(sample)
    assert not result.errors, result.errors
    assert result.checks > 30
    assert result.documents == result.conditions == result.equipment_tables == 1


@pytest.mark.parametrize('damage', ['pdf_hash', 'receipt_hash', 'receipt_url', 'receipt_size', 'receipt_time',
                                  'wrong_page', 'unreviewed_page', 'bad_correction', 'missing_visual_image',
                                  'wrong_registration_identity', 'claimed_current', 'wrong_page_count'])
def test_archive_integrity_mutations_fail_closed(sample, damage):
    record = sample['manifest']['documents']['11790-1']
    if damage == 'pdf_hash': sample['raw'] += b'changed'
    elif damage == 'receipt_hash': sample['receipt']['sha256'] = '0' * 64
    elif damage == 'receipt_url': sample['receipt']['url'] += '?other-document'
    elif damage == 'receipt_size': sample['receipt']['bytes'] += 1
    elif damage == 'receipt_time': sample['receipt']['retrieved_at'] = None
    elif damage == 'wrong_page': record['conditions'][0]['pdf_page'] = 1
    elif damage == 'unreviewed_page': record['eligible_condition_pages'] = []
    elif damage == 'bad_correction': record['corrections'][0]['old_text'] = '583,400'
    elif damage == 'missing_visual_image': sample['image'] = b''
    elif damage == 'wrong_registration_identity': record['identity_review']['registration']['value'] = '11791'
    elif damage == 'claimed_current': record['supersession_review']['current_legal_effectiveness'] = 'effective'
    elif damage == 'wrong_page_count': record['page_count'] = 3
    assert run(sample).errors


@pytest.mark.parametrize('target', ['source', 'public'])
def test_uncorrected_583500_is_rejected_even_when_old_ocr_text_contains_it(sample, target):
    if target == 'source':
        item = sample['source']['permits'][0]['document']['obligations'][0]
        item['value'] = 583500
        item['citation']['quote'] = item['citation']['quote'].replace('583,600', '583,500')
    else:
        item = sample['cards']['facility']['facility']['permit_limits'][0]
        item['value'] = 583500
        item['quote'] = item['quote'].replace('583,600', '583,500')
    assert run(sample).errors


@pytest.mark.parametrize('damage', ['wrong_registration', 'wrong_pdf', 'wrong_page', 'wrong_scope',
                                  'wrong_native_hash', 'hidden_transcription', 'public_hidden_transcription',
                                  'public_lost_scope', 'equipment_total', 'equipment_whole_site'])
def test_replay_and_publication_mutations_rejected(sample, damage):
    doc = sample['source']['permits'][0]['document']
    limit = sample['cards']['facility']['facility']['permit_limits'][0]
    if damage == 'wrong_registration': limit['registration'] = '11791-1'
    elif damage == 'wrong_pdf': limit['content_sha256'] = 'f' * 64
    elif damage == 'wrong_page': limit['page'] = 1
    elif damage == 'wrong_scope': doc['obligations'][0]['reviewed_condition_scope'] = 'Whole campus'
    elif damage == 'wrong_native_hash': doc['obligations'][0]['citation']['native_page_text_sha256'] = 'f' * 64
    elif damage == 'hidden_transcription': doc['obligations'][0]['citation']['representation'] = 'publisher_pdf'
    elif damage == 'public_hidden_transcription': limit['representation'] = 'publisher_pdf'
    elif damage == 'public_lost_scope': limit.pop('reviewed_condition_scope')
    elif damage == 'equipment_total': sample['cards']['facility']['claims'][0]['value'] = 2000
    elif damage == 'equipment_whole_site': sample['cards']['facility']['claims'][0]['is_complete_fleet'] = True
    assert run(sample).errors


@pytest.mark.parametrize('compressed_image', [False, True])
def test_s3_style_content_addressed_gzip_archive_cli(sample, tmp_path, monkeypatch, capsys, compressed_image):
    import gzip
    archive = tmp_path / 'archive'
    (archive / 'raw').mkdir(parents=True)
    sha = sample['receipt']['sha256']
    (archive / 'raw' / (sha + '.gz')).write_bytes(gzip.compress(sample['raw']))
    if compressed_image:
        image_sha = audit_module.digest(sample['image'])
        (archive / 'raw' / (image_sha + '.gz')).write_bytes(gzip.compress(sample['image']))
    else:
        (archive / 'review.png').write_bytes(sample['image'])
    (archive / 'capture-receipts.json').write_text(json.dumps([sample['receipt']]))
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(sample['manifest']))
    monkeypatch.setattr('sys.argv', ['verify', '--manifest', str(manifest), '--archive-dir', str(archive)])
    assert audit_module.main() == 0
    assert json.loads(capsys.readouterr().out)['passed'] is True


def test_no_production_extraction_or_recovery_imports():
    text = (ROOT / 'scripts/verify_recovered_permit_archive.py').read_text()
    assert 'from wattdocket' not in text
    assert 'import wattdocket' not in text


@pytest.mark.parametrize('damage', [None, 'omit', 'wrong_registration', 'wrong_value'])
def test_standalone_unjoined_public_document_is_audited(sample, damage):
    record = sample['manifest']['documents']['11790-1']
    row = {'registration': '11790-1', 'content_sha256': record['document_sha256'], 'url': record['publisher_url'],
           'conditions': copy.deepcopy(sample['cards']['facility']['facility']['permit_limits']),
           'equipment_claims': copy.deepcopy(sample['cards']['facility']['claims']), 'inventory_record_ids': []}
    rows = [row]
    if damage == 'omit': rows = []
    elif damage == 'wrong_registration': row['conditions'][0]['registration'] = '73200-3'
    elif damage == 'wrong_value': row['conditions'][0]['value'] = 583500
    result = audit_module.verify(sample['manifest'], lambda reg, sha: sample['raw'], [sample['receipt']],
                                 image_loader=lambda sha: sample['image'], cards={}, public_documents=rows)
    assert bool(result.errors) == (damage is not None), result.errors
