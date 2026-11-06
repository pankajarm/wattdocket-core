#!/usr/bin/env python3
"""Independent offline evidence audit for the thirteen Virginia site/history reviews.

No ingestion, identity or permit parser imports. This proves receipt/byte/row/page
consistency with reviewed records, not present legal effectiveness or independent
human interpretation of an image. --assemble creates a private upload bundle only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from datetime import timedelta
import gzip
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

MAX_OBJECT = 16_000_000
MAX_BUNDLE = 100_000_000
EXPECTED = {'11541-3', '11790-1', '72374-4', '73170-1', '73200-3', '73363-2',
            '73370-12', '73567-1', '73643-5', '73977-2', '74060-2', '74094-1', '81609-5'}
SAFE_HEADERS = {'content-type', 'content-length', 'last-modified', 'etag', 'date'}
SECRET_HEADERS = {'set-cookie', 'cookie', 'authorization', 'proxy-authorization'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def flat(value):
    return ' '.join(str(value).split())


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def dated(value):
    try:
        return bool(datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo)
    except (ValueError, TypeError, AttributeError):
        return False


def sanitize(value):
    if isinstance(value, list):
        return [sanitize(x) for x in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key.casefold() in SECRET_HEADERS:
                continue
            if key in {'headers', 'response_headers'} and isinstance(item, dict):
                item = {k: v for k, v in item.items() if k.casefold() in SAFE_HEADERS}
            result[key] = sanitize(item)
        return result
    return value


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        if tag in {'td', 'th'}:
            self.cell = []

    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {'td', 'th'} and self.cell is not None:
            if self.row is not None:
                self.row.append(flat(' '.join(self.cell)))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def xlsx_cells(raw, sheet_name):
    """Read native XML cells without production parsing or formatted-cell guessing."""
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        book = ET.fromstring(z.read('xl/workbook.xml'))
        sheets = [s for s in book.findall('m:sheets/m:sheet', ns) if s.attrib['name'] == sheet_name]
        if len(sheets) != 1:
            raise ValueError('worksheet missing/ambiguous')
        rid = sheets[0].attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
        rel = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target = next(r.attrib['Target'] for r in rel if r.attrib['Id'] == rid)
        path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for item in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns):
                shared.append(''.join(t.text or '' for t in item.findall('.//m:t', ns)))
        cells = {}
        for cell in ET.fromstring(z.read(path)).findall('.//m:sheetData/m:row/m:c', ns):
            value = cell.find('m:v', ns)
            text = value.text if value is not None else ''
            if cell.attrib.get('t') == 's':
                text = shared[int(text)]
            elif cell.attrib.get('t') == 'inlineStr':
                text = ''.join(t.text or '' for t in cell.findall('.//m:t', ns))
            cells[cell.attrib['r']] = text or ''
        return cells


class Audit:
    def __init__(self):
        self.checks = 0
        self.errors = []
        self.links = []
        self.quotes = 0
        self.sources = set()

    def require(self, ok, label):
        self.checks += 1
        if not ok:
            self.errors.append(label)
        return bool(ok)

    def report(self):
        return {'passed': not self.errors, 'checks': self.checks, 'errors': self.errors,
                'verified_links': self.links, 'pdf_quotes': self.quotes,
                'distinct_source_objects': len(self.sources),
                'scope': 'Offline byte, receipt, regulator cross-reference and quoted-page consistency. Does not establish current legal effectiveness or independently interpret images.'}


def verify_reports(reports, load, receipts, *, expected_registrations=None):
    """load(sha256)->original bytes. Receipts retain original source retrieval dates."""
    import pymupdf
    audit = Audit()
    checked = set()
    page_cache = {}
    all_receipts = list(walk(receipts))
    audit.require(receipts == sanitize(receipts), 'Archive receipts contain non-allowlisted HTTP headers')

    def source(spec, label):
        digest = spec.get('sha256') or spec.get('pdf_sha256')
        url = spec.get('capture_url') or spec.get('requested_url') or spec.get('url')
        if not audit.require(isinstance(digest, str) and re.fullmatch(r'[a-f0-9]{64}', digest), label + ': missing valid source SHA'):
            raise ValueError('invalid source SHA')
        if not audit.require(isinstance(url, str) and url.startswith('https://'), label + ': missing HTTPS source URL'):
            raise ValueError('invalid source URL')
        raw = load(digest)
        if not audit.require(len(raw) <= MAX_OBJECT and sha(raw) == digest, label + ': source bytes/hash mismatch'):
            raise ValueError('source hash mismatch')
        matches = [r for r in all_receipts if r.get('sha256') == digest
                   and (r.get('requested_url') or r.get('url')) == url
                   and r.get('bytes') == len(raw)
                   and r.get('http_status', r.get('status')) == 200
                   and dated(r.get('retrieved_at'))]
        audit.require(bool(matches), label + ': no exact successful original receipt')
        if spec.get('retrieved_at'):
            audit.require(any(r.get('retrieved_at') == spec['retrieved_at'] for r in matches), label + ': retrieval date not supported by receipt')
        audit.sources.add(digest)
        return raw

    def quote(item, evidence, label):
        nonlocal page_cache
        if not item.get('quote'):
            return
        raw = source(evidence, label)
        if not audit.require(raw.startswith(b'%PDF-'), label + ': cited source is not original PDF'):
            return
        digest = sha(raw)
        if digest not in page_cache:
            doc = pymupdf.open(stream=raw, filetype='pdf')
            page_cache[digest] = [flat(p.get_text()) for p in doc]
        pages = page_cache[digest]
        number = item.get('pdf_page')
        if not audit.require(type(number) is int and 1 <= number <= len(pages), label + ': physical page out of range'):
            return
        text = pages[number - 1]
        excerpt = flat(item['quote'])
        if excerpt not in text:
            image_sha = item.get('visual_review_sha256')
            pinned = item.get('reviewed_quote_sha256')
            is_reviewed = item.get('quote_method') == 'reviewed_pdf_transcription'
            valid = is_reviewed and pinned == sha(excerpt.encode()) and isinstance(image_sha, str) and bool(re.fullmatch(r'[a-f0-9]{64}', image_sha))
            if audit.require(valid, label + ': quote absent from original page without pinned visual transcription'):
                image = load(image_sha)
                audit.require(image.startswith(b'\x89PNG\r\n\x1a\n') and sha(image) == image_sha, label + ': reviewed page image missing/corrupt')
                audit.sources.add(image_sha)
        if item.get('native_page_text_sha256'):
            audit.require(sha(text.encode()) == item['native_page_text_sha256'], label + ': native page text hash changed')
        audit.quotes += 1

    for report in reports:
        audit.require(report == sanitize(report), 'Review report contains non-allowlisted HTTP headers')
        # Hash/receipt-check every referenced source, including HTML/XML workbook
        # evidence that does not appear in a permit-page or regulatory-ID join.
        seen_specs = set()
        for spec in walk(report):
            if not spec.get('sha256') or not (spec.get('url') or spec.get('requested_url')):
                continue
            key = (spec['sha256'], spec.get('capture_url') or spec.get('requested_url') or spec.get('url'), spec.get('retrieved_at'))
            if key in seen_specs:
                continue
            seen_specs.add(key)
            try:
                raw = source(spec, 'Referenced source')
                if spec.get('quote') and not raw.startswith(b'%PDF-'):
                    if spec.get('quote_representation', '').startswith('Exact cell strings'):
                        table = Tables()
                        table.feed(raw.decode('utf-8'))
                        expected = [flat(x) for x in spec['quote'].split('|')]
                        audit.require(expected in table.rows, 'Structured HTML quote does not match a single native row')
                    elif b'<html' in raw[:1000].lower() or b'<!doctype' in raw[:1000].lower():
                        table = Tables()
                        table.feed(raw.decode('utf-8'))
                        audit.require(flat(spec['quote']) in flat(' '.join(table.text)), 'HTML source quote not found')
                    else:
                        audit.require(flat(spec['quote']) in flat(raw.decode('utf-8')), 'Source representation quote not found')
            except Exception as exc:
                audit.require(False, 'Referenced source: ' + str(exc))
        documents = report.get('documents')
        if not audit.require(isinstance(documents, list) and bool(documents), 'Review lacks document list'):
            continue
        for d in documents:
            reg = d.get('registration', '')
            prefix = reg + ': '
            try:
                audit.require(bool(re.fullmatch(r'\d+-\d+', reg)) and reg not in checked, prefix + 'invalid or duplicate registration')
                checked.add(reg)
                root = reg.split('-')[0]
                is_b = 'expected_gis_fields' in d
                if is_b:
                    pdf = {'url': d['source_url'], 'sha256': d['pdf_sha256'], 'retrieved_at': d['fresh_receipt']['retrieved_at']}
                    gis_spec = d['gis_evidence']['receipt']
                    air_spec = d['air_evidence']['receipt']
                    expected_gis = d['expected_gis_fields']
                    expected_air = d['expected_air_fields']
                    matched_id = d['inventory_match']['record_id']
                    legal = d['permit_history_review']['current_legal_effectiveness']
                    for key, item in d['identity_review'].items():
                        quote(item, pdf, prefix + key)
                    for relation in d['permit_history_review']['explicit_relationships']:
                        quote(relation['evidence'], relation['evidence'], prefix + relation['relationship'])
                    for chain in d['permit_history_review']['indexed_chain']:
                        if chain.get('source_captured'):
                            source(chain['receipt'], prefix + 'chain ' + chain['registration'])
                else:
                    site = d['physical_site_review']
                    gis_spec, air_spec = site['gis_evidence'], site['epa_evidence']
                    matches = site['inventory_matches']
                    audit.require(len(matches) == 1, prefix + 'expected one independently verified regulatory FRS link')
                    match = matches[0]
                    matched_id = match['id']
                    expected_air = match['air_links'][0]
                    address = site['physical_address']
                    expected_gis = {'PLA_ID': site['pla_id'], 'PLA_REG_NUM': int(site['deq_registration']),
                                    'PLA_ICIS_ID': site['icis_air_id'], 'FAC_L_ADDR_1': address['street'],
                                    'FAC_L_CITY': address['city'], 'FAC_L_STATE': address['state'],
                                    'STATUS_GROUP': site['deq_status_group'], 'AIR_OP_STATUS': site['deq_air_operating_status'],
                                    'PLA_DATA_CENTER_YN': site['data_center_flag']}
                    legal = d['currentness_conclusion']['legal_effectiveness']
                    primary = [x for x in d['reviewed_available_chain'] if x.get('registration') == reg]
                    audit.require(len(primary) == 1, prefix + 'missing original reviewed document')
                    pdf = primary[0]['evidence']
                    for chain in d['reviewed_available_chain']:
                        if chain.get('evidence', {}).get('sha256'):
                            source(chain['evidence'], prefix + 'chain source')
                        for key, item in chain.get('identity_evidence', {}).items():
                            quote(item, chain['evidence'], prefix + key)
                        for item in chain.get('replacement_statements', []):
                            quote(item, chain['evidence'], prefix + 'replacement')
                audit.require(legal == 'unknown', prefix + 'unsupported current legal-effectiveness assertion')
                raw_pdf = source(pdf, prefix + 'original')
                audit.require(raw_pdf.startswith(b'%PDF-'), prefix + 'original is not PDF')
                original_text = flat(' '.join(p.get_text() for p in pymupdf.open(stream=raw_pdf, filetype='pdf')))
                audit.require(bool(re.search(r'\b' + root + r'\b', original_text)), prefix + 'registration absent from original PDF')
                gj = json.loads(source(gis_spec, prefix + 'GIS'))
                audit.require(not gj.get('exceededTransferLimit'), prefix + 'truncated GIS query')
                gs = [x['attributes'] for x in gj.get('features', []) if str(x['attributes'].get('PLA_REG_NUM')) == root]
                if not audit.require(len(gs) == 1, prefix + 'GIS registration missing/ambiguous'):
                    continue
                g = gs[0]
                for key, value in expected_gis.items():
                    audit.require(g.get(key) == value, prefix + 'GIS native field mismatch ' + key)
                aj = json.loads(source(air_spec, prefix + 'EPA AIR'))
                ars = aj.get('Results', {}).get('Facilities', [])
                ars = [x for x in ars if x.get('SourceID') == g['PLA_ICIS_ID']]
                if not audit.require(len(ars) == 1, prefix + 'EPA AIR ICIS missing/ambiguous'):
                    continue
                a = ars[0]
                for key, value in expected_air.items():
                    audit.require(a.get(key) == value, prefix + 'EPA native field mismatch ' + key)
                audit.require('echo:' + str(a.get('RegistryID')) == matched_id, prefix + 'FRS identity not supported by AIR source')
                tokens = set(re.split(r'[,;|\s]+', str(a.get('OtherPermitIDs', ''))))
                audit.require(root in tokens, prefix + 'registration absent from exact OtherPermitIDs tokens')
                audit.require(a.get('SourceID') == g['PLA_ICIS_ID'], prefix + 'DEQ/EPA ICIS bridge mismatch')
                county = a.get('AIRFIPSCode', '')
                audit.require(bool(re.fullmatch(r'51\d{3}', str(county))) and g['PLA_ICIS_ID'].startswith('VA000000' + str(county)), prefix + 'county/ICIS identity mismatch')
                audit.require(g.get('PLA_DATA_CENTER_YN') == 'Y', prefix + 'DEQ did not flag data center')
                if root == '11541':
                    audit.require(g.get('AIR_OP_STATUS') == 'Temporarily Shutdown' and a.get('AIRStatus') == 'Temporarily Closed', prefix + 'shutdown status erased')
                if root == '81609':
                    conflict = d.get('inventory_match', {}).get('combined_address_conflict', {})
                    audit.require(conflict and conflict.get('consolidated_frs_address') == '139 TYCO STREET' and conflict.get('air_program_address') == '1175 NORTH MAIN STREET', prefix + 'known FRS/AIR address conflict omitted')
                if root == '74094':
                    audit.require(g.get('FAC_L_ADDR_1') == '44610 Guilford Drive' and a.get('AIRStreet') == '44610 GUILFORD DRIVE', prefix + 'VA2 confused with mailing-address VA1')
                if root == '73370':
                    conflicts = d.get('identity_conflicts', [])
                    audit.require(any(c.get('echo_air_and_gis_linked_value') == '110054206783'
                                      and c.get('epa_proposed_title_v_table_value') == '110038106849'
                                      and 'unresolved' in c.get('resolution', '').lower()
                                      for c in conflicts), prefix + 'proposed-action FRS conflict erased')
                for application in d.get('active_applications', []):
                    cells = xlsx_cells(source(application['evidence'], prefix + 'active application'), application['worksheet'])
                    for address, value in application['raw_cells'].items():
                        audit.require(cells.get(address) == value, prefix + 'active-application cell changed ' + address)
                    row = str(application['row'])
                    audit.require(cells.get('B' + row) == root, prefix + 'active application belongs to different registration')
                    received = (datetime(1899, 12, 30) + timedelta(days=float(cells['G' + row]))).date().isoformat()
                    audit.require(received == application['received_date'], prefix + 'application receipt date changed')
                audit.links.append({'registration': reg, 'registry_id': a['RegistryID'], 'icis_id': a['SourceID'],
                                    'deq_site_id': g['PLA_ID'], 'air_operating_status': g['AIR_OP_STATUS']})
            except Exception as exc:
                audit.require(False, prefix + type(exc).__name__ + ': ' + str(exc))
        if report.get('active_applications', {}).get('source'):
            summary = report['active_applications']
            try:
                cells = xlsx_cells(source(summary['source'], 'Active application summary'), summary['worksheet'])
                native_roots = [v for k, v in cells.items() if re.fullmatch(r'B\d+', k) and re.fullmatch(r'\d+', v)]
                audit.require(len(native_roots) == summary['application_count'], 'Active application row count changed')
                report_roots = {d['registration'].split('-')[0] for d in documents}
                audit.require(sorted(report_roots.intersection(native_roots)) == summary['matching_registration_roots'], 'Active application absence claim contradicts workbook')
            except Exception as exc:
                audit.require(False, 'Active application summary: ' + str(exc))
    if expected_registrations is not None:
        audit.require(checked == set(expected_registrations), 'Reviewed registration set incomplete/unexpected')
        audit.require({r['registration'] for r in audit.links} == set(expected_registrations), 'Verified link set incomplete')
    return audit


def load_archive(archive):
    def load(digest):
        if not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('unsafe archive digest')
        with gzip.open(archive / 'raw' / (digest + '.gz'), 'rb') as stream:
            raw = stream.read(MAX_OBJECT + 1)
        if len(raw) > MAX_OBJECT:
            raise ValueError('archive object exceeds bound')
        return raw
    return load


def assemble(report_paths, archive, capture_dirs):
    """Package originals and sanitized receipts; never update any source state."""
    reports = [json.loads(p.read_text()) for p in report_paths]
    specs = [x for r in reports for x in walk(r) if x.get('sha256') and (x.get('url') or x.get('requested_url'))]
    receipts = []
    candidates = {}
    images = set()
    for directory in capture_dirs:
        for p in directory.rglob('*'):
            if not p.is_file() or p.name.startswith('._') or p.stat().st_size > MAX_OBJECT:
                continue
            if p.suffix.lower() in {'.pdf', '.png', '.html', '.xlsx', '.txt', '.json'}:
                candidates.setdefault(sha(p.read_bytes()), p)
            if p.suffix == '.png':
                images.add(sha(p.read_bytes()))
            if 'receipt' in p.name and p.suffix == '.json':
                try:
                    receipts.extend(sanitize(x) for x in walk(json.loads(p.read_text()))
                                    if x.get('sha256') and x.get('bytes') is not None and (x.get('url') or x.get('requested_url')))
                except (ValueError, UnicodeError):
                    continue
    for r in reports:
        receipts.extend(sanitize(x) for x in walk(r)
                        if x.get('sha256') and x.get('bytes') is not None and (x.get('url') or x.get('requested_url')))
    receipts = list({json.dumps(r, sort_keys=True): r for r in receipts}.values())
    wanted = {s['sha256'] for s in specs} | images
    # A report's original PDFs carry sha256, while B also carries pdf_sha256.
    wanted |= {x['pdf_sha256'] for r in reports for x in walk(r) if x.get('pdf_sha256')}
    wanted |= {x['visual_review_sha256'] for r in reports for x in walk(r) if x.get('visual_review_sha256')}
    # Include source receipts referenced by exact path, without changing timestamps.
    missing = sorted(wanted - candidates.keys())
    if missing:
        raise ValueError('Missing original artifacts: ' + ','.join(missing))
    total = sum(candidates[s].stat().st_size for s in wanted)
    if total > MAX_BUNDLE:
        raise ValueError('Evidence bundle exceeds100MB')
    base = archive / 'review-evidence' / 'permit-currentness'
    (archive / 'raw').mkdir(parents=True, exist_ok=True)
    (base / 'reports').mkdir(parents=True, exist_ok=True)
    (base / 'receipts').mkdir(exist_ok=True)
    objects = []
    for digest in sorted(wanted):
        raw = candidates[digest].read_bytes()
        target = archive / 'raw' / (digest + '.gz')
        if target.exists():
            if load_archive(archive)(digest) != raw:
                raise ValueError('Immutable archive conflict: ' + digest)
        else:
            target.write_bytes(gzip.compress(raw, mtime=0))
        objects.append({'sha256': digest, 'bytes': len(raw), 'raw_key': 'raw/' + digest + '.gz',
                        'kind': 'inspection_image' if raw.startswith(b'\x89PNG') else 'source_capture'})
    used_receipts = [r for r in receipts if r['sha256'] in wanted]
    for i, r in enumerate(used_receipts):
        (base / 'receipts' / (str(i).zfill(4) + '.json')).write_text(json.dumps(r, sort_keys=True, indent=2) + '\n')
    report_items = []
    for p, r in zip(report_paths, reports):
        safe = sanitize(r)
        if safe != r:
            raise ValueError('Source research report must be sanitized first: ' + str(p))
        raw = p.read_bytes()
        (base / 'reports' / p.name).write_bytes(raw)
        report_items.append({'path': 'review-evidence/permit-currentness/reports/' + p.name, 'sha256': sha(raw)})
    manifest = {'schema_version': '1', 'assembled_at': datetime.now(timezone.utc).isoformat(),
                'objects': objects, 'reports': report_items, 'receipt_count': len(used_receipts),
                'object_count': len(objects), 'uncompressed_bytes': total,
                'source_state_modified': False,
                'date_semantics': 'assembled_at is local packaging time. Each receipt retains its original retrieved_at; inspection images have no invented source retrieval date.'}
    (base / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def include_normalized_review(archive, review_path, state_path, prior_archives):
    """Preserve old guard representations with their true archive-date semantics."""
    base = archive / 'review-evidence/permit-currentness'
    manifest = json.loads((base / 'manifest.json').read_text())
    review_bytes = review_path.read_bytes()
    review = json.loads(review_bytes)
    if review != sanitize(review):
        raise ValueError('Normalized review contains unnecessary HTTP headers')
    required = {x['sha256'] for x in walk(review) if x.get('sha256')}
    required |= {x['document_sha256'] for x in walk(review) if x.get('document_sha256')}
    state = json.loads(state_path.read_text())
    historical = [sanitize(x) for x in walk(state) if x.get('sha256') in required
                  and x.get('bytes') is not None and x.get('url')]
    objects = {x['sha256']: x for x in manifest['objects']}
    for digest in sorted(required):
        if not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('Invalid normalized manifest digest')
        if digest not in objects:
            found = next((a for a in prior_archives if (a / 'raw' / (digest + '.gz')).exists()), None)
            if found is None:
                raise ValueError('Normalized manifest artifact missing: ' + digest)
            raw = load_archive(found)(digest)
            if sha(raw) != digest:
                raise ValueError('Prior archive corruption: ' + digest)
            target = archive / 'raw' / (digest + '.gz')
            if target.exists() and load_archive(archive)(digest) != raw:
                raise ValueError('Immutable archive conflict: ' + digest)
            if not target.exists():
                target.write_bytes(gzip.compress(raw, mtime=0))
            objects[digest] = {'sha256': digest, 'bytes': len(raw), 'raw_key': 'raw/' + digest + '.gz',
                               'kind': 'historical_source_guard'}
        matching = [x for x in historical if x['sha256'] == digest]
        for i, item in enumerate(matching):
            (base / 'receipts' / ('historical-' + digest + '-' + str(i) + '.json')).write_text(json.dumps(item, indent=2) + '\n')
    total = sum(x['bytes'] for x in objects.values())
    if total > MAX_BUNDLE:
        raise ValueError('Evidence bundle exceeds100MB')
    target = base / 'normalized-review-manifest.json'
    target.write_bytes(review_bytes)
    manifest.update(objects=list(objects.values()), object_count=len(objects), uncompressed_bytes=total,
                    normalized_review={'path': str(target.relative_to(archive)), 'sha256': sha(review_bytes),
                                       'referenced_original_objects': len(required)},
                    receipt_count=len(list((base / 'receipts').glob('*.json'))))
    (base / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--reports', nargs='+', type=Path)
    parser.add_argument('--assemble', action='store_true')
    parser.add_argument('--captures', nargs='*', type=Path, default=[])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--allow-subset', action='store_true')
    parser.add_argument('--normalized-review', type=Path)
    parser.add_argument('--legacy-state', type=Path)
    parser.add_argument('--prior-archives', nargs='*', type=Path, default=[])
    args = parser.parse_args()
    manifest_path = args.archive / 'review-evidence/permit-currentness/manifest.json'
    if args.assemble:
        assemble(args.reports, args.archive, args.captures)
        if args.normalized_review:
            if not args.legacy_state:
                parser.error('--normalized-review assembly requires --legacy-state')
            include_normalized_review(args.archive, args.normalized_review, args.legacy_state, args.prior_archives)
    manifest = json.loads(manifest_path.read_text())
    report_paths = args.reports or [args.archive / x['path'] for x in manifest['reports']]
    reports = [json.loads(p.read_text()) for p in report_paths]
    receipts = [json.loads(p.read_text()) for p in (args.archive / 'review-evidence/permit-currentness/receipts').glob('*.json')]
    audit = verify_reports(reports, load_archive(args.archive), receipts,
                           expected_registrations=None if args.allow_subset else EXPECTED)
    load = load_archive(args.archive)
    for obj in manifest['objects']:
        try:
            raw = load(obj['sha256'])
            audit.require(len(raw) == obj['bytes'] and sha(raw) == obj['sha256'], 'Archive manifest object hash/length mismatch')
        except Exception as exc:
            audit.require(False, 'Archive object unreadable: ' + str(exc))
    for item in manifest['reports']:
        audit.require(sha((args.archive / item['path']).read_bytes()) == item['sha256'], 'Archived report changed')
    if manifest.get('normalized_review'):
        info = manifest['normalized_review']
        raw = (args.archive / info['path']).read_bytes()
        audit.require(sha(raw) == info['sha256'], 'Archived normalized review changed')
        normalized = json.loads(raw)
        required = {x['sha256'] for x in walk(normalized) if x.get('sha256')}
        required |= {x['document_sha256'] for x in walk(normalized) if x.get('document_sha256')}
        for digest in required:
            try:
                audit.require(sha(load(digest)) == digest, 'Normalized review source object missing/corrupt')
            except Exception as exc:
                audit.require(False, 'Normalized review source object: ' + str(exc))
        for receipt in receipts:
            if receipt.get('capture_kind') == 'retained_repository_reader_text' and receipt.get('sha256') in required:
                audit.require(receipt.get('retrieved_at') is None and dated(receipt.get('archived_by_at'))
                              and bool(receipt.get('archive_commit')),
                              'Historical archive capture falsely relabeled as original HTTP retrieval')
    audit.require(manifest['uncompressed_bytes'] <= MAX_BUNDLE, 'Archive exceeds100MB')
    result = {**audit.report(), 'archive_objects': manifest['object_count'], 'archive_uncompressed_bytes': manifest['uncompressed_bytes']}
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
