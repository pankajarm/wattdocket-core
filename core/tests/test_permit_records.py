"""Permit facts attach to the right building, or not at all."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from wattdocket.permit_records import _street_key, attach  # noqa: E402

ROOT = pathlib.Path(__file__).parent.parent.parent


def test_street_suffix_and_case_do_not_matter():
    assert _street_key("5207 ROGERS RD") == _street_key("5207 Rogers Road")
    assert _street_key("14720 OMICRON DR") == _street_key("14720 Omicron Dr")


def test_a_permit_address_is_not_polluted_by_the_next_label():
    """TCEQ writes the whole block on one line.

    'Physical Location: 14720 Omicron Dr Nearest City: San Antonio County:
    Bexar' — a greedy capture swallowed all of it, so no address ever matched.
    """
    key = _street_key("14720 Omicron Dr Nearest City: San Antonio County: Bexar")
    assert key == "14720 omicron"


def test_different_streets_never_match():
    assert _street_key("5207 Rogers Rd") != _street_key("5209 Rogers Rd")
    assert _street_key("5207 Rogers Rd") != _street_key("14720 Omicron Dr")


def test_attach_requires_an_exact_street():
    cards = {
        "a": {"address_line": "5207 ROGERS RD", "name": "VANTAGE TX2"},
        "b": {"address_line": "5209 ROGERS RD", "name": "NEIGHBOUR"},
    }
    permits = [{"file": "p.pdf", "street_key": _street_key("5207 Rogers Road"),
                "sha256": "x", "identity": {}, "equipment": []}]
    matched = attach(cards, permits)
    assert list(matched) == ["a"]


def test_the_shipped_permits_attach_to_the_shipped_cards():
    """Two Vantage sites in San Antonio, both with a TCEQ operating permit."""
    perm = ROOT / "artifacts" / "permits.json"
    if not perm.exists():
        pytest.skip("no permits.json")
    import json
    data = json.loads(perm.read_text())
    assert len(data["matches"]) == 2
    ids = set(data["matches"])
    assert ids == {"echo_110071655854", "echo_110072155814"}
    for rec in data["matches"].values():
        assert rec["identity"].get("permit_no", "").startswith("O4")
