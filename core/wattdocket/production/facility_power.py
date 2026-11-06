"""Dated site power and meter series, separate from annual resource totals.

Public interval objects are loaded on demand. A reporting site does not imply
an EPA/OSM inventory match or a whole-building meter boundary.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
import re

from .storage import json_bytes
from .power_quality import validate_demand

COLUMNS = ["timestamp_utc", "value", "source_row", "source_timestamp", "source_timezone"]
SERIES_METHODS = {"inl_c3_power": "publisher_reported_meter_power",
                  "nrel_esif_power": "derived_hourly_arithmetic_mean_of_metered_samples"}


def require(condition, message):
    if not condition:
        raise ValueError("Facility power: " + message)


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.utcoffset() is not None, "timestamp lacks timezone")
    return parsed


def receipt_for(state, url, sha):
    found = [r for r in state.get("receipts", []) if r.get("url") == url and r.get("sha256") == sha]
    require(len(found) == 1, "source lacks one exact archived receipt: " + str(url))
    receipt = deepcopy(found[0])
    # Public dataset hosts may redirect to expiring signed object URLs. Keep
    # those transport credentials only in the private capture, not publication.
    receipt.pop("final_url", None)
    require(re.fullmatch(r"[0-9a-f]{64}", sha or "") and receipt.get("raw_key") == f"raw/{sha}.gz", "invalid archive digest/key")
    timestamp(receipt["retrieved_at"])
    return receipt


def _provenance(row, state, sid):
    row["source_id"] = sid
    row["source_receipt"] = receipt_for(state, row["source_url"], row["source_sha256"])
    row["retrieved_at"] = row["source_receipt"]["retrieved_at"]
    row["checked_at"] = state.get("last_success_at")
    row["collection_status"] = state.get("status")
    require(row.get("source_locator"), "source locator missing")


def validate_series(series):
    require(series.get("metric") == "electricity_power" and series.get("unit") == "kW", "unsupported interval quantity")
    require(series.get("historical_snapshot") is True, "historical series cannot claim live telemetry")
    require(type(series.get("whole_facility")) is bool and series.get("measurement_boundary"), "meter boundary missing")
    require(series.get("measurement_basis") and series.get("time_basis") and series.get("methodology_note"), "meter method missing")
    require(series.get("license"), "redistribution license missing")
    columns, rows = series.get("columns", []), series.get("readings", [])
    require(columns[:5] == COLUMNS and len(set(columns)) == len(columns), "unknown reading columns")
    require(0 < len(rows) <= 150_000, "interval count exceeds reviewed bound")
    step = series.get("interval_minutes")
    require(step is None or type(step) is int and 1 <= step <= 60, "invalid interval length")
    previous, missing, values = None, 0, []
    for row in rows:
        require(len(row) == len(columns), "interval column count differs")
        current = timestamp(row[0])
        require(current.utcoffset().total_seconds() == 0, "normalized reading is not UTC")
        if previous is not None:
            seconds = (current - previous).total_seconds()
            require(seconds > 0 and (step is None or seconds == step * 60), "duplicate, unordered or silently missing interval")
        previous = current
        empty_bin = (row[1] is None and "sample_count" in columns and row[columns.index("sample_count")] == 0)
        require(empty_bin or all(v is not None and str(v).strip() for v in row[2:5]), "original timestamp/row/timezone missing")
        if row[1] is None:
            missing += 1
        else:
            require(type(row[1]) in (int, float) and math.isfinite(row[1]) and 0 <= row[1] <= 1_000_000, "invalid power reading")
            values.append(row[1])
        if series.get("measurement_basis") == "derived_hourly_arithmetic_mean_of_metered_samples":
            require(columns[5:] == ["sample_count", "max_gap_seconds", "sample_sum_kw", "duplicate_timestamp_count"], "derived mean lacks audit columns")
            count, gap, total, duplicates = row[5:]
            require(type(count) is int and count >= 0 and type(duplicates) is int and duplicates >= 0,
                    "invalid source sample counts")
            require(type(total) in (int, float) and math.isfinite(total) and total >= 0, "invalid sample sum")
            eligible = count >= 45 and type(gap) in (int, float) and math.isfinite(gap) and 0 <= gap <= 300 and duplicates == 0
            require((row[1] is not None) == eligible, "hourly mean bypasses source coverage gate")
            if eligible:
                require(math.isclose(row[1], total / count, rel_tol=1e-12, abs_tol=1e-8), "hourly arithmetic mean differs from its samples")
    require(values and missing == series.get("missing_count"), "empty numeric series or wrong missing count")
    require(series.get("observed_start") == rows[0][0] and series.get("observed_end") == rows[-1][0], "observation bounds differ from rows")
    return {"reading_count": len(rows), "measured_count": len(values), "missing_count": missing,
            "maximum_reported_kw": max(values), "minimum_reported_kw": min(values)}


def build_facility_power(states, release_id, built_at):
    sites, series, demands, excluded, files = {}, [], [], [], {}
    seen = set()
    for sid, state in sorted(states.items()):
        data = (state.get("result") or {}).get("data") or {}
        if not isinstance(data, dict) or "power_sites" not in data:
            continue
        local_sites = set()
        for original in data["power_sites"]:
            site = deepcopy(original)
            site_id = site.get("site_id")
            require(site_id and site_id not in sites, "duplicate or empty reporting site")
            require(site.get("name") and site.get("address") and site.get("reporting_boundary"), "site identity/boundary missing")
            require(site.get("inventory_record_ids") == [], "inventory binding needs a separately reviewed resolver")
            evidence = site.get("identity_evidence", [])
            require(evidence, "physical site lacks primary identity evidence")
            for proof in evidence:
                require(proof.get("locator") and (proof.get("quote") or
                        proof.get("representation") == "publisher_json" and proof.get("source_fields")),
                        "identity proof lacks locator or primary identity fields")
                proof["receipt"] = receipt_for(state, proof["url"], proof["sha256"])
            site["source_id"], site["checked_at"] = sid, state.get("last_success_at")
            sites[site_id] = site
            local_sites.add(site_id)
        for original in data.get("power_series", []):
            item = deepcopy(original)
            series_id = item.get("series_id", "")
            require(re.fullmatch(r"[a-z0-9_-]+", series_id) and series_id not in seen, "unsafe/duplicate series ID")
            seen.add(series_id)
            require(item.get("site_id") in local_sites, "series lacks its own verified site")
            require(sid in SERIES_METHODS and item.get("measurement_basis") == SERIES_METHODS[sid]
                    and item.get("whole_facility") is False and item.get("interval_minutes") == 60,
                    "source's reviewed method or meter boundary changed")
            stats = validate_series(item)
            _provenance(item, state, sid)
            for proof in item.get("method_evidence", []):
                proof["receipt"] = receipt_for(state, proof["url"], proof["sha256"])
            license_info = item.get("license")
            if isinstance(license_info, dict) and license_info.get("url"):
                license_info["receipt"] = receipt_for(state, license_info["url"], license_info["source_sha256"])
            item.update(stats, release_id=release_id)
            path = f"power/{series_id}.json"
            files[path] = json_bytes(item)
            series.append({k: v for k, v in item.items() if k != "readings"} | {"readings_path": path})
        for original in data.get("power_demands", []):
            item = deepcopy(original)
            require(item.get("demand_id") and item["demand_id"] not in seen, "duplicate demand ID")
            seen.add(item["demand_id"])
            require(item.get("site_id") in local_sites, "demand lacks its own verified site")
            require(item.get("metric") == "electricity_peak_demand_reported" and item.get("unit") in {"kW", "MW"}, "unsupported demand metric/unit")
            require(type(item.get("value")) in (int, float) and math.isfinite(item["value"]) and 0 < item["value"] <= 1_000_000, "invalid demand value")
            require(type(item.get("reporting_year")) is int and 2000 <= item["reporting_year"] < timestamp(built_at).year, "invalid annual demand period")
            boundary = item.get("measurement_boundary")
            require(boundary in {"largest_individual_meter_entry", "reported_campus_maximum"}, "unreviewed demand boundary")
            require(item.get("whole_facility") is (boundary == "reported_campus_maximum"), "demand scope contradicts boundary")
            require(item.get("note"), "demand limitations missing")
            _provenance(item, state, sid)
            validate_demand(item, sites[item["site_id"]])
            demands.append(item)
        excluded.extend(dict(row, source_id=sid) for row in data.get("excluded", []))
    coverage = {"power_reporting_sites": len(sites), "power_interval_series": len(series),
                "power_interval_readings": sum(s["measured_count"] for s in series),
                "power_missing_intervals": sum(s["missing_count"] for s in series),
                "reported_peak_demand_observations": len(demands),
                "reported_campus_peak_observations": sum(d["whole_facility"] for d in demands),
                "live_facility_meter_series": 0}
    index = {"schema_version": "1", "release_id": release_id, "built_at": built_at,
             "sites": list(sites.values()), "series": series, "demands": demands,
             "excluded": excluded, "coverage": coverage}
    if sites:
        files["facility-power.json"] = json_bytes(index)
    return files, coverage
