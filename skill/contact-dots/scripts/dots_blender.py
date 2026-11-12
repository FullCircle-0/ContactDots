"""Contact dots measurer. Runs INSIDE Blender (the host script dots.py launches it):
    blender -b --factory-startup --python-exit-code 1 -P dots_blender.py -- <config.json>
    blender -b --factory-startup --python-exit-code 1 -P dots_blender.py -- --selftest <out folder>

Measures only; it never changes a source file and writes only into the config's output folder. Every length inside is
metres (Blender units); every number written out is mm (lengths) or degrees (angles).

Config (json; relative paths are relative to the config file):
  out          output folder (dots.json is written there)
  characters   [{"role", "file": FBX or .blend, "armature" (optional name), "bodyMesh" (optional name or part of it),
                 "boneMap": {canonical bone: rig bone} (optional)}]; any mesh of a character file that is not skinned to
                its armature is a prop surface
  props        [{"file"}] extra prop files (FBX or .blend); every mesh in them is a prop surface 'prop:<object name>'
  frames       [n, ...] | {"range": [first, last]} | "all" (default): plain scene frame numbers
  calibFrame   frame where skin radii are taken (default: the first measured frame)
  contacts     [{"id", "a": "<role>.<dot or group>", "b": surface, "frames": [...] (default all measured)}]
  floorZ       floor height in metres (default 0); null = no floor surface
  signRangeM   a dot centre is trusted to be inside a surface only within this distance of it (default 0.03)
  observeMm    surfaces nearer than this are listed as observed contacts (default 30)
  joints       canonical bone names whose world positions are written per frame
  closeups     how many worst penetrations get dot positions for the close-up sheet (default 6)
  snapshot     {"frame", "dots", "surface", "path", "cropM"}: mesh snapshot around those dots (reference tests)

Dots per character: <S>.<finger>.tip / .mid, <S>.palm (snapped to the palm skin), <S>.forearm|upperarm|thigh|shin.1-3,
<S>.knee, <S>.heel (snapped), <S>.ball, <S>.toe, pelvis.front / pelvis.back (snapped); S is R or L. Groups: <S>.hand,
<S>.foot, <S>.forearm, <S>.upperarm, <S>.thigh, <S>.shin, pelvis. A missing bone drops only its dots (warning).
Skin radius per dot = distance to the dot's own skin at the calibration frame (snapped dots: 0), so
gap = distance(dot centre, surface) - skin radius; negative = inside.

Surfaces: '<role>' (whole body; for a dot on its own body the dot's part and its chain neighbours are excluded),
'<role>.<canonical bone>' (one region by dominant bone weight), 'self', 'self.<bone>', 'prop:<name>', 'floor', 'nearest'.
Prints DOTS_DONE <json> at the end.
"""
import json
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bone_maps  # noqa: E402

FINGERS = ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky')
FINGERS_LOWER = tuple(f.lower() for f in FINGERS)
SIDES = {'R': 'Right', 'L': 'Left'}
LIMBS = {'forearm': ('ForeArm', 'Hand'), 'upperarm': ('Arm', 'ForeArm'), 'thigh': ('UpLeg', 'Leg'), 'shin': ('Leg', 'Foot')}
LIMB_T = (0.25, 0.5, 0.75)
PALM_SIGN = {'R': 1.0, 'L': -1.0}          # (index-pinky) x (middle-wrist) points out of the right palm, the back of the left
SIGN_RANGE = 0.03
OBSERVE_MM = 30.0
SNAPPED = ('palm', 'heel', 'front', 'back')


# ---------------------------------------------------------------- geometry helpers

def bvh_from(co, tris):
    return BVHTree.FromPolygons([tuple(c) for c in co], [tuple(int(i) for i in t) for t in tris])


def nearest(trees, p):
    """trees: [(label, BVHTree)]. Returns (loc, normal, label, distance) of the nearest surface point, or None."""
    best = None
    for label, tree in trees:
        loc, nrm, _, d = tree.find_nearest(p)
        if loc is not None and (best is None or d < best[3]):
            best = (loc, nrm, label, d)
    return best


def signed(hit, p, sign_range=SIGN_RANGE):
    """Signed distance (m): negative when p is behind the nearest face. Only trusted near the surface (open regions
    give false insides far away)."""
    if hit is None:
        return None
    loc, nrm, _, d = hit
    return -d if (d < sign_range and (p - loc).dot(nrm) < 0) else d


def ray(trees, origin, direction, max_dist):
    best = None
    for label, tree in trees:
        loc, nrm, _, d = tree.ray_cast(origin, direction, max_dist)
        if loc is not None and (best is None or d < best[3]):
            best = (loc, nrm, label, d)
    return best


