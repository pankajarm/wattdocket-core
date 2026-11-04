"""Build the card set once per test session, so a fresh clone passes.

`artifacts/cards/*.json` is generated and gitignored, and several invariant
tests read it. Without this, a fresh clone skips some of them and fails
`test_class_verified_is_only_set_by_a_regulator_or_not_at_all` with 0 != 118.
The cards are rebuilt every session rather than only when missing, because a
stale set from an older checkout would test yesterday's code. The index goes to
a temp dir: `artifacts/cards.json` is committed and must not churn on a test run.

**The hero series is re-applied afterwards.** `wattdocket.cards` rewrites every
card from the CSV, and `wattdocket.hero` adds the hourly series to one of them as
a separate step. Rebuilding here therefore wiped it, and the next test to look at
the hero card failed with "hero card lost its hourly series" — a true statement
about a file the test suite had just broken itself.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

CORE = pathlib.Path(__file__).parent.parent
REPO = CORE.parent


@pytest.fixture(scope="session", autouse=True)
def generated_cards(tmp_path_factory) -> pathlib.Path:
    out = REPO / "artifacts" / "cards"
    index = tmp_path_factory.mktemp("cards_index") / "cards.json"
    subprocess.run(
        [sys.executable, "-m", "wattdocket.cards",
         "--csv", str(REPO / "data" / "echo_datacenters_naics518210.csv"),
         "--out", str(out), "--index", str(index)],
        cwd=CORE, check=True, capture_output=True, text=True,
    )

    # Re-apply the hero series, which the card rebuild just wiped: from the
    # ERCOT cache when present, otherwise from the series already published on
    # the committed hero card (ERCOT no longer serves that day; build_all.sh
    # does the same).
    ercot = pathlib.Path("/tmp/ercot.json")
    published = REPO / "site" / "data" / "cards" / "echo_110072155814.json"
    source = (["--ercot", str(ercot)] if ercot.exists()
              else ["--from-card", str(published)] if published.exists()
              else None)
    if source:
        subprocess.run(
            [sys.executable, "-m", "wattdocket.hero",
             "--cards", str(out), "--out", str(out),
             *source, "--facility", "echo_110072155814"],
            cwd=CORE, check=True, capture_output=True, text=True,
        )
    return out
