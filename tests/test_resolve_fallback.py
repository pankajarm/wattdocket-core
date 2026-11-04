"""The address fallback's numbers are the estimator's, not the browser's.

web/public/data/resolve-fallback.json is committed so the static site can serve
it. If a rate, a prior or the factor set changes and nobody reruns the builder,
the fallback would quietly disagree with every card. This keeps them together.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_resolve_fallback", ROOT / "scripts" / "build_resolve_fallback.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_table_matches_the_estimator():
    committed = json.loads((ROOT / "web" / "public" / "data" / "resolve-fallback.json").read_text())
    rebuilt = json.loads(json.dumps(_builder().build(), sort_keys=True))
    assert committed == rebuilt, "rerun scripts/build_resolve_fallback.py"


def test_every_polygon_has_a_reference_row():
    table = _builder().build()
    geo = json.loads((ROOT / "data" / "egrid_subregions.geojson").read_text())
    subs = {f["properties"]["subregion"] for f in geo["features"]}
    assert subs <= set(table["subregions"])
    assert set(table["state_default"].values()) <= set(table["subregions"])


def test_fallback_is_badged_estimated_with_a_band():
    for sub, row in _builder().build()["subregions"].items():
        assert row["badge"] == "ESTIMATED", sub
        c = row["carbon_t"]
        assert 0 < c["p10"] < c["p50"] < c["p90"], sub
        cit = row["citation"]
        assert cit["verified"] is True, sub


def test_fallback_quote_is_a_real_line_of_the_cited_extract():
    """The quote must be text the cited document contains, at the cited line.

    It used to be a sentence assembled by the builder, in quotation marks."""
    import hashlib
    text = (ROOT / "data" / "egrid_subregion_rates.json").read_text()
    sha = hashlib.sha256(text.encode()).hexdigest()
    lines = text.splitlines()
    for sub, row in _builder().build()["subregions"].items():
        cit = row["citation"]
        assert cit["doc_sha256"] == sha, sub
        assert cit["quote"] in lines[cit["line"] - 1], sub
        assert float(cit["quote"].split(":")[1]) == row["rate_lb_per_mwh"], sub
