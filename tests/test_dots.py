"""Host script: judging, summary, config checks, exit codes (canned dots.json, no Blender)."""
import json

import pytest

import dots


def gaps(*mm):
    return {str(i + 1): {'mm': v, 'dot': 'her.R.index.tip', 'surface': 'his.LeftUpLeg', 'p': [0, 0, 0], 'q': [0, 0, 0]}
            for i, v in enumerate(mm)}


def payload(contact_mm, pens=()):
    return {'frames': [1, 2, 3], 'seconds': 1.0,
            'contacts': [{'id': 'palm', 'a': 'her.R.hand', 'b': 'his.LeftUpLeg', 'frames': [1, 2, 3], 'gaps': gaps(*contact_mm)}],
            'penetrations': [{'dot': 'her.L.knee', 'surface': 'his.Hips', 'mm': m, 'frame': 2} for m in pens],
            'observed': [], 'roots': {}}


FINGER = dots.limits_of('finger')


def test_pass():
    p = payload([0.5, 1.0, -1.5])
    v = dots.judge(p, FINGER)
    assert v['status'] == 'PASS'
    assert p['contacts'][0]['worstMm'] == 1.0 and p['contacts'][0]['ok']
    assert dots.describe(p, 't')[0].startswith('dots: PASS t limits finger')


def test_floating():
    p = payload([0.5, 4.2, 1.0])
    v = dots.judge(p, FINGER)
    assert v['status'] == 'FAIL' and not v['checks'][0]['ok']
    assert 'worst +4.2 mm f2' in dots.describe(p)[1]
    assert dots.judge(payload([0.5, 4.2, 1.0]), dots.limits_of('limb'))['status'] == 'PASS'


def test_penetration():
    p = payload([0.0, 0.0, 0.0], pens=[-2.5, -1.0])
    v = dots.judge(p, FINGER)
    assert v['status'] == 'FAIL' and v['penetrationsOverLimit'] == 1
    assert any(l.strip().startswith('inside her.L.knee in his.Hips -2.5 mm') for l in dots.describe(p))
    deep = payload([0.0, -2.5, 0.0])
    assert dots.judge(deep, FINGER)['checks'][0]['ok'] is False


def test_no_contacts_lists_observed():
    p = payload([0.0]); p['contacts'] = []
    p['observed'] = [{'dot': 'her.R.palm', 'surface': 'his.Spine', 'mm': 12.0, 'frame': 1}]
    dots.judge(p, FINGER)
    assert 'her.R.palm~his.Spine +12.0 f1' in dots.describe(p)[-1]


def test_limits():
    assert dots.limits_of(None) == {'offMm': 3.0, 'insideMm': 2.0, 'name': 'finger'}
    assert dots.limits_of({'preset': 'limb', 'insideMm': 5})['insideMm'] == 5.0
    with pytest.raises(dots.Skipped):
        dots.limits_of('loose')


def run(argv):
    lines = []
    code = dots.main(argv, echo=lines.append)
    return code, lines


def test_skipped_no_blender(tmp_path):
    fbx = tmp_path / 'a.fbx'; fbx.write_bytes(b'x')
    cfg = tmp_path / 'c.json'
    cfg.write_text(json.dumps({'out': 'out', 'characters': [{'role': 'her', 'file': 'a.fbx'}]}))
    code, lines = run(['measure', str(cfg), '--blender', str(tmp_path / 'none.exe')])
    assert code == 2 and lines[0].startswith('dots: skipped (no Blender at')
    resolved = json.loads((tmp_path / 'out' / 'dots-config.json').read_text())
    assert resolved['characters'][0]['file'] == str(fbx.resolve()) and resolved['limits']['name'] == 'finger'


@pytest.mark.parametrize('cfg, why', [
    ({'characters': []}, 'no "out"'),
    ({'out': 'o', 'characters': [{'role': 'her', 'file': 'missing.fbx'}]}, 'no file'),
    ({'out': 'o', 'characters': [{'role': 'h.r', 'file': 'a.fbx'}]}, 'role'),
    ({'out': 'o', 'characters': [{'role': 'her', 'file': 'a.fbx'}], 'frames': 'some'}, 'frames'),
    ({'out': 'o', 'characters': [{'role': 'her', 'file': 'a.fbx'}], 'contacts': [{'a': 'his.R.hand', 'b': 'floor'}]}, 'role'),
    ({'out': 'o', 'characters': [{'role': 'her', 'file': 'a.fbx'}], 'limts': 'finger'}, 'unknown keys'),
    ({'out': 'o'}, 'nothing to measure'),
])
def test_bad_config(tmp_path, cfg, why):
    (tmp_path / 'a.fbx').write_bytes(b'x')
    path = tmp_path / 'c.json'; path.write_text(json.dumps(cfg))
    code, lines = run(['measure', str(path), '--blender', str(tmp_path / 'none.exe')])
    assert code == 2 and lines[0].startswith('dots: skipped (bad config') and why in lines[0]


def test_bad_json(tmp_path):
    path = tmp_path / 'c.json'; path.write_text('{not json')
    code, lines = run(['measure', str(path)])
    assert code == 2 and 'bad config' in lines[0]
