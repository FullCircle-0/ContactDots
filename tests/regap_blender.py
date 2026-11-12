"""Blender side of tests/test_dots_real.py: today's gap rule (dots_blender's BVH nearest + signed + skin radius) on
the triangles stored in each real sample. Writes {sample: [[dot, gap mm, skin mm], ...]} as json.

  blender -b --factory-startup --python tests/regap_blender.py -- <out.json> <sample.npz> ...
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill' / 'contact-dots' / 'scripts'))
import dots_blender as D  # noqa: E402
from mathutils import Vector  # noqa: E402


def tree(tris):
    if len(tris) == 0:
        return []
    co = tris.reshape(-1, 3).astype(np.float64)
    return [('s', D.bvh_from(co, np.arange(len(co)).reshape(-1, 3)))]


def regap(path):
    z = np.load(path)
    meta = json.loads(bytes(z['meta']).decode())
    sign_range = float(z['signRangeM'])
    tris = {k[5:]: z[k] for k in z.files if k.startswith('tris_')}
    rows = []
    for i, dot in enumerate(meta['dots']):
        p = Vector(z['dot%d' % i].tolist())
        own = tree(tris[dot['ownRole']][z['own%d' % i]])
        surf = tree(tris[dot['surfRole']][z['surf%d' % i]])
        hit = D.nearest(own, p)
        skin = 0.0 if dot['snapped'] or hit is None else hit[3] * 1000.0
        s = D.nearest(surf, p)
        d = None if s is None else D.signed(s, p, sign_range)[0]     # the crop is open: no parity test here
        if d is not None and (dot.get('toolParity') or 0) >= D.PARITY_INSIDE:
            d = -abs(d)                                                 # the tool's votes say inside
        gap = float('inf') if d is None else d * 1000.0 - skin
        rows.append([dot['name'], gap, skin])
    return rows


def main(argv):
    out = {Path(p).stem: regap(p) for p in argv[1:]}
    Path(argv[0]).write_text(json.dumps(out), encoding='utf-8')
    print('DOTS_REGAP_DONE', len(out), flush=True)


main(sys.argv[sys.argv.index('--') + 1:])
