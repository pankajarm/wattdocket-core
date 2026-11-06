"""Pinned permit-history research reconciled against fresh official identities.

An active air site or newest index row never proves a permit legally effective.
"""
from __future__ import annotations
import copy
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from ..classify import VA_COUNTY_FIPS

MANIFEST = Path(__file__).with_name("data") / "virginia_history_reviews.json"
METHOD = "reviewed_deq_icis_cross_reference"
GIS_KEYS = ("PLA_ID", "PLA_REG_NUM", "PLA_ICIS_ID", "FAC_L_ADDR_1", "FAC_L_CITY", "FAC_L_STATE", "FAC_L_ZIP5")
AIR_KEYS = ("RegistryID", "SourceID", "OtherPermitIDs", "AIRStreet", "AIRCity", "AIRState", "AIRFIPSCode")

def reviewed_history():
    return json.loads(MANIFEST.read_text())["documents"] if MANIFEST.exists() else []

def _row(p):
    return {"registration":p.get("registration"),"url":p.get("permit_url"),"issued_at":p.get("issued_iso"),"program":p.get("program")}

def _same(a,b,keys):
    return all(str(a.get(k, "")).strip().casefold() == str(b.get(k, "")).strip().casefold() for k in keys)

def _evidence_ok(e):
    r=e.get("receipt") or {}
    return (str(e.get("url", "")).startswith("https://") and re.fullmatch(r"[0-9a-f]{64}",str(e.get("sha256", "")))
            and r.get("url")==e["url"] and r.get("sha256")==e["sha256"]
            and r.get("raw_key")=="raw/"+e["sha256"]+".gz" and bool(r.get("retrieved_at")) and bool(e.get("locator")))

def _legal(rule, valid):
    legal=copy.deepcopy(rule.get("legal_effectiveness") or {"status":"unknown","scope":"not_established","condition_refs":[],"evidence":[],"note":"Current legal effectiveness is unknown."})
    if legal.get("status") not in {"unknown","source_confirmed_current","superseded"}:
        raise ValueError("Unsupported permit legal-status review")
    if legal["status"] != "unknown":
        if (not legal.get("explicit_exact_document_or_condition_assertion") or legal.get("scope") not in {"entire_document","specified_conditions"}
                or not legal.get("evidence") or not all(_evidence_ok(e) and e.get("quote") for e in legal["evidence"])
                or (legal["scope"]=="specified_conditions" and not legal.get("condition_refs"))):
            raise ValueError("Legal-status assertion lacks exact scoped affirmative evidence")
    if not valid:
        return {"status":"unknown","scope":"not_established","condition_refs":[],"evidence":[],"note":"The source record changed or refresh is unavailable; the review must be repeated."}
    return legal

