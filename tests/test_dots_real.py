"""Real-mesh checks of the contact dots (Spec A2): the samples in tests/fixtures/real/dots (README there).

Each .npz holds a few dots at one frame of a solved two-character take: the dot centres, their own skin triangles and
the surface triangles around them, plus the measurer's answer when the sample was written. tests/dots_reference.py
(numpy, every triangle, no BVH, no code shared with the measurer) recomputes every gap. Two comparisons, both within
0.5 mm on every sample:
  - the stored answer against the reference (always runs);
  - today's measurer gap rule run in Blender on the stored triangles against the reference (skipped without Blender).
The samples stay here, outside the skill folder.
"""
import json
from pathlib import Path

import numpy as np
import pytest

import dots
import dots_reference
from blender_util import blender_or_skip

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / 'fixtures' / 'real' / 'dots'
SAMPLES = sorted(FIXTURES.glob('*.npz'))
TOLERANCE_MM = 0.5
CROP_MM = 250.0                 # samples keep the surface within 25 cm of each dot
NEAR_MM = 30.0


def compared(rows):
    return [r for r in rows if np.isfinite(r[1]) and r[1] < CROP_MM - 50]


@pytest.mark.parametrize('path', SAMPLES, ids=[p.stem for p in SAMPLES])
def test_stored_answer_matches_brute_force(path):
    _, rows = dots_reference.sample_gaps(path)
    near = compared(rows)
    assert len(near) >= 3, 'too few dots near the surface in %s' % path.name
    worst = max(near, key=lambda r: abs(r[1] - r[2]))
    skin = max(rows, key=lambda r: abs(r[3] - r[4]))
    assert abs(worst[1] - worst[2]) <= TOLERANCE_MM, worst
    assert abs(skin[3] - skin[4]) <= TOLERANCE_MM, skin


def test_samples_cover_real_contacts():
    """7 samples; 20+ dots within 30 mm of their surface, some of them inside it."""
    assert len(SAMPLES) == 7
    refs = [r[1] for p in SAMPLES for r in dots_reference.sample_gaps(p)[1]]
    assert sum(abs(g) <= NEAR_MM for g in refs) >= 20 and min(refs) < 0


@pytest.fixture(scope='module')
def today(tmp_path_factory):
    exe = blender_or_skip()
    out = tmp_path_factory.mktemp('regap') / 'regap.json'
    dots.run_blender(exe, HERE / 'regap_blender.py', [out] + SAMPLES, 600, 'DOTS_REGAP_DONE', echo=lambda *_: None)
    return json.loads(out.read_text(encoding='utf-8'))


@pytest.mark.parametrize('path', SAMPLES, ids=[p.stem for p in SAMPLES])
def test_todays_gap_rule_matches_brute_force(path, today):
    _, rows = dots_reference.sample_gaps(path)
    mine = {name: (gap, skin) for name, gap, skin in today[path.stem]}
    checked = 0
    for name, ref_gap, _, ref_skin, _ in rows:
        gap, skin = mine[name]
        assert abs(skin - ref_skin) <= TOLERANCE_MM, (name, skin, ref_skin)
        if np.isfinite(ref_gap) and ref_gap < CROP_MM - 50:
            assert abs(gap - ref_gap) <= TOLERANCE_MM, (name, gap, ref_gap)
            checked += 1
    assert checked >= 3