def v3(p):
    return [round(float(x), 5) for x in p]


# ---------------------------------------------------------------- characters

def is_skinned_to(o, arm):
    return o.type == 'MESH' and any(m.type == 'ARMATURE' and m.object == arm for m in o.modifiers)


def is_skinned(o):
    return o.type == 'MESH' and any(m.type == 'ARMATURE' and m.object is not None for m in o.modifiers)


class Character:
    def __init__(self, role, objects, body=None, bone_map=None, armature=None):
        self.role = role
        arms = [o for o in objects if o.type == 'ARMATURE']
        if armature:
            arms = [o for o in arms if o.name == armature] or [o for o in arms if armature in o.name]
        if not arms:
            raise RuntimeError('%s: no armature%s in the file' % (role, ' named %r' % armature if armature else ''))
        scored = [(bone_maps.propose([b.name for b in a.data.bones], bone_map), a) for a in arms]
        self.proposal, self.arm = max(scored, key=lambda s: s[0]['matches'])
        if not self.proposal['usable']:
            raise RuntimeError(bone_maps.no_map_message(role, self.proposal, [b.name for b in self.arm.data.bones]))
        self.rig_name = dict(self.proposal['map'])                                                       # canonical -> rig
        back = {r: c for c, r in self.rig_name.items()}
        self.names = {b.name: back.get(b.name, b.name) for b in self.arm.data.bones}                     # rig -> canonical
        self.parent = {self.names[b.name]: (self.names[b.parent.name] if b.parent else None) for b in self.arm.data.bones}
        meshes = [o for o in objects if is_skinned_to(o, self.arm)]
        if body:
            named = [o for o in meshes if o.name == body] or [o for o in meshes if body in o.name]
            if not named:
                raise RuntimeError('%s: body mesh %r not found (meshes: %s)' % (role, body, [o.name for o in meshes]))
            self.body = named[0]
        else:
            if not meshes:
                raise RuntimeError('%s: no skinned mesh on the armature' % role)
            self.body = max(meshes, key=lambda o: ('body' in o.name.lower(), len(o.vertex_groups), len(o.data.vertices)))
        self.regions = None
        self.trees = {}
        self.co = None
        self.dropped = {}

    def bone(self, name):
        return self.arm.pose.bones[self.rig_name[name]]

    def has(self, name):
        return name in self.rig_name

    def head(self, name):
        return self.arm.matrix_world @ self.bone(name).head

    def tail(self, name):
        return self.arm.matrix_world @ self.bone(name).tail

    # -- regions: faces grouped by their vertices' dominant bone, fixed topology
    def _evaluated(self):
        dg = bpy.context.evaluated_depsgraph_get()
        ev = self.body.evaluated_get(dg)
        return ev, ev.to_mesh()

    def build_regions(self):
        ev, me = self._evaluated()
        try:
            me.calc_loop_triangles()
            groups = {g.index: g.name for g in self.body.vertex_groups}
            dom = []
            for v in me.vertices:
                g = max(v.groups, key=lambda x: x.weight) if len(v.groups) else None
                name = groups.get(g.group, '') if g is not None else ''
                dom.append(self.names.get(name, name))
            tris = np.empty(len(me.loop_triangles) * 3, np.int64)
            me.loop_triangles.foreach_get('vertices', tris); tris = tris.reshape(-1, 3)
            self.nverts = len(me.vertices)
        finally:
            ev.to_mesh_clear()
        dom = np.array(dom, dtype=object)
        owner = []
        for a, b, c in tris:                                    # a face belongs to the bone most of its corners follow
            da, db, dc = dom[a], dom[b], dom[c]
            owner.append(da if (da == db or da == dc) else db if db == dc else da)
        owner = np.array(owner, dtype=object)
        self.regions = {}
        for bone in sorted(set(owner) - {''}):
            t = tris[owner == bone]
            idx, inv = np.unique(t, return_inverse=True)
            self.regions[bone] = (idx, inv.reshape(-1, 3))
        self.tris = tris

    def update(self):
        """World vertex positions and one BVH per region for the current frame."""
        ev, me = self._evaluated()
        try:
            if len(me.vertices) != self.nverts:
                raise RuntimeError('%s: evaluated mesh changed vertex count (%d -> %d)' % (self.role, self.nverts, len(me.vertices)))
            co = np.empty(len(me.vertices) * 3, np.float64); me.vertices.foreach_get('co', co)
            m = np.array(ev.matrix_world, dtype=np.float64)
        finally:
            ev.to_mesh_clear()
        co = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        self.co = co
        self.trees = {bone: BVHTree.FromPolygons(co[idx].tolist(), t.tolist()) for bone, (idx, t) in self.regions.items()}

    def region_trees(self, include=None, exclude=()):
        return [('%s.%s' % (self.role, b), t) for b, t in self.trees.items()
                if (include is None or b in include) and b not in exclude]

    # -- dot sets
    def hand_bones(self, s):
        return {n for n in self.rig_name if n.startswith(SIDES[s] + 'Hand')}

    def chain(self, bones, up=2, down=2):
        out = set(bones)
        for b in bones:
            p = b
            for _ in range(up):
                p = self.parent.get(p)
                if p:
                    out.add(p)
        level = set(bones)
        for _ in range(down):
            level = {c for c, p in self.parent.items() if p in level}
            out |= level
        return out

    def part(self, dot):
        """Bones whose skin is the dot's own skin."""
        if dot.startswith('pelvis'):
            return {'Hips'}
        s, kind = dot.split('.')[0], dot.split('.')[1]
        side = SIDES[s]
        if kind in FINGERS_LOWER or kind == 'palm':
            return self.hand_bones(s)
        if kind in LIMBS:
            return {side + LIMBS[kind][0]}
        if kind == 'knee':
            return {n for n in (side + 'UpLeg', side + 'Leg') if self.has(n)}
        return {n for n in (side + 'Foot', side + 'ToeBase', side + 'Toe_End') if self.has(n)}

    def _need(self, dot, *bones):
        missing = [b for b in bones if not self.has(b)]
        if missing:
            self.dropped[dot] = missing[0]
        return not missing

    def dots(self):
        """{dot: Vector} for the current frame (snapped dots snapped onto this frame's mesh). Dots whose bones are
        missing are left out and listed in self.dropped."""
        D = {}
        for s, side in SIDES.items():
            for f in FINGERS:
                b4, b3, b2 = ('%sHand%s%d' % (side, f, i) for i in (4, 3, 2))
                if self.has(b4):
                    D['%s.%s.tip' % (s, f.lower())] = self.head(b4)
                elif self._need('%s.%s.tip' % (s, f.lower()), b3):
                    D['%s.%s.tip' % (s, f.lower())] = self.tail(b3)          # rigs without an end bone
                if self._need('%s.%s.mid' % (s, f.lower()), b2):
                    D['%s.%s.mid' % (s, f.lower())] = self.head(b2)
            mcp_names = ['%sHand%s1' % (side, f) for f in FINGERS[1:]]
            if self._need(s + '.palm', side + 'Hand', *mcp_names):
                mcp = [self.head(n) for n in mcp_names]
                wrist = self.head(side + 'Hand')
                c = sum(mcp, Vector()) / 4 * 0.6 + wrist * 0.4
                n = (mcp[0] - mcp[3]).cross(mcp[1] - wrist).normalized() * PALM_SIGN[s]
                hit = ray(self.region_trees(self.hand_bones(s)), c, n, 0.06)
                D[s + '.palm'] = hit[0] if hit else c + n * 0.012
            for limb, (a, b) in LIMBS.items():
                if self._need('%s.%s' % (s, limb), side + a, side + b):
                    pa, pb = self.head(side + a), self.head(side + b)
                    for i, t in enumerate(LIMB_T, 1):
                        D['%s.%s.%d' % (s, limb, i)] = pa.lerp(pb, t)
            if self._need(s + '.knee', side + 'Leg'):
                D[s + '.knee'] = self.head(side + 'Leg')
            if self._need(s + '.heel', side + 'Foot', side + 'ToeBase', side + 'Leg'):
                ankle, knee, ball = self.head(side + 'Foot'), self.head(side + 'Leg'), self.head(side + 'ToeBase')
                back = ((ankle - ball).normalized() + (ankle - knee).normalized()).normalized()
                hit = ray(self.region_trees(self.part(s + '.heel')), ankle, back, 0.2)
                D[s + '.heel'] = hit[0] if hit else ankle
            if self._need(s + '.ball', side + 'ToeBase'):
                D[s + '.ball'] = self.head(side + 'ToeBase')
                D[s + '.toe'] = self.head(side + 'Toe_End') if self.has(side + 'Toe_End') else self.tail(side + 'ToeBase')
        if self._need('pelvis', 'Hips', 'LeftUpLeg', 'RightUpLeg'):
            hips = self.head('Hips')
            left = self.head('LeftUpLeg') - self.head('RightUpLeg')
            up = (self.head('Spine') - hips) if self.has('Spine') else Vector((0, 0, 1))
            forward = left.cross(up).normalized()
            around = self.region_trees({'Hips', 'Spine', 'LeftUpLeg', 'RightUpLeg'})
            for name, d in (('pelvis.front', forward), ('pelvis.back', -forward)):
                hit = ray(around, hips, d, 0.4)
                D[name] = hit[0] if hit else hips + d * 0.1
        return D

    def root(self):
        return self.head('Hips') if self.has('Hips') else self.arm.matrix_world.translation


