"""Reparse captured VA text without network or invented retrieval timestamps.

Defaults to a dry run. --write updates only the normalized VA state with CAS;
it does not publish or populate the HTTP cache with historical repository text.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from wattdocket.production.sources_facilities import READER, extract_virginia_document
from wattdocket.production.storage import LocalStore, digest, json_bytes, read_json


def captured(store, url):
    receipt = read_json(store, "http/" + digest(url.encode()) + ".json")
    if not receipt:
        return None
    obj = store.get(receipt["raw_key"])
    if not obj:
        return None
    raw = gzip.decompress(obj[0])
    if digest(raw) != receipt["sha256"]:
        raise ValueError("Archived capture checksum mismatch")
    return raw, receipt


def retained(permit):
    path = ROOT / "data/va_permits" / (permit["registration"].replace("-", "_") + ".txt")
    if not path.is_file():
        return None
    raw = path.read_bytes()
    source = re.search(rb"^URL Source:\s*(\S+)", raw, re.M)
    if not source or source.group(1).decode() != permit["permit_url"]:
        return None
    relative = path.relative_to(ROOT).as_posix()
    metadata = subprocess.check_output(["git", "log", "-1", "--format=%H%x00%cI", "--", relative], cwd=ROOT).decode().strip()
    if not metadata:
        return None
    commit, archived_at = metadata.split("\0")
    if subprocess.check_output(["git", "show", f"{commit}:{relative}"], cwd=ROOT) != raw:
        raise ValueError("Retained corpus differs from its archival commit: " + relative)
    sha = digest(raw)
    published = re.search(rb"^Published Time:\s*([^\n]+)", raw, re.M)
    return raw, {"url": READER + permit["permit_url"], "publisher_url": permit["permit_url"],
                 "sha256": sha, "raw_key": "raw/" + sha + ".gz", "bytes": len(raw),
                 "retrieved_at": None, "archived_by_at": archived_at, "archive_commit": commit,
                 "archive_path": relative, "capture_kind": "retained_repository_reader_text",
                 "publisher_reported_time": published.group(1).decode() if published else None,
                 "note": "Original retrieval time was not recorded; git proves these exact bytes were archived by this date."}


def replay(store, *, write=False):
    key = "state/sources/virginia_permits.json"
    prior = store.get(key)
    if not prior:
        raise ValueError("A current captured Virginia registry is required")
    state = json.loads(prior[0])
    data = state["result"]["data"]
    totals = {"registry_rows": len(data["permits"]), "captured_parsed": 0, "retained_parsed": 0, "failed": 0}
    failures, archived_receipts = [], []
    for permit in data["permits"]:
        errors, document = [], None
        for url in (permit["permit_url"], READER + permit["permit_url"]):
            capture = captured(store, url)
            if not capture:
                continue
            raw, receipt = capture
            try:
                document = extract_virginia_document(raw, permit["permit_url"], retrieved_via=url,
                                                     registration=permit["registration"])
                document.update(receipt=receipt, retrieved_at=receipt.get("retrieved_at"),
                                archive_source="captured_http_response")
                totals["captured_parsed"] += 1
                break
            except Exception as exc:
                errors.append(str(exc))
        if document is None:
            capture = retained(permit)
            if capture:
                raw, receipt = capture
                try:
                    document = extract_virginia_document(raw, permit["permit_url"], retrieved_via=READER,
                                                         registration=permit["registration"])
                    document.update(receipt=receipt, retrieved_at=None,
                                    archived_by_at=receipt["archived_by_at"],
                                    archive_source="retained_repository_reader_text",
                                    retrieval_time_known=False,
                                    note=receipt["note"])
                    totals["retained_parsed"] += 1
                    archived_receipts.append(receipt)
                    if write:
                        store.put(receipt["raw_key"], gzip.compress(raw, mtime=0), create_only=True,
                                  content_type="application/gzip")
                except Exception as exc:
                    errors.append(str(exc))
        if document is None:
            document = {"status": "failed", "obligations": [], "equipment": [],
                        "error": "; ".join(errors) or "No exact-URL verifiable captured text available"}
            totals["failed"] += 1
            failures.append({"registration": permit["registration"], "error": document["error"]})
        permit["document"] = document
    data.update(documents_ok=totals["captured_parsed"] + totals["retained_parsed"],
                documents_failed=totals["failed"], documents_not_fetched=0,
                documents_from_retained_archive=totals["retained_parsed"], extraction_version="va-deterministic-3")
    note = (f"Registry has {totals['registry_rows']} rows; {data['documents_ok']} documents parsed. "
            f"{totals['retained_parsed']} documents use exact-URL repository text with unknown original retrieval time; "
            f"{totals['failed']} documents lack verifiable bounded text and their facts are withheld.")
    state["result"].update(note=note, coverage=note)
    state.update(note=note, coverage=note, documents_failed=totals["failed"], partial=bool(totals["failed"]),
                 documents_from_retained_archive=totals["retained_parsed"],
                 result_sha256=digest(json_bytes(state["result"])))
    state.pop("archival_receipts", None)
    if write:
        # Preserve the complete old normalized record, including every failed
        # fetch/error and its receipt, before replacing extraction results.
        store.put("replays/virginia/" + digest(prior[0]) + "/before.json", prior[0], create_only=True)
        store.put("replays/virginia/" + digest(prior[0]) + "/archival-receipts.json", json_bytes(archived_receipts), create_only=True)
        store.put(key, json_bytes(state), expected=prior[1])
    return {**totals, "write": write, "failures": failures,
            "registry_retrieved_at": state.get("retrieved_at"), "result_sha256": state["result_sha256"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, default=ROOT / ".production/archive")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    print(json.dumps(replay(LocalStore(args.archive_dir), write=args.write), indent=2))
