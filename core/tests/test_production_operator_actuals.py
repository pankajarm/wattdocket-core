"""Annual campus reports preserve scope, time, units and accounting methods."""
import hashlib
from datetime import datetime, timezone

import pymupdf
import pytest

from wattdocket.production import sources_operator_actuals as actuals


METHODS = (
    "rounded to the nearest whole digit\nOwned, online data centers are always reported by site\n"
    "operational control approach\nactivity data is incomplete or unavailable\n"
    "water utilities or meter data\nestimate water withdrawal and consumption\n"
    "one EAC for every MWh\n1,019 mega liters"
)


def table_text(kind, *, replacements=None):
    titles = {
        "electricity": ("Electricity Consumption by Facility (in MWh)", "Total electricity consumption", [100], [100]),
        "water": ("Water Withdrawal by Facility (in megaliters)", "Total water withdrawal", [10], [10]),
        "operational_emissions": ("Market-Based Scope 1 and 2 Emissions (in metric tons CO2e)", "Total operational GHG emissions", [5], [5]),
        "scope2": ("Scope 2 Emissions (in metric tons CO2e)", "Total facilities GHG emissions", [0, 50], [1, 50]),
    }
    title, overall, campus, offices = titles[kind]
    years = "2020 2021 2022 2023 2024"
    lines = ["2025 Environmental Data Index", title, years]
    if kind == "scope2":
        lines.append("Market-based Location-based " * 5)
    dc = [value * 18 for value in campus]
    rows = [(overall, [x + y for x, y in zip(dc, offices)]), ("Data centers total", dc)]
    rows += [(label, campus) for label in actuals.LOCATION_IDS]
    rows += [("Leased data center facilities", [0] * len(campus)),
             ("Other data center-related facilities", [0] * len(campus)), ("Offices total", offices)]
    for label, latest in rows:
        # Historical values deliberately differ; latest paired columns must win.
        values = ["1", "2", "3", "4"] if kind != "scope2" else ["7", "80"] * 4
        values += [str(value) for value in latest]
        lines.append(label + " " + " ".join(values))
        if kind == "electricity" and label == overall:
            lines.append("Electricity from grid (%) >99% 100% 100% 100% 100%")
    if kind == "scope2":
        footer = title + " " + " ".join(f"{year} {basis}" for year in range(2020, 2025)
                                           for basis in ("Market-based", "Location-based"))
        lines.append(footer[:-1])
    else:
        lines += [title, years]
    if kind == "water":
        lines.append("Not included in our 2024 water withdrawal numbers are an additional 1,019 mega liters of water withdrawn for the construction of Meta data centers.")
    text = "\n".join(lines)
    for before, after in (replacements or {}).items():
        text = text.replace(before, after)
    return text


def pdf_bytes(*, methods=METHODS, pages=19, cover_year=2024, tables=None):
    document = pymupdf.open()
    for _ in range(pages):
        document.new_page(width=2000, height=2000)
    document[0].insert_text((36, 36), f"Reporting on fiscal year {cover_year}")
    for kind, spec in actuals.TABLES.items():
        document[spec["page"] - 1].insert_text((36, 36), (tables or {}).get(kind, table_text(kind)), fontsize=6)
    document[12].insert_text((36, 36), methods, fontsize=6)
    document.set_metadata({"creationDate": "D:20250919135644-04'00'", "modDate": "D:20251017102648-04'00'"})
    raw = document.tobytes()
    document.close()
    return raw


class Fetcher:
    def __init__(self, raw):
        self.values = {actuals.DISCOVERY_URL: ('<a href="' + actuals.PDF_URL + '">2025 index</a>').encode(), actuals.PDF_URL: raw}

    def get_bytes(self, url):
        return self.values[url]


def normalize(monkeypatch, **kwargs):
    raw = pdf_bytes(**kwargs)
    monkeypatch.setattr(actuals, "PDF_SHA256", hashlib.sha256(raw).hexdigest())
    return actuals.normalize_meta_actuals(raw)


def test_extracts_distinct_year_units_scope_and_market_location_columns(monkeypatch):
    raw = pdf_bytes()
    monkeypatch.setattr(actuals, "PDF_SHA256", hashlib.sha256(raw).hexdigest())
    result = actuals.collect_meta_actuals(Fetcher(raw), now=datetime(2026, 11, 3, tzinfo=timezone.utc))
    assert result.source_id == "meta_actuals2024" and result.record_count == 90
    assert result.published_at is None
    assert result.observation_start == "2024-01-01T00:00:00Z"
    assert result.observation_end == "2025-01-01T00:00:00Z"
    data = result.data
    assert len(data["locations"]) == 18 and len(data["aggregates"]) == 25
    rows = {r["metric"]: r for r in data["observations"] if r["disclosure_location_id"] == "meta:altoona-ia"}
    assert {metric: (row["value"], row["unit"]) for metric, row in rows.items()} == {
        "electricity_consumption": (100, "MWh"), "water_withdrawal": (10, "million liters"),
        "emissions_scope1_and2_market": (5, "metric tonnes CO2e"),
        "emissions_scope2_market": (0, "metric tonnes CO2e"),
        "emissions_scope2_location": (50, "metric tonnes CO2e"),
    }
    assert "emissions_scope1" not in rows and "water_consumption" not in rows
    assert all(row["reporting_scope"] == "campus" and row["reporting_basis"] == "operator_reported"
               and not row["facility_binding_verified"] and not row["is_meter_observation"]
               and row["data_year"] == 2024 for row in data["observations"])
    assert all(row["reporting_scope"] == "operator_aggregate" and row["disclosure_location_id"] is None for row in data["aggregates"])
    assert rows["electricity_consumption"]["source_page"] == 6
    assert rows["water_withdrawal"]["source_native_unit"] == "megaliters"
    assert rows["emissions_scope2_market"]["source_locator"]["subcolumn"] == "Market-based"
    assert rows["emissions_scope2_location"]["source_locator"]["subcolumn"] == "Location-based"
    assert rows["emissions_scope2_location"]["gwp_basis"] is None
    assert "may contain multiple buildings" in data["source"]["scope_note"]
    assert data["source"]["checked_at"] == "2026-11-03T00:00:00Z"
    assert data["source"]["pdf_sha256"] == hashlib.sha256(raw).hexdigest()
    assert data["source"]["pdf_metadata_date_is_publication_date"] is False
    assert data["source"]["published_at"] is None
    assert data["validation"]["facility_bindings"] == 0