def is_snapped(dot):
    return dot.split('.')[-1] in SNAPPED


def group_members(dots, group):
    """'R.hand' -> the hand's dots; 'R.forearm' -> its 3; 'pelvis' -> front and back; a plain dot -> itself."""
    if group in dots:
        return [group]
    if group == 'pelvis':
        return [d for d in dots if d.startswith('pelvis.')]
    s, _, kind = group.partition('.')
    if kind == 'hand':
        return [d for d in dots if d.startswith(s + '.') and (d.split('.')[1] in FINGERS_LOWER or d.endswith('.palm'))]
    if kind == 'foot':
        return [s + '.' + n for n in ('heel', 'ball', 'toe') if s + '.' + n in dots]
    if kind in LIMBS:
        return [d for d in dots if d.startswith('%s.%s.' % (s, kind))]
    return []


# ---------------------------------------------------------------- scene

def load_file(path):
    """Bring every object of an FBX or .blend into the scene (read only). Returns the new objects."""
    path = Path(path)
    if not path.exists():
        raise RuntimeError('no file %s' % path)
    before = set(bpy.data.objects)
    ext = path.suffix.lower()
    if ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif ext == '.blend':
        with bpy.data.libraries.load(str(path), link=False) as (src, dst):
            dst.objects = list(src.objects)
        for o in dst.objects:
            if o is not None and o.name not in bpy.context.scene.collection.objects:
                bpy.context.scene.collection.objects.link(o)
    else:
        raise RuntimeError('%s: unsupported file type (FBX or .blend)' % path.name)
    return [o for o in bpy.data.objects if o not in before]


