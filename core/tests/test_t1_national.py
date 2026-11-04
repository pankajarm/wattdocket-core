"""Pin the national sanity check's unit arithmetic.

The first version of this script was wrong by a factor of 1000 and printed
"+64704% vs LBNL" without complaining. A ratio that absurd should be caught by
a test, not by a reader.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "scripts"))

from t1_national import (  # noqa: E402
    UTILISATION_DEFAULT,
    implied_capacity,
    twh_from_capacity,
)


def test_one_gigawatt_at_full_load_is_8760_gwh():
    assert twh_from_capacity(1.0, 1.0) == 8.76


def test_the_default_case_matches_the_reference_estimator():
    # 1 MW x 8760 h x 0.62 = 5431.2 MWh = 5.4312 GWh
    assert round(twh_from_capacity(1.0, 0.62), 4) == 5.4312


def test_capacity_and_energy_round_trip():
    for gw in (1.0, 21.0, 32.4):
        for util in (0.45, 0.65, 0.80):
            assert abs(implied_capacity(twh_from_capacity(gw, util), util) - gw) < 1e-9


def test_the_national_answer_is_in_a_plausible_range():
    """176 TWh cannot imply thousands of GW; it implies tens."""
    gw = implied_capacity(176.0, UTILISATION_DEFAULT)
    assert 20 < gw < 60, gw
