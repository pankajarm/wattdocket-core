"""Upgrade facility-type evidence using state regulators' own statements.

A NAICS code says a facility is *classified* as computing infrastructure. It does
not say anyone has confirmed a data center is there, and our extract provably
contains at least one paper mill. Virginia, however, publishes a registry titled
"Issued Air Permits for Data Centers" — when the Commonwealth has issued an air
permit to a site under that programme, the state is asserting the facility type,
not us.

The join is deliberately dumb: an exact match on the normalised facility name.
Fuzzy matching here would manufacture the very overclaim this module exists to
remove, so a name that does not match exactly stays unverified.

When the names differ, there is a second exact key. EPA's ECHO source id for a
Virginia air source is the state's own plant id: "VA00000051" + county FIPS +
five digits, and for the newer permits those five digits *are* the DEQ
registration number ("VA0000005110774269" is registration 74269 in Loudoun,
FIPS 107). ECHO calls that site "STACK INFRASTRUCTURE - SI NVA06A LLC" at "TBD
COMPASS CREEK PARKWAY"; DEQ calls it "STACK Infrastructure Data Center - SI
NVA06A LLC". The name join misses it, the number does not. The number is only
trusted when the county in the id is the county DEQ prints for that permit.

    from wattdocket.classify import VirginiaRegistry
    reg = VirginiaRegistry.load("data/deq_air_permits.csv")
    reg.evidence_for("TECfusions Clarksville LLC")
    # -> {...} or None
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

_SEP = re.compile(r"[^a-z0-9]+")


def name_key(name: str) -> str:
    """Normalise a facility name for exact comparison, nothing more.

    Case, punctuation and spacing are layout artefacts. Beyond that, two names
    are either the same or they are not.
    """
    return _SEP.sub(" ", (name or "").lower()).strip()


# County FIPS codes for every county or independent city the DEQ registry
# names. A registration number is only accepted from an ECHO source id when the
# id's county is the one DEQ prints for that permit.
VA_COUNTY_FIPS = {
    "Caroline Co.": "033", "Chesterfield Co.": "041", "Culpeper Co.": "047",
    "Fairfax Co.": "059", "Fauquier Co.": "061", "Frederick Co.": "069",
    "Hanover Co.": "085", "Henrico Co.": "087", "Loudoun Co.": "107",
    "Louisa Co.": "109", "Mecklenburg Co.": "117", "Prince William Co.": "153",
    "Russell Co.": "167", "Spotsylvania Co.": "177", "Stafford Co.": "179",
    "Wise Co.": "195", "Harrisonburg City": "660", "Manassas City": "683",
    "Virginia Beach City": "810",
}

_VA_SOURCE_ID = re.compile(r"^VA00000051(\d{3})(\d{5})$")


def source_id_parts(source_id: str) -> tuple[str, str] | None:
    """(county FIPS, plant id) from a Virginia ECHO air source id, or None."""
    m = _VA_SOURCE_ID.match((source_id or "").strip())
    return (m.group(1), m.group(2)) if m else None


def registration_base(registration: str) -> str:
    """'74269-2' -> '74269': the number without DEQ's revision suffix."""
    return (registration or "").split("-", 1)[0].strip()


@dataclass
class VirginiaRegistry:
    by_key: dict[str, list[dict]] = field(default_factory=dict)
    by_number: dict[str, list[dict]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "VirginiaRegistry":
        path = Path(path)
        if not path.is_file():
            return cls()
        by_key: dict[str, list[dict]] = {}
        by_number: dict[str, list[dict]] = {}
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                key = name_key(row.get("site", ""))
                if key:
                    by_key.setdefault(key, []).append(row)
                number = registration_base(row.get("registration", ""))
                if number.isdigit():
                    by_number.setdefault(number, []).append(row)
        return cls(by_key=by_key, by_number=by_number)

    def __len__(self) -> int:
        return len(self.by_key)

    def permits_for(self, name: str) -> list[dict]:
        return self.by_key.get(name_key(name), [])

    def permits_for_source_id(self, source_id: str) -> list[dict]:
        """Permits whose registration number is the plant id in an ECHO id.

        Both halves must agree: the five digits equal the registration number,
        and the county FIPS in the id is the county DEQ lists on the permit.
        Plant ids from the older numbering (e.g. "00839") never equal a DEQ
        registration, so they fall through to nothing rather than to a guess.
        """
        parts = source_id_parts(source_id)
        if not parts:
            return []
        fips, number = parts
        return [p for p in self.by_number.get(number, [])
                if VA_COUNTY_FIPS.get(p.get("county", "")) == fips]

    def evidence_for(self, name: str) -> dict | None:
        """Class evidence from the state's own registry, or None.

        The returned dict is ready to merge onto a card's facility block. Note
        what it claims and what it does not: the state issued a data-center air
        permit to a site of this name. It does not claim we read the permit —
        the PDFs are behind a WAF we do not bypass — so the evidence string says
        "not extracted" plainly.
        """
        return self._evidence(self.permits_for(name), "exact name match",
                              "exact_normalised_name")

    def evidence_for_source_id(self, source_id: str) -> dict | None:
        """Class evidence from the DEQ registration number in an ECHO source id."""
        return self._evidence(
            self.permits_for_source_id(source_id),
            "DEQ registration number equals the plant id in EPA's source id, "
            "same county",
            "exact_registration_number")

    @staticmethod
    def _evidence(permits: list[dict], how: str, method: str) -> dict | None:
        if not permits:
            return None
        regs = ", ".join(sorted({p["registration"] for p in permits}))
        first = permits[0]
        return {
            "class_label": f"Virginia DEQ air-permitted data center (permit {regs})",
            "class_verified": True,
            "class_evidence": (
                "Virginia DEQ, 'Issued Air Permits for Data Centers' registry — "
                f"{how}; permit document not text-extracted"
            ),
            "class_match_method": method,
            "class_match_confidence": "registry_statement",
            "permit_registrations": sorted({p["registration"] for p in permits}),
            "permit_issued": first.get("issued"),
            "permit_county": first.get("county"),
            "permit_program": first.get("program"),
            "permit_url": first.get("permit_url"),
        }


REGISTRY_STATE = "VA"


def apply_registry(facility_block: dict, registry: VirginiaRegistry,
                   source_id: str | None = None) -> dict:
    """Merge class evidence into a card's facility block, in place.

    Jurisdiction is checked first, and this is not defensive padding — it was a
    real defect. A Virginia permit for "EQUINIX LLC" matched an Equinix facility
    in Illinois, Oregon and New Jersey, because the name is the same and the
    name is all this join looks at. The registry covers one state; the match must
    too. Without this guard, 5 of 479 cards claimed a Virginia permit for a
    building a thousand miles away.
    """
    if str(facility_block.get("state") or "").upper() != REGISTRY_STATE:
        return facility_block
    found = registry.evidence_for(facility_block.get("name", ""))
    # The name is tried first so every card it already verified keeps its
    # evidence unchanged. The registration number only fills the gaps.
    if not found and source_id:
        found = registry.evidence_for_source_id(source_id)
    if found:
        facility_block.update(found)
    return facility_block