def load_scene(cfg):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    chars, props = [], []
    for t in cfg.get('characters') or []:
        new = load_file(t['file'])
        ch = Character(t['role'], new, t.get('bodyMesh'), t.get('boneMap'), t.get('armature'))
        chars.append(ch)
        props += [o for o in new if o.type == 'MESH' and not is_skinned(o)]
    for p in cfg.get('props') or []:
        props += [o for o in load_file(p['file'] if isinstance(p, dict) else p) if o.type == 'MESH']
    return chars, props


def frame_list(spec, chars):
    if isinstance(spec, dict) and 'range' in spec:
        a, b = (int(x) for x in spec['range'])
        return list(range(a, b + 1))
    if isinstance(spec, (list, tuple)):
        return sorted({int(f) for f in spec})
    ranges = [c.arm.animation_data.action.frame_range for c in chars
              if c.arm.animation_data and c.arm.animation_data.action]
    if ranges:
        return list(range(int(min(r[0] for r in ranges)), int(max(r[1] for r in ranges)) + 1))
    sc = bpy.context.scene
    return list(range(sc.frame_start, sc.frame_end + 1))


def prop_trees(props):
    out = []
    dg = bpy.context.evaluated_depsgraph_get()
    for o in props:
        ev = o.evaluated_get(dg); me = ev.to_mesh()
        try:
            mw = ev.matrix_world
            out.append(('prop:' + o.name, BVHTree.FromPolygons([mw @ v.co for v in me.vertices], [tuple(p.vertices) for p in me.polygons])))
        finally:
            ev.to_mesh_clear()
    return out