@pytest.mark.parametrize("problem", ["missing", "qualified", "negative", "nonfinite", "duplicate", "extra", "units", "year", "report_year", "column_order"])
def test_missing_values_changed_schema_or_unparsed_locations_fail_closed(problem):
    kind = "scope2" if problem == "column_order" else "electricity"
    text = table_text(kind)
    original = "Altoona (IA) 1 2 3 4 100"
    if problem in ("missing", "qualified", "negative", "nonfinite"):
        replacement = {"missing": "-", "qualified": "<500", "negative": "-100", "nonfinite": "NaN"}[problem]
        text = text.replace(original, "Altoona (IA) 1 2 3 4 " + replacement)
    elif problem == "duplicate":
        text = text.replace("Clonee (Ireland)", "Altoona (IA)")
    elif problem == "extra":
        text = text.replace("Leased data center facilities", "Unreviewed campus 1 2 3 4 100\nLeased data center facilities", 1)
    elif problem == "units":
        text = text.replace("(in MWh)", "(in MW)", 1)
    elif problem == "year":
        text = text.replace("2020 2021 2022 2023 2024", "2021 2022 2023 2024 2025", 1)
    elif problem == "report_year":
        text = text.replace("2025 Environmental", "2026 Environmental", 1)
    else:
        text = text.replace("Market-based Location-based", "Location-based Market-based", 1)
    with pytest.raises(actuals.OperatorDisclosureError):
        actuals.parse_meta_table(text, kind)


def test_historical_missing_is_allowed_but_latest_zero_is_explicit():
    text = table_text("electricity").replace("Altoona (IA) 1 2 3 4 100", "Altoona (IA) - <500 3 4 0")
    assert actuals.parse_meta_table(text, "electricity")["Altoona (IA)"] == [0]


@pytest.mark.parametrize("mutate", ["data_centers", "all_total", "combined_below_scope2"])
def test_semantically_inconsistent_operator_totals_are_rejected(monkeypatch, mutate):
    tables = {}
    if mutate == "data_centers":
        tables["electricity"] = table_text("electricity").replace("Data centers total 1 2 3 4 1800", "Data centers total 1 2 3 4 1820").replace("Total electricity consumption 1 2 3 4 1900", "Total electricity consumption 1 2 3 4 1920")
    elif mutate == "all_total":
        tables["water"] = table_text("water").replace("Total water withdrawal 1 2 3 4 190", "Total water withdrawal 1 2 3 4 195")
    else:
        # Keep aggregate arithmetic internally consistent, but make the combined
        # Scope1+2-MB less than Scope2-MB for one campus.
        tables["scope2"] = table_text("scope2").replace("Altoona (IA) 7 80 7 80 7 80 7 80 0 50", "Altoona (IA) 7 80 7 80 7 80 7 80 10 50").replace("Data centers total 7 80 7 80 7 80 7 80 0 900", "Data centers total 7 80 7 80 7 80 7 80 10 900").replace("Total facilities GHG emissions 7 80 7 80 7 80 7 80 1 950", "Total facilities GHG emissions 7 80 7 80 7 80 7 80 11 950")
    with pytest.raises(actuals.OperatorDisclosureError):
        normalize(monkeypatch, tables=tables)


def test_publisher_rounding_is_allowed_without_rewriting_observations(monkeypatch):
    table = table_text("electricity").replace("Data centers total 1 2 3 4 1800", "Data centers total 1 2 3 4 1802").replace("Total electricity consumption 1 2 3 4 1900", "Total electricity consumption 1 2 3 4 1902")
    data = normalize(monkeypatch, tables={"electricity": table})
    check = data["validation"]["aggregate_reconciliation"][0]
    assert check["difference"] == 2 and check["published_data_center_total"] == 1802
    assert data["observations"][0]["value"] == 100


@pytest.mark.parametrize("problem", ["checksum", "new_edition", "untrusted_domain", "method", "pages", "cover"])
def test_unreviewed_report_revision_or_method_cannot_be_promoted(monkeypatch, problem):
    raw = pdf_bytes(methods="Unreviewed methodology" if problem == "method" else METHODS,
                    pages=20 if problem == "pages" else 19, cover_year=2025 if problem == "cover" else 2024)
    monkeypatch.setattr(actuals, "PDF_SHA256", hashlib.sha256(raw).hexdigest())
    fetcher = Fetcher(raw)
    if problem == "checksum":
        fetcher.values[actuals.PDF_URL] += b"revision"
    elif problem == "new_edition":
        fetcher.values[actuals.DISCOVERY_URL] += b'<a href="https://sustainability.atmeta.com/asset/2026-environmental-data-index/">new</a>'
    elif problem == "untrusted_domain":
        fetcher.values[actuals.DISCOVERY_URL] = b'<a href="https://example.org/asset/2025-environmental-data-index/">index</a>'
    with pytest.raises(actuals.OperatorDisclosureError):
        actuals.collect_meta_actuals(fetcher, now=datetime(2026, 11, 3, tzinfo=timezone.utc))
