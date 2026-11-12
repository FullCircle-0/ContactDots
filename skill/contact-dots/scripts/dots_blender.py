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
import math
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector
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

    def mcp_names(self, s):
        return ['%sHand%s1' % (SIDES[s], f) for f in FINGERS[1:]]

    def palm(self, s):
        """(palm centre inside the hand, unit normal out of the palm) for side R or L."""
        mcp = [self.head(n) for n in self.mcp_names(s)]
        wrist = self.head(SIDES[s] + 'Hand')
        c = sum(mcp, Vector()) / 4 * 0.6 + wrist * 0.4
        return c, (mcp[0] - mcp[3]).cross(mcp[1] - wrist).normalized() * PALM_SIGN[s]

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
            if self._need(s + '.palm', side + 'Hand', *self.mcp_names(s)):
                c, n = self.palm(s)
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
    animated = [c.arm for c in chars] or list(bpy.context.scene.objects)
    ranges = [o.animation_data.action.frame_range for o in animated if o.animation_data and o.animation_data.action]
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


AXES = {'x': 0, 'y': 1, 'z': 2}


def axis_of(matrix, axis):
    """Unit world vector of a local axis 'x', '-y', ... of a 3x3/4x4 matrix."""
    axis = str(axis or 'y').lower()
    if axis.lstrip('+-') not in AXES:
        raise RuntimeError('axis %r: use x, y, z, -x, -y or -z' % axis)
    v = matrix.to_3x3().col[AXES[axis.lstrip('+-')]].normalized()
    return -v if axis.startswith('-') else v


def find_object(name):
    o = bpy.data.objects.get(name)
    if o is None:
        raise RuntimeError('no object %r in the scene (objects: %s)' % (name, sorted(x.name for x in bpy.data.objects)[:30]))
    return o


def evaluated_coords(obj):
    """World vertex positions of an object's evaluated mesh, (N,3) numpy."""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg); me = ev.to_mesh()
    try:
        co = np.empty(len(me.vertices) * 3, np.float64); me.vertices.foreach_get('co', co)
        m = np.array(ev.matrix_world, dtype=np.float64)
    finally:
        ev.to_mesh_clear()
    return co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]


class CustomDot:
    """A dot on anything (B6a): a bone + offset, an object + local point, or a mesh region (vertex group, or the
    vertices a skinned prop's bone dominates) taken at its centre or (at: nearest) at its point nearest the surface."""

    def __init__(self, spec, chars):
        self.name = spec['name']; self.spec = spec
        self.penetration = spec.get('penetration', True)
        self.at = spec.get('at', 'centre')
        self.obj = self.arm = self.pbone = self.char = self.canon = None
        self.verts = None
        if spec.get('object'):
            self.obj = find_object(spec['object'])
            group = spec.get('group'); region = spec.get('regionBone')
            if group or region:
                if self.obj.type != 'MESH':
                    raise RuntimeError('dot %s: %s is not a mesh' % (self.name, self.obj.name))
                names = {g.index: g.name for g in self.obj.vertex_groups}
                pick = []
                for v in self.obj.data.vertices:
                    ws = {names[g.group]: g.weight for g in v.groups}
                    if group and ws.get(group, 0) >= 0.5:
                        pick.append(v.index)
                    elif region and ws and max(ws, key=ws.get) == region:
                        pick.append(v.index)
                if not pick:
                    raise RuntimeError('dot %s: no vertices in %s %r of %s' % (self.name, 'group' if group else 'regionBone',
                                                                                 group or region, self.obj.name))
                self.verts = np.array(pick)
        elif spec.get('bone'):
            if spec.get('armature'):
                self.arm = find_object(spec['armature'])
                if self.arm.type != 'ARMATURE' or spec['bone'] not in self.arm.pose.bones:
                    raise RuntimeError('dot %s: no bone %r on armature %r' % (self.name, spec['bone'], spec['armature']))
                self.pbone = self.arm.pose.bones[spec['bone']]
            else:
                role, _, canon_name = spec['bone'].partition('.')
                ch = next((c for c in chars if c.role == role), None)
                if ch is None or not ch.has(canon_name):
                    raise RuntimeError('dot %s: no bone %r (use <role>.<canonical bone>, or armature + bone)' % (self.name, spec['bone']))
                self.char, self.canon, self.arm, self.pbone = ch, canon_name, ch.arm, ch.bone(canon_name)
        else:
            raise RuntimeError('dot %s: needs "bone" or "object"' % self.name)
        self.offset = Vector(spec.get('offset') or spec.get('point') or (0, 0, 0))

    def region_world(self):
        return evaluated_coords(self.obj)[self.verts]

    def pos(self):
        if self.obj is not None:
            if self.verts is not None:
                return Vector(self.region_world().mean(axis=0).tolist())
            return self.obj.matrix_world @ self.offset
        return self.arm.matrix_world @ self.pbone.matrix @ self.offset

    def nearest_to(self, S, trees):
        """Region dot, at: nearest: the region vertex with the smallest gap to `trees` (or None for other dots)."""
        if self.verts is None or self.at != 'nearest':
            return None
        best = None
        for p in self.region_world():
            p = Vector(p.tolist())
            if trees == 'floor':
                g = (p.z - S.floor_z) * 1000.0
                hit = (g, 'floor', Vector((p.x, p.y, S.floor_z)), p)
            else:
                h = nearest(trees, p)
                if h is None:
                    continue
                hit = (signed(h, p, S.sign_range) * 1000.0, h[2], h[0], p)
            if best is None or hit[0] < best[0]:
                best = hit
        return best

    def own_tree(self):
        if self.obj is not None and self.obj.type == 'MESH':
            co = evaluated_coords(self.obj)
            return [('own', BVHTree.FromPolygons(co.tolist(), [tuple(p.vertices) for p in self.obj.data.polygons]))]
        if self.char is not None:
            return self.char.region_trees({self.canon})
        return []


