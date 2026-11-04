"""An independently labeled 2024 comparison, never an EPA baseline replacement.

The pinned release's stated AR5 method disagrees with its annual CO2e mass.
Reconcile all 27 rows, disclose that conflict, and reconstruct the comparison
using annual gas masses and explicit AR5 weights. The companion comparison
uses AR6 non-fossil methane weights, also independently validated and preserved.
No geographic layer is implied.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime

from .models import Fetcher, SourceResult
from .sources_grid import (ACCOUNTING_BASIS, EGRID_CODES, FACTOR_LIMITATIONS, GWP_BASES,
                           GridSourceError, _number, _stamp, _utc,
                           annual_grid_components, identify_annual_gwp)

SOURCE_ID = "cornerstone_egrid2024"
RECORD_ID = "18968658"
RECORD_URL = "https://zenodo.org/records/" + RECORD_ID
RECORD_API = "https://zenodo.org/api/records/" + RECORD_ID
WORKBOOK_NAME = "egrid2024_data_metric.xlsx"
COMPARISON_NAME = "eGRID2023_vs_eGRID2024.xlsx"
METADATA_NAME = "metadata.txt"
RELEASE_VERSION = "1.0.0"
CODE_COMMIT = "8932a073aa4c0dac227f953e2805d5f64634b57e"
CODE_URL = "https://github.com/USEPA/egrid/blob/" + CODE_COMMIT + "/scripts/1_production_model/plant_file_create.R"
METHOD_ID = "cornerstone2024-annual-components-ar5-v1"
GEOGRAPHY_NOTE = ("Comparison by published eGRID region code only. A matching 2024 geographic boundary layer has not been verified; "
                  "this is not a 2024 factor assignment to a facility or utility.")


def file_url(name: str) -> str:
    return RECORD_API + "/files/" + name + "/content"


def _record_files(record: object) -> tuple[dict, dict]:
    if not isinstance(record, dict) or str(record.get("id")) != RECORD_ID:
        raise GridSourceError("Cornerstone record identity changed")
    metadata = record.get("metadata") or {}
    if (metadata.get("version") != RELEASE_VERSION or metadata.get("doi") != "10.5281/zenodo." + RECORD_ID or
            metadata.get("license", {}).get("id") != "cc-by-4.0" or metadata.get("publication_date") != "2026-03-17" or
            metadata.get("access_right") != "open"):
        raise GridSourceError("Cornerstone pinned version, publication date, access or license changed; review required")
    creators = [row.get("name", "") for row in metadata.get("creators", [])]
    if "Cornerstone Sustainability Data Initiative" not in creators:
        raise GridSourceError("Cornerstone publisher identity is missing")
    description = re.sub(r"<[^>]+>", " ", metadata.get("description", ""))
    if not all(term in description for term in ("AR5", "28", "265")):
        raise GridSourceError("Cornerstone declared GWP method changed; conflict reconciliation needs review")
    files = {}
    for entry in record.get("files", []):
        name = entry.get("key")
        if name in files:
            raise GridSourceError("Duplicate Cornerstone file identity")
        files[name] = entry
    for name in (WORKBOOK_NAME, COMPARISON_NAME, METADATA_NAME):
        entry = files.get(name, {})
        if (entry.get("links", {}).get("self") != file_url(name) or
                not re.fullmatch(r"md5:[0-9a-f]{32}", entry.get("checksum", "")) or
                not isinstance(entry.get("size"), int) or not 0 < entry["size"] < 25_000_000):
            raise GridSourceError("Cornerstone pinned file manifest is missing or changed")
    return metadata, files


def _checked_file(fetcher: Fetcher, name: str, manifest: dict) -> bytes:
    raw = fetcher.get_bytes(file_url(name))
    if len(raw) != manifest["size"] or "md5:" + hashlib.md5(raw).hexdigest() != manifest["checksum"]:
        raise GridSourceError(f"Cornerstone publisher checksum/size mismatch for {name}")
    return raw


def _input_metadata(raw: bytes) -> dict:
    text = raw.decode("utf-8-sig")
    expected = ("Repository: https://github.com/USEPA/egrid", "Branch: development", "Commit: " + CODE_COMMIT,
                "Script executed: eGRID_master.qmd")
    if not all(line in text for line in expected):
        raise GridSourceError("Cornerstone source code provenance changed; review required")
    inputs = {}
    for kind in ("EIA", "CEMS"):
        match = re.search(rf"^{kind} data retrieved:\s*(\S+)\s*$", text, re.M)
        if not match:
            raise GridSourceError("Cornerstone input retrieval metadata is missing")
        try:
            parsed = datetime.fromisoformat(match[1])
        except ValueError as exc:
            raise GridSourceError("Cornerstone input retrieval metadata is invalid") from exc
        if parsed.tzinfo is None:
            raise GridSourceError("Cornerstone input retrieval metadata lacks an offset")
        # The publisher uses -00:00 (unknown offset). Preserve its literal
        # representation rather than changing it into an asserted UTC clock.
        inputs[kind] = {"reported_timestamp": match[1], "offset_known": not match[1].endswith("-00:00")}
    return inputs


def _comparison_rows(raw: bytes) -> dict[str, dict]:
    import openpyxl
    workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    prefix = "2024 development branch eGRID subregion "
    fields = {"code": "eGRID subregion acronym", "co2": prefix + "annual CO2 total output emission rate (kg/MWh)",
              "ch4": prefix + "annual CH4 total output emission rate (kg/MWh)",
              "n2o": prefix + "annual N2O total output emission rate (kg/MWh)",
              "co2e": prefix + "CO2e output emission rate (kg/MWh)"}
    try:
        sheet = workbook.worksheets[0]
        sheet.reset_dimensions()
        rows = sheet.iter_rows(values_only=True)
        headers = next(rows)
        if not set(fields.values()) <= set(headers):
            raise GridSourceError("Cornerstone comparison workbook lost its gas-rate or CO2e columns")
        positions = {name: headers.index(label) for name, label in fields.items()}
        out = {}
        for row in rows:
            code = row[positions["code"]] if len(row) > positions["code"] else None
            if not code or code == "SUBRGN":
                continue
            if code not in EGRID_CODES or code in out:
                raise GridSourceError("Cornerstone comparison has unexpected or duplicate region")
            out[code] = {}
            for key, index in positions.items():
                if key == "code":
                    continue
                value = _number(row[index] if index < len(row) else None, field=key)
                if value is None or value < 0:
                    raise GridSourceError("Cornerstone comparison has missing or invalid values")
                out[code][key] = value
        if out.keys() != EGRID_CODES:
            raise GridSourceError("Cornerstone comparison requires all 27 regions")
        return out
    finally:
        workbook.close()


def normalize_cornerstone_2024(workbook_raw: bytes, comparison_raw: bytes) -> tuple[dict, dict]:
    components = annual_grid_components(workbook_raw, 2024, metric=True)
    published_gwp = identify_annual_gwp(components, metric=True)
    if published_gwp["basis"] != "IPCC AR6":
        raise GridSourceError("Cornerstone annual mass no longer matches its reviewed AR6 method; review required")
    comparison = _comparison_rows(comparison_raw)
    comparison_gwps = [label for label, factors in GWP_BASES.items()
                       if all(abs(sum(row[gas] * weight for gas, weight in factors.items()) - row["co2e"]) <= 1e-7
                              for row in comparison.values())]
    if comparison_gwps != ["IPCC AR6 non-fossil methane"]:
        raise GridSourceError("Cornerstone comparison no longer reconciles uniquely to reviewed AR6 non-fossil methane weights")
    rates, max_rounded_difference = {}, 0.0
    for code, row in sorted(components.items()):
        published = comparison[code]
        for gas, field in (("co2", "SRCO2RTA"), ("ch4", "SRCH4RTA"), ("n2o", "SRN2ORTA")):
            if abs(published[gas] - row[field]) > 1e-8:
                raise GridSourceError("Cornerstone comparison disagrees with the metric workbook's gas rates")
        rounded_ar5 = sum(published[gas] * weight for gas, weight in GWP_BASES["IPCC AR5"].items())
        ar5_mass_kg = row["SRCO2AN"] * 1000 + row["SRCH4AN"] * 28 + row["SRN2OAN"] * 265
        ar5_rate = ar5_mass_kg / row["SRNGENAN"]
        difference = abs(ar5_rate - rounded_ar5)
        # Source gas-rate columns are rounded before CO2e is reconstructed.
        # 0.0005 lb/MWh * 0.4536 kg/lb * (1+28+265) = 0.06668 kg/MWh.
        if difference > .067:
            raise GridSourceError("Cornerstone annual component masses disagree with rounded AR5 rates beyond their precision")
        max_rounded_difference = max(max_rounded_difference, difference)
        ar6_rate = row["SRCO2EQA"] * 1000 / row["SRNGENAN"]
        rates[code] = {
            "subregion": code, "name": row["SRNAME"], "data_year": 2024,
            "co2e_rate_kg_per_mwh": ar5_rate, "co2e_rate_lb_per_mwh": ar5_rate / .45359237,
            "gen_mwh": row["SRNGENAN"], "co2_metric_tonnes_annual": row["SRCO2AN"],
            "ch4_kg_annual": row["SRCH4AN"], "n2o_kg_annual": row["SRN2OAN"],
            "co2e_ar5_metric_tonnes_annual": ar5_mass_kg / 1000,
            "published_co2e_metric_tonnes_annual": row["SRCO2EQA"],
            "published_ar6_rate_kg_per_mwh": ar6_rate, "published_ar6_rate_lb_per_mwh": ar6_rate / .45359237,
            "published_comparison_rate_kg_per_mwh": published["co2e"],
            "published_comparison_gwp_basis": comparison_gwps[0],
            "published_comparison_gwp_factors": GWP_BASES[comparison_gwps[0]],
            "source_id": SOURCE_ID, "publisher": "Cornerstone Sustainability Data Initiative",
            "source_role": "independent regional comparison", "source_url": file_url(WORKBOOK_NAME),
            "unit": "lb CO2e/MWh", "native_rate_unit": "kg CO2e/MWh", "license": "CC-BY-4.0",
            "accounting_basis": ACCOUNTING_BASIS, "gwp_basis": "IPCC AR5", "gwp_factors": GWP_BASES["IPCC AR5"],
            "gwp_horizon_years": 100,
            "published_annual_mass_gwp_basis": "IPCC AR6", "method_id": METHOD_ID,
            "method": "(CO2 metric tonnes × 1000 + CH4 kg × 28 + N2O kg × 265) / annual net generation MWh",
            "observation_start": "2024-01-01T00:00:00Z", "observation_end": "2025-01-01T00:00:00Z",
            "published_at": "2026-03-17", "geometry_binding_verified": False,
            "geography_note": GEOGRAPHY_NOTE, "can_replace_official_baseline": False,
            "limitations": FACTOR_LIMITATIONS,
        }
    validation = {"status": "validated_with_disclosed_method_difference", "validated_regions": len(rates),
                  "declared_gwp": "IPCC AR5", "comparison_gwp": comparison_gwps[0],
                  "comparison_gwp_factors": GWP_BASES[comparison_gwps[0]],
                  "published_annual_mass_gwp": published_gwp, "selected_comparison_gwp": "IPCC AR5",
                  "resolution": "Recalculate comparison from annual gas masses with explicit AR5 weights; preserve annual-mass AR6 fossil and companion-comparison AR6 non-fossil values separately",
                  "max_rounded_rate_difference_kg_per_mwh": max_rounded_difference, "rounded_rate_tolerance_kg_per_mwh": .067,
                  "geometry_binding_verified": False, "automatic_official_replacement": False}
    return rates, validation


def collect_cornerstone_egrid2024(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    now = _utc(now)
    metadata, files = _record_files(fetcher.get_json(RECORD_API))
    raw = {name: _checked_file(fetcher, name, files[name]) for name in (METADATA_NAME, COMPARISON_NAME, WORKBOOK_NAME)}
    inputs = _input_metadata(raw[METADATA_NAME])
    rates, validation = normalize_cornerstone_2024(raw[WORKBOOK_NAME], raw[COMPARISON_NAME])
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in raw.items()}
    for row in rates.values():
        row.update(workbook_sha256=hashes[WORKBOOK_NAME], source_version=RELEASE_VERSION,
                   code_commit=CODE_COMMIT, validation_status=validation["status"])
    return SourceResult(
        source_id=SOURCE_ID, label="Cornerstone 2024 independent regional comparison", url=RECORD_URL,
        data={"rates": rates, "validation": validation,
              "source": {"publisher": "Cornerstone Sustainability Data Initiative", "version": RELEASE_VERSION,
                         "doi": metadata["doi"], "license": "CC-BY-4.0", "code_license": "MIT", "data_year": 2024,
                         "published_at": metadata["publication_date"], "checked_at": _stamp(now),
                         "record_url": RECORD_URL, "record_api": RECORD_API, "file_sha256": hashes,
                         "workbook_sha256": hashes[WORKBOOK_NAME], "code_commit": CODE_COMMIT, "code_url": CODE_URL,
                         "input_retrieval_metadata": inputs, "accounting_basis": ACCOUNTING_BASIS,
                         "method_id": METHOD_ID, "geography_note": GEOGRAPHY_NOTE}},
        record_count=len(rates), observation_start="2024-01-01T00:00:00Z", observation_end="2025-01-01T00:00:00Z",
        published_at=metadata["publication_date"], data_vintage="2024 data; Cornerstone release 1.0.0, March 17, 2026",
        coverage="27 eGRID regional-code comparisons; no verified 2024 site/utility assignment",
        note="Independent reconstruction from public EPA code, not an EPA-published release. "
             "The description says AR5, annual CO2e mass uses AR6 fossil methane, and the companion comparison uses AR6 non-fossil methane. "
             "WattDocket explicitly recalculates AR5 from annual gas masses and retains both published AR6 variants and the discrepancy. "
             "This source never automatically replaces the official baseline or supplies facility measurements.",
    )
