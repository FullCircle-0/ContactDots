"""Independent brute-force reference for the contact dots gaps (Spec A2). Shares no code with
skill/contact-dots/scripts/dots_blender.py: plain numpy, every triangle tested, no BVH.

gap = signed distance(dot centre, surface triangles) - skin radius, mm
  skin radius = distance(dot centre, its own skin triangles) (0 for a dot snapped onto its skin)
  signed: negative when the centre is behind the nearest triangle's face (winding normal), only within sign_range metres
"""
import json

import numpy as np


def closest_points(p, tris):
    """Closest point on each triangle (T,3,3) to point p (3,). Ericson, Real-Time Collision Detection 5.1.5, vectorised."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = np.einsum('ij,ij->i', ab, ap), np.einsum('ij,ij->i', ac, ap)
    bp = p - b
    d3, d4 = np.einsum('ij,ij->i', ab, bp), np.einsum('ij,ij->i', ac, bp)
    cp = p - c
    d5, d6 = np.einsum('ij,ij->i', ab, cp), np.einsum('ij,ij->i', ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    out = np.empty_like(a)
    done = np.zeros(len(a), bool)

    def put(mask, value):
        nonlocal done
        m = mask & ~done
        out[m] = value[m]
        done |= m

    put((d1 <= 0) & (d2 <= 0), a)                                                     # vertex a
    put((d3 >= 0) & (d4 <= d3), b)                                                    # vertex b
    with np.errstate(divide='ignore', invalid='ignore'):
        v = d1 / (d1 - d3)
        put((vc <= 0) & (d1 >= 0) & (d3 <= 0), a + v[:, None] * ab)                  # edge ab
        put((d6 >= 0) & (d5 <= d6), c)                                                # vertex c
        w = d2 / (d2 - d6)
        put((vb <= 0) & (d2 >= 0) & (d6 <= 0), a + w[:, None] * ac)                  # edge ac
        w2 = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        put((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), b + w2[:, None] * (c - b))  # edge bc
        denom = 1.0 / (va + vb + vc)
        v, w = vb * denom, vc * denom
        put(np.ones(len(a), bool), a + ab * v[:, None] + ac * w[:, None])             # inside the face
    return out


def nearest(p, tris):
    """(distance, closest point, triangle index); (inf, None, -1) without triangles."""
    if len(tris) == 0:
        return float('inf'), None, -1
    q = closest_points(p, tris)
    d = np.linalg.norm(q - p, axis=1)
    d = np.where(np.isfinite(d), d, np.inf)                                           # degenerate triangles
    i = int(np.argmin(d))
    return float(d[i]), q[i], i


def signed(p, tris, sign_range):
    d, q, i = nearest(p, tris)
    if q is None:
        return d
    t = tris[i]
    n = np.cross(t[1] - t[0], t[2] - t[0])
    return -d if (d < sign_range and np.dot(p - q, n) < 0) else d


def sample_gaps(npz_path):
    """[(dot name, reference gap mm, tool gap mm, reference skin mm, tool skin mm)] for one fixture file."""
    z = np.load(npz_path)
    meta = json.loads(bytes(z['meta']).decode())
    sign_range = float(z['signRangeM'])
    rows = []
    if meta['surface'] == 'floor':
        raise ValueError('floor samples are not brute-force material')
    tris = {k[5:]: z[k].astype(np.float64) for k in z.files if k.startswith('tris_')}
    for i, dot in enumerate(meta['dots']):
        p = z['dot%d' % i]
        own = tris[dot['ownRole']][z['own%d' % i]]
        surf = tris[dot['surfRole']][z['surf%d' % i]]
        skin = 0.0 if dot['snapped'] else nearest(p, own)[0] * 1000.0
        gap = signed(p, surf, sign_range) * 1000.0 - skin
        rows.append((dot['name'], gap, dot['toolGapMm'], skin, dot['toolSkinMm']))
    return meta, rows