class Scene:
    """All surfaces at the current frame and the gap rule."""

    def __init__(self, chars, props, floor_z, sign_range, custom=()):
        self.chars = {c.role: c for c in chars}
        self.props = props
        self.floor_z = floor_z
        self.sign_range = sign_range
        self.custom = {d.name: d for d in custom}
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
        for name, d in self.custom.items():
            self.dot_pos[name] = d.pos()

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
        for name, d in self.custom.items():
            if d.char is not None:
                self.excl[name] = d.char.chain({d.canon})
            r = d.spec.get('radius', 0)
            if r == 'measure':
                hit = nearest(d.own_tree(), d.pos())
                if hit is None:
                    raise RuntimeError('dot %s: radius "measure" needs a mesh of its own' % name)
                self.skin[name] = hit[3] * 1000.0
            else:
                self.skin[name] = float(r)

    def role_of(self, full):
        d = self.custom.get(full)
        if d is not None:
            return d.char.role if d.char is not None else None
        return full.split('.')[0]

    def groups(self, full):
        """Surface groups a dot is measured against: name -> trees (or 'floor')."""
        role = self.role_of(full)
        d = self.custom.get(full)
        own_prop = 'prop:' + d.obj.name if d is not None and d.obj is not None else None
        out = {}
        for r, c in self.chars.items():
            out[r] = c.region_trees(exclude=self.excl.get(full, ()) if r == role else ())
        for label, tree in self.prop_list:
            if label != own_prop:
                out[label] = [(label, tree)]
        if self.floor_z is not None:
            out['floor'] = 'floor'
        return out

    def gap(self, full, trees):
        """(gap mm, surface label, nearest surface point, dot point) of one dot against one surface group, or None."""
        p = self.dot_pos[full]
        d = self.custom.get(full)
        if d is not None and d.at == 'nearest' and d.verts is not None:
            hit = d.nearest_to(self, trees)
            return None if hit is None else (hit[0] - self.skin[full], hit[1], hit[2], hit[3])
        if trees == 'floor':
            return (p.z - self.floor_z) * 1000.0 - self.skin[full], 'floor', Vector((p.x, p.y, self.floor_z)), p
        hit = nearest(trees, p)
        if hit is None:
            return None
        return signed(hit, p, self.sign_range) * 1000.0 - self.skin[full], hit[2], hit[0], p

    def surface(self, b, full):
        """Trees for a surface spec, seen from dot `full`."""
        role = self.role_of(full)
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
            if role is None:
                raise RuntimeError('surface %r: dot %s is not on a character' % (b, full))
            who = role
        if who not in self.chars:
            raise RuntimeError('surface %r: no character %r' % (b, who))
        c = self.chars[who]
        if bone:
            return c.region_trees({bone})
        return c.region_trees(exclude=self.excl.get(full, ()) if who == role else ())