class Scene:
    """All surfaces at the current frame and the gap rule."""

    def __init__(self, chars, props, floor_z, sign_range):
        self.chars = {c.role: c for c in chars}
        self.props = props
        self.floor_z = floor_z
        self.sign_range = sign_range
        self.skin = {}
        self.excl = {}
        self.prop_list = []
        self.dot_pos = {}

    def frame(self):
        for c in self.chars.values():
            c.update()
        self.prop_list = prop_trees(self.props)
        self.dot_pos = {}
        for role, c in self.chars.items():
            for d, p in c.dots().items():
                self.dot_pos['%s.%s' % (role, d)] = p

    def calibrate(self):
        for role, c in self.chars.items():
            for d, p in c.dots().items():
                full = '%s.%s' % (role, d)
                part = c.part(d)
                self.excl[full] = c.chain(part)
                if is_snapped(d):
                    self.skin[full] = 0.0
                else:
                    hit = nearest(c.region_trees(part), p)
                    self.skin[full] = hit[3] * 1000.0 if hit else 0.0

    def groups(self, full):
        """Surface groups a dot is measured against: name -> trees (or 'floor')."""
        role = full.split('.')[0]
        out = {}
        for r, c in self.chars.items():
            out[r] = c.region_trees(exclude=self.excl.get(full, ()) if r == role else ())
        for label, tree in self.prop_list:
            out[label] = [(label, tree)]
        if self.floor_z is not None:
            out['floor'] = 'floor'
        return out

    def gap(self, full, trees):
        """(gap mm, surface label, nearest point) of one dot against one surface group, or None."""
        p = self.dot_pos[full]
        if trees == 'floor':
            return (p.z - self.floor_z) * 1000.0 - self.skin[full], 'floor', Vector((p.x, p.y, self.floor_z))
        hit = nearest(trees, p)
        if hit is None:
            return None
        return signed(hit, p, self.sign_range) * 1000.0 - self.skin[full], hit[2], hit[0]

    def surface(self, b, full):
        """Trees for a surface spec, seen from dot `full`."""
        role = full.split('.')[0]
        if b == 'floor':
            if self.floor_z is None:
                raise RuntimeError('surface floor: the config has floorZ null')
            return 'floor'
        if b.startswith('prop:'):
            hit = [t for t in self.prop_list if t[0] == b]
            if not hit:
                raise RuntimeError('surface %r: no such prop (props: %s)' % (b, [t[0] for t in self.prop_list]))
            return hit
        who, _, bone = b.partition('.')
        if who == 'self':
            who = role
        if who not in self.chars:
            raise RuntimeError('surface %r: no character %r' % (b, who))
        c = self.chars[who]
        if bone:
            return c.region_trees({bone})
        return c.region_trees(exclude=self.excl.get(full, ()) if who == role else ())


def measure(cfg, chars=None, props=None):
    began = time.time()
    if chars is None:
        chars, props = load_scene(cfg)
    sc = bpy.context.scene
    contacts = cfg.get('contacts') or []
    frames = frame_list(cfg.get('frames', 'all'), chars)
    for c in contacts:
        c['frames'] = [int(f) for f in (c.get('frames') or frames)]
    frames = sorted(set(frames) | {f for c in contacts for f in c['frames']})
    if not frames:
        raise RuntimeError('no frames to measure')
    calib = int(cfg.get('calibFrame', frames[0]))
    floor = cfg.get('floorZ', 0.0)
    S = Scene(chars, props or [], None if floor is None else float(floor), float(cfg.get('signRangeM', SIGN_RANGE)))
    sc.frame_set(calib)
    for c in chars:
        c.build_regions(); c.update()
    S.calibrate()
    warnings = ['%s: no bone %s, dropped %s' % (c.role, bone, dot) for c in chars for dot, bone in sorted(c.dropped.items())]
    for w in warnings[:6]:
        print('DOTS_WARNING', w, flush=True)
    if len(warnings) > 6:
        print('DOTS_WARNING ... %d more dropped dots in dots.json' % (len(warnings) - 6), flush=True)
    observe = float(cfg.get('observeMm', OBSERVE_MM))
    per_contact = {c['id']: {} for c in contacts}
    pen, seen, closeup_at = {}, {}, {}
    roots = {c.role: {} for c in chars}
    joints = {c.role: {} for c in chars} if cfg.get('joints') else None
    snap = cfg.get('snapshot')
    for f in frames:
        sc.frame_set(f)
        S.frame()
        for c in chars:
            roots[c.role][str(f)] = v3(c.root())
            if joints is not None:
                joints[c.role][str(f)] = {j: v3(c.head(j)) for j in cfg['joints'] if c.has(j)}
        for full in S.dot_pos:
            for gname, trees in S.groups(full).items():
                g = S.gap(full, trees)
                if g is None:
                    continue
                mm, label, q = g
                key = (full, gname)
                if mm < 0 and (key not in pen or mm < pen[key]['mm']):
                    pen[key] = {'dot': full, 'surface': label, 'group': gname, 'mm': round(mm, 2), 'frame': f,
                                'p': v3(S.dot_pos[full]), 'q': v3(q)}
                if mm <= observe and (key not in seen or mm < seen[key]['mm']):
                    seen[key] = {'dot': full, 'surface': label, 'mm': round(mm, 2), 'frame': f}
        for c in contacts:
            if f not in c['frames']:
                continue
            role, _, grp = c['a'].partition('.')
            local = {d.partition('.')[2] for d in S.dot_pos if d.startswith(role + '.')}
            members = ['%s.%s' % (role, d) for d in group_members(local, grp)]
            if not members:
                raise RuntimeError('contact %s: no dots for %r' % (c['id'], c['a']))
            best = None
            for full in members:
                surfaces = S.groups(full).values() if c['b'] == 'nearest' else [S.surface(c['b'], full)]
                for trees in surfaces:
                    g = S.gap(full, trees)
                    if g and (best is None or g[0] < best[0]):
                        best = (g[0], full, g[1], g[2])
            if best:
                per_contact[c['id']][str(f)] = {'mm': round(best[0], 3), 'dot': best[1], 'surface': best[2],
                                               'p': v3(S.dot_pos[best[1]]), 'q': v3(best[3])}
        if snap and int(snap['frame']) == f:
            write_snapshot(S, snap)
        print('DOTS_FRAME', f, round(time.time() - began, 1), flush=True)
    pens = sorted(pen.values(), key=lambda r: r['mm'])
    want = {int(r['frame']) for r in pens[:int(cfg.get('closeups', 6))]}
    for c in contacts:
        rows = per_contact[c['id']]
        if rows:
            want.add(int(max(rows, key=lambda k: rows[k]['mm'])))             # the most-off frame
            want.add(int(min(rows, key=lambda k: rows[k]['mm'])))             # the deepest frame
    for f in sorted(want):
        sc.frame_set(f); S.frame()
        closeup_at[str(f)] = {k: v3(p) for k, p in S.dot_pos.items()}
    result = {'version': 1, 'blender': bpy.app.version_string, 'frames': frames, 'calibFrame': calib,
              'floorZ': S.floor_z, 'observeMm': observe,
              'roles': {c.role: {'armature': c.arm.name, 'body': c.body.name, 'bonePreset': c.proposal['preset'],
                                 'boneMatches': c.proposal['matches'], 'verts': c.nverts, 'regions': len(c.regions)}
                        for c in chars},
              'warnings': warnings, 'roots': roots,
              'skinMm': {k: round(v, 3) for k, v in S.skin.items()},
              'contacts': [dict(c, gaps=per_contact[c['id']]) for c in contacts],
              'penetrations': pens, 'observed': sorted(seen.values(), key=lambda r: r['mm']),
              'dotsAt': closeup_at, 'props': [o.name for o in props or []],
              'seconds': round(time.time() - began, 1)}
    if joints is not None:
        result['joints'] = joints
    out = Path(cfg['out']); out.mkdir(parents=True, exist_ok=True)
    (out / 'dots.json').write_text(json.dumps(result, indent=1), encoding='utf-8')
    print('DOTS_DONE', json.dumps({'seconds': result['seconds'], 'frames': len(frames), 'penetrations': len(pens)}), flush=True)
    return result


