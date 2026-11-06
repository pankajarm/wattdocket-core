"""Measured ESIF IT power, with bounded and explicitly derived hourly means.

This is a historical data publication, not a live meter API. The selected raw
archive is streamed; no annual energy, whole-building power or carbon is inferred.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import math
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import pymupdf

from .models import Fetcher, SourceResult


PAGE_URL = "https://data.nlr.gov/submissions/300"
ZIP_URL = "https://data.nlr.gov/system/files/300/1757105566-esif.influx.buildingData.PUE.combined.csv.zip"
README_URL = "https://data.nlr.gov/system/files/300/1769711385-README.md"
LICENSE_URL = "https://data.nlr.gov/node/300/license"
METHOD_URL = "https://www.nlr.gov/computational-science/measuring-efficiency-pue"
FACILITY_URL = "https://www.nlr.gov/computational-science/hpc-data-center"
ADDRESS_URL = "https://docs.nlr.gov/docs/fy15osti/63638.pdf"
MEMBER = "esif.influx.buildingData.PUE.combined.csv"
SOURCE_ID = "nrel_esif_power"
SITE_ID = "nlr:esif-hpc-golden-co"
HEADER = ["tags", "ts", "cooling_kw", "energy_reuse", "ere", "hvac_kw", "it_power_kw",
          "plug_and_light_kw", "pue", "pump_kw", "day"]
MONTHS = {month: number for number, month in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
TIMESTAMP = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([A-Z][a-z]{2}) (\d{2}) (\d{4}) "
                       r"(\d{2}):(\d{2}):(\d{2}) GMT(-0[67]00) \(Mountain (Standard|Daylight) Time\)$")
MAX_ARCHIVE_BYTES = 120_000_000
MAX_EXPANDED_BYTES = 650_000_000
MAX_ROWS = 6_000_000
REVIEWED_LICENSE_SHA256 = "90ae26ef438e533cc9d946f6d4cbd399c3d16bd36bd7c51e0f7d03fbc3cb5553"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text(raw: bytes) -> str:
    text = re.sub(r"<[^>]*>", " ", raw.decode("utf-8"))
    return " ".join(html.unescape(text).split())


def _require(text: str, fragments: tuple[str, ...], label: str) -> None:
    if any(fragment not in text for fragment in fragments):
        raise ValueError(f"ESIF {label} evidence changed or is incomplete")


def parse_license(raw: bytes) -> dict:
    """Retain the entire publisher notice, as its permission explicitly requires."""
    text = _text(raw)
    start = text.find("The National Renewable Energy Laboratory (NREL) is operated")
    end_text = "THE ACCESS, USE OR PERFORMANCE OF THE DATA."
    end = text.find(end_text, start)
    if start < 0 or end < 0:
        raise ValueError("ESIF complete license notice is unavailable")
    notice = text[start:end + len(end_text)]
    if _sha(notice.encode()) != REVIEWED_LICENSE_SHA256:
        raise ValueError("ESIF license terms changed; review is required before reuse")
    _require(notice, ("without any fee or cost", "this entire notice appears in all copies",
                      "credit DOE/NREL/ALLIANCE", 'THE DATA ARE PROVIDED BY DOE/NREL/ALLIANCE "AS IS,"'), "license")
    return {"name": "DOE/NREL/Alliance data-use notice", "url": LICENSE_URL,
            "notice": notice, "notice_sha256": _sha(notice.encode()), "source_sha256": _sha(raw),
            "attribution": "Clark, Struan, and Justin Strelka (2025), NLR HPC Facility Power Usage "
                           "Effectiveness (PUE) Data, DOI:10.7799/3015212. Credit: DOE/NREL/ALLIANCE.",
            "requires_full_notice_with_copies": True}


def parse_timestamp(value: str) -> tuple[datetime, str]:
    match = TIMESTAMP.fullmatch(value)
    if not match:
        raise ValueError("ESIF timestamp lacks the reviewed explicit UTC offset and Mountain time label")
    weekday, month, day, year, hour, minute, second, offset, season = match.groups()
    if month not in MONTHS or (offset, season) not in {("-0700", "Standard"), ("-0600", "Daylight")}:
        raise ValueError("ESIF timezone offset and name disagree")
    local = datetime(int(year), MONTHS[month], int(day), int(hour), int(minute), int(second),
                     tzinfo=timezone(timedelta(hours=int(offset[:3]))))
    if local.strftime("%a") != weekday:
        raise ValueError("ESIF timestamp weekday disagrees with date")
    return local.astimezone(timezone.utc), f"GMT{offset} (Mountain {season} Time)"


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_power_archive(raw: bytes) -> dict:
    """Stream the one reviewed CSV, keeping only the latest observed local year.

    Arithmetic means describe the available measured samples. They are neither
    time-weighted demand nor interval energy. Coverage guards do not impute gaps.
    """
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("ESIF source archive exceeds capture bound")
    buckets: dict[datetime, list] = defaultdict(list)
    years: dict[int, dict] = {}
    selected_year = 0
    previous = None
    native_steps: dict[int, int] = defaultdict(int)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        csv_members = [entry for entry in members if entry.filename.endswith(".csv")
                       and not entry.filename.startswith("__MACOSX/")]
        if len(members) > 4 or len(csv_members) != 1 or csv_members[0].filename != MEMBER:
            raise ValueError("ESIF archive member schema changed")
        if sum(entry.file_size for entry in members) > MAX_EXPANDED_BYTES or any(entry.flag_bits & 1 for entry in members):
            raise ValueError("ESIF archive exceeds expanded-size bound or is encrypted")
        with archive.open(MEMBER) as stream:
            reader = csv.reader(io.TextIOWrapper(stream, encoding="utf-8", newline=""))
            if next(reader, None) != HEADER:
                raise ValueError("ESIF power CSV fields or units changed")
            for source_row, row in enumerate(reader, 2):
                if source_row > MAX_ROWS + 1 or len(row) != len(HEADER):
                    raise ValueError("ESIF CSV row limit or column count failed")
                native = row[1]
                match = TIMESTAMP.fullmatch(native)
                if not match:
                    raise ValueError("ESIF source timestamp schema changed")
                year = int(match.group(4))
                if year < selected_year:
                    raise ValueError("ESIF source years are not ordered")
                info = years.setdefault(year, {"rows": 0, "numeric_it_samples": 0, "duplicate_timestamp_rows": 0,
                                               "first_source_timestamp": native, "last_source_timestamp": native})
                info["rows"] += 1
                info["last_source_timestamp"] = native
                value = None
                if row[6] != "":
                    value = float(row[6])
                    if not math.isfinite(value) or value < 0 or value > 100_000:
                        raise ValueError("ESIF IT power must be finite nonnegative kW within the reviewed range")
                    info["numeric_it_samples"] += 1
                if year > selected_year:
                    selected_year = year
                    buckets.clear()
                    native_steps.clear()
                    previous = None
                utc, source_zone = parse_timestamp(native)
                if previous is not None:
                    delta = int((utc - previous).total_seconds())
                    if delta < 0:
                        raise ValueError("ESIF reversed UTC timestamps")
                    if delta == 0:
                        info["duplicate_timestamp_rows"] += 1
                    else:
                        native_steps[delta] += 1
                previous = utc
                hour = utc.replace(minute=0, second=0, microsecond=0)
                buckets[hour].append((utc, value, source_row, native, source_zone))
    if not buckets or not years[selected_year]["numeric_it_samples"]:
        raise ValueError("ESIF latest observed year has no numeric IT meter samples")
    first, last = min(buckets), max(buckets)
    if last - first > timedelta(days=367):
        raise ValueError("ESIF selected year spans too many UTC hours")
    readings = []
    excluded_reasons: dict[str, int] = defaultdict(int)
    hour = first
    while hour <= last:
        rows = buckets.get(hour, [])
        timestamp_counts = Counter(row[0] for row in rows)
        duplicated = any(count > 1 for count in timestamp_counts.values())
        # Duplicate source timestamps can contain conflicting readings. Never
        # select one, average the conflict, or count it as extra sample coverage.
        samples = [row for row in rows if row[1] is not None and timestamp_counts[row[0]] == 1]
        edges = [hour] + [row[0] for row in samples] + [hour + timedelta(hours=1)]
        max_gap = int(max((right - left).total_seconds() for left, right in zip(edges, edges[1:])))
        count = len(samples)
        sample_sum = math.fsum(row[1] for row in samples)
        enough = count >= 45 and max_gap <= 300 and not duplicated
        if not enough:
            reason = "duplicate_publisher_timestamp" if duplicated else (
                "fewer_than_45_numeric_samples" if count < 45 else "sample_gap_exceeds_300_seconds")
            excluded_reasons[reason] += 1
        mean = sample_sum / count if enough else None
        readings.append([_stamp(hour), mean,
                         f"{MEMBER}:rows {rows[0][2]}-{rows[-1][2]}" if rows else None,
                         f"{rows[0][3]} | {rows[-1][3]}" if rows else None,
                         " | ".join(sorted({row[4] for row in rows})) if rows else None,
                         count, max_gap, sample_sum,
                         sum(count > 1 for count in timestamp_counts.values())])
        hour += timedelta(hours=1)
    missing = sum(row[1] is None for row in readings)
    if missing == len(readings):
        raise ValueError("ESIF latest year has no sufficiently covered hourly bins")
    mode_step = max(native_steps, key=native_steps.get) if native_steps else None
    return {"readings": readings, "selected_year": selected_year, "missing_count": missing,
            "observed_start": readings[0][0], "observed_end": readings[-1][0],
            "native_observation_start": _stamp(buckets[first][0][0]),
            "native_observation_end": _stamp(buckets[last][-1][0]),
            "available_years": [{"year": year, **info} for year, info in sorted(years.items())],
            "native_sample_count": years[selected_year]["rows"],
            "native_numeric_sample_count": years[selected_year]["numeric_it_samples"],
            "native_sampling_mode_seconds": mode_step, "native_sampling_irregular": True,
            "withheld_hour_reasons": dict(excluded_reasons)}


def collect_esif_power(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    page_raw = fetcher.get_bytes(PAGE_URL)
    page = _text(page_raw)
    _require(page, ("HPC Facility Power Usage Effectiveness", "10.7799/3015212", "Status Public"), "catalog")
    if ZIP_URL not in page_raw.decode("utf-8"):
        raise ValueError("Reviewed ESIF source archive is no longer linked by its publisher")
    update = re.search(r"Last Updated (\d{2}/\d{2}/\d{4})", page)
    if not update:
        raise ValueError("ESIF publication update date is unavailable")
    published = datetime.strptime(update.group(1), "%m/%d/%Y").replace(tzinfo=timezone.utc)
    if published > now:
        raise ValueError("ESIF catalog update is in the future")
    readme_raw = fetcher.get_bytes(README_URL)
    _require(readme_raw.decode("utf-8"), ("it_power_kw", "IT equipment (kilowatts)",
                                         "IT equipment on the data center floor"), "units and meter boundary")
    method_raw = fetcher.get_bytes(METHOD_URL)
    _require(_text(method_raw), ("All of the above are measured with electrical power meters except for:",
                                "Generator heat", "tower filter pump", "IT equipment"), "measurement method")
    facility_raw = fetcher.get_bytes(FACILITY_URL)
    _require(_text(facility_raw), ("High-Performance Computing", "Located in the Energy Systems Integration Facility (ESIF)"), "physical facility")
    address_raw = fetcher.get_bytes(ADDRESS_URL)
    with pymupdf.open(stream=address_raw, filetype="pdf") as pdf:
        matches = [(index + 1, " ".join(page.get_text().split())) for index, page in enumerate(pdf)
                   if "15257 Denver West Parkway, Golden, CO 80401" in " ".join(page.get_text().split())]
    if len(matches) != 1 or "Energy Systems Integration Facility" not in matches[0][1]:
        raise ValueError("ESIF street-address evidence no longer resolves uniquely")
    license_info = parse_license(fetcher.get_bytes(LICENSE_URL))
    raw = fetcher.get_bytes(ZIP_URL)
    parsed = parse_power_archive(raw)
    if datetime.fromisoformat(parsed["native_observation_end"].replace("Z", "+00:00")) > now:
        raise ValueError("ESIF meter observations are in the future")
    series = {"series_id": "nlr-esif-it-power-hourly", "site_id": SITE_ID,
              "label": "ESIF IT power — hourly means of measured samples",
              "metric": "electricity_power", "unit": "kW", "interval_minutes": 60,
              "measurement_basis": "derived_hourly_arithmetic_mean_of_metered_samples",
              "measurement_boundary": "IT equipment on the ESIF data center floor; excludes cooling and other building loads",
              "whole_facility": False,
              "time_basis": "UTC hour bins derived from each publisher timestamp's explicit GMT offset; no inferred DST offsets",
              "columns": ["timestamp_utc", "value", "source_row", "source_timestamp", "source_timezone",
                          "sample_count", "max_gap_seconds", "sample_sum_kw", "duplicate_timestamp_count"],
              **parsed, "source_url": ZIP_URL, "source_sha256": _sha(raw), "source_locator": MEMBER + " / it_power_kw",
              "license": license_info, "historical_snapshot": True,
              "methodology_note": "Irregular approximately minute-spaced electrical meter samples are grouped into UTC hours. "
                                  "The arithmetic mean is published only with at least 45 numeric samples and no gap greater "
                                  "than 300 seconds, including both hour edges. Hours with duplicate publisher timestamps "
                                  "are withheld; conflicting readings are not resolved. Other incomplete hours also remain null. No filling, "
                                  "time-weighting, energy integration or whole-facility total is performed. These historical "
                                  "IT equipment readings do not include cooling, generator-heat allocations or tower-pump allocations.",
              "aggregation": {"method": "arithmetic_mean", "minimum_numeric_samples": 45,
                              "maximum_gap_including_edges_seconds": 300, "missing_values_imputed": False,
                              "annual_energy_derivable": False,
                              "sample_count_basis": "numeric samples whose publisher timestamp occurs exactly once",
                              "sample_sum_kw_is_energy": False,
                              "duplicate_timestamp_policy": "withhold the entire hour; exclude all conflicting timestamps from sample count and sum"},
              "method_evidence": [{"url": README_URL, "sha256": _sha(readme_raw)},
                                  {"url": METHOD_URL, "sha256": _sha(method_raw)}]}
    site = {"site_id": SITE_ID, "name": "ESIF High-Performance Computing Data Center",
            "operator": "National Laboratory of the Rockies (formerly NREL)",
            "address": {"street": "15257 Denver West Parkway", "city": "Golden", "state": "CO", "country": "US"},
            "reporting_boundary": "IT equipment on the data center floor within ESIF; not all ESIF research laboratories or the campus",
            "inventory_record_ids": [],
            "identity_evidence": [{"url": ADDRESS_URL, "sha256": _sha(address_raw),
                                   "locator": f"PDF page {matches[0][0]}, Appendix B (printed page 21)",
                                   "quote": "Energy Systems Integration Facility National Renewable Energy Laboratory "
                                            "15257 Denver West Parkway, Golden, CO 80401"},
                                  {"url": FACILITY_URL, "sha256": _sha(facility_raw),
                                   "locator": "High-Performance Computing Data Center introduction",
                                   "quote": "Located in the Energy Systems Integration Facility (ESIF)"}]}
    return SourceResult(SOURCE_ID, "NLR ESIF measured IT power (historical; derived hourly means)", PAGE_URL,
                        {"power_sites": [site], "power_series": [series],
                         "excluded": [{"fields": ["cooling_kw", "hvac_kw", "plug_and_light_kw", "pump_kw", "pue", "ere", "energy_reuse"],
                                       "reason": "Only the explicitly metered IT boundary is selected; no whole-building total or allocated support loads are inferred."}],
                         "source": {"publisher": site["operator"], "doi": "10.7799/3015212", "catalog_sha256": _sha(page_raw),
                                    "catalog_last_updated": published.date().isoformat(), "raw_archive_bytes": len(raw),
                                    "refresh_policy": "monthly metadata check; historical snapshot, no live endpoint claim"}},
                        len(series["readings"]) - series["missing_count"],
                        parsed["native_observation_start"], parsed["native_observation_end"],
                        published.date().isoformat(), str(parsed["selected_year"]),
                        "One physically identified ESIF data center IT equipment boundary in Golden, Colorado",
                        "Historical measured samples; hourly arithmetic means withheld where sample coverage is inadequate. "
                        "Not a live feed, whole-facility meter, or annual energy observation.")