# ---------------------------------------------------------------- structural and rotational checks (B6b)

PEN_EPS_MM = 0.005
UNITS = {'seat': 'mm', 'rest': 'mm', 'hold': 'mm', 'distance': 'mm', 'align': 'deg', 'range': 'deg'}
WORLD = {'x': Vector((1, 0, 0)), 'y': Vector((0, 1, 0)), 'z': Vector((0, 0, 1))}


def _bone_of(spec, S):
    """(armature object, pose bone) from {"armature", "bone"} or {"bone": "<role>.<canonical>"}."""
    if spec.get('armature'):
        arm = find_object(spec['armature'])
        if arm.type != 'ARMATURE' or spec['bone'] not in arm.pose.bones:
            raise RuntimeError('no bone %r on armature %r' % (spec['bone'], spec['armature']))
        return arm, arm.pose.bones[spec['bone']]
    role, _, canon_name = spec['bone'].partition('.')
    c = S.chars.get(role)
    if c is None or not c.has(canon_name):
        raise RuntimeError('no bone %r (use <role>.<canonical bone>, or armature + bone)' % spec['bone'])
    return c.arm, c.bone(canon_name)


def _dot(S, name):
    if name not in S.dot_pos:
        raise RuntimeError('no dot %r (custom dots: %s)' % (name, sorted(S.custom)[:20]))
    return S.dot_pos[name]


def _world_axis(axis):
    axis = str(axis).lower()
    v = WORLD.get(axis.lstrip('+-'))
    if v is None:
        raise RuntimeError('world axis %r: use x, y, z, -x, -y or -z' % axis)
    return -v if axis.startswith('-') else v.copy()


def direction(spec, S):
    """A direction (B6b) -> (unit world vector, origin). Kinds: {"bone"[, "armature"], "axis"}, {"object", "axis"},
    {"from", "to"} dots, {"normal": dot, "surface"} (the face normal under the dot; floor is +z), {"palm": "<role>.R|L"},
    {"world": "z"}; a plain string is a world axis."""
    if isinstance(spec, str):
        spec = {'world': spec}
    if 'world' in spec:
        return _world_axis(spec['world']), Vector()
    if 'from' in spec:
        a, b = _dot(S, spec['from']), _dot(S, spec['to'])
        if (b - a).length < 1e-9:
            raise RuntimeError('direction from %s to %s: the dots coincide' % (spec['from'], spec['to']))
        return (b - a).normalized(), a
    if 'normal' in spec:
        p = _dot(S, spec['normal'])
        trees = S.surface(spec.get('surface', 'floor'), spec['normal'])
        if trees == 'floor':
            return Vector((0, 0, 1)), Vector((p.x, p.y, S.floor_z))
        hit = nearest(trees, p)
        if hit is None:
            raise RuntimeError('direction normal under %s: no surface %r' % (spec['normal'], spec.get('surface')))
        return hit[1].normalized(), hit[0]
    if 'palm' in spec:
        role, _, side = spec['palm'].partition('.')
        c = S.chars.get(role)
        if c is None or side not in SIDES:
            raise RuntimeError('direction palm %r: use <role>.R or <role>.L' % spec['palm'])
        centre, n = c.palm(side)
        return n, centre
    if 'bone' in spec:
        arm, pb = _bone_of(spec, S)
        m = arm.matrix_world @ pb.matrix
        return axis_of(m, spec.get('axis', 'y')), m.translation
    if 'object' in spec:
        o = find_object(spec['object'])
        return axis_of(o.matrix_world, spec.get('axis', 'z')), o.matrix_world.translation
    raise RuntimeError('direction %r: give world, from/to, normal, palm, bone or object' % (spec,))


def ref_matrix(spec, S):
    """World matrix (scale removed) of a hold's frame of reference: "world", an object name, "<role>.<bone>",
    or {"armature", "bone"} / {"object"}."""
    if spec in (None, 'world'):
        return Matrix.Identity(4)
    if isinstance(spec, str):
        if '.' in spec and spec.partition('.')[0] in S.chars:
            spec = {'bone': spec}
        else:
            spec = {'object': spec}
    if 'bone' in spec:
        arm, pb = _bone_of(spec, S)
        m = arm.matrix_world @ pb.matrix
    else:
        m = find_object(spec['object']).matrix_world
    loc, rot, _ = m.decompose()
    return Matrix.Translation(loc) @ rot.to_matrix().to_4x4()