# ---------------------------------------------------------------- mesh snapshot (reference tests)

def _all_tris(c):
    """Every world triangle of a character (T,3,3) and its region (bone) per triangle, current frame."""
    tris, owner = [], []
    for bone, (idx, t) in c.regions.items():
        tris.append(c.co[idx][t]); owner += [bone] * len(t)
    return np.concatenate(tris), np.array(owner, dtype=object)


def _near(tris, owner, include, exclude, centre, crop):
    ok = np.array([(include is None or b in include) and b not in exclude for b in owner])
    ok &= (np.linalg.norm(tris - np.array(centre), axis=2) < crop).any(axis=1)
    return np.where(ok)[0]


def write_snapshot(S, snap):
    """Single-frame mesh snapshot around a few dots: what the brute-force reference needs, plus the tool's answer."""
    crop = float(snap.get('cropM', 0.15)); arrays = {}; meta = {'frame': int(snap['frame']), 'surface': snap['surface'], 'dots': []}
    b = snap['surface']; every = {r: _all_tris(c) for r, c in S.chars.items()}; used = {r: set() for r in S.chars}; picks = []
    for i, full in enumerate(snap['dots']):
        role, _, dot = full.partition('.')
        c = S.chars[role]; p = S.dot_pos[full]
        own = _near(*every[role], c.part(dot), (), p, crop)
        trees = S.surface(b, full)
        who = b.partition('.')[0]; who = role if who in ('self', 'floor') or who.startswith('prop:') else who
        if trees == 'floor':
            surf = np.zeros(0, np.int64)
        else:
            bone = b.partition('.')[2]
            surf = _near(*every[who], {bone} if bone else None, S.excl[full] if (who == role and not bone) else (), p, crop)
        used[role] |= set(own.tolist()); used[who] |= set(surf.tolist())
        picks.append((role, own, who, surf))
        mm, label, _ = S.gap(full, trees)
        arrays['dot%d' % i] = np.array(p, dtype=np.float64)
        meta['dots'].append({'name': full, 'snapped': is_snapped(dot), 'toolSkinMm': round(S.skin[full], 3),
                             'toolGapMm': round(mm, 3), 'toolSurface': label, 'ownRole': role, 'surfRole': who})
    keep = {r: np.array(sorted(u), dtype=np.int64) for r, u in used.items()}
    for r, k in keep.items():
        arrays['tris_%s' % r] = every[r][0][k].astype(np.float32)
    for i, (role, own, who, surf) in enumerate(picks):
        arrays['own%d' % i] = np.searchsorted(keep[role], own).astype(np.int32)
        arrays['surf%d' % i] = np.searchsorted(keep[who], surf).astype(np.int32)
    arrays['meta'] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
    arrays['signRangeM'] = np.array(S.sign_range)
    np.savez_compressed(snap['path'], **arrays)
    print('DOTS_SNAPSHOT', snap['path'], len(snap['dots']), flush=True)


