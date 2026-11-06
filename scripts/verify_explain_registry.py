"""Independently reconcile explanation inputs with the same public release.

No ingestion or registry-builder imports. This checks typed values and boundaries,
not the full semantics of generated language; model responses have separate QA.
"""
import hashlib
import math
from collections import Counter
from urllib.parse import urlsplit


def validate_explain_registry(objects, raw_objects, manifest, checks):
    registry = objects.get("explain-registry.json", {})
    raw_hashes = {path: hashlib.sha256(raw).hexdigest() for path, raw in raw_objects.items()}
    release = manifest["release_id"]
    checks.require(registry.get("schema_version") == "1" and registry.get("release_id") == release
                   and registry.get("built_at") == manifest["built_at"], "Explain registry release/schema mismatch")
    subjects = registry.get("subjects", {})
    cards = {c["facility"]["id"]: c for c in objects["cards.json"].values()}
    locations = {r["disclosure_location_id"]: r for r in objects.get("facility-observations.json", {}).get("locations", [])}
    power = objects.get("facility-power.json", {})
    sites = {r["site_id"]: r for r in power.get("sites", [])}
    permit_rows = objects.get("data-status.json", {}).get("permit_recovery", {}).get("documents", [])
    permits = {row.get("registration"): row for row in permit_rows}
    checks.require(len(permits) == len(permit_rows) and all(isinstance(reg, str) and reg for reg in permits), "Duplicate or missing permit document identity")
    expected = ({"facility:" + ident for ident in cards} | {"disclosure_location:" + ident for ident in locations}
                | {"power_site:" + ident for ident in sites} | {"permit_document:" + ident for ident in permits if isinstance(ident, str)})
    checks.require(set(subjects) == expected, "Explain subject coverage differs from published identities")
    counts = Counter()
    total = 0
    for key, subject in subjects.items():
        kind, ident = subject.get("kind"), subject.get("id")
        label = "Explain " + key + ": "
        counts[kind] += 1
        checks.require(key == str(kind) + ":" + str(ident), label + "subject identity mismatch")
        facts = subject.get("facts", [])
        total += len(facts)
        checks.require(1 <= len(facts) <= 12 and len({f["id"] for f in facts}) == len(facts), label + "invalid fact count/identity")
        by_id = {f["id"]: f for f in facts}
        fallback = subject.get("fallback", {})
        selected = fallback.get("fact_ids", [])
        checks.require(1 <= len(selected) <= 5 and len(set(selected)) == len(selected)
                       and all(x in by_id for x in selected)
                       and fallback.get("text") == " ".join(by_id[x]["text"] for x in selected if x in by_id), label + "fallback differs from trusted facts")
        card = cards.get(ident) if kind == "facility" else None
        permit_document = permits.get(ident) if kind == "permit_document" else None
        if kind == "permit_document":
            checks.require(permit_document is not None and any(f.get("kind") == "permit_document_review" for f in facts), label + "missing standalone document review")
            checks.require(all(f.get("kind") in {"permit_condition", "permit_document_review", "equipment_nameplate", "regulator_site_identity", "permit_history_review"} for f in facts), label + "unrelated facts assigned to permit document")
        for fact in facts:
            text = fact.get("text", "")
            qualifiers = fact.get("required_qualifiers", [])
            checks.require(bool(text) and len(text) <= 1200 and all(q.casefold() in text.casefold() for q in qualifiers), label + "qualifier omitted from fact")
            evidence = fact.get("evidence", [])
            checks.require(bool(evidence), label + "uncited fact")
            for item in evidence:
                url = urlsplit(item.get("url", ""))
                checks.require(url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
                               and bool(item.get("locator")), label + "invalid evidence URL/locator")
                prefix = "/data/releases/" + release + "/"
                if url.hostname == "wattdocket.com" and url.path.startswith("/data/"):
                    path = url.path[len(prefix):] if url.path.startswith(prefix) else ""
                    checks.require(path in raw_hashes and raw_hashes[path] == item.get("sha256"), label + "source-object hash or release mismatch")
            numbers = fact.get("numbers", [])
            checks.require(all(type(n.get("value")) in {int, float} and math.isfinite(n["value"])
                               and isinstance(n.get("unit"), str) and n.get("display") in text for n in numbers), label + "invalid numeric metadata/display")
            values = {n.get("label"): n for n in numbers}
            category = fact.get("kind")
            period = fact.get("period") or {}
            selector = fact.get("source_selector") or {}
            if category not in {"data_gap", "identity_boundary"}:
                expected_path = ("data-status.json" if kind == "permit_document" else "facility-observations.json" if "annual_disclosure" in category else "facility-power.json" if category in {"reported_peak_demand", "historical_power_series", "series_completeness"} else "cards.json")
                checks.require(selector.get("path") == expected_path, label + "source selector path mismatch")
                if expected_path == "cards.json": checks.require(selector.get("facility_id") == ident, label + "source selector facility mismatch")
                if expected_path == "facility-power.json": checks.require(selector.get("site_id") == ident, label + "source selector site mismatch")
                if expected_path == "data-status.json":
                    checks.require(selector.get("registration") == ident and "facility_id" not in selector, label + "source selector permit identity mismatch")

            def require_words(*phrases):
                checks.require(all(p.casefold() in text.casefold() for p in phrases), label + "missing boundary wording: " + ", ".join(phrases))

            def cited(row):
                return any(e.get("url") == row.get("source_url") and e.get("sha256") == row.get("source_sha256") for e in evidence)

            if category in {"annual_disclosure", "linked_annual_disclosure"}:
                lid = ident if kind == "disclosure_location" else str(fact.get("related_subject", "")).removeprefix("disclosure_location:")
                location = locations.get(lid, {})
                matches = [o for o in location.get("observations", []) if o.get("observation_id") == selector.get("observation_id") and lid == selector.get("disclosure_location_id") and o.get("metric") in values
                           and values[o["metric"]]["value"] == o.get("value") and values[o["metric"]]["unit"] == o.get("unit") and cited(o)]
                checks.require(len(matches) == 1, label + "annual quantity/source does not identify one observation")
                if matches:
                    row = matches[0]
                    checks.require(period.get("year") == row["data_year"] and period.get("start") == row["period_start"] and period.get("end") == row["period_end"]
                                   and fact.get("scope") == row["reporting_scope"] and row.get("inventory_allocation") is False, label + "annual year/boundary mismatch")
                    require_words(str(row["data_year"]))
                    if row["metric"] == "electricity_consumption": require_words("not peak demand")
                    if row["reporting_scope"] == "building": require_words("whole-property", "not data-center-only")
                    if row["metric"] == "reported_ghg_total":
                        if location.get("source_id") == "ghgrp": require_words("Subpart C", "excluding electricity Scope 2")
                        else: require_words("publisher-calculated")
                if category == "linked_annual_disclosure":
                    links = [d.get("link", {}) for d in (card or {}).get("disclosures", [])]
                    checks.require(any(l.get("disclosure_location_id") == lid and l.get("record_id") == ident
                                       and l.get("verification_status") == "verified" and l.get("relationship") == "within_disclosure_location"
                                       and l.get("inventory_allocation") is False and l in location.get("record_links", []) for l in links), label + "unverified inventory/disclosure join")
                    require_words("Related reporting location", "not allocated to this inventory record")
            elif category == "permit_condition":
                checks.require(kind in {"facility", "permit_document"} and fact.get("scope") == "quoted_condition", label + "permit scope mismatch")
                limits = (permit_document or {}).get("conditions", []) if kind == "permit_document" else (card or {}).get("facility", {}).get("permit_limits", [])
                matches = [p for p in limits if p.get("registration") == selector.get("registration") and p.get("metric") == selector.get("metric") and p.get("content_sha256") == selector.get("content_sha256") and p.get("metric") in values
                           and p.get("value") == values[p["metric"]]["value"] and p.get("unit") == values[p["metric"]]["unit"]
                           and any(e.get("sha256") == p.get("content_sha256") and e.get("quote") == p.get("quote") and e.get("url") == p.get("url") for e in evidence)]
                checks.require(bool(matches) and all(p.get("is_document_condition") is True and p.get("applies_to_entire_site") is False for p in matches), label + "unverified permit quantity")
                checks.require(period.get("current_effectiveness_verified") is False, label + "claims current legal effectiveness")
                require_words("document condition", "not actual use", "current effectiveness is unknown", "only the equipment")
                for match in matches:
                    checks.require(period.get("issued_at") == match.get("issued_at"), label + "permit issue date mismatch")
                    for field in ("reviewed_condition_scope", "reviewed_operating_purpose", "reviewed_period"):
                        if isinstance(match.get(field), str) and match[field]:
                            require_words(match[field].rstrip("."))
                    if kind == "permit_document":
                        checks.require(match.get("registration") == ident and match.get("content_sha256") == permit_document.get("content_sha256"), label + "condition belongs to another document")
                    review = match.get("recovery_review") or {}
                    if review.get("document_extent") == "partial_amendment": require_words("partial amendment", "not a complete permit")
                    if match.get("representation") == "reviewed_pdf_transcription": require_words("visually checked transcription")
            elif category == "permit_document_review":
                checks.require(kind in {"facility", "permit_document"} and fact.get("scope") == "document_review", label + "document review scope mismatch")
                candidates = [permit_document] if permit_document else (card or {}).get("facility", {}).get("permit_reviews", [])
                matches = [p for p in candidates
                           if p.get("registration") == selector.get("registration")
                           and p.get("content_sha256") == selector.get("content_sha256")]
                checks.require(len(matches) == 1 and not numbers, label + "review does not identify one published document")
                if matches:
                    row = matches[0]
                    checks.require(period.get("issued_at") == row.get("issued_at"), label + "review issue date mismatch")
                    review = row.get("recovery_review", {})
                    checks.require(review.get("status") == "verified" and review.get("document_sha256") == row.get("content_sha256")
                                   and any(e.get("sha256") == row.get("content_sha256") and e.get("url") == row.get("url") for e in evidence),
                                   label + "unverified recovered document")
                    if review.get("document_extent") == "partial_amendment": require_words("partial amendment", "not a complete permit")
                    if review.get("document_extent") in {"correspondence", "permit_letter"}: require_words("permit correspondence", "not a complete permit")
                checks.require(period.get("current_effectiveness_verified") is False, label + "claims current legal effectiveness")
                require_words("current effectiveness is unknown")
                if kind == "permit_document":
                    require_words("separate permit document", "no facility measurement or inventory allocation is inferred")
            elif category in {"regulator_site_identity", "permit_history_review"}:
                checks.require(kind in {"facility", "permit_document"} and not numbers, label + "context promoted to numeric measurement")
                candidates = [permit_document] if permit_document else (card or {}).get("facility", {}).get("permit_reviews", [])
                matches = [p for p in candidates if p.get("registration") == selector.get("registration")
                           and p.get("content_sha256") == selector.get("content_sha256")]
                checks.require(len(matches) == 1, label + "context does not identify one reviewed document")
                if matches:
                    row = matches[0]
                    if category == "regulator_site_identity":
                        site = row.get("site_identity_review") or {}
                        checks.require(selector.get("field") == "site_identity_review" and fact.get("scope") == "regulatory_air_site"
                                       and site.get("status") == "regulator_site_verified"
                                       and evidence == site.get("evidence"), label + "site identity evidence differs from public review")
                        address = site.get("physical_address") or {}
                        require_words(*(str(address[k]) for k in ("street", "city", "state", "postal_code") if address.get(k)))
                        operating = (site.get("site_status") or {}).get("air_operating_status")
                        if operating: require_words(operating)
                        if site.get("retrieved_at"): require_words(site["retrieved_at"][:10])
                        if site.get("source_freshness") != "current": require_words("last-known site record; refresh delayed")
                        require_words("regulatory-site identity", "not permit legal effectiveness", "not a whole-campus or meter boundary")
                    else:
                        history = row.get("permit_history_review") or {}
                        checks.require(selector.get("field") == "permit_history_review" and fact.get("scope") == "document_history"
                                       and history.get("legal_effectiveness", {}).get("status") == "unknown", label + "history legal conclusion differs from published review")
                        expected = [e for event in history.get("events", []) for e in event.get("evidence", [])]
                        if expected:
                            checks.require(evidence == expected, label + "history citations differ from reviewed events")
                        else:
                            receipt = history.get("registry_listing", {}).get("receipt") or {}
                            checks.require(len(evidence) == 1 and evidence[0].get("url") == receipt.get("url")
                                           and evidence[0].get("sha256") == receipt.get("sha256"), label + "history fallback lacks exact index receipt")
                        require_words("Current legal effectiveness is unknown")
                        for phrase in ("not final issuance", "pending", "not an issued replacement"):
                            if phrase in history.get("note", ""): require_words(phrase)
                        if history.get("status") != "verified": require_words("requires a new review")
            elif category == "equipment_nameplate":
                checks.require(kind in {"facility", "permit_document"} and fact.get("scope") == "equipment_table_page", label + "equipment scope mismatch")
                candidates = (permit_document or {}).get("equipment_claims", []) if kind == "permit_document" else (card or {}).get("claims", [])
                claims = [c for c in candidates if c.get("id") == selector.get("claim_id", "diesel-mw")]
                checks.require(len(claims) == 1, label + "equipment selector does not identify one claim")
                claim = claims[0] if claims else {}
                n = values.get("generator table capacity", {})
                checks.require(n.get("value") == claim.get("value") and n.get("unit") == claim.get("unit") == "MW nameplate"
                               and claim.get("evidence_scope") == "equipment_table_page" and claim.get("is_complete_fleet") is False
                               and bool(evidence) and all(any(e.get("sha256") == c.get("doc_sha256") and e.get("quote") == c.get("quote") and e.get("url") == c.get("url") for c in claim.get("citations", [])) for e in evidence), label + "equipment quantity/source mismatch")
                if kind == "permit_document":
                    checks.require(selector.get("claim_id") == "diesel-mw" and claim.get("permit_metadata", {}).get("registration") == ident
                                   and all(e.get("sha256") == permit_document.get("content_sha256") for e in evidence), label + "equipment belongs to another permit")
                require_words("not a whole-site total", "current effectiveness is unknown")
            elif category == "scenario":
                claim = next((c for c in (card or {}).get("claims", []) if c.get("id") == "scenario-carbon"), {})
                inputs = claim.get("inputs", {})
                factor_path = (fact.get("source_selector") or {}).get("factor_path")
                factor = objects.get(factor_path, {})
                checks.require(values.get("scenario emissions", {}).get("value") == claim.get("value")
                               and period.get("year") == inputs.get("factor_year")
                               and claim.get("scope") == "scenario" and inputs.get("facility_measurement") is False, label + "scenario differs from card")
                checks.require(values.get("hypothetical grid-side load", {}).get("value") == inputs.get("load_mw")
                               and values.get("chosen utilization", {}).get("value") == inputs.get("utilization", {}).get("base", -1) * 100
                               and values.get("hypothetical annual electricity", {}).get("value") == inputs.get("load_mw", -1) * inputs.get("utilization", {}).get("base", -1) * inputs.get("hours_per_year", -1)
                               and values.get("historical regional factor", {}).get("value") == factor.get("co2e_rate_lb_per_mwh")
                               and values.get("factor year", {}).get("value") == factor.get("data_year"), label + "scenario inputs/factor differ from receipt")
                require_words("hypothetical", "not a facility measurement", str(period.get("year")))
            elif category == "reported_peak_demand":
                matches = [d for d in power.get("demands", []) if d.get("site_id") == ident and d.get("demand_id") == selector.get("demand_id") and cited(d)
                           and d.get("value") == values.get("reported peak demand", {}).get("value")
                           and d.get("unit") == values.get("reported peak demand", {}).get("unit")]
                checks.require(kind == "power_site" and len(matches) == 1, label + "demand subject/source/value mismatch")
                if matches:
                    row = matches[0]
                    checks.require(period.get("year") == row["reporting_year"], label + "demand reporting year mismatch")
                    require_words("interval is unknown", "campus maximum" if row["whole_facility"] else "largest individual meter entry")
                    if not row["whole_facility"]: require_words("not a whole-property peak")
            elif category in {"historical_power_series", "series_completeness"}:
                matches = [s for s in power.get("series", []) if s.get("site_id") == ident and s.get("series_id") == selector.get("series_id") and cited(s)]
                checks.require(kind == "power_site" and len(matches) == 1, label + "hourly subject/source mismatch")
                if matches:
                    row = matches[0]
                    checks.require(period.get("start") == row["observed_start"] and period.get("end") == row["observed_end"] and row["whole_facility"] is False, label + "hourly period/scope mismatch")
                    if category == "historical_power_series":
                        checks.require(values.get("maximum retained series value", {}).get("value") == row["maximum_reported_kw"], label + "series maximum mismatch")
                        require_words("historical", "not live telemetry")
                        if "derived" in row["measurement_basis"]: require_words("hourly arithmetic means", "not a billing peak", "IT equipment")
                        else: require_words("HPC meter", "whole-facility coverage is unverified")
                    else:
                        checks.require(values.get("numeric hours", {}).get("value") == row["measured_count"] and values.get("missing hours", {}).get("value") == row["missing_count"], label + "series missingness mismatch")
                        require_words("unknown, not zero")
            elif category == "identity_boundary":
                checks.require(kind == "power_site" and sites.get(ident, {}).get("inventory_record_ids") == [], label + "power identity allocation mismatch")
                require_words("separate reporting entity", "not assigned")
            elif category == "data_gap":
                require_words("unknown, not zero")
                checks.require(not numbers, label + "invented gap quantity")
            else:
                checks.require(False, label + "unknown fact category " + str(category))
    coverage = manifest["coverage"]
    for field, value in {"explain_subjects": len(subjects), "explain_facts": total, "explain_subject_kinds": dict(counts)}.items():
        checks.require(coverage.get(field) == value and registry.get("coverage", {}).get(field) == value, "Explain coverage mismatch: " + field)
    for alias, key in registry.get("aliases", {}).items():
        checks.require(key in subjects and subjects[key].get("kind") == "facility"
                       and (alias == subjects[key]["id"] or objects["cards.json"].get(alias, {}).get("facility", {}).get("id") == subjects[key]["id"]), "Explain legacy alias mismatch")
    return {"explain-registry.json"}