def _angle(a, b):
    return math.degrees(a.angle(b)) if a.length > 1e-12 and b.length > 1e-12 else 0.0


def eval_check(ch, S, hold_ref):
    """One check at the current frame -> {'v': number, 'p', 'q' (points for the sheet), ...extras}."""
    kind = ch['kind']
    if kind in ('seat', 'distance'):
        p, q = _dot(S, ch['a']), _dot(S, ch['b'])
        return {'v': round((p - q).length * 1000.0, 3), 'p': v3(p), 'q': v3(q)}
    if kind == 'rest':
        gaps, worst = {}, None
        for name in ch['a']:
            g = S.gap(name, S.surface(ch['b'], name))
            if g is None:
                raise RuntimeError('check %s: dot %s has no surface %r' % (ch['id'], name, ch['b']))
            gaps[name] = round(g[0], 3)
            if worst is None or g[0] > worst[0]:
                worst = g
        return {'v': max(gaps.values()), 'min': min(gaps.values()), 'gaps': gaps, 'p': v3(worst[3]), 'q': v3(worst[2])}
    if kind == 'hold':
        p = _dot(S, ch['a'])
        local = ref_matrix(ch.get('b', 'world'), S).inverted() @ p
        first = hold_ref.setdefault(ch['id'], local.copy())
        return {'v': round((local - first).length * 1000.0, 3), 'p': v3(p),
                'q': v3(ref_matrix(ch.get('b', 'world'), S) @ first)}
    if kind in ('align', 'range'):
        a, oa = direction(ch['a'], S)
        b, ob = direction(ch['b'], S)
        if kind == 'range' and ch.get('axis') is not None:
            c, _ = direction(ch['axis'], S)
            ang = math.degrees(math.atan2(c.dot(b.cross(a)), b.dot(a)))
        else:
            ang = _angle(a, b)
        return {'v': round(ang, 3), 'p': v3(oa), 'q': v3(ob), 'da': v3(a), 'db': v3(b)}
    raise RuntimeError('check %s: kind %r (use seat, rest, hold, distance, align, range)' % (ch.get('id'), kind))


def measure(cfg, chars=None, props=None):
    began = time.time()
    if chars is None:
        chars, props = load_scene(cfg)
    sc = bpy.context.scene
    contacts = list(cfg.get('contacts') or [])
    checks = []
    for c in cfg.get('checks') or []:
        (contacts if c.get('kind', 'touch') == 'touch' else checks).append(c)
    frames = frame_list(cfg.get('frames', 'all'), chars)
    for c in contacts + checks:
        c['frames'] = [int(f) for f in (c.get('frames') or frames)]
    frames = sorted(set(frames) | {f for c in contacts + checks for f in c['frames']})
    if not frames:
        raise RuntimeError('no frames to measure')
    calib = int(cfg.get('calibFrame', frames[0]))
    floor = cfg.get('floorZ', 0.0)
    sc.frame_set(calib)
    custom = [CustomDot(d, chars) for d in cfg.get('dots') or []]
    S = Scene(chars, props or [], None if floor is None else float(floor), float(cfg.get('signRangeM', SIGN_RANGE)), custom)
    per_check = {c['id']: {} for c in checks}
    hold_ref = {}
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
            if full in S.custom and not S.custom[full].penetration:
                continue
            for gname, trees in S.groups(full).items():
                g = S.gap(full, trees)
                if g is None:
                    continue
                mm, label, q, p = g
                key = (full, gname)
                if mm < -PEN_EPS_MM and (key not in pen or mm < pen[key]['mm']):
                    pen[key] = {'dot': full, 'surface': label, 'group': gname, 'mm': round(mm, 2), 'frame': f,
                                'p': v3(p), 'q': v3(q)}
                if mm <= observe and (key not in seen or mm < seen[key]['mm']):
                    seen[key] = {'dot': full, 'surface': label, 'mm': round(mm, 2), 'frame': f}
        for c in contacts:
            if f not in c['frames']:
                continue
            if c['a'] in S.custom:
                members = [c['a']]
            else:
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
                        best = (g[0], full, g[1], g[2], g[3])
            if best:
                per_contact[c['id']][str(f)] = {'mm': round(best[0], 3), 'dot': best[1], 'surface': best[2],
                                               'p': v3(best[4]), 'q': v3(best[3])}
        for ch in checks:
            if f in ch['frames']:
                per_check[ch['id']][str(f)] = eval_check(ch, S, hold_ref)
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
    for c in checks:
        rows = per_check[c['id']]
        if rows:
            want.add(int(max(rows, key=lambda k: rows[k]['v'])))
            want.add(int(min(rows, key=lambda k: rows[k]['v'])))
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
              'checks': [dict(c, values=per_check[c['id']], unit=UNITS[c['kind']]) for c in checks],
              'dots': {n: {'radiusMm': round(S.skin[n], 3)} for n in S.custom},
              'penetrations': pens, 'observed': sorted(seen.values(), key=lambda r: r['mm']),
              'dotsAt': closeup_at, 'props': [o.name for o in props or []],
              'config': json.loads(json.dumps(cfg)),           # the sheet rebuilds the scene from it
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
        mm, label, _, _ = S.gap(full, trees)
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


