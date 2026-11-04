"""The scheduling explanation must match the hours used in its calculation."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_operator', ROOT / 'scripts/build_operator.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_scheduling_explanation_matches_number_of_hours():
    card = json.loads((ROOT / 'site/api/card/echo_110072155814').read_text())
    assert 'the 4 dirtiest hours' in builder.build(card)['levers'][0]['detail']
    card['hourly']['means_lb_per_mwh'] = list(range(1, 19))
    lever = builder.build(card)['levers'][0]
    assert 'the 3 dirtiest hours' in lever['detail']
    assert 'Move 17%' in lever['title']
