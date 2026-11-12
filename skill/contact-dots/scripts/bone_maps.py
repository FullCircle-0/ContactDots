"""Bone maps: rig bone names -> the canonical names the dots use (Mixamo-style names without a prefix: Hips, Spine,
RightArm, RightForeArm, RightHand, RightHandIndex1-4, RightUpLeg, RightLeg, RightFoot, RightToeBase, ...).

Built-in presets are tried on every armature; the one with the most matches wins. A hand-written map in the config
({canonical: rig bone}) always wins over the preset for the bones it names. Pure Python: used inside Blender and by tests.
"""
import re

SIDES = ('Left', 'Right')
FINGERS = ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky')
MIN_MATCHES = 5          # fewer matches than this (or no Hips) = no usable map


def canonical_names():
    names = ['Hips', 'Spine', 'Spine1', 'Spine2', 'Neck', 'Head']
    for s in SIDES:
        names += [s + n for n in ('Shoulder', 'Arm', 'ForeArm', 'Hand', 'UpLeg', 'Leg', 'Foot', 'ToeBase', 'Toe_End')]
        names += ['%sHand%s%d' % (s, f, i) for f in FINGERS for i in (1, 2, 3, 4)]
    return names


CANONICAL = canonical_names()


def _mixamo():
    return {c: c for c in CANONICAL}


def _unity():
    """Unity humanoid (HumanTrait) names; finger end bones do not exist (the tip is the distal bone's tail)."""
    m = {'Hips': 'Hips', 'Spine': 'Spine', 'Spine1': 'Chest', 'Spine2': 'UpperChest', 'Neck': 'Neck', 'Head': 'Head'}
    parts = {'Shoulder': 'Shoulder', 'Arm': 'UpperArm', 'ForeArm': 'LowerArm', 'Hand': 'Hand', 'UpLeg': 'UpperLeg',
             'Leg': 'LowerLeg', 'Foot': 'Foot', 'ToeBase': 'Toes'}
    for s in SIDES:
        for c, u in parts.items():
            m[s + c] = s + u
        for f in FINGERS:
            for i, seg in enumerate(('Proximal', 'Intermediate', 'Distal'), 1):
                m['%sHand%s%d' % (s, f, i)] = '%s %s %s' % (s, f, seg)
    return m


def _base():
    """The base humanoid rig of our exports: Mixamo layout under the 'mixamorig:' namespace, 4 bones per finger."""
    return {c: 'mixamorig:' + c for c in CANONICAL}


PRESETS = {'mixamo': _mixamo(), 'unity': _unity(), 'base-humanoid': _base()}


def norm(name):
    """Compare key: no namespace, lower case, letters and digits only."""
    return re.sub(r'[^a-z0-9]', '', name.rsplit(':', 1)[-1].lower())


def _prefix(rig_names):
    """A common prefix in front of 'Hips' (e.g. 'Raven_', 'mixamorig1:', 'CC_Base_'), normalised."""
    for n in rig_names:
        k = norm(n)
        if k.endswith('hips'):
            return k[:-4]
    return ''


def match(preset, rig_names):
    """{canonical: rig bone} for one preset."""
    pre = _prefix(rig_names)
    by_key = {}
    for n in rig_names:
        k = norm(n)
        by_key.setdefault(k, n)
        if pre and k.startswith(pre):
            by_key.setdefault(k[len(pre):], n)
    out = {}
    for c, target in preset.items():
        hit = by_key.get(norm(target))
        if hit is not None and hit not in out.values():
            out[c] = hit
    return out


def propose(rig_names, override=None):
    """Best preset for these bone names, with the config's map on top.
    Returns {'preset', 'map': {canonical: rig}, 'matches', 'usable', 'scores': {preset: n}, 'unmatched': [rig bones]}."""
    rig_names = list(rig_names)
    scores, maps = {}, {}
    for name, preset in PRESETS.items():
        maps[name] = match(preset, rig_names)
        scores[name] = len(maps[name])
    best = max(PRESETS, key=lambda n: scores[n])          # ties: the first preset
    m = dict(maps[best])
    bad = []
    for c, r in (override or {}).items():
        if r in rig_names:
            for k in [k for k, v in m.items() if v == r]:
                del m[k]
            m[c] = r
        else:
            bad.append(r)
    used = set(m.values())
    return {'preset': best if scores[best] else None, 'map': m, 'matches': len(m),
            'usable': 'Hips' in m and len(m) >= MIN_MATCHES, 'scores': scores,
            'unmatched': [n for n in rig_names if n not in used], 'badOverride': bad}


def no_map_message(role, proposal, rig_names):
    return ('%s: no bone map: the rig matches no preset (best %s, %d bones; needs Hips and %d). '
            'Run `dots bones <file>` and add "boneMap": {canonical: rig bone} to the character. Rig bones: %s' % (
                role, proposal['preset'], proposal['matches'], MIN_MATCHES, ', '.join(list(rig_names)[:20])))
