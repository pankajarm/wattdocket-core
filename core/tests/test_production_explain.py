"""Each release publishes server-owned facts for the Muse Spark explanation."""
from __future__ import annotations

import copy
import json

from test_production_pipeline import NOW, build_states, stamp
from wattdocket.production.build import build_release
from wattdocket.production.explain import card_facts
from wattdocket.prose import prose_is_bound


def test_release_publishes_explain_facts_for_every_card():
    files, _ = build_release(build_states(), "release-x", stamp(NOW))
    cards = json.loads(files["cards.json"])
    bundle = json.loads(files["explain.json"])
    assert bundle["release_id"] == "release-x"
    assert set(bundle["records"]) == set(cards)
    for key, record in bundle["records"].items():
        assert record["facility_id"] == cards[key]["facility"]["id"]
        assert record["template"] == cards[key]["verdict"]["sentence"]
        assert record["facts"] and all(isinstance(f, str) and f for f in record["facts"])


def test_facts_state_only_what_the_card_shows():
    files, _ = build_release(build_states(), "release-x", stamp(NOW))
    card = json.loads(files["cards.json"])["echo_110000000001"]
    facts = " ".join(card_facts(card))
    assert "1,000.0 pounds" in facts and "not this facility" in facts
    assert "actual electricity demand" in facts and "hypothetical" in facts
    # A scenario number is not a fact about the facility.
    assert "25,827" not in facts and "25827" not in facts
    assert prose_is_bound("The regional grid average is 1,000.0 pounds per megawatt-hour.", {"facts": card_facts(card)})
    assert not prose_is_bound("The site uses 25,828 megawatt-hours.", {"facts": card_facts(card)})


def test_absence_is_never_inferred_from_a_missing_field():
    card = {"facility": {"id": "osm:node:1", "name": "Mapped site", "source_label": "OpenStreetMap",
                         "location_quality": {"map_eligible": False}},
            "grid": {"subregion": "SRVC", "rate_lb_per_mwh": 600.0}, "claims": []}
    facts = " ".join(card_facts(card))
    assert "needs review" in facts and "SRVC" not in facts
    assert "Not available" not in facts and "water" not in facts


def test_cited_generator_table_and_disclosures_keep_their_scope():
    files, _ = build_release(build_states(), "release-x", stamp(NOW))
    card = copy.deepcopy(json.loads(files["cards.json"])["echo_110000000001"])
    card["claims"][3].update(value=12.5, citations=[{"quote": "row"}])
    card["disclosures"] = [{"location": {"location_label": "Henrico campus", "operator": "Meta", "reporting_scope": "campus",
                                         "observations": [{"label": "Electricity use", "value": 1234567, "unit": "MWh", "data_year": 2024}]},
                            "link": {}}]
    facts = " ".join(card_facts(card))
    assert "12.5 megawatts" in facts and "not a whole-site total" in facts
    assert "Meta reports electricity use of 1,234,567 MWh for 2024 for the reporting location Henrico campus (campus scope)" in facts
    assert "not allocated to this record" in facts
    assert "backup generator capacity" not in facts
