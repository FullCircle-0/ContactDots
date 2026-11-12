"""Structural and rotational checks: Spec A7, A8, A9 (Blender) and the host's judging and validation (no Blender)."""
import json

import pytest

import dots
from blender_util import blender_or_skip, make_sample, run


# ---------------------------------------------------------------- host only

def vals(*v, **extra):
    return {str(i + 1): dict({'v': x}, **extra) for i, x in enumerate(v)}


LIM = dots.limits_of('finger')


@pytest.mark.parametrize('check, ok', [
    ({'kind': 'seat', 'values': vals(0.2, 0.9)}, True),
    ({'kind': 'seat', 'values': vals(0.2, 1.2)}, False),
    ({'kind': 'seat', 'max': 2, 'values': vals(1.5)}, True),
    ({'kind': 'hold', 'values': vals(0, 2.9)}, True),                      # default: limits offMm (3)
    ({'kind': 'hold', 'max': 1, 'values': vals(0, 2.9)}, False),
    ({'kind': 'distance', 'min': 99, 'max': 101, 'values': vals(100, 100.9)}, True),
    ({'kind': 'distance', 'min': 99, 'max': 101, 'values': vals(98.9, 100)}, False),
    ({'kind': 'align', 'values': vals(4.9)}, True),                        # default 5 degrees
    ({'kind': 'align', 'maxDeg': 20, 'values': vals(15, 21)}, False),
    ({'kind': 'range', 'min': 0, 'max': 110, 'values': vals(0, 110)}, True),
    ({'kind': 'range', 'min': 0, 'max': 110, 'values': vals(-10, 50)}, False),
    ({'kind': 'rest', 'values': {'1': {'v': 2.0, 'min': -1.0}}}, True),
    ({'kind': 'rest', 'values': {'1': {'v': 6.0, 'min': 0.0}}}, False),
    ({'kind': 'rest', 'values': {'1': {'v': 0.0, 'min': -2.5}}}, False),
])
def test_judge_check(check, ok):
    c = dict(check, id='c', a='x', b='y', unit='deg' if check['kind'] in ('align', 'range') else 'mm')
    row = dots.judge_check(c, LIM)
    assert row['ok'] is ok and c['ok'] is ok
    assert 'limit' in row['text']


def test_checks_in_summary_and_verdict():
    p = {'frames': [1, 2], 'seconds': 1.0, 'contacts': [], 'penetrations': [], 'observed': [], 'roots': {},
         'checks': [{'id': 'lid', 'kind': 'range', 'a': {'bone': 'r.Lid'}, 'b': {'bone': 'r.Base'}, 'min': 0, 'max': 110,
                     'unit': 'deg', 'values': vals(20, 120)}]}
    v = dots.judge(p, LIM)
    assert v['status'] == 'FAIL'
    lines = dots.describe(p, 't')
    assert any('range lid' in l and 'inside [0, 110] deg' in l and 'FAIL' in l for l in lines)


def write(tmp_path, cfg):
    path = tmp_path / 'c.json'
    path.write_text(json.dumps(dict({'out': 'o'}, **cfg)), encoding='utf-8')
    return path


DOTS = [{'name': 'peg', 'object': 'Peg'}, {'name': 'socket', 'object': 'Socket'}]


@pytest.mark.parametrize('cfg, why', [
    ({'dots': DOTS, 'checks': [{'id': 'a', 'kind': 'bend', 'a': 'peg', 'b': 'socket'}]}, 'kind'),
    ({'dots': DOTS, 'checks': [{'id': 'a', 'kind': 'seat', 'a': 'peg', 'b': 'nope'}]}, 'dot name'),
    ({'dots': DOTS, 'checks': [{'id': 'a', 'kind': 'seat', 'a': 'peg', 'b': 'socket'},
                               {'id': 'a', 'kind': 'seat', 'a': 'peg', 'b': 'socket'}]}, 'duplicate id'),
    ({'dots': DOTS, 'checks': [{'id': 'a', 'kind': 'rest', 'a': ['peg', 'socket'], 'b': 'floor'}]}, '3 or more'),
    ({'dots': DOTS, 'checks': [{'id': 'a', 'kind': 'distance', 'a': 'peg', 'b': 'socket'}]}, 'min'),
    ({'dots': DOTS, 'checks': [{'id': 'a', 'kind': 'align', 'a': {'up': 1}, 'b': 'z'}]}, 'direction'),
    ({'dots': DOTS, 'checks': [{'id': 'a', 'kind': 'range', 'a': 'z', 'b': 'x'}]}, 'min'),
    ({'dots': DOTS + [{'name': 'peg', 'object': 'X'}]}, 'duplicate dot'),
    ({'dots': [{'name': 'd'}]}, '"bone" or "object"'),
    ({'dots': DOTS, 'contacts': [{'a': 'nobody.R.palm', 'b': 'floor'}]}, 'custom dot or start'),
])
def test_bad_check_config(tmp_path, cfg, why):
    code, lines = run(['measure', str(write(tmp_path, cfg)), '--blender', str(tmp_path / 'none.exe')])
    assert code == 2 and lines[0].startswith('dots: skipped (bad config') and why in lines[0], lines


