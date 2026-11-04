"""Scheduled capture -> validate -> immutable publish -> conditional promotion."""
from __future__ import annotations

import argparse
import gzip
import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .build import SCHEMA_VERSION, build_release
from .fetch import CaptureFetcher, parsed, stamp
from .storage import LocalStore, S3Store, digest, json_bytes, read_json

POLICIES = {
    "echo": {"interval": 86400, "stale": 3 * 86400, "required": True, "minimum": 400},
    "egrid": {"interval": 86400, "stale": 7 * 86400, "required": True, "minimum": 25},
    "egrid_geometry": {"interval": 30 * 86400, "stale": 45 * 86400, "required": True, "minimum": 25},
    "eia930": {"interval": 6 * 3600, "stale": 48 * 3600, "required": True, "minimum": 40},
    "ercot": {"interval": 15 * 60, "stale": 2 * 3600, "required": True, "minimum": 10},
    "virginia_permits": {"interval": 86400, "stale": 3 * 86400, "required": True, "minimum": 150},
    "texas_permits": {"interval": 7 * 86400, "stale": 14 * 86400, "required": False, "minimum": 1},
    "osm": {"interval": 7 * 86400, "stale": 14 * 86400, "required": False, "minimum": 1},
    "cornerstone_egrid2024": {"interval": 7 * 86400, "stale": 30 * 86400, "required": False, "minimum": 25},
    "google_water2025": {"interval": 7 * 86400, "stale": 30 * 86400, "required": False, "minimum": 40},
    # Annual disclosures and reviewed identity evidence add no paid API or job.
    # Slow/changed sources retain dated last-good data and retry at most daily.
    "meta_actuals2024": {"interval": 7 * 86400, "stale": 30 * 86400, "required": False,
                         "minimum": 90, "maximum": 90, "retry_interval": 86400,
                         "max_normalized_bytes": 2_000_000,
                         "fetcher": {"source_budget_seconds": 60, "max_bytes": 4_000_000}},
    "massachusetts_energy": {"interval": 7 * 86400, "stale": 30 * 86400, "required": False,
                             "minimum": 14, "maximum": 18, "retry_interval": 86400,
                             "max_normalized_bytes": 2_000_000,
                             "fetcher": {"source_budget_seconds": 60, "max_bytes": 2_000_000}},
    "ghgrp": {"interval": 7 * 86400, "stale": 30 * 86400, "required": False,
              "minimum": 10, "maximum": 1000, "retry_interval": 86400,
              "max_normalized_bytes": 5_000_000,
              "fetcher": {"source_budget_seconds": 60, "max_bytes": 2_000_000}},
    "disclosure_identity": {"interval": 7 * 86400, "stale": 30 * 86400, "required": False,
                            "minimum": 6, "maximum": 6, "retry_interval": 86400,
                            "max_normalized_bytes": 1_000_000,
                            "fetcher": {"source_budget_seconds": 60, "max_bytes": 16_000_000}},
}
LABELS = {"echo": "EPA ECHO", "egrid": "EPA eGRID", "egrid_geometry": "EPA eGRID boundaries",
          "eia930": "EIA-930", "ercot": "ERCOT fuel mix", "virginia_permits": "Virginia DEQ permits",
          "texas_permits": "Selected Texas permits", "osm": "OpenStreetMap",
          "cornerstone_egrid2024": "Cornerstone independent 2024 regional comparison",
          "google_water2025": "Google 2025 reported water by location",
          "meta_actuals2024": "Meta 2024 reported campus electricity, water and emissions",
          "massachusetts_energy": "Massachusetts selected building energy disclosures",
          "ghgrp": "EPA GHGRP selected data-processing combustion reports",
          "disclosure_identity": "Reviewed operator-disclosure identity evidence"}

# Includes the immutable manifest object; the current.json pointer is separate.
MAX_RELEASE_OBJECTS = 256
MAX_RELEASE_GZIP_BYTES = 12_000_000