# ---------------------------------------------------------------- rigid selftest (Spec A7, A8, A9)

RIGID_FRAMES = 14
TILT_MM = 6.0
SLIDE_MM = 5.0


def hinge_deg(f):
    return -10.0 + 10.0 * (f - 1)                         # -10 .. 120 over frames 1..14


def _empty(name, location=(0, 0, 0), rotation=(0, 0, 0)):
    o = bpy.data.objects.new(name, None); bpy.context.scene.collection.objects.link(o)
    o.location = location; o.rotation_euler = rotation
    return o


def _armature(name, bones, location=(0, 0, 0)):
    """bones: (name, head, tail, parent) in armature space."""
    data = bpy.data.armatures.new(name)
    arm = bpy.data.objects.new(name, data); bpy.context.scene.collection.objects.link(arm)
    arm.location = location
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    made = {}
    for n, head, tail, parent in bones:
        eb = data.edit_bones.new(n); eb.head, eb.tail, eb.roll = head, tail, 0.0
        if parent:
            eb.parent = made[parent]
        made[n] = eb
    bpy.ops.object.mode_set(mode='OBJECT')
    return arm


def _linear(obj):
    ad = obj.animation_data
    if ad and ad.action:
        for fc in getattr(ad.action, 'fcurves', ()):
            for k in fc.keyframe_points:
                k.interpolation = 'LINEAR'


def build_rigid_scene():
    """No characters. Table (1 m cube, top z 0.5); a flat box and a box tipped on one bottom edge (far edge 6 mm up);
    peg/socket empties 0 and 4 mm apart; a moving Carrier bone holding one box fixed and sliding another 5 mm;
    align objects at 0, 15 and 90 degrees; a Hinge armature whose Lid sweeps -10..120 degrees about Base x."""
    import bmesh
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene; sc.frame_start, sc.frame_end = 1, RIGID_FRAMES
    cube = lambda s: (lambda bm: bmesh.ops.create_cube(bm, size=s))
    table = _mesh_object('Table', cube(1.0))
    _mesh_object('BoxFlat', cube(0.2), (-0.3, 0.0, 0.6))
    tilt = _mesh_object('BoxTilt', cube(0.2))
    th = math.asin(TILT_MM / 1000.0 / 0.2)
    pivot_w, pivot_l = Vector((0.3, -0.1, 0.5)), Vector((0.0, -0.1, -0.1))
    tilt.matrix_world = Matrix.Translation(pivot_w) @ Matrix.Rotation(th, 4, 'X') @ Matrix.Translation(-pivot_l)
    _empty('Socket0', (0.0, 0.3, 0.6)); _empty('Peg0', (0.0, 0.3, 0.6))
    _empty('Socket4', (0.1, 0.3, 0.6)); _empty('Peg4', (0.1, 0.3, 0.604))
    for n, deg in (('Ref', 0), ('Tilt0', 0), ('Tilt15', 15), ('Tilt90', 90)):
        _empty(n, (0.4, 0.4, 0.6), (math.radians(deg), 0, 0))
    carrier = _armature('Carrier', [('Hand', (0, 0, 0), (0, 0.1, 0), None)], (0, -0.4, 1.0))
    for f in (1, RIGID_FRAMES):
        t = (f - 1) / (RIGID_FRAMES - 1)
        carrier.location = (0.5 * t, -0.4, 1.0 + 0.2 * t); carrier.rotation_euler = (0.3 * t, 0, math.radians(90) * t)
        carrier.keyframe_insert('location', frame=f); carrier.keyframe_insert('rotation_euler', frame=f)
    _linear(carrier)
    for n, x in (('Carried', 0.0), ('Sliding', 0.2)):
        b = _mesh_object(n, cube(0.1))
        b.parent = carrier; b.parent_type = 'BONE'; b.parent_bone = 'Hand'
        b.location = (x, 0, 0)
        if n == 'Sliding':
            b.keyframe_insert('location', frame=1)
            b.location = (x + SLIDE_MM / 1000.0, 0, 0); b.keyframe_insert('location', frame=RIGID_FRAMES)
            _linear(b)
    hinge = _armature('Hinge', [('Base', (0, 0, 0), (0, 0.1, 0), None), ('Lid', (0, 0.1, 0), (0, 0.2, 0), 'Base')],
                      (-0.4, 0.4, 0.6))
    lid = hinge.pose.bones['Lid']; lid.rotation_mode = 'XYZ'
    for f in range(1, RIGID_FRAMES + 1):
        lid.rotation_euler = (math.radians(hinge_deg(f)), 0, 0)
        lid.keyframe_insert('rotation_euler', frame=f)
    _linear(hinge)
    sc.frame_set(1)
    return table