def apply_permit_history(permits, records, states, *, built_at, rules=None):
    """Return isolated permit copies; fail closed if reviewed identity fields change."""
    permits=copy.deepcopy(permits)
    rules=reviewed_history() if rules is None else rules
    source=states.get("virginia_site_registry",{})
    data=(source.get("result") or {}).get("data",{})
    sites={x["registration_root"]:x for x in data.get("sites",[])}
    inventory={x["id"]:x for x in records}
    pstate=states.get("virginia_permits",{})
    original_retrieved_at=(data.get("source_receipt") or {}).get("retrieved_at")
    try:
        captured=datetime.fromisoformat(original_retrieved_at.replace("Z", "+00:00"))
        publication=datetime.fromisoformat(built_at.replace("Z", "+00:00"))
        if captured.tzinfo is None or publication.tzinfo is None:
            raise ValueError("Identity evidence dates must include their time zone")
        capture_age_seconds=(publication-captured).total_seconds()
    except (AttributeError, TypeError, ValueError):
        capture_age_seconds=None
    # A failed refresh is not affirmative evidence that a stable regulator ID
    # changed. Retain only the exact dated capture within the existing 3-day
    # freshness window, and expose the failure alongside its original date.
    usable_capture=(source.get("status") in {"current", "stale"}
                    and capture_age_seconds is not None and 0 <= capture_age_seconds <= 3 * 86400)
    source_freshness="stale" if source.get("error") else source.get("status", "unavailable")
    pdata=(pstate.get("result") or {}).get("data",{})
    registry_receipt=pdata.get("registry_receipt") or {}
    registry_checked=registry_receipt.get("retrieved_at")
    pfresh=pstate.get("status")=="current" and not pstate.get("error")
    byreg={r["registration"]:r for r in rules}
    for permit in permits:
        rule=byreg.get(permit.get("registration"))
        if not rule: continue
        doc=permit.get("document") or {}; root=permit["registration"].split("-")[0]
        pinned=permit.get("permit_url")==rule["publisher_url"] and doc.get("content_sha256")==rule["document_sha256"]
        current=sorted([_row(p) for p in permits if p.get("registration","").split("-")[0]==root],key=lambda x:x["registration"])
        expected=sorted(rule["registry_rows"],key=lambda x:x["registration"])
        stable=current==expected and pinned and pfresh
        # A known predecessor's bytes changing also withdraws currentness review.
        for p in permits:
            guard=rule.get("collected_document_guards",{}).get(p.get("registration"))
            current_doc=p.get("document") or {}
            if guard and (current_doc.get("representation") != guard.get("representation")
                          or current_doc.get("content_sha256") != guard.get("sha256")): stable=False
            prior=rule.get("reviewed_document_hashes",{}).get(p.get("registration"))
            if prior and current_doc.get("representation")=="publisher_pdf" and current_doc.get("content_sha256") != prior: stable=False
        events=copy.deepcopy(rule.get("events",[]))
        if any(not e.get("evidence") or not all(_evidence_ok(x) for x in e["evidence"]) for e in events):
            raise ValueError("Permit-history event lacks archived original evidence")
        latest=max((x["issued_at"] or "" for x in current),default="")
        listing="not_listed" if not any(x["registration"]==permit["registration"] and x["url"]==permit["permit_url"] for x in current) else "listed_with_later_document" if latest>(permit.get("issued_iso") or "") else "latest_listed_for_registration"
        history={"review_version":"va-history-1","status":"verified" if stable else "review_required",
            "reviewed_at":rule["reviewed_at"],"checked_at":registry_checked,"registration":permit["registration"],
            "publisher_url":rule["publisher_url"],"document_sha256":rule["document_sha256"],
            "registry_listing":{"status":listing,"registry_as_of":pdata.get("registry_as_of"),"checked_at":registry_checked,
                "receipt":registry_receipt or rule.get("registry_receipt"),"current_rows":current},
            "legal_effectiveness":_legal(rule,stable),"events":events,"note":rule.get("note",""),
            "unresolved_history":rule.get("unresolved_history",[]),"active_application_check":rule.get("active_application_check")}
        site=sites.get(root); expected_gis=rule["expected_gis_fields"]
        valid_site=pinned and usable_capture and site is not None and _same(site["attributes"],expected_gis,GIS_KEYS)
        evidence=[]
        if site:
            e={"url":data.get("query_url"),"sha256":data.get("source_sha256"),"receipt":data.get("source_receipt"),
               "locator":"features[attributes.PLA_REG_NUM="+root+"]","record":site["attributes"]}
            if not _evidence_ok(e): valid_site=False
            else: evidence.append(e)
        identity={"review_version":"va-site-1","status":"regulator_site_verified" if valid_site else "review_required",
            "registration_root":root,"county_fips":VA_COUNTY_FIPS.get(permit.get("county")),"regulator_site_id":site.get("regulator_site_id") if site else None,
            "physical_address":site.get("physical_address") if site else None,"site_status":site.get("site_status") if site else None,
            "source_freshness":source_freshness,"retrieved_at":original_retrieved_at,
            "capture_age_seconds":capture_age_seconds,"retained_last_good":bool(valid_site and source_freshness=="stale"),
            "latest_fetch_status":"failed" if source.get("error") else "succeeded" if source.get("last_success_at") else "unavailable",
            "latest_fetch_error":source.get("error"),"latest_fetch_at":source.get("last_attempt_at"),
            "checked_at":source.get("last_success_at"),"evidence":evidence,"inventory_links":[],
            "note":rule.get("identity_note","The regulator's physical air-site identity is verified independently of inventory coverage; no complete campus or meter boundary is inferred.")}
        if valid_site and source_freshness=="stale":
            identity["note"] += " The latest registry refresh failed or is stale; this identity uses the unchanged original capture within its three-day review window."
        air=rule["expected_air_fields"]; fid="echo:"+air["RegistryID"]; record=inventory.get(fid)
        matched=[a for a in (record or {}).get("air_records",[]) if _same(a,air,AIR_KEYS)]
        county=VA_COUNTY_FIPS.get(permit.get("county"))
        if (valid_site and len(matched)==1 and air["SourceID"]==site["attributes"]["PLA_ICIS_ID"]
                and root in re.findall(r"\d+",str(air["OtherPermitIDs"])) and str(air["AIRFIPSCode"])=="51"+str(county)):
            ae=copy.deepcopy(rule["air_evidence"])
            if not _evidence_ok(ae): raise ValueError("Reviewed EPA bridge lacks archived evidence")
            identity["inventory_links"]=[{"record_id":fid,"relationship":"same_regulator_record","verification_status":"verified",
                "method":METHOD,"expected_record":{"id":fid,"air_record":{k:air[k] for k in AIR_KEYS}},
                "evidence":[*evidence,ae],"combined_address_conflict":rule.get("combined_address_conflict"),
                "note":"Same regulatory air-site record; conditions retain their quoted equipment and purpose. No campus-wide allocation is implied."}]
        permit["permit_history_review"]=history;permit["site_identity_review"]=identity
    return permits

def reviewed_link(facility,permit):
    review=permit.get("site_identity_review") or {}
    if review.get("status")!="regulator_site_verified": return None
    for link in review.get("inventory_links",[]):
        expected=(link.get("expected_record") or {}).get("air_record") or {}
        if (link.get("record_id")==facility.get("id") and link.get("verification_status")=="verified"
                and link.get("relationship")=="same_regulator_record" and expected
                and any(_same(a,expected,AIR_KEYS) for a in facility.get("air_records",[]))): return link
    return None