def test_touch_check_becomes_contact(tmp_path):
    cfg = dots.validate({'out': 'o', 'dots': DOTS, 'checks': [{'id': 't', 'a': 'peg', 'b': 'floor'},
                                                           {'id': 's', 'kind': 'seat', 'a': 'peg', 'b': 'socket'}]}, tmp_path)
    assert [c['id'] for c in cfg['contacts']] == ['t'] and [c['id'] for c in cfg['checks']] == ['s']


# ---------------------------------------------------------------- Blender: A1 re-run, A7, A8

@pytest.fixture(scope='module')
def selftest_out(tmp_path_factory):
    blender_or_skip()
    out = tmp_path_factory.mktemp('selftest')
    code, lines = run(['selftest', '--out', str(out)])
    return code, lines, out


def test_selftest_pass(selftest_out):
    code, lines, _ = selftest_out
    assert code == 0 and lines[-1].startswith('dots: PASS selftest'), lines
    assert sum(1 for l in lines if l.strip().startswith('tip-on') and l.endswith(' ok')) == 8, lines   # A1 unchanged


def rigid(out):
    return {c['id']: c for c in json.loads((out / 'rigid' / 'dots.json').read_text(encoding='utf-8'))['checks']}


@pytest.mark.parametrize('cid, want, ok', [
    ('seat-0', 0.0, True), ('seat-4', 4.0, False),
    ('rest-flat', 0.0, True), ('rest-tilted', 6.0, False),
    ('hold-carried', 0.0, True), ('hold-sliding', 5.0, False),
    ('distance-100', 100.0, True), ('distance-far', 100.0, False),
])
def test_a7_structural(selftest_out, cid, want, ok):
    c = rigid(selftest_out[2])[cid]
    assert abs(c['worst'] - want) <= 0.1 and c['ok'] is ok, c


@pytest.mark.parametrize('cid, want, ok', [
    ('align-0', 0.0, True), ('align-15', 15.0, True), ('align-90', 90.0, False),
    ('align-line', 0.0, True), ('align-normal', 0.0, True),
])
def test_a8_align(selftest_out, cid, want, ok):
    c = rigid(selftest_out[2])[cid]
    assert abs(c['worst'] - want) <= 0.1 and c['ok'] is ok, c


def test_a8_hinge(selftest_out):
    checks = rigid(selftest_out[2])
    sweep = checks['hinge-sweep']
    for f in range(1, 15):
        assert abs(sweep['values'][str(f)]['v'] - (-10 + 10 * (f - 1))) <= 0.1
    assert not sweep['ok'] and checks['hinge-open']['ok']


# ---------------------------------------------------------------- Blender: A9 (props and custom dots only)

def test_a9_no_characters(tmp_path):
    sample = make_sample(tmp_path, 'rigid', 'rigid.blend')
    hinge = lambda bone, axis: {'armature': 'Hinge', 'bone': bone, 'axis': axis}
    cfg = {'out': 'out', 'props': ['rigid.blend'], 'floorZ': None, 'frames': {'range': [2, 12]},
           'dots': [{'name': 'peg', 'object': 'Peg0'}, {'name': 'socket', 'object': 'Socket0'},
                    {'name': 'carried', 'object': 'Carried'}],
           'checks': [{'id': 'seated', 'kind': 'seat', 'a': 'peg', 'b': 'socket'},
                      {'id': 'held', 'kind': 'hold', 'a': 'carried', 'b': {'armature': 'Carrier', 'bone': 'Hand'}},
                      {'id': 'tilt', 'kind': 'align', 'a': {'object': 'Tilt15', 'axis': 'z'}, 'b': 'z', 'maxDeg': 20},
                      {'id': 'lid', 'kind': 'range', 'a': hinge('Lid', 'y'), 'b': hinge('Base', 'y'),
                       'axis': hinge('Base', 'x'), 'min': 0, 'max': 110}]}
    path = tmp_path / 'rigid.json'; path.write_text(json.dumps(cfg), encoding='utf-8')
    code, lines = run(['measure', str(path)])
    assert code == 0 and lines[0].startswith('dots: PASS rigid'), lines
    out = json.loads((tmp_path / 'out' / 'dots.json').read_text(encoding='utf-8'))
    assert out['roles'] == {} and len(out['checks']) == 4 and out['summary'] == lines[-len(out['summary']):]
    assert abs(out['checks'][3]['worst'] - 100.0) <= 0.1 and out['checks'][3]['least'] == pytest.approx(0.0, abs=0.1)
