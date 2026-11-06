"""Scoped citation, numeral and qualification checks, not semantic proof.

Every model paragraph is checked only against its cited trusted fact texts.
Identifiers, URLs, uncited facts and raw numeric metadata are never allowances.
"""
from decimal import Decimal, InvalidOperation
import math
import re
from urllib.parse import urlparse

from registry_loader import RegistryError, decode, identity

NUMBER_TOKEN = r"(?<![A-Za-z0-9])[-+−]?\s*\d[\d,]*(?:\.\d+)?"
NUMBER = re.compile(NUMBER_TOKEN)
QUANTITY = re.compile(r"(?P<number>" + NUMBER_TOKEN + r")\s*(?P<unit>(?:"
    r"million\s+(?:US\s+)?gallons?|million\s+lit(?:er|re)s?|metric\s+(?:tonnes?|tons?)(?:\s+(?:of\s+)?(?:CO2e|carbon dioxide equivalent))?|"
    r"megawatt[- ]hours?|kilowatt[- ]hours?|megawatts?|kilowatts?|MW[h]?|kW[h]?|GW[h]?|"
    r"(?:US\s+)?gallons?|hours?|percent|%|cubic\s+met(?:er|re)s?|m³|ML|kBtu|kgal|(?:t|lb)\s*CO2e)"
    r"(?:\s*/\s*(?:years?|yrs?|hours?|hr|h|days?|months?|MWh))?)(?![A-Za-z])", re.IGNORECASE)
MAX_FACTS = 12
MAX_MODEL_TEXT = 12_000
UNITS = {"megawatts": "mw", "megawatt": "mw", "kilowatts": "kw", "kilowatt": "kw",
         "megawatt-hours": "mwh", "megawatt-hour": "mwh", "megawatt hours": "mwh", "megawatt hour": "mwh",
         "kilowatt-hours": "kwh", "kilowatt-hour": "kwh", "kilowatt hours": "kwh", "kilowatt hour": "kwh",
         "gallon": "gallons", "hour": "hours", "percent": "%", "million gallon": "million gallons"}


class InvalidExplanation(ValueError):
    def __init__(self, reason="INVALID_MODEL_RESPONSE"):
        super().__init__(reason)
        self.reason = reason


def _flat(value):
    return " ".join(value.casefold().split())


def _number(value):
    try:
        return str(Decimal(re.sub(r"\s", "", value).replace(",", "").replace("−", "-")).normalize())
    except InvalidOperation:
        raise InvalidExplanation("UNBOUND_NUMERAL")


def numerals(text):
    text = re.sub(r"\bminus\s+(?=\d)", "-", text, flags=re.IGNORECASE)
    return {_number(match.group()) for match in NUMBER.finditer(text)}


def _unit(value):
    value = _flat(value)
    return UNITS.get(value, value)


def quantities(text):
    return {(_number(match["number"]), _unit(match["unit"])) for match in QUANTITY.finditer(text)}


def _evidence(value):
    if not isinstance(value, dict) or not isinstance(value.get("url"), str):
        return False
    url = urlparse(value.get("url", ""))
    return (url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
            and isinstance(value.get("sha256"), str) and re.fullmatch("[a-f0-9]{64}", value["sha256"])
            and isinstance(value.get("locator"), str) and bool(value["locator"].strip()))


def validate_subject(subject):
    facts = subject.get("facts")
    if (not isinstance(facts, list) or not 1 <= len(facts) <= MAX_FACTS or not isinstance(subject.get("title"), str)
            or not 0 < len(subject["title"]) <= 500):
        raise RegistryError("INVALID_SUBJECT_FACTS")
    seen = set()
    for fact in facts:
        if not isinstance(fact, dict) or not identity(fact.get("id")) or fact["id"] in seen:
            raise RegistryError("INVALID_SUBJECT_FACTS")
        seen.add(fact["id"])
        text = fact.get("text")
        if (not isinstance(text, str) or not 1 <= len(text) <= 4000 or not isinstance(fact.get("scope"), str)
                or not fact["scope"] or not isinstance(fact.get("required_qualifiers"), list)
                or not all(isinstance(q, str) and q.strip() and _flat(q) in _flat(text) for q in fact["required_qualifiers"])
                or not isinstance(fact.get("evidence"), list) or not fact["evidence"] or not all(_evidence(e) for e in fact["evidence"])):
            raise RegistryError("INVALID_SUBJECT_FACTS")
        numbers = fact.get("numbers", [])
        if not isinstance(numbers, list) or len(numbers) > 64:
            raise RegistryError("INVALID_SUBJECT_FACTS")
        for number in numbers:
            if (not isinstance(number, dict) or type(number.get("value")) not in (int, float)
                    or not math.isfinite(number["value"]) or not isinstance(number.get("unit"), str)
                    or not isinstance(number.get("display"), str) or not numerals(number["display"])
                    or not numerals(number["display"]) <= numerals(text)):
                raise RegistryError("INVALID_SUBJECT_FACTS")
    fallback = subject.get("fallback") or {}
    ids = fallback.get("fact_ids")
    by_id = {fact["id"]: fact for fact in facts}
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 5 or not all(isinstance(fid, str) for fid in ids)
            or len(set(ids)) != len(ids)
            or not all(fid in by_id for fid in ids) or fallback.get("text") != " ".join(by_id[fid]["text"] for fid in ids)):
        raise RegistryError("INVALID_SUBJECT_FALLBACK")
    return subject