def adapters():
    from . import sources_facilities as f, sources_grid as g
    from .sources_grid_alternative import collect_cornerstone_egrid2024
    from .sources_operator_reports import collect_google_water_disclosures
    from .sources_operator_actuals import collect_meta_actuals
    from .sources_facility_reporting import collect_massachusetts_reporting, collect_ghgrp_reporting
    from .facility_disclosure_identity import collect_disclosure_identity
    out = {"echo": f.collect_echo, "virginia_permits": f.collect_virginia,
           "osm": f.collect_osm, "egrid": g.collect_egrid, "eia930": g.collect_eia930,
           "ercot": g.collect_ercot, "cornerstone_egrid2024": collect_cornerstone_egrid2024,
           "google_water2025": collect_google_water_disclosures,
           "meta_actuals2024": collect_meta_actuals,
           "massachusetts_energy": collect_massachusetts_reporting,
           "ghgrp": collect_ghgrp_reporting,
           "disclosure_identity": collect_disclosure_identity}
    if hasattr(g, "collect_egrid_geometry"):
        out["egrid_geometry"] = g.collect_egrid_geometry
    if hasattr(f, "collect_texas"):
        out["texas_permits"] = f.collect_texas
    return out


def health(state: dict, now: datetime, policy: dict):
    state = dict(state)
    if not state.get("label") or state["label"] == state.get("id"):
        state["label"] = LABELS.get(state.get("id"), state.get("id", "Unknown source"))
    if not state.get("result"):
        state["status"] = "failed"
        state["freshness_basis"] = "collection_health; no successful source result"
        return state
    fetched = state.get("last_success_at")
    age = (now - parsed(fetched)).total_seconds() if fetched else float("inf")
    state["capture_age_seconds"] = max(0, int(age)) if fetched else None
    observed = state.get("observation_end")
    observed_time = parsed(observed) if observed else None
    if observed_time is not None and observed_time.tzinfo is None:
        observed_time = observed_time.replace(tzinfo=timezone.utc)
    state["observation_age_seconds"] = max(0, int((now - observed_time).total_seconds())) if observed_time else None
    state["freshness_basis"] = ("collection_health_and_reported_interval" if state["id"] in ("ercot", "eia930")
                                else "collection_health_only; observation period and publisher edition remain unchanged")
    if state["id"] in ("ercot", "eia930") and observed:
        age = max(age, (now - observed_time).total_seconds())
    state["status"] = "stale" if state.get("error") or age > policy["stale"] else "current"
    state["stale_after_seconds"] = policy["stale"]
    return state


def retain_virginia_document_evidence(result: dict, previous: dict, timestamp: str):
    """Keep last-known quoted facts only for the exact same registry document.

    A failed request is still a failure; keeping its last-good text cannot turn
    it into a fresh document or silently reattach an older permit revision.
    """
    if result.get("source_id") != "virginia_permits":
        return
    data = result.get("data") or {}
    if not isinstance(data.get("permits"), list):
        return
    old_data = previous.get("result", {}).get("data", {})
    old_docs = {(p.get("registration"), p.get("permit_url")): p.get("document") or {}
                for p in old_data.get("permits", [])}
    stale, current, unavailable, cached = 0, 0, 0, 0
    for permit in data.get("permits", []):
        document = permit.get("document") or {}
        if document.get("status") == "parsed":
            if document.get("cache_reused"):
                cached += 1
            else:
                document.update(freshness="current", latest_fetch_status="succeeded")
                current += 1
            continue
        old = old_docs.get((permit.get("registration"), permit.get("permit_url")), {})
        if document.get("status") == "failed" and old.get("status") == "parsed" and old.get("extraction_version") == "va-deterministic-3":
            stale_note = " Latest retrieval failed; quoted facts come from the exact document's last-known captured text."
            permit["document"] = {**old, "freshness": "stale", "latest_fetch_status": "failed",
                                  "latest_fetch_error": document.get("error"),
                                  "latest_fetch_attempt_at": timestamp,
                                  "latest_failed_document": document,
                                  "note": (old.get("note") or "").removesuffix(stale_note) + stale_note}
            stale += 1
        else:
            document["freshness"] = "unavailable"
            unavailable += 1
    data.update(documents_ok=current + stale + cached, documents_current_parsed=current, documents_cached=cached,
                documents_stale=stale, documents_unavailable=unavailable,
                documents_failed=stale + unavailable)


