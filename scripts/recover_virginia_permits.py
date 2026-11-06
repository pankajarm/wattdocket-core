"""Import exact reviewed original PDFs, then replay only the Virginia state.

No network or AWS operations. Dry run by default. --write seeds original raw
captures and HTTP metadata plus a CAS-protected normalized local source state.
Retrieval dates come from captured receipts; review/replay never renew them.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from wattdocket.production.fetch import parsed
from wattdocket.production.permit_recovery import apply_recovery_review, reviewed_records, REVIEW_VERSION
from wattdocket.production.sources_facilities import extract_virginia_document, parse_virginia_registry, VA_REGISTRY
from wattdocket.production.identity import permit_metadata
from wattdocket.production.storage import LocalStore, ConflictError, digest, json_bytes


def read_json_file(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def verified_receipt(record, raw, url, *, pdf=True):
    if (record.get("url", record.get("requested_url")) != url
            or record.get("final_url", url) != url
            or record.get("status", record.get("http_status")) != 200
            or record.get("sha256") != digest(raw) or record.get("bytes") != len(raw)
            or (pdf and not raw.startswith(b"%PDF-"))):
        raise ValueError("Recovered artifact does not match its original successful receipt")
    date = parsed(record["retrieved_at"])
    if date.tzinfo is None:
        raise ValueError("Original receipt has no timezone")
    headers = record.get("headers", {})
    return {"url": url, "final_url": url, "retrieved_at": record["retrieved_at"],
            "sha256": digest(raw), "bytes": len(raw), "raw_key": "raw/" + digest(raw) + ".gz",
            "content_type": "application/pdf" if pdf else "text/html",
            "etag": record.get("etag") or headers.get("ETag"),
            "last_modified": record.get("last_modified") or headers.get("Last-Modified"),
            "capture_origin": "local_verified_public_source_capture",
            "request_profile": "deq_public_pdf" if pdf else "public_registry_html",
            "request_headers": {"User-Agent": "Mozilla/5.0", "Accept": "application/pdf"} if pdf else {},
            "imported_without_new_retrieval": True}


def recover(states, artifact_dir, store, *, write=False):
    states = copy.deepcopy(states)
    previous = states["virginia_permits"]
    source_key = "state/sources/virginia_permits.json"
    prior_object = store.get(source_key)
    if prior_object and json.loads(prior_object[0]).get("result_sha256") != previous.get("result_sha256"):
        raise ValueError("Local archive source differs from the requested replay snapshot")
    records = reviewed_records()
    if not records:
        raise ValueError("No packaged reviewed document approvals")
    registry_raw = (artifact_dir / "research-b/official-index.html").read_bytes()
    registry_receipt = verified_receipt(read_json_file(artifact_dir / "research-b/official-index-receipt.json"),
                                        registry_raw, VA_REGISTRY, pdf=False)
    rows, stamp = parse_virginia_registry(registry_raw.decode())
    old_rows = {(p["registration"], p["permit_url"]): p for p in previous["result"]["data"]["permits"]}
    old_by_reg = {p["registration"]: p for p in old_rows.values()}
    if set(old_by_reg) != {p["registration"] for p in rows}:
        raise ValueError("Registry identities changed beyond this bounded recovery; review required")
    receipts_b = {r["registration"]: r for r in read_json_file(artifact_dir / "research-b/browser-header-receipts.json")}
    captures = [(registry_raw, registry_receipt)]
    audit = []
    equivalent_urls = []
    for p in rows:
        old = old_by_reg[p["registration"]]
        p["document"] = copy.deepcopy(old["document"])
        reg = p["registration"]
        if old["permit_url"] != p["permit_url"]:
            # No timestamp/name shortcut: both exact public URLs must have
            # successful original captures with identical PDF bytes.
            base = artifact_dir / "registry-version-check"
            raw_old, raw_new = [(base / f"{reg}-{n}.pdf").read_bytes() for n in (0, 1)]
            rec_old = verified_receipt(read_json_file(base / f"{reg}-0-receipt.json"), raw_old, old["permit_url"])
            rec_new = verified_receipt(read_json_file(base / f"{reg}-1-receipt.json"), raw_new, p["permit_url"])
            if raw_old != raw_new:
                raise ValueError("Changed registry URL does not serve the same verified PDF bytes")
            doc = extract_virginia_document(raw_new, p["permit_url"], retrieved_via=p["permit_url"], registration=reg)
            condition_key = lambda d: sorted((o["metric"], o["value"], o["unit"]) for o in d.get("obligations", []))
            table_key = lambda d: sorted((e["value"], e["mw_total"]) for e in d.get("equipment", []))
            if condition_key(doc) != condition_key(old["document"]) or table_key(doc) != table_key(old["document"]):
                raise ValueError("Equivalent URL re-extraction changes numerical evidence; separate review required")
            proof = {"previous_url": old["permit_url"], "current_url": p["permit_url"],
                     "document_sha256": digest(raw_new), "old_url_receipt": rec_old, "new_url_receipt": rec_new,
                     "basis": "Exact original bytes retrieved successfully from both URLs; numeric facts reconciled."}
            doc.update(receipt=rec_new, retrieved_at=rec_new["retrieved_at"], freshness="current",
                       latest_fetch_status="succeeded_local_capture", publisher_url_equivalence=proof)
            p["document"] = doc
            captures.extend([(raw_old, rec_old), (raw_new, rec_new)])
            equivalent_urls.append(proof)
        if reg not in records:
            p.update(permit_metadata(p))
            continue
        a = artifact_dir / "research-a" / (reg + ".pdf")
        path = a if a.is_file() else artifact_dir / "research-b" / (reg + ".pdf")
        raw = path.read_bytes()
        original_receipt = read_json_file(a.with_name(reg + "-direct-receipt.json")) if a.is_file() else receipts_b[reg]
        receipt = verified_receipt(original_receipt, raw, p["permit_url"])
        doc = extract_virginia_document(raw, p["permit_url"], retrieved_via=p["permit_url"], registration=reg)
        doc = apply_recovery_review(doc, raw, p, registry_receipt=registry_receipt)
        if doc.get("recovery_review", {}).get("status") != "verified":
            raise ValueError("Recovered PDF requires new source review: " + reg)
        doc.update(receipt=receipt, retrieved_at=receipt["retrieved_at"], freshness="current",
                   latest_fetch_status="succeeded_local_capture", archive_source="imported_original_publisher_pdf",
                   prior_recovery_failure={"status": old["document"].get("status"),
                                           "error": old["document"].get("error"),
                                           "source_result_sha256": previous.get("result_sha256")})
        p["document"] = doc
        p.update(permit_metadata(p))
        captures.append((raw, receipt))
        audit.append({"registration": reg, "document_sha256": digest(raw), "pages": doc["pages_claimed"],
                      "conditions": len(doc["obligations"]), "equipment_tables": len(doc["equipment"]),
                      "original_retrieved_at": receipt["retrieved_at"], "document_extent": doc["recovery_review"]["document_extent"]})
    if len(audit) != len(records):
        raise ValueError("A reviewed registration is not in the current official registry")
    state = copy.deepcopy(previous)
    data = state["result"]["data"]
    ok = sum(p["document"].get("status") == "parsed" for p in rows)
    failed = len(rows) - ok
    archived = sum(p["document"].get("retrieved_at") is None and bool(p["document"].get("archived_by_at")) for p in rows)
    data.update(permits=rows, registry_as_of=stamp, registry_sha256=digest(registry_raw),
                registry_receipt=registry_receipt, registry_retrieved_via=VA_REGISTRY,
                documents_ok=ok, documents_failed=failed, documents_unavailable=failed,
                documents_stale=sum(p["document"].get("freshness") == "stale" for p in rows),
                documents_current_parsed=len(audit)+len(equivalent_urls), documents_cached=ok-len(audit)-len(equivalent_urls),
                documents_not_fetched=0, documents_from_retained_archive=archived,
                recovery_review_version=REVIEW_VERSION, reviewed_original_documents=len(audit))
    note = (f"{len(rows)} issued-registry rows; {ok} readable documents; {len(audit)} formerly unreadable documents "
            "recovered from original publisher PDFs with pinned visual page review. Current legal effectiveness remains unknown.")
    state["result"].update(published_at=stamp, coverage=note, note=note)
    state.update(retrieved_at=registry_receipt["retrieved_at"], last_success_at=registry_receipt["retrieved_at"],
                 last_attempt_at=registry_receipt["retrieved_at"], published_at=stamp, status="current", error=None,
                 documents_failed=failed, partial=bool(failed), note=note, coverage=note,
                 documents_from_retained_archive=archived, result_sha256=digest(json_bytes(state["result"])))
    old_receipts = {r["url"]: r for r in state.get("receipts", [])}
    old_receipts.update({r["url"]: r for _, r in captures})
    state["receipts"] = list(old_receipts.values())
    states["virginia_permits"] = state
    report = {"write": write, "recovered_documents": len(audit), "documents_ok": ok, "documents_failed": failed,
              "registry_retrieved_at": registry_receipt["retrieved_at"], "result_sha256": state["result_sha256"],
              "local_capture_not_aws_retrieval": True, "captures": audit,
              "exact_byte_url_equivalences": equivalent_urls}
    if write:
        # Validate the complete replay before any mutation. Persist old state and
        # original bytes first; replace normalized source state last with CAS.
        before = json_bytes(previous)
        prefix = "replays/virginia-recovery/" + digest(before)
        store.put(prefix + "/before.json", before, create_only=True)
        store.put(prefix + "/review-manifest.json", json_bytes({"review_version": REVIEW_VERSION, "documents": records}), create_only=True)
        for raw, receipt in captures:
            try:
                store.put(receipt["raw_key"], gzip.compress(raw, mtime=0), create_only=True, content_type="application/gzip")
            except ConflictError:
                old = store.get(receipt["raw_key"])
                if not old or digest(gzip.decompress(old[0])) != receipt["sha256"]:
                    raise ValueError("Existing archive bytes conflict with recovered original PDF")
            key = "http/" + digest(receipt["url"].encode()) + ".json"
            existing = store.get(key)
            if existing:
                metadata = json.loads(existing[0])
                if parsed(metadata["retrieved_at"]) > parsed(receipt["retrieved_at"]):
                    raise ValueError("Refusing to replace a newer HTTP receipt with a recovery capture")
            store.put(key, json_bytes(receipt), expected=existing[1] if existing else None,
                      create_only=existing is None)
        store.put(prefix + "/report.json", json_bytes(report), create_only=True)
        store.put(source_key, json_bytes(state), expected=prior_object[1] if prior_object else None,
                  create_only=prior_object is None)
    return states, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--states-file", type=Path, required=True)
    p.add_argument("--artifact-dir", type=Path, default=ROOT / ".production/permit-recovery")
    p.add_argument("--archive-dir", type=Path, default=ROOT / ".production/permit-recovery/archive")
    p.add_argument("--output-states", type=Path)
    p.add_argument("--report", type=Path)
    p.add_argument("--write", action="store_true")
    a = p.parse_args()
    states, report = recover(read_json_file(a.states_file), a.artifact_dir, LocalStore(a.archive_dir), write=a.write)
    if a.output_states:
        a.output_states.write_bytes(json_bytes(states))
    if a.report:
        a.report.write_bytes(json_bytes(report))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