def prompt_facts(subject, limit=6000):
    """Select whole trusted facts in registry priority order; never truncate one."""
    selected, used = [], 0
    for fact in subject["facts"]:
        candidate = {key: fact.get(key) for key in ("id", "text", "scope", "required_qualifiers")}
        import json
        length = len(json.dumps(candidate, ensure_ascii=False))
        if used + length > limit:
            continue
        selected.append(fact)
        used += length
    return selected


def _affirmative_match(pattern, text):
    for match in re.finditer(pattern, text):
        # A negative qualifier is not positive evidence. Check each occurrence:
        # "not live telemetry, but currently draws" still fails on the latter.
        before = text[max(0, match.start() - 50):match.start()]
        if not re.search(r"\b(?:not|never|no)(?:\s+(?:a|an|the))?\s*$", before):
            return True
    return False


def _contradictions(text, facts):
    lower = _flat(text)
    source = _flat(" ".join(fact["text"] for fact in facts))
    scopes = " ".join(str(fact.get("scope", "")) + " " + str(fact.get("kind", "")) + " " + str(fact.get("type", "")) for fact in facts).lower()
    if re.search(r"\b(?:compliant|noncompliant|illegal|unlawful|violates regulations|safe for|unsafe for)\b", lower):
        return True
    # This registry contains historical/annual evidence, never a live feed.
    # A cited disclaimer saying "not live telemetry" must not authorize the
    # opposite claim elsewhere in the same paragraph.
    if _affirmative_match(r"\b(?:live telemetry|real[- ]time|right now|today['’]s|currently (?:draws|uses|consumes|emits|runs|operates)|current (?:load|demand|power use))\b", lower):
        return True
    if "annual_disclosure" in scopes and _affirmative_match(
            r"\b(?:daily|weekly|monthly|(?:per|each|every) (?:hour|day|week|month)|hourly (?:use|consumption|emissions|total|average|rate))\b", lower):
        return True
    if ("permit" in scopes or "nameplate" in scopes) and re.search(r"\b(?:uses|consumes|draws|emits|burns|operated for)\b", lower):
        return True
    unknown_permit_effect = ("permit" in scopes and re.search(r"current (?:legal )?effectiveness is unknown", source)) or (
        "regulator_site_identity" in scopes and "not permit legal effectiveness" in source)
    if unknown_permit_effect and _affirmative_match(
            r"\b(?:currently (?:effective|enforceable)|legally binding|current legal (?:limit|obligation)s?"
            r"|(?:permit|condition|limit)s? (?:is|are|remains?|continue to be) (?:still )?(?:current|active|valid|in force|enforceable)"
            r"|(?:permit|condition|limit)s? (?:still )?(?:applies|apply) today"
            r"|(?:is|remains) (?:a|an) (?:currently )?(?:active|valid|current) permit)\b", lower):
        return True
    if "not a complete permit" in source and _affirmative_match(
            r"\b(?:complete permit|complete set of permit (?:conditions|limits|terms))\b", lower):
        return True
    if re.search(r"\b(?:this|the) (?:epa |inventory |regulatory )?record (?:uses|consumes|draws|emits)\b", lower):
        return True
    if ("it equipment" in source or "individual meter" in source or "not established" in source) and re.search(
            r"\b(?:measures|shows|represents|is|was) (?:the )?(?:whole[- ](?:facility|building|site)|entire (?:facility|building|site)|site[- ]wide) (?:power|demand|load|use|consumption)\b", lower):
        return True
    if "may include estimates" in source and re.search(r"\b(?:entirely measured|directly metered|exactly measured|verified actual)\b", lower):
        return True
    # Absence is evidence only when a cited fact explicitly says so.
    absence = r"\b(?:not published|unpublished|no (?:data|figure|measurement|information)|unavailable)\b"
    if re.search(absence, lower) and not re.search(absence, source) and "data_gap" not in scopes:
        return True
    return False