# ---------------------------------------------------------------- selftest (Spec A1)

SELFTEST_GAPS = (0.0, 5.0, -3.0, -25.0)     # mm, one per frame 1..4 (-25: the dot centre 5 mm inside the cube)
TIP_RADIUS = 0.02
# canonical bone -> (head, tail, parent), armature space, metres. The index tip hangs lowest, at TIP_REST.
SELFTEST_BONES = (
    ('Hips', (0, 0, 1.0), (0, 0, 1.1), None),
    ('Spine', (0, 0, 1.1), (0, 0, 1.3), 'Hips'),
    ('RightArm', (-0.2, 0, 1.3), (-0.2, 0, 1.0), 'Spine'),
    ('RightForeArm', (-0.2, 0, 1.0), (-0.2, 0, 0.75), 'RightArm'),
    ('RightHand', (-0.2, 0, 0.75), (-0.2, 0, 0.70), 'RightForeArm'),
    ('RightHandIndex1', (-0.2, 0, 0.70), (-0.2, 0, 0.68), 'RightHand'),
    ('RightHandIndex2', (-0.2, 0, 0.68), (-0.2, 0, 0.66), 'RightHandIndex1'),
    ('RightHandIndex3', (-0.2, 0, 0.66), (-0.2, 0, 0.64), 'RightHandIndex2'),
    ('RightHandIndex4', (-0.2, 0, 0.64), (-0.2, 0, 0.62), 'RightHandIndex3'),
)
TIP_REST = Vector((-0.2, 0, 0.64))


def _mesh_object(name, make, location=(0, 0, 0)):
    import bmesh
    me = bpy.data.meshes.new(name); bm = bmesh.new(); make(bm); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(o)
    o.location = location
    return o


def _skin(obj, arm, bone, verts=None):
    g = obj.vertex_groups.get(bone) or obj.vertex_groups.new(name=bone)
    g.add(list(range(len(obj.data.vertices))) if verts is None else verts, 1.0, 'REPLACE')


def build_selftest_character(rig_names=None, name='Rig'):
    """A small skinned character: a hips box on Hips and a 20 mm sphere around the index tip on RightHandIndex4.
    rig_names maps canonical -> rig bone name (None: Mixamo names)."""
    import bmesh
    rig = dict(rig_names or {})
    arm_data = bpy.data.armatures.new(name)
    arm = bpy.data.objects.new(name, arm_data); bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    made = {}
    for canon_name, head, tail, parent in SELFTEST_BONES:
        rn = rig.get(canon_name, canon_name)
        if rn is None:
            continue
        eb = arm_data.edit_bones.new(rn); eb.head, eb.tail = head, tail
        if parent and parent in made:
            eb.parent = made[parent]
        made[canon_name] = eb
    bpy.ops.object.mode_set(mode='OBJECT')
    body_parts = []
    hips = _mesh_object(name + '_hips', lambda bm: bmesh.ops.create_cube(bm, size=0.25), (0, 0, 1.05))
    _skin(hips, arm, rig.get('Hips', 'Hips')); body_parts.append(hips)
    tip_bone = rig.get('RightHandIndex4', 'RightHandIndex4') or rig.get('RightHandIndex3', 'RightHandIndex3')
    tip = _mesh_object(name + '_tip', lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=192, v_segments=96, radius=TIP_RADIUS), TIP_REST)
    _skin(tip, arm, tip_bone); body_parts.append(tip)
    # one body mesh
    bpy.ops.object.select_all(action='DESELECT')
    for o in body_parts:
        o.select_set(True)
    bpy.context.view_layer.objects.active = hips
    bpy.ops.object.join()
    body = hips; body.name = name + '_Body'
    mod = body.modifiers.new('Armature', 'ARMATURE'); mod.object = arm
    body.parent = arm
    return arm, body