def collect_one(source_id, adapter, store, now, *, force=False, fetcher_factory=CaptureFetcher):
    policy = POLICIES[source_id]
    key = f"state/sources/{source_id}.json"
    previous = read_json(store, key, {})
    previous.setdefault("id", source_id)
    last_attempt = previous.get("last_attempt_at")
    # Annual-report failures retry daily; existing operational feeds at most
    # hourly. A manual force remains an explicit bounded repair attempt.
    wait = min(policy["interval"], policy.get("retry_interval", 3600)) if previous.get("error") else policy["interval"]
    due = force or not last_attempt or (now - parsed(last_attempt)).total_seconds() >= wait
    if not due:
        return health(previous, now, policy)
    timestamp = stamp(now)
    fetcher = fetcher_factory(store, now, **policy.get("fetcher", {}))
    state = {**previous, "last_attempt_at": timestamp, "error": None}
    try:
        result = adapter(fetcher, now=now).as_dict()
        if result["source_id"] != source_id:
            raise ValueError("Adapter source identity changed")
        retain_virginia_document_evidence(result, previous, timestamp)
        count = result["record_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < policy["minimum"]:
            raise ValueError(f"Source count {count} is below coverage gate {policy['minimum']}")
        if policy.get("maximum") is not None and count > policy["maximum"]:
            raise ValueError(f"Source count {count} exceeds reviewed coverage gate {policy['maximum']}")
        prior_count = previous.get("record_count") or 0
        if prior_count and count < prior_count * .9:
            raise ValueError(f"Unexplained inventory drop: {prior_count} -> {count}; requires review")
        canonical = json_bytes(result)
        if len(canonical) > policy.get("max_normalized_bytes", 100_000_000):
            raise ValueError("Normalized source exceeds size limit")
        state.update({"id": source_id, "label": result["label"], "url": result["url"],
                      "result": result, "result_sha256": digest(canonical), "record_count": count,
                      "retrieved_at": timestamp, "last_success_at": timestamp,
                      **{k: result.get(k) for k in ("observation_start", "observation_end", "published_at", "data_vintage", "coverage", "note")}})
        # A partially read permit corpus is visible, never a silent success.
        documents_failed = result.get("data", {}).get("documents_failed", 0) if isinstance(result.get("data"), dict) else 0
        state["documents_failed"] = documents_failed
        state["partial"] = documents_failed > 0
        if documents_failed:
            stale_docs = result.get("data", {}).get("documents_stale", 0)
            state["note"] = (state.get("note") or "") + f" {documents_failed} linked documents failed extraction."
            state["note"] += (f" {stale_docs} retain labeled last-known evidence; unavailable documents have no facts."
                              if stale_docs else " Their facts are withheld.")
        state["receipts"] = list(fetcher.receipts.values())
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"[:2000]
        state.setdefault("label", LABELS.get(source_id, source_id))
        state.setdefault("url", None)
        state.setdefault("record_count", 0)
        state["failed_attempt_receipts"] = list(fetcher.receipts.values())
    state = health(state, now, policy)
    store.put(key, json_bytes(state))
    store.put(f"runs/{timestamp.replace(':', '')}/sources/{source_id}.json", json_bytes(state), create_only=True)
    print(json.dumps({"source": source_id, "status": state["status"], "count": state["record_count"],
                      "error": state.get("error"), "partial": state.get("partial", False)}), flush=True)
    return state


def publish(public, files: dict[str, bytes], manifest: dict, *, previous=None):
    """Verify each immutable object before a compare-and-swap of the manifest."""
    if len(files) + 1 > MAX_RELEASE_OBJECTS:
        raise ValueError("Release exceeds the bounded public object count")
    # Preflight every object before uploading any of this release. Gzip size
    # depends on content; normalized byte bounds alone cannot bound storage.
    compressed_files = {}
    total_gzip_bytes = 0
    for path, raw in sorted(files.items()):
        compressed = gzip.compress(raw, compresslevel=6, mtime=0)
        total_gzip_bytes += len(compressed)
        if total_gzip_bytes > MAX_RELEASE_GZIP_BYTES:
            raise ValueError("Release exceeds the bounded public gzip byte count")
        compressed_files[path] = compressed
    prefix = manifest["base_path"].lstrip("/")
    objects = {}
    for path, raw in sorted(files.items()):
        compressed = compressed_files[path]
        public.put(prefix + "/" + path, compressed, create_only=True,
                   content_type="application/json", content_encoding="gzip",
                   cache_control="public,max-age=86400,immutable")
        objects[path] = {"sha256": digest(raw), "bytes": len(raw), "gzip_bytes": len(compressed)}
    manifest = {**manifest, "objects": objects}
    manifest_raw = json_bytes(manifest)
    public.put(prefix + "/manifest.json", manifest_raw, create_only=True,
               cache_control="public,max-age=86400,immutable")
    public.put("data/current.json", manifest_raw, expected=previous[1] if previous else None,
               create_only=previous is None, cache_control="public,max-age=60,must-revalidate")
    return manifest


def run(archive, public, *, now=None, force=False, selected=None, adapter_map=None, revision="unknown", promote=True):
    now = now or datetime.now(timezone.utc)
    timestamp = stamp(now)
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    previous = public.get("data/current.json")
    old_manifest = json.loads(previous[0]) if previous else None
    available = adapter_map or adapters()
    selected = set(selected or available)
    report = {"run_id": run_id, "started_at": timestamp, "status": "running", "revision": revision,
              "build_id": os.environ.get("CODEBUILD_BUILD_ID"),
              "last_published_release": old_manifest.get("release_id") if old_manifest else None}
    public.put("data/pipeline-status.json", json_bytes(report), cache_control="public,max-age=60,must-revalidate")
    states = {}
    try:
        # Bound network/CPU concurrency and isolate each source's failure.
        with ThreadPoolExecutor(max_workers=3) as pool:
            pending = {pool.submit(collect_one, sid, adapter, archive, now, force=force): sid
                       for sid, adapter in available.items() if sid in selected}
            for future in as_completed(pending):
                states[pending[future]] = future.result()
        for sid, policy in POLICIES.items():
            if sid not in states:
                saved = read_json(archive, f"state/sources/{sid}.json", {"id": sid, "label": sid, "record_count": 0})
                states[sid] = health(saved, now, policy)
        # A newly published annual workbook must not wait for the monthly
        # geometry cadence. Retry the geometry edition once, unless it already
        # ran this job. A failure retains its dated prior source; the builder
        # withholds assignments across mismatched editions.
        rates = states.get("egrid", {}).get("result", {}).get("data", {}).get("rates", {})
        rate_years = {row.get("data_year") for row in rates.values()}
        geo_state = states.get("egrid_geometry", {})
        geo_data = geo_state.get("result", {}).get("data")
        if (len(rate_years) == 1 and None not in rate_years and geo_data and
                geo_data.get("data_year") not in rate_years and "egrid_geometry" in available and
                geo_state.get("last_attempt_at") != timestamp):
            states["egrid_geometry"] = collect_one("egrid_geometry", available["egrid_geometry"], archive, now, force=True)
        missing = [sid for sid, policy in POLICIES.items() if policy["required"] and not states[sid].get("result")]
        if missing:
            raise ValueError("Required sources unavailable: " + ", ".join(missing))
        # A last-good source may be served with delayed status for a bounded
        # interval. Beyond it the release stays pinned and health explains why.
        expired = [sid for sid, policy in POLICIES.items() if policy["required"] and states[sid].get("last_success_at")
                   and (now - parsed(states[sid]["last_success_at"])).total_seconds() > policy["stale"] * 2]
        expired += [sid for sid in ("eia930", "ercot") if states.get(sid, {}).get("observation_end")
                    and (now - parsed(states[sid]["observation_end"])).total_seconds() > POLICIES[sid]["stale"] * 2]
        if expired:
            raise ValueError("Required last-good sources beyond retention grace: " + ", ".join(expired))
        source_hash = digest(json_bytes({sid: state.get("result_sha256") for sid, state in sorted(states.items())}))[:12]
        release_id = f"{run_id}-{source_hash}"
        files, coverage = build_release(states, release_id, timestamp)
        manifest = {"schema_version": SCHEMA_VERSION, "release_id": release_id, "built_at": timestamp,
                    "revision": revision, "base_path": f"/data/releases/{release_id}",
                    "coverage": coverage, "previous_release_id": old_manifest.get("release_id") if old_manifest else None}
        for name, filename in (("cards_path", "cards.json"), ("drawers_path", "drawers.json"), ("operators_path", "operators.json"), ("explain_path", "explain.json")):
            manifest[name] = manifest["base_path"] + "/" + filename
        if promote:
            manifest = publish(public, files, manifest, previous=previous)
        else:
            for path, raw in files.items():
                archive.put(f"preview/{release_id}/{path}", raw)
        report.update({"status": "degraded" if any(s["status"] != "current" or s.get("partial") for s in states.values()) else "succeeded",
                       "completed_at": stamp(datetime.now(timezone.utc)), "release_id": release_id,
                       "promoted": promote, "coverage": coverage,
                       "sources": [{k: v for k, v in s.items() if k not in ("result", "receipts", "failed_attempt_receipts")} for s in states.values()]})
    except Exception as exc:
        report.update({"status": "failed", "completed_at": stamp(datetime.now(timezone.utc)),
                       "error": f"{type(exc).__name__}: {exc}", "promoted": False,
                       "sources": [{k: v for k, v in s.items() if k not in ("result", "receipts", "failed_attempt_receipts")} for s in states.values()]})
        archive.put(f"runs/{run_id}/failure.txt", traceback.format_exc().encode(), content_type="text/plain")
        raise
    finally:
        archive.put(f"runs/{run_id}/report.json", json_bytes(report))
        public.put("data/pipeline-status.json", json_bytes(report), cache_control="public,max-age=60,must-revalidate")
        print(json.dumps({k: v for k, v in report.items() if k != "sources"}), flush=True)
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--archive-dir", type=Path, default=Path(".production/archive"))
    p.add_argument("--public-dir", type=Path, default=Path(".production/public"))
    p.add_argument("--archive-bucket", default=os.environ.get("WATTDOCKET_ARCHIVE_BUCKET"))
    p.add_argument("--public-bucket", default=os.environ.get("WATTDOCKET_PUBLIC_BUCKET"))
    p.add_argument("--force", action="store_true")
    p.add_argument("--sources", help="Comma-separated adapter IDs; other sources read last-good state")
    p.add_argument("--no-promote", action="store_true")
    args = p.parse_args()
    archive = S3Store(args.archive_bucket) if args.archive_bucket else LocalStore(args.archive_dir)
    public = S3Store(args.public_bucket) if args.public_bucket else LocalStore(args.public_dir)
    run(archive, public, force=args.force or os.environ.get("WATTDOCKET_FORCE_REFRESH") == "1", selected=args.sources.split(",") if args.sources else None,
        revision=os.environ.get("WATTDOCKET_REVISION", "local"), promote=not args.no_promote)


if __name__ == "__main__":
    main()
