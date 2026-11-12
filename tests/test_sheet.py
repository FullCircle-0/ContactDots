"""Close-up sheet (Plan step 6, Spec B9): the host's tile list (no Blender) and the selftest sheets (Blender)."""
import json
import pathlib

import pytest

import dots
from blender_util import blender_or_skip, run

LIM = dots.limits_of('finger')


def payload():
    gaps = {'1': {'mm': 0.5, 'dot': 'her.R.palm', 'surface': 'his.L.thigh', 'p': [0, 0, 1], 'q': [0, 0, 0.99]},
            '2': {'mm': 6.0, 'dot': 'her.R.palm', 'surface': 'his.L.thigh', 'p': [0, 0, 1], 'q': [0, 0, 0.98]}}
    vals = {'1': {'v': 3.0, 'p': [0, 0, 1], 'q': [0, 0, 1], 'da': [1, 0, 0], 'db': [0, 1, 0]},
            '2': {'v': 30.0, 'p': [0, 0, 1], 'q': [0, 0, 1], 'da': [1, 0, 0], 'db': [0, 1, 0]}}
    pens = [{'dot': 'his.R.knee', 'surface': 'her', 'mm': -12.0, 'frame': 2, 'p': [0, 0, 0], 'q': [0, 0, 0.01]},
            {'dot': 'his.L.knee', 'surface': 'her', 'mm': -1.0, 'frame': 2, 'p': [0, 0, 0], 'q': [0, 0, 0.01]}]
    p = {'contacts': [{'id': 'palm', 'a': 'her.R.hand', 'b': 'his.L.thigh', 'gaps': gaps}],
         'checks': [{'id': 'elbow', 'kind': 'align', 'a': {}, 'b': {}, 'values': vals, 'unit': 'deg'}],
         'penetrations': pens, 'config': {}}
    dots.judge(p, LIM)
    return p


def test_tiles_one_per_check_and_deep_penetration():
    tiles = dots.sheet_tiles(payload())
    assert [t['name'] for t in tiles] == ['touch-palm', 'align-elbow', 'inside-his.R.knee-f2']   # 1 mm pen under limit
    touch, align, pen = tiles
    assert touch['frame'] == 2 and not touch['ok'] and 'gap +6.0 mm' in touch['caption'] and 'FAIL' in touch['caption']
    assert align['da'] == [1, 0, 0] and align['db'] == [0, 1, 0] and '30.0 deg' in align['caption']
    assert pen['q'] == [0, 0, 0.01] and 'inside <= 2 mm' in pen['caption']


def test_penetration_tiles_capped():
    p = payload()
    p['penetrations'] = [dict(p['penetrations'][0], frame=f) for f in range(10)]
    p['config'] = {'closeups': 3}
    assert sum(t['name'].startswith('inside') for t in dots.sheet_tiles(p)) == 3


def test_sheet_needs_config(tmp_path):
    path = tmp_path / 'dots.json'
    path.write_text(json.dumps({'contacts': []}), encoding='utf-8')
    code, lines = run(['sheet', str(path), '--blender', 'nowhere/blender.exe'])
    assert code == 2 and lines[0].startswith('dots: skipped'), lines


# ---------------------------------------------------------------- Blender

@pytest.fixture(scope='module')
def sheets(tmp_path_factory):
    blender_or_skip()
    out = tmp_path_factory.mktemp('sheet')
    code, lines = run(['selftest', '--out', str(out)])
    assert code == 0, lines
    made = {}
    for name, path in (('body', out / 'dots.json'), ('rigid', out / 'rigid' / 'dots.json')):
        code, lines = run(['sheet', str(path)])
        assert code == 0 and lines[-1].startswith('dots: sheet '), lines
        made[name] = (path.parent, json.loads((path.parent / 'dots-sheet-job.json').read_text(encoding='utf-8')))
    return made


@pytest.mark.parametrize('name, tiles', [('body', 4), ('rigid', 15)])
def test_selftest_sheet(sheets, name, tiles):
    Image = pytest.importorskip('PIL.Image')
    np = pytest.importorskip('numpy')
    folder, job = sheets[name]
    assert len(job['tiles']) == tiles
    assert len(list((folder / 'dots-sheet-tiles').glob('*.png'))) == tiles
    im = np.asarray(Image.open(folder / 'dots-sheet.png').convert('RGB'), dtype=float)
    cols = min(3, tiles); rows = -(-tiles // cols)
    assert im.shape[1] == cols * 480 + (cols + 1) * 6 and im.shape[0] == rows * 480 + (rows + 1) * 6
    for t in job['tiles']:                                  # every tile has content: not one flat colour
        px = np.asarray(Image.open(folder / 'dots-sheet-tiles' / ('%s.png' % t['name'])).convert('RGB'), dtype=float)
        assert px.std() > 8, t['name']
        r, g, b = px[..., 0], px[..., 1], px[..., 2]       # the verdict colour, also seen through a see-through body
        mark = (np.minimum(r, g) - b > 90) if t['ok'] else (np.minimum(r, b) - g > 90)
        assert mark.sum() > 20, '%s: no %s mark' % (t['name'], 'yellow' if t['ok'] else 'magenta')


# ---------------------------------------------------------------- Showcase S7 look (grab example)

AXIS_RGB = [(255, 38, 38), (51, 255, 51), (64, 128, 255)]   # AXES in dots_sheet_blender (X red, Y green, Z light blue)
BAR_DISC_RGB = (219, 181, 121)                              # complement() of PROP[0], the Bar's flat colour


@pytest.fixture(scope='module')
def grab(tmp_path_factory):
    blender_or_skip()
    src = pathlib.Path(__file__).resolve().parents[1] / 'examples' / 'grab'
    tmp = tmp_path_factory.mktemp('grab')
    cfg = json.loads((src / 'config.json').read_text(encoding='utf-8'))
    for c in cfg['characters']:
        c['file'] = str(src / c['file'])
    cfg['out'] = str(tmp / 'out')
    (tmp / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
    code, lines = run(['measure', str(tmp / 'config.json')])
    assert any('dots-sheet.png' in line for line in lines), lines
    return tmp / 'out' / 'dots-sheet-tiles'


def count(tiles, name, rgb, tol=12):
    Image = pytest.importorskip('PIL.Image')
    np = pytest.importorskip('numpy')
    px = np.asarray(Image.open(tiles / ('%s.png' % name)).convert('RGB'), dtype=float)
    return int((np.abs(px - np.array(rgb, dtype=float)).max(-1) < tol).sum())


def test_grab_touch_tile_has_triads_and_disc(grab):
    for axis, rgb in zip('XYZ', AXIS_RGB):                  # palm/wrist triad beside the finger contact
        assert count(grab, 'touch-index-on-bar', rgb) > 300, 'no %s axis' % axis
    assert count(grab, 'touch-index-on-bar', BAR_DISC_RGB, 8) > 300, 'no disc edge on the bar'


def test_grab_marks_only_where_meant(grab):
    assert count(grab, 'touch-palm-on-bar', AXIS_RGB[0]) < 20       # no triad on top of the contact itself
    assert count(grab, 'align-palm-faces-bar', BAR_DISC_RGB, 8) < 20  # arrows, no disc