def selftest_config(out):
    return {'out': str(out), 'frames': [1, 2, 3, 4], 'calibFrame': 1, 'floorZ': 0.1,
            'contacts': [{'id': 'tip-on-cube', 'a': 'rig.R.index.tip', 'b': 'prop:Cube'},
                         {'id': 'tip-on-floor', 'a': 'rig.R.index.tip', 'b': 'floor'}]}


def build_selftest_scene(rig_names=None):
    """Factory scene: the character over a 20 cm cube (top at z 0.1); the index tip's skin is 0, +5, -3, -25 mm from
    the cube top on frames 1-4 (the floor at z 0.1 gives the same numbers)."""
    import bmesh
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm, body = build_selftest_character(rig_names)
    cube = _mesh_object('Cube', lambda bm: bmesh.ops.create_cube(bm, size=0.2))
    target = Vector((0.01, -0.02, 0.0))
    for i, want in enumerate(SELFTEST_GAPS, 1):
        tip = Vector((target.x, target.y, 0.1 + TIP_RADIUS + want / 1000.0))
        arm.location = tip - TIP_REST
        arm.keyframe_insert('location', frame=i)
    sc = bpy.context.scene; sc.frame_start, sc.frame_end = 1, len(SELFTEST_GAPS)
    return arm, body, cube


def selftest_rig(kind):
    """Bone names of the selftest character: mixamo (prefixed), unity (no finger end bone) or unknown."""
    names = [b[0] for b in SELFTEST_BONES]
    if kind == 'mixamo':
        return {n: 'mixamorig:' + n for n in names}
    if kind == 'unity':
        rig = {n: bone_maps.PRESETS['unity'].get(n) for n in names}
        return rig
    if kind == 'unknown':
        return {n: 'bone_%03d' % i for i, n in enumerate(names)}
    return None


def selftest(out, rig_names=None, bone_map=None):
    arm, body, cube = build_selftest_scene(rig_names)
    cfg = selftest_config(out)
    chars = [Character('rig', [arm, body], bone_map=bone_map)]
    result = measure(cfg, chars, [cube])
    ok, rows = True, []
    for c in result['contacts']:
        for i, want in enumerate(SELFTEST_GAPS, 1):
            got = c['gaps'].get(str(i), {}).get('mm')
            good = got is not None and abs(got - want) <= 0.1
            ok &= good
            rows.append('%s f%d want %+6.2f got %s %s' % (c['id'], i, want, 'none' if got is None else '%+7.3f' % got,
                                                       'ok' if good else 'OFF'))
    skin = result['skinMm'].get('rig.R.index.tip')
    rows.append('skin rig.R.index.tip %.3f mm (sphere %.1f mm)' % (skin or 0, TIP_RADIUS * 1000))
    for row in rows:
        print('DOTS_SELFTEST', row)
    print('DOTS_SELFTEST', 'PASS' if ok else 'FAIL', flush=True)
    return ok


def bones(path):
    """Every armature in a file, its bones and the proposed map (one DOTS_BONES json line)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    new = load_file(path)
    out = []
    for a in [o for o in new if o.type == 'ARMATURE']:
        names = [b.name for b in a.data.bones]
        p = bone_maps.propose(names)
        out.append({'armature': a.name, 'bones': names, 'preset': p['preset'], 'usable': p['usable'],
                    'scores': p['scores'], 'map': p['map'], 'unmatched': p['unmatched'],
                    'skinnedMeshes': [o.name for o in new if is_skinned_to(o, a)]})
    print('DOTS_BONES', json.dumps(out), flush=True)
    return 0


def main(argv):
    if argv and argv[0] == '--selftest':
        out = Path(argv[1]) if len(argv) > 1 else Path(bpy.app.tempdir) / 'dots-selftest'
        rig = argv[argv.index('--rig') + 1] if '--rig' in argv else 'plain'
        return 0 if selftest(out, selftest_rig(rig)) else 1
    if argv and argv[0] == '--make-sample':                 # the selftest scene saved as a .blend (tests, examples)
        rig = argv[argv.index('--rig') + 1] if '--rig' in argv else 'plain'
        build_selftest_scene(selftest_rig(rig))
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(argv[1]).resolve()), check_existing=False)
        print('DOTS_SAMPLE', argv[1], flush=True)
        return 0
    if argv and argv[0] == '--bones':
        return bones(argv[1])
    path = Path(argv[0])
    cfg = json.loads(path.read_text(encoding='utf-8'))
    measure(cfg)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []))
