#!/usr/bin/env python3
"""Independently reconcile reviewed permits with original PDFs and publications.

No production extraction/recovery helpers are imported. The signed-off manifest
is the review authority; this audit proves byte/page/quote/quantity consistency,
not that software can repeat a person's visual judgment or establish legal effect.
Local research folders and an S3 archive downloaded to a local directory work alike.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
from pathlib import Path

VERSION = 'va-reviewed-pdf-1'
MAX_PDF = 16_000_000


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def flat(text):
    return ' '.join(text.split())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


class Audit:
    def __init__(self):
        self.checks = 0
        self.errors = []
        self.documents = 0
        self.conditions = 0
        self.equipment_tables = 0

    def require(self, condition, label):
        self.checks += 1
        if not condition:
            self.errors.append(label)
        return bool(condition)

    def report(self):
        return {'passed': not self.errors, 'checks': self.checks, 'documents': self.documents,
                'conditions': self.conditions, 'equipment_tables': self.equipment_tables,
                'errors': self.errors,
                'scope': 'Original PDF and reviewed manifest consistency; not current legal effectiveness or independent visual interpretation.'}


def _date(value):
    from datetime import datetime
    try:
        return bool(datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo)
    except (ValueError, TypeError, AttributeError):
        return False


def _numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def verify(manifest, pdf_loader, receipts, *, image_loader, source=None, cards=None, public_documents=None):
    """Loaders receive (registration, sha256) and (image sha256), respectively."""
    import pymupdf
    audit = Audit()
    if not audit.require(manifest.get('review_version') == VERSION, 'Unknown manifest version'):
        return audit
    specs = manifest.get('documents')
    if not audit.require(isinstance(specs, dict) and bool(specs), 'Missing reviewed documents'):
        return audit
    captures = list(walk(receipts))
    validated = {}
    for registration, spec in specs.items():
        prefix = registration + ': '
        try:
            expected_sha = spec['document_sha256']
            audit.require(spec.get('registration') == registration and bool(re.fullmatch(r'\d+-\d+', registration)), prefix + 'manifest registration mismatch')
            raw = pdf_loader(registration, expected_sha)
            if not audit.require(raw.startswith(b'%PDF-') and len(raw) <= MAX_PDF and digest(raw) == expected_sha, prefix + 'original PDF hash/format/size mismatch'):
                continue
            matches = [r for r in captures if r.get('sha256') == expected_sha
                       and (r.get('url') or r.get('requested_url')) == spec['publisher_url']]
            good = [r for r in matches if r.get('bytes') == len(raw) and _date(r.get('retrieved_at'))
                    and r.get('status', r.get('http_status', 200)) == 200
                    and r.get('is_pdf', True) is True
                    and r.get('final_url', spec['publisher_url']) == spec['publisher_url']]
            audit.require(bool(good), prefix + 'matching original PDF capture receipt missing')
            with pymupdf.open(stream=raw, filetype='pdf') as pdf:
                if not audit.require(not pdf.needs_pass and 0 < len(pdf) <= 200 and len(pdf) == spec.get('page_count'), prefix + 'physical PDF page count mismatch'):
                    continue
                original = {i + 1: flat(page.get_text()) for i, page in enumerate(pdf)}
            corrected = dict(original)
            for fix in spec.get('corrections', []):
                page = fix['pdf_page']
                old, new = flat(fix['old_text']), flat(fix['new_text'])
                valid = (page in corrected and old and old != new and corrected[page].count(old) == 1)
                if audit.require(valid, prefix + 'correction does not uniquely match its original physical page'):
                    corrected[page] = corrected[page].replace(old, new, 1)
                image_sha = fix.get('visual_review_sha256', '')
                audit.require(bool(re.fullmatch('[a-f0-9]{64}', image_sha)), prefix + 'invalid visual review hash')
                image = image_loader(image_sha)
                audit.require(image.startswith(b'\x89PNG\r\n\x1a\n') and digest(image) == image_sha, prefix + 'visual correction artifact missing or changed')
                audit.require(bool(fix.get('reason')), prefix + 'correction reason missing')
            identity = spec.get('identity_review') or {}
            reg = identity.get('registration') or {}
            audit.require(str(reg.get('value')) == registration.split('-')[0] and
                          bool(re.search(r'(?<!\d)' + re.escape(registration.split('-')[0]) + r'(?!\d)', reg.get('quote', ''))), prefix + 'registration identity is not proved')
            for field, proof in identity.items():
                if isinstance(proof, dict) and proof.get('quote'):
                    audit.require(flat(proof['quote']) in corrected.get(proof.get('pdf_page'), ''), prefix + field + ' identity quote absent from its page')
            supersession = spec.get('supersession_review') or {}
            audit.require(supersession.get('current_legal_effectiveness') == 'unknown', prefix + 'unproved current legal effectiveness')
            for statement in supersession.get('statements', []):
                audit.require(flat(statement['quote']) in corrected.get(statement['pdf_page'], ''), prefix + 'replacement statement absent from its page')
            for field in ('eligible_condition_pages', 'eligible_equipment_pages'):
                approved = spec.get(field)
                audit.require(isinstance(approved, list) and all(type(p) is int and p in original for p in approved)
                              and len(approved) == len(set(approved)), prefix + 'invalid ' + field)

            def checked_quote(item, allowed):
                page, quote = item.get('pdf_page'), flat(item.get('quote', ''))
                audit.require(page in spec.get(allowed, []), prefix + 'numeric quote on unreviewed page')
                audit.require(bool(quote) and quote in corrected.get(page, ''), prefix + 'approved quote differs from corrected original page')
                return quote

            for item in spec.get('conditions', []):
                quote = checked_quote(item, 'eligible_condition_pages')
                value = item.get('value')
                audit.require(_numeric(value), prefix + 'invalid condition quantity')
                if _numeric(value):
                    token = str(int(value)) if float(value).is_integer() else str(value)
                    audit.require(bool(re.search(r'(?<![\d.])' + re.escape(token) + r'(?![\d.])', quote.replace(',', ''))), prefix + 'condition quantity absent from approved quote')
                audit.require(bool(item.get('subject_scope')) and bool(item.get('unit')), prefix + 'condition scope/unit missing')
                audit.conditions += 1
            for table in spec.get('equipment', []):
                rows = table.get('rows', [])
                audit.require(bool(rows) and len({r.get('pdf_page') for r in rows}) == 1, prefix + 'equipment merges physical table pages')
                for row in rows:
                    checked_quote(row, 'eligible_equipment_pages')
                    audit.require(type(row.get('count')) is int and 1 <= row['count'] <= 500 and
                                  _numeric(row.get('rated_ekw_each')) and 50 <= row['rated_ekw_each'] <= 20000,
                                  prefix + 'invalid equipment row quantity')
                audit.equipment_tables += 1
            validated[registration] = (spec, original, corrected)
            audit.documents += 1
        except (KeyError, ValueError, TypeError, OSError, RuntimeError) as error:
            audit.require(False, prefix + 'archive validation failed: ' + str(error))

    def compare_citation(citation, item, registration, *, public=False):
        spec, original, corrected = validated[registration]
        page = citation.get('page')
        label = registration + ': published citation '
        audit.require(page == item['pdf_page'] and flat(citation.get('quote', '')) == flat(item['quote']), label + 'page/quote mismatch')
        audit.require(citation.get('url') == spec['publisher_url'] and
                      (citation.get('content_sha256') or citation.get('doc_sha256')) == spec['document_sha256'], label + 'document registration/hash/URL mismatch')
        if page in original:
            audit.require(citation.get('native_page_text_sha256') == digest(original[page].encode()) and
                          citation.get('reviewed_page_text_sha256') == digest(corrected[page].encode()), label + 'native/reviewed text hash mismatch')
            transcribed = flat(item['quote']) not in original[page]
            audit.require(citation.get('representation') == ('reviewed_pdf_transcription' if transcribed else 'publisher_pdf'), label + 'transcription provenance mismatch')

    if source is not None:
        found = {}
        for row in walk(source):
            if isinstance(row.get('registration'), str) and row['registration'] in specs and isinstance(row.get('document'), dict):
                found[row['registration']] = row
        for registration, (spec, original, corrected) in validated.items():
            if not audit.require(registration in found, registration + ': recovered source document missing'):
                continue
            doc = found[registration]['document']
            review = doc.get('recovery_review') or {}
            audit.require(doc.get('content_sha256') == spec['document_sha256'] and doc.get('extraction_version') == VERSION and
                          review.get('registration') == registration and review.get('status') == 'verified' and
                          review.get('manifest_record_sha256') == digest(canonical(spec)), registration + ': source review identity mismatch')
            actual = doc.get('obligations', [])
            expected = spec.get('conditions', [])
            audit.require(len(actual) == len(expected), registration + ': recovered condition count mismatch')
            for item, obligation in zip(expected, actual):
                audit.require(all(obligation.get(k) == item.get(k) for k in ('metric', 'value', 'unit')), registration + ': recovered condition quantity mismatch')
                audit.require(obligation.get('reviewed_condition_scope') == item['subject_scope'], registration + ': recovered condition scope mismatch')
                compare_citation(obligation.get('citation', {}), item, registration)
            tables = doc.get('equipment', [])
            audit.require(len(tables) == len(spec.get('equipment', [])), registration + ': equipment table count mismatch')
            for table, approved in zip(tables, spec.get('equipment', [])):
                total = sum(r['count'] * r['rated_ekw_each'] for r in approved['rows'])
                audit.require(table.get('rated_ekw') == total and table.get('mw_total') == total / 1000 and
                              table.get('is_complete_fleet') is False, registration + ': equipment capacity/scope mismatch')
                rows = table.get('equipment_rows', [])
                audit.require(len(rows) == len(approved['rows']), registration + ': equipment row count mismatch')
                for row, item in zip(rows, approved['rows']):
                    audit.require(all(row.get(k) == item.get(k) for k in ('count', 'rated_ekw_each')), registration + ': equipment row quantity mismatch')
                    compare_citation(row.get('citation', {}), item, registration)

    publications = list((cards or {}).values())
    if public_documents is not None:
        by_registration = {row.get('registration'): row for row in public_documents}
        audit.require(len(by_registration) == len(public_documents) and set(by_registration) == set(specs), 'Public standalone document coverage differs from reviewed archive')
        for registration, row in by_registration.items():
            if registration not in validated:
                continue
            spec = validated[registration][0]
            conditions = row.get('conditions', [])
            audit.require(row.get('content_sha256') == spec['document_sha256'] and row.get('url') == spec['publisher_url'], registration + ': standalone document identity differs')
            audit.require(len(conditions) == len(spec.get('conditions', [])), registration + ': standalone condition coverage differs')
            audit.require(len(row.get('equipment_claims', [])) == len(spec.get('equipment', [])), registration + ': standalone equipment coverage differs')
            audit.require(all(x.get('registration') == registration for x in conditions), registration + ': standalone condition assigned to another registration')
            publications.append({'facility': {'permit_limits': conditions}, 'claims': row.get('equipment_claims', [])})
    if publications:
        for card in publications:
            for limit in card.get('facility', {}).get('permit_limits', []):
                if limit.get('extraction_version') != VERSION:
                    continue
                registration = limit.get('registration')
                if not audit.require(registration in validated, 'Public condition assigned to unknown reviewed registration'):
                    continue
                spec = validated[registration][0]
                approved = [x for x in spec.get('conditions', []) if all(x.get(k) == limit.get(k) for k in ('metric', 'value', 'unit')) and x['pdf_page'] == limit.get('page') and flat(x['quote']) == flat(limit.get('quote', ''))]
                if audit.require(len(approved) == 1, registration + ': public condition not approved by original document review'):
                    compare_citation(limit, approved[0], registration, public=True)
                    for public_key, manifest_key in (('reviewed_condition_scope', 'subject_scope'),
                                                     ('reviewed_operating_purpose', 'purpose'), ('reviewed_period', 'period')):
                        audit.require(limit.get(public_key) == approved[0].get(manifest_key), registration + ': public condition scope/purpose/period differs')
                audit.require(limit.get('applies_to_entire_site') is False and limit.get('is_document_condition') is True,
                              registration + ': condition promoted to site measurement')
            for claim in card.get('claims', []):
                metadata = claim.get('permit_metadata') or {}
                review = metadata.get('recovery_review') or {}
                if not review:
                    continue
                registration = review.get('registration')
                if not audit.require(registration in validated, 'Public equipment assigned to unknown reviewed registration'):
                    continue
                spec = validated[registration][0]
                tables = spec.get('equipment', [])
                if not audit.require(len(tables) == 1, registration + ': public equipment lacks single approved table'):
                    continue
                expected = tables[0]['rows']
                total = sum(r['count'] * r['rated_ekw_each'] for r in expected) / 1000
                audit.require(claim.get('value') == total and claim.get('unit') == 'MW nameplate' and claim.get('is_complete_fleet') is False,
                              registration + ': public equipment capacity/scope differs')
                citations = claim.get('citations', [])
                audit.require(len(citations) == len(expected), registration + ': public equipment citations differ')
                for citation, item in zip(citations, expected):
                    compare_citation(citation, item, registration, public=True)
    return audit


def read_json(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path(__file__).resolve().parents[1] / 'core/wattdocket/production/data/virginia_recovery_reviews.json')
    parser.add_argument('--archive-dir', action='append', type=Path, required=True)
    parser.add_argument('--receipts', action='append', type=Path, default=[])
    parser.add_argument('--source-state', type=Path)
    parser.add_argument('--public-dir', type=Path, help='Local directory containing cards.json, including an S3 mirror')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    files = [p for root in args.archive_dir for p in root.rglob('*') if p.is_file()]
    captures = []
    for p in set(args.receipts + [p for p in files if 'receipt' in p.name and p.suffix == '.json']):
        captures.append(read_json(p))
    source = read_json(args.source_state) if args.source_state else None
    if source:
        captures.append(source)
    images = {digest(p.read_bytes()): p for p in files if p.suffix.lower() == '.png'}

    def pdf_loader(registration, sha):
        candidates = [p for p in files if p.name in {registration + '.pdf', sha, sha + '.pdf', sha + '.gz'}]
        for p in candidates:
            with (gzip.open(p, 'rb') if p.suffix == '.gz' else open(p, 'rb')) as handle:
                raw = handle.read(MAX_PDF + 1)
            if digest(raw) == sha:
                return raw
        raise FileNotFoundError('No original PDF matching reviewed hash for ' + registration)

    def image_loader(sha):
        if sha in images:
            return images[sha].read_bytes()
        # Uploaded review evidence uses the same immutable raw/SHA.gz storage
        # as PDFs. Do not depend on loose research PNG filenames being present.
        for p in files:
            if p.name not in {sha, sha + '.png', sha + '.gz'}:
                continue
            with (gzip.open(p, 'rb') if p.suffix == '.gz' else open(p, 'rb')) as handle:
                raw = handle.read(MAX_PDF + 1)
            if len(raw) <= MAX_PDF and raw.startswith(b'\x89PNG\r\n\x1a\n') and digest(raw) == sha:
                return raw
        return b''

    cards = read_json(args.public_dir / 'cards.json') if args.public_dir else None
    public_documents = read_json(args.public_dir / 'data-status.json').get('permit_recovery', {}).get('documents', []) if args.public_dir else None
    audit = verify(read_json(args.manifest), pdf_loader, captures,
                   image_loader=image_loader, source=source, cards=cards, public_documents=public_documents)
    output = json.dumps(audit.report(), indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    print(output, end='')
    return 0 if not audit.errors else 1


if __name__ == '__main__':
    raise SystemExit(main())
