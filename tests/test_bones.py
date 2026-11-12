"""Bone maps (Spec B4, A4)."""
import json

import pytest

import bone_maps
from blender_util import blender_or_skip, make_sample, run

MIXAMO = ['mixamorig:' + n for n in bone_maps.CANONICAL]
UNITY = [n for n in bone_maps.PRESETS['unity'].values()]


def test_mixamo_prefixed():
    p = bone_maps.propose(MIXAMO)
    assert p['preset'] == 'mixamo' and p['usable'] and p['map']['RightHandIndex4'] == 'mixamorig:RightHandIndex4'


def test_other_prefix():
    p = bone_maps.propose(['Raven_' + n for n in bone_maps.CANONICAL])
    assert p['usable'] and p['map']['LeftUpLeg'] == 'Raven_LeftUpLeg'


def test_unity_names():
    p = bone_maps.propose(UNITY)
    assert p['preset'] == 'unity' and p['usable']
    assert p['map']['RightForeArm'] == 'RightLowerArm' and p['map']['LeftHandIndex3'] == 'Left Index Distal'
    assert 'RightHandIndex4' not in p['map']
    nospace = bone_maps.propose([n.replace(' ', '') for n in UNITY])
    assert nospace['map']['LeftHandIndex3'] == 'LeftIndexDistal'


def test_override_wins():
    p = bone_maps.propose(MIXAMO + ['spine_extra'], {'Spine': 'spine_extra', 'Neck': 'missing'})
    assert p['map']['Spine'] == 'spine_extra' and p['badOverride'] == ['missing']


def test_unknown_rig():
    names = ['bone_%03d' % i for i in range(30)]
    p = bone_maps.propose(names)
    assert not p['usable'] and p['matches'] == 0
    assert 'no bone map' in bone_maps.no_map_message('her', p, names)


@pytest.mark.blender
@pytest.mark.parametrize('rig', ['mixamo', 'unity'])
def test_selftest_with_rig_names(tmp_path, rig):
    blender_or_skip()
    code, lines = run(['selftest', '--rig', rig, '--out', str(tmp_path)])
    assert code == 0, lines
    data = json.loads((tmp_path / 'dots.json').read_text())
    assert data['roles']['rig']['bonePreset'] == rig


@pytest.mark.blender
def test_unknown_rig_message_and_bones(tmp_path):
    blender_or_skip()
    code, lines = run(['selftest', '--rig', 'unknown', '--out', str(tmp_path)])
    assert code == 2 and 'no bone map' in lines[-1], lines
    sample = make_sample(tmp_path, 'unknown')
    code, lines = run(['bones', str(sample)])
    assert code == 1 and 'NO BONE MAP' in '\n'.join(lines) and 'bone_000' in '\n'.join(lines), lines
    code, lines = run(['bones', str(make_sample(tmp_path, 'mixamo', 'm.blend'))])
    assert code == 0 and 'preset mixamo' in lines[1], lines