def rigid_config(out):
    """Custom dots and checks of the rigid scene; each check carries want (value, or frame -> value) and expect."""
    corners = [(x, y, -0.1) for x in (-0.1, 0.1) for y in (-0.1, 0.1)]
    dots = []
    for box in ('BoxFlat', 'BoxTilt'):
        dots += [{'name': '%s.%d' % (box, i), 'object': box, 'point': list(c)} for i, c in enumerate(corners)]
    dots += [{'name': n, 'object': n} for n in ('Socket0', 'Peg0', 'Socket4', 'Peg4', 'Carried', 'Sliding')]
    dots += [{'name': 'mark-a', 'object': 'Table', 'point': [-0.05, 0.4, 0.5], 'penetration': False},
             {'name': 'mark-b', 'object': 'Table', 'point': [0.05, 0.4, 0.5], 'penetration': False}]
    carrier = {'armature': 'Carrier', 'bone': 'Hand'}
    hb = lambda bone, axis: {'armature': 'Hinge', 'bone': bone, 'axis': axis}
    all_f = list(range(1, RIGID_FRAMES + 1))
    checks = [
        {'id': 'seat-0', 'kind': 'seat', 'a': 'Peg0', 'b': 'Socket0', 'want': 0.0, 'expect': 'pass'},
        {'id': 'seat-4', 'kind': 'seat', 'a': 'Peg4', 'b': 'Socket4', 'want': 4.0, 'expect': 'fail'},
        {'id': 'rest-flat', 'kind': 'rest', 'a': ['BoxFlat.%d' % i for i in range(4)], 'b': 'prop:Table',
         'want': 0.0, 'expect': 'pass'},
        {'id': 'rest-tilted', 'kind': 'rest', 'a': ['BoxTilt.%d' % i for i in range(4)], 'b': 'prop:Table',
         'want': TILT_MM, 'expect': 'fail'},
        {'id': 'hold-carried', 'kind': 'hold', 'a': 'Carried', 'b': carrier, 'want': 0.0, 'expect': 'pass'},
        {'id': 'hold-sliding', 'kind': 'hold', 'a': 'Sliding', 'b': carrier, 'frames': [1, RIGID_FRAMES],
         'want': {'1': 0.0, str(RIGID_FRAMES): SLIDE_MM}, 'expect': 'fail'},
        {'id': 'distance-100', 'kind': 'distance', 'a': 'mark-a', 'b': 'mark-b', 'min': 99.0, 'max': 101.0,
         'want': 100.0, 'expect': 'pass'},
        {'id': 'distance-far', 'kind': 'distance', 'a': 'mark-a', 'b': 'mark-b', 'min': 120.0, 'max': 200.0,
         'want': 100.0, 'expect': 'fail'},
        {'id': 'align-0', 'kind': 'align', 'a': {'object': 'Tilt0', 'axis': 'z'}, 'b': {'object': 'Ref', 'axis': 'z'},
         'maxDeg': 20, 'want': 0.0, 'expect': 'pass'},
        {'id': 'align-15', 'kind': 'align', 'a': {'object': 'Tilt15', 'axis': 'z'}, 'b': {'object': 'Ref', 'axis': 'z'},
         'maxDeg': 20, 'want': 15.0, 'expect': 'pass'},
        {'id': 'align-90', 'kind': 'align', 'a': {'object': 'Tilt90', 'axis': 'z'}, 'b': {'object': 'Ref', 'axis': 'z'},
         'maxDeg': 20, 'want': 90.0, 'expect': 'fail'},
        {'id': 'align-line', 'kind': 'align', 'a': {'from': 'mark-a', 'to': 'mark-b'}, 'b': {'world': 'x'},
         'want': 0.0, 'expect': 'pass'},
        {'id': 'align-normal', 'kind': 'align', 'a': {'normal': 'BoxFlat.0', 'surface': 'prop:Table'}, 'b': 'z',
         'want': 0.0, 'expect': 'pass'},
        {'id': 'hinge-sweep', 'kind': 'range', 'a': hb('Lid', 'y'), 'b': hb('Base', 'y'), 'axis': hb('Base', 'x'),
         'min': 0, 'max': 110, 'want': {str(f): hinge_deg(f) for f in all_f}, 'expect': 'fail'},
        {'id': 'hinge-open', 'kind': 'range', 'a': hb('Lid', 'y'), 'b': hb('Base', 'y'), 'axis': hb('Base', 'x'),
         'min': 0, 'max': 110, 'frames': list(range(2, 13)), 'want': {str(f): hinge_deg(f) for f in range(2, 13)},
         'expect': 'pass'},
    ]
    return {'out': str(out), 'frames': {'range': [1, RIGID_FRAMES]}, 'floorZ': None, 'dots': dots, 'checks': checks}