def validate_model_response(raw, facts):
    if not isinstance(raw, str) or not 0 < len(raw) <= MAX_MODEL_TEXT:
        raise InvalidExplanation()
    try:
        output = decode(raw)
    except RegistryError as exc:
        raise InvalidExplanation() from exc
    if not isinstance(output, dict) or set(output) != {"paragraphs"}:
        raise InvalidExplanation()
    paragraphs = output["paragraphs"]
    if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 4:
        raise InvalidExplanation()
    by_id = {fact["id"]: fact for fact in facts}
    used, texts = set(), []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict) or set(paragraph) != {"text", "fact_ids"}:
            raise InvalidExplanation()
        text, ids = paragraph["text"], paragraph["fact_ids"]
        if (not isinstance(text, str) or not 1 <= len(text.strip()) <= 1800 or not isinstance(ids, list)
                or not 1 <= len(ids) <= 3 or any(not isinstance(fid, str) or fid not in by_id for fid in ids)
                or len(set(ids)) != len(ids)):
            raise InvalidExplanation("INVALID_CITATION")
        if re.search(r"https?://|<[^>]+>", text, flags=re.IGNORECASE):
            raise InvalidExplanation("INVALID_MODEL_RESPONSE")
        cited = [by_id[fid] for fid in ids]
        if sum(any(n.get("unit") != "year" for n in fact.get("numbers", [])) for fact in cited) > 1:
            # A union of two totals can authorize a swapped accounting basis.
            # Keep each quantitative interpretation tied to its own fact.
            raise InvalidExplanation("AMBIGUOUS_QUANTITATIVE_CITATIONS")
        trusted = " ".join(fact["text"] for fact in cited)
        if not numerals(text) <= numerals(trusted):
            raise InvalidExplanation("UNBOUND_NUMERAL")
        if not quantities(text) <= quantities(trusted):
            raise InvalidExplanation("UNBOUND_QUANTITY_UNIT")
        for fact in cited:
            if not all(_flat(qualifier) in _flat(text) for qualifier in fact["required_qualifiers"]):
                raise InvalidExplanation("MISSING_SCOPE_QUALIFIER")
        if _contradictions(text, cited):
            raise InvalidExplanation("UNSUPPORTED_SCOPE_CLAIM")
        # Metadata is used only to demand substantive coverage; the numerical
        # allowance above still comes exclusively from each cited fact's text.
        for fact in cited:
            values = [numerals(number["display"]) for number in fact.get("numbers", []) if number.get("unit") != "year"]
            if values and not any(value and value <= numerals(text) for value in values):
                raise InvalidExplanation("INSUFFICIENT_EVIDENCE_COVERAGE")
        used.update(ids)
        texts.append(text.strip())
    words = len(" ".join(texts).split())
    quantitative = {fact["id"] for fact in facts if any(number.get("unit") != "year" for number in fact.get("numbers", []))}
    if (not 15 <= words <= 240 or len(used) < min(2, len(facts))
            or len(used & quantitative) < min(2, len(quantitative))):
        raise InvalidExplanation("INSUFFICIENT_EVIDENCE_COVERAGE")
    return {"paragraphs": [{"text": text, "fact_ids": paragraph["fact_ids"]}
                            for text, paragraph in zip(texts, paragraphs)],
            "text": "\n\n".join(texts), "fact_ids": list(dict.fromkeys(fid for paragraph in paragraphs for fid in paragraph["fact_ids"]))}


def response_evidence(subject, ids):
    by_id = {fact["id"]: fact for fact in subject["facts"]}
    selected = [by_id[fid] for fid in ids]
    citations = [{"fact_id": fact["id"], "label": evidence.get("publisher") or subject["title"],
                  **{key: evidence[key] for key in ("url", "locator", "sha256")}}
                 for fact in selected for evidence in fact["evidence"]]
    return {"fact_ids": ids, "cited_facts": selected, "citations": citations,
            "qualifiers": list(dict.fromkeys(q for fact in selected for q in fact["required_qualifiers"]))}