def want_at(ch, f):
    w = ch.get('want')
    return w.get(str(f)) if isinstance(w, dict) else w


def rigid_selftest(out):
    table = build_rigid_scene()
    props = [o for o in bpy.data.objects if o.type == 'MESH']
    cfg = dict(rigid_config(out), builtin={'scene': 'rigid'})
    result = measure(cfg, [], props)
    ok, rows = True, []
    for ch in result['checks']:
        worst = None
        for f, val in sorted(ch['values'].items(), key=lambda kv: int(kv[0])):
            want = want_at(ch, f)
            err = abs(val['v'] - want) if want is not None else 0.0
            if worst is None or err > worst[0]:
                worst = (err, f, val['v'], want)
        good = worst is not None and worst[0] <= 0.1
        ok &= good
        rows.append('rigid %s f%s want %s got %.3f %s %s' % (ch['id'], worst[1], worst[3], worst[2], ch['unit'],
                                                             'ok' if good else 'OFF') if worst else
                    'rigid %s no values OFF' % ch['id'])
    return ok, rows


def build_builtin(spec):
    """Rebuild a selftest scene from a dots.json config's 'builtin' entry (the sheet uses it)."""
    if spec.get('scene') == 'rigid':
        return build_rigid_scene()
    return build_selftest_scene(selftest_rig(spec.get('rig', 'plain')))


def selftest(out, rig='plain', bone_map=None):
    ok_r, rows_r = rigid_selftest(Path(out) / 'rigid')
    arm, body, cube = build_selftest_scene(selftest_rig(rig))
    cfg = dict(selftest_config(out), builtin={'scene': 'body', 'rig': rig})
    chars = [Character('rig', [arm, body], bone_map=bone_map)]
    result = measure(cfg, chars, [cube])
    ok, rows = ok_r, list(rows_r)
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
        return 0 if selftest(out, rig) else 1
    if argv and argv[0] == '--make-sample':                 # the selftest scene saved as a .blend (tests, examples)
        rig = argv[argv.index('--rig') + 1] if '--rig' in argv else 'plain'
        if rig == 'rigid':
            build_rigid_scene()
        else:
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
