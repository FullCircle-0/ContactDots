"""Build the contact-dots showcase scene: a featureless mannequin reaches for a bar, grips it and lifts it.

  blender -b --factory-startup -P build_grab.py -- <out folder>

Writes grab.blend and grab.fbx. No outside assets: the skeleton uses Mixamo bone names (65 bones), the body is one
grey mesh of rounded segments rigidly skinned to it, the bar (3 cm x 40 cm) stands on a block table. Frames 1-10
the open hand rises over the bar, 11-15 it slides down beside it, 16-20 the fingers close (the knuckle curls until
the finger meets the bar, then the two outer joints put the tip on it), 21-40 the hand lifts the bar 20 cm; the bar
follows the hand rigidly from frame 20.
"""
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

P = 'mixamorig:'
FRAMES = 40
REACH_END, GRIP_END = 15, 20
BAR_R, BAR_LEN = 0.015, 0.40
PALM_HALF = 0.0125              # half thickness of the palm block
BAR_Y = 0.060                   # bar axis along the hand bone (m from the wrist): under the palm centre
ABDUCT_DEG = 15.0              # hanging upper arms swing out this far, clear of the hips
BAR_ABOVE = 0.10                # bar centre sits this far below the grip line (grip near the top)
PRE_UP = 0.17                   # the open hand comes in this far above the grip, then slides down onto the bar
PRE_END = 10                    # frames 1-10 lift the hand over the bar, 11-15 slide it down
LIFT_DEG = 73.0                 # shoulder swing that lifts the hand ~20 cm

# ---------------------------------------------------------------- skeleton (right side; the left mirrors x)
SPINE = [  # name, head, tail, parent, radius
    ('Hips', (0, 0, 1.00), (0, 0, 1.10), None, None),
    ('Spine', (0, 0, 1.10), (0, 0, 1.22), 'Hips', 0.120),
    ('Spine1', (0, 0, 1.22), (0, 0, 1.34), 'Spine', 0.125),
    ('Spine2', (0, 0, 1.34), (0, 0, 1.46), 'Spine1', 0.130),
    ('Neck', (0, 0, 1.46), (0, 0, 1.56), 'Spine2', 0.050),
    ('Head', (0, 0, 1.56), (0, 0, 1.75), 'Neck', None),
    ('HeadTop_End', (0, 0, 1.75), (0, 0, 1.80), 'Head', None),
]
ARM = [  # x is for the right side (negative); radius
    ('Shoulder', (-0.03, 0, 1.42), (-0.20, 0, 1.42), 'Spine2', 0.050),
    ('Arm', (-0.20, 0, 1.42), (-0.48, 0, 1.42), 'Shoulder', 0.045),
    ('ForeArm', (-0.48, 0, 1.42), (-0.73, 0, 1.42), 'Arm', 0.038),
    ('Hand', (-0.73, 0, 1.42), (-0.82, 0, 1.42), 'ForeArm', None),
]
# fingers in the rest T-pose: palm down, fingers along -x, spread along y (index forward at -y)
FINGERS = {  # name: (y offset, lengths of 1, 2, 3, end, radius)
    'Index': (-0.0315, (0.045, 0.027, 0.022, 0.012), 0.0090),
    'Middle': (-0.0105, (0.048, 0.030, 0.024, 0.012), 0.0095),
    'Ring': (0.0105, (0.045, 0.028, 0.022, 0.012), 0.0090),
    'Pinky': (0.0305, (0.035, 0.022, 0.019, 0.010), 0.0080),
}
THUMB_BASE = (-0.755, -0.035, 1.415)   # wrist side of the grip line, index edge of the palm
THUMB_DIR = Vector((-0.1, -0.35, -0.93)).normalized()   # out of the palm (down), a little to the index side
THUMB_LEN, THUMB_R = (0.040, 0.035, 0.030, 0.012), 0.0105
LEG = [
    ('UpLeg', (-0.09, 0, 0.98), (-0.09, 0, 0.53), 'Hips', 0.070),
    ('Leg', (-0.09, 0, 0.53), (-0.09, 0, 0.08), 'UpLeg', 0.050),
    ('Foot', (-0.09, 0, 0.08), (-0.09, -0.12, 0.02), 'Leg', None),
    ('ToeBase', (-0.09, -0.12, 0.02), (-0.09, -0.18, 0.02), 'Foot', None),
    ('Toe_End', (-0.09, -0.18, 0.02), (-0.09, -0.22, 0.02), 'ToeBase', None),
]


def side_name(s, n):
    return P + ('Right' if s < 0 else 'Left') + n


def mirror(v, s):
    return (v[0] if s < 0 else -v[0], v[1], v[2])


def bone_specs():
    """[(name, head, tail, parent, radius, connected)] for the whole skeleton."""
    out = [(P + n, h, t, P + p if p else None, r, p is not None and n not in ('Hips',)) for n, h, t, p, r in SPINE]
    for s in (-1, 1):
        for n, h, t, p, r in ARM:
            parent = P + 'Spine2' if p == 'Spine2' else side_name(s, p)
            out.append((side_name(s, n), mirror(h, s), mirror(t, s), parent, r, n != 'Shoulder'))
        hand_tail = Vector(mirror(ARM[3][2], s))
        for f, (dy, lens, r) in FINGERS.items():
            head = hand_tail + Vector((0, dy, 0))
            d = Vector((-1 if s < 0 else 1, 0, 0))
            parent = side_name(s, 'Hand')
            for i, L in enumerate(lens):
                tail = head + d * L
                out.append((side_name(s, 'Hand%s%d' % (f, i + 1)), tuple(head), tuple(tail), parent,
                            r if i < 3 else None, i > 0))
                parent = side_name(s, 'Hand%s%d' % (f, i + 1)); head = tail
        head = Vector(mirror(THUMB_BASE, s))
        d = THUMB_DIR.copy()
        if s > 0:
            d.x = -d.x
        parent = side_name(s, 'Hand')
        for i, L in enumerate(THUMB_LEN):
            tail = head + d * L
            out.append((side_name(s, 'HandThumb%d' % (i + 1)), tuple(head), tuple(tail), parent,
                        THUMB_R if i < 3 else None, i > 0))
            parent = side_name(s, 'HandThumb%d' % (i + 1)); head = tail
        for n, h, t, p, r in LEG:
            parent = P + 'Hips' if p == 'Hips' else side_name(s, p)
            out.append((side_name(s, n), mirror(h, s), mirror(t, s), parent, r, n not in ('UpLeg',)))
    return out


def build_armature():
    data = bpy.data.armatures.new('Mannequin')
    arm = bpy.data.objects.new('Mannequin', data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    made = {}
    specs = bone_specs()
    for name, head, tail, parent, _r, conn in specs:
        eb = data.edit_bones.new(name); eb.head, eb.tail = head, tail
        eb.align_roll(Vector((0, 0, 1)) if abs(Vector(tail)[2] - Vector(head)[2]) < 0.9 * (Vector(tail) - Vector(head)).length
                      else Vector((0, -1, 0)))
        if parent:
            eb.parent = made[parent]
            eb.use_connect = conn and (Vector(head) - made[parent].tail).length < 1e-6
        made[name] = eb
    bpy.ops.object.mode_set(mode='OBJECT')
    for pb in arm.pose.bones:
        pb.rotation_mode = 'QUATERNION'
    return arm, specs


# ---------------------------------------------------------------- body mesh
def _capsule(bm, head, tail, r, seg=32, rings=16):
    head, tail = Vector(head), Vector(tail)
    d = tail - head
    new = bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=rings, radius=r)['verts']
    rot = Vector((0, 0, 1)).rotation_difference(d.normalized()).to_matrix()
    for v in new:
        if v.co.z > 1e-9:
            v.co.z += d.length
        v.co = rot @ v.co + head
    return new


def _box(bm, centre, size, rot):
    new = bmesh.ops.create_cube(bm, size=1.0)['verts']
    for v in new:
        v.co = rot @ Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2])) + Vector(centre)
    return new


def build_body(arm, specs):
    bm = bmesh.new()
    dl = bm.verts.layers.deform.verify()
    groups = []

    def assign(verts, bone):
        if bone not in groups:
            groups.append(bone)
        gi = groups.index(bone)
        for v in verts:
            v[dl][gi] = 1.0

    rest = {b.name: b.matrix_local.copy() for b in arm.data.bones}
    for name, head, tail, _p, r, _c in specs:
        if r:
            assign(_capsule(bm, head, tail, r), name)
    assign(_capsule(bm, (-0.10, 0, 1.00), (0.10, 0, 1.00), 0.12), P + 'Hips')
    hs = bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=0.10)['verts']
    for v in hs:
        v.co = Vector((v.co.x, v.co.y * 1.1, v.co.z * 1.15)) + Vector((0, -0.01, 1.66))
    assign(hs, P + 'Head')
    for s in (-1, 1):
        hand = rest[side_name(s, 'Hand')]
        rot = hand.to_3x3()
        centre = hand @ Vector((0, 0.047, 0))
        assign(_box(bm, centre, (0.085, 0.094, 2 * PALM_HALF), rot), side_name(s, 'Hand'))
        foot = rest[side_name(s, 'Foot')]
        fh = foot.translation   # flat on the floor: sole at z 0, toes forward (-y)
        assign(_box(bm, (fh.x, -0.06, 0.04), (0.09, 0.24, 0.08), Matrix.Identity(3)), side_name(s, 'Foot'))
    mesh = bpy.data.meshes.new('Mannequin_Body')
    bm.to_mesh(mesh); bm.free()
    for p in mesh.polygons:
        p.use_smooth = True
    body = bpy.data.objects.new('Mannequin_Body', mesh)
    bpy.context.scene.collection.objects.link(body)
    for g in groups:
        body.vertex_groups.new(name=g)
    body.parent = arm
    body.modifiers.new('Armature', 'ARMATURE').object = arm
    mat = coloured_material('Mannequin_Grey', (0.55, 0.55, 0.55, 1))
    mesh.materials.append(mat)
    return body


def coloured_material(name, colour):
    """A material that shows its colour in the viewport and in renders."""
    mat = bpy.data.materials.new(name); mat.diffuse_color = colour
    nt = getattr(mat, 'node_tree', None)
    if nt is None and hasattr(mat, 'use_nodes'):
        mat.use_nodes = True; nt = mat.node_tree
    bsdf = nt.nodes.get('Principled BSDF') if nt else None
    if bsdf:
        bsdf.inputs['Base Color'].default_value = colour
    return mat


def mesh_object(name, build, colour):
    bm = bmesh.new(); build(bm)
    mesh = bpy.data.meshes.new(name); bm.to_mesh(mesh); bm.free()
    ob = bpy.data.objects.new(name, mesh); bpy.context.scene.collection.objects.link(ob)
    mat = coloured_material(name + '_mat', colour)
    mesh.materials.append(mat)
    return ob


# ---------------------------------------------------------------- posing
def frame_rot(y, z):
    """Armature-space rotation whose bone y and z axes are the given directions."""
    y = Vector(y).normalized(); z = Vector(z)
    z = (z - y * z.dot(y)).normalized()
    x = y.cross(z)
    return Matrix((x, y, z)).transposed()


def smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


SHOULDER = Vector((-0.20, 0, 1.42))
UPPER_LEN, FORE_LEN = 0.28, 0.25


def ik_arm(up_by, down, fwd, med, w=None, hand_rot=None, pole=None):
    """Rotations of upper arm, forearm, hand with the wrist `up_by` m above its grip spot (or at `w`), the hand turned
    as in the grip (or `hand_rot`), the elbow bending in the grip's arm plane."""
    elbow0 = SHOULDER + down * UPPER_LEN
    if w is None:
        w = elbow0 + fwd * FORE_LEN + Vector((0, 0, up_by))
    d = min((w - SHOULDER).length, UPPER_LEN + FORE_LEN - 1e-6)
    u = (w - SHOULDER) / d
    pole = pole if pole is not None else elbow0 - SHOULDER
    v = pole - u * pole.dot(u)
    v = v.normalized() if v.length > 1e-9 else Vector((0, -1, 0))
    a = (UPPER_LEN ** 2 - FORE_LEN ** 2 + d * d) / (2 * d)
    h = math.sqrt(max(0.0, UPPER_LEN ** 2 - a * a))
    e = SHOULDER + u * a + v * h
    w = SHOULDER + u * d
    return [frame_rot(e - SHOULDER, med), frame_rot(w - e, med), hand_rot or frame_rot(fwd, med)]


def arm_targets(f):
    """Armature-space rotations of RightArm, RightForeArm, RightHand at frame f."""
    a = math.radians(ABDUCT_DEG)
    down, fwd, med = Vector((-math.sin(a), 0, -math.cos(a))), Vector((0, -1, 0)), Vector((-1, 0, 0))
    rest = [frame_rot(down, med), frame_rot(down, med), frame_rot(down, med)]
    grip = [frame_rot(down, med), frame_rot(fwd, med), frame_rot(fwd, med)]
    if f <= PRE_END:   # the wrist rises first, then goes forward (clear of the table and the bar top)
        t = (f - 1) / (PRE_END - 1)
        w0 = SHOULDER + down * (UPPER_LEN + FORE_LEN)
        w1 = SHOULDER + down * UPPER_LEN + fwd * FORE_LEN + Vector((0, 0, PRE_UP))
        rise, go = smooth(t / 0.5), smooth((t - 0.3) / 0.7)
        w = Vector((w0.x + (w1.x - w0.x) * go, w0.y + (w1.y - w0.y) * go, w0.z + (w1.z - w0.z) * rise))
        hand_rot = rest[2].to_quaternion().slerp(grip[2].to_quaternion(), smooth(t)).to_matrix()
        out_back = Vector((-0.5, 1.0, -0.2)).normalized()   # elbow out and back while the hand rises
        pole = out_back.lerp((down * UPPER_LEN).normalized(), go)
        return ik_arm(0, down, fwd, med, w=w, hand_rot=hand_rot, pole=pole)
    if f <= REACH_END:
        return ik_arm(PRE_UP * (1 - smooth((f - PRE_END) / (REACH_END - PRE_END))), down, fwd, med)
    if f <= GRIP_END:
        return grip
    th = math.radians(LIFT_DEG) * smooth((f - GRIP_END) / (FRAMES - GRIP_END))
    up = Vector((-math.sin(a), -math.cos(a) * math.sin(th), -math.cos(a) * math.cos(th)))
    return [frame_rot(up, med), grip[1], grip[2]]


def set_matrix(arm, name, rot):
    pb = arm.pose.bones[name]
    pb.matrix = Matrix.Translation(pb.head) @ rot.to_4x4()
    bpy.context.view_layer.update()


def pose_arm(arm, f):
    for n, rot in zip(('Arm', 'ForeArm', 'Hand'), arm_targets(f)):
        set_matrix(arm, side_name(-1, n), rot)


def seg_line_dist(a, b, c, u):
    """Distance between segment a-b and the infinite line through c along unit u."""
    best = None
    for i in range(201):
        p = a + (b - a) * (i / 200.0)
        w = p - c
        d = (w - u * w.dot(u)).length
        best = d if best is None or d < best else best
    return best


def _seg_seg(a, b, c, d):
    return min(((a + (b - a) * (i / 50.0)) - (c + (d - c) * (j / 50.0))).length for i in range(51) for j in range(51))


def chain_gap(arm, bones, radius, c, u):
    bpy.context.view_layer.update()
    return min(seg_line_dist(arm.pose.bones[b].head, arm.pose.bones[b].tail, c, u) for b in bones) - (BAR_R + radius)


def solve_joint(arm, bones, k, axis, sign, radius, c, u, lo=-30.0, hi=110.0):
    """Curl bone k about its local axis until the chain k.. just touches the bar; returns degrees."""
    pb = arm.pose.bones[bones[k]]

    def gap(deg):
        pb.rotation_quaternion = Quaternion(axis, sign * math.radians(deg))
        return chain_gap(arm, bones[k:], radius, c, u)

    if gap(0.0) <= 0:   # already touching: open until clear
        a, b = lo, 0.0
        if gap(lo) <= 0:
            gap(0.0); return 0.0
    else:
        step, a, b = 1.0, 0.0, None
        x = 0.0
        while x < hi:
            x += step
            if gap(x) <= 0:
                a, b = x - step, x; break
        if b is None:
            gap(hi); return hi
    for _ in range(40):
        m = 0.5 * (a + b)
        if gap(m) > 0:
            a = m
        else:
            b = m
    gap(a)
    return a


def solve_tip(arm, bones, axes, sgns, radius, c, u, slack=0.0002, most=True):
    """Joints 2 and 3 together: the most wrap (largest joint-2 curl; least with most=False) whose joint-3 curl puts the tip on the bar
    while no part of segments 2-3 sinks deeper than `slack`. Returns (a2, a3) in degrees or None."""
    pb2, pb3 = arm.pose.bones[bones[1]], arm.pose.bones[bones[2]]

    def put(a2, a3):
        pb2.rotation_quaternion = Quaternion(axes[1], sgns[1] * math.radians(a2))
        pb3.rotation_quaternion = Quaternion(axes[2], sgns[2] * math.radians(a3))
        bpy.context.view_layer.update()

    def tip_gap(a2, a3):
        put(a2, a3)
        t = pb3.tail - c
        return (t - u * t.dot(u)).length - (BAR_R + radius)

    best = None
    for a2 in [x * 2.0 for x in (range(55, -1, -1) if most else range(0, 56))]:
        prev, hit = tip_gap(a2, 0.0), None
        if prev <= 0:
            continue
        x = 0.0
        while x < 110.0:
            x += 2.0
            g = tip_gap(a2, x)
            if g <= 0:
                lo, hi = x - 2.0, x
                for _ in range(30):
                    m = 0.5 * (lo + hi)
                    if tip_gap(a2, m) > 0:
                        lo = m
                    else:
                        hi = m
                hit = hi; break
        if hit is None:
            continue
        put(a2, hit)
        if chain_gap(arm, bones[1:], radius, c, u) >= -slack:
            best = (a2, hit); break
    if best:
        put(*best)
    return best


def curl_sign(arm, bone, axis, toward):
    """+1 if a positive turn about the bone's local axis moves its tail toward `toward` (armature space)."""
    pb = arm.pose.bones[bone]
    pb.rotation_quaternion = Quaternion(); bpy.context.view_layer.update()
    t0 = pb.tail.copy()
    pb.rotation_quaternion = Quaternion(axis, math.radians(10)); bpy.context.view_layer.update()
    s = 1 if (pb.tail - t0).dot(toward) > 0 else -1
    pb.rotation_quaternion = Quaternion(); bpy.context.view_layer.update()
    return s


def action_fcurves(ob):
    """Every F-curve of the object's action (Blender 5 layered actions, or the older flat list)."""
    act = ob.animation_data.action if ob.animation_data else None
    if act is None:
        return []
    if getattr(act, 'layers', None):
        slot = ob.animation_data.action_slot
        return [fc for layer in act.layers for strip in layer.strips
                for bag in strip.channelbags if bag.slot == slot for fc in bag.fcurves]
    return list(getattr(act, 'fcurves', []))


def main():
    out = Path(sys.argv[sys.argv.index('--') + 1]).resolve() if '--' in sys.argv else Path(__file__).parent
    out.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end, sc.render.fps = 1, FRAMES, 30
    arm, specs = build_armature()
    build_body(arm, specs)

    # grip pose: bar axis along the hand's x, against the palm
    pose_arm(arm, GRIP_END)
    hand = arm.pose.bones[side_name(-1, 'Hand')].matrix.copy()
    hx, hz = hand.col[0].xyz.normalized(), hand.col[2].xyz.normalized()
    c = hand @ Vector((0, BAR_Y, -(PALM_HALF + BAR_R)))
    bar_centre = c + hx * BAR_ABOVE
    table_top = bar_centre.z - BAR_LEN / 2

    bar = mesh_object('Bar', lambda bm: bmesh.ops.create_cone(
        bm, cap_ends=True, segments=64, radius1=BAR_R, radius2=BAR_R, depth=BAR_LEN), (0.85, 0.55, 0.15, 1))
    bar.rotation_mode = 'QUATERNION'
    bar_rest = Matrix.Translation(bar_centre) @ Matrix.Identity(4)   # upright: cone depth is along z
    bar.matrix_world = bar_rest

    def table(bm):
        top = bmesh.ops.create_cube(bm, size=1.0)['verts']
        for v in top:
            v.co = Vector((v.co.x * 0.7, v.co.y * 0.45, v.co.z * table_top)) + Vector((bar_centre.x, bar_centre.y - 0.14,
                                                                                        table_top / 2))
    mesh_object('Table', table, (0.35, 0.30, 0.25, 1))

    # fingers: solve the curl at the grip frame, one joint at a time
    curls = {}
    for f, (_dy, _lens, r) in list(FINGERS.items()) + [('Thumb', (0, THUMB_LEN, THUMB_R))]:
        bones = [side_name(-1, 'Hand%s%d' % (f, i)) for i in (1, 2, 3)]
        angles, axes, sgns = [], [], []
        for k in range(3):
            if f == 'Thumb':   # the thumb wraps the bar from the wrist side: turn about the bar axis toward the knuckles
                pb = arm.pose.bones[bones[k]]
                pb.rotation_quaternion = Quaternion(); bpy.context.view_layer.update()
                axis = (pb.matrix.to_3x3().transposed() @ hx).normalized()
                toward = hand.col[1].xyz.normalized()
            else:
                axis, toward = Vector((1, 0, 0)), -hz     # curl toward the palm about the bone's x
            if f == 'Thumb' or k == 0:   # a finger keeps its first joint's sense (later joints point back under the bar)
                sgn = curl_sign(arm, bones[k], axis, toward)
            axes.append(axis); sgns.append(sgn)
            if k == 0:
                angles.append(solve_joint(arm, bones, k, axis, sgn, r, c, hx, hi=90.0))   # knuckle at most 90 deg
        tip = solve_tip(arm, bones, axes, sgns, r, c, hx, most=(f != 'Thumb'))   # the thumb stays clear of the index
        if tip is None:   # no tip contact without sinking in: fall back to joint-by-joint chain contact
            angles = angles[:1]
            for k in (1, 2):
                angles.append(solve_joint(arm, bones, k, axes[k], sgns[k], r, c, hx))
        else:
            angles += list(tip)
        curls[f] = (bones, axes, sgns, angles)
        gap = chain_gap(arm, bones, r, c, hx)
        tip = arm.pose.bones[bones[2]].tail - c
        tip -= hx * tip.dot(hx)
        wrap = math.degrees(math.atan2(tip.dot(hand.col[1].xyz.normalized()), tip.dot(hz)))
        print('DOTS_GRAB finger %s curls %s gap %.2f mm tip at %.0f deg round the bar (palm 0, knuckles +90, wrist -90)'
              % (f, ['%.1f' % a for a in angles], gap * 1000, wrap))

    thumb = curls['Thumb'][0]
    for f in FINGERS:
        clear = min(min(_seg_seg(arm.pose.bones[t].head, arm.pose.bones[t].tail, arm.pose.bones[b].head,
                                 arm.pose.bones[b].tail) for t in thumb) for b in curls[f][0])
        print('DOTS_GRAB thumb to %s clearance %.1f mm' % (f, (clear - THUMB_R - FINGERS[f][2]) * 1000))

    # left arm hangs at the side for the whole take
    for n in ('Arm', 'ForeArm', 'Hand'):
        a = math.radians(ABDUCT_DEG)
        set_matrix(arm, side_name(1, n), frame_rot(Vector((math.sin(a), 0, -math.cos(a))), Vector((1, 0, 0))))
        arm.pose.bones[side_name(1, n)].keyframe_insert('rotation_quaternion', frame=1)

    # keys
    hand_grip = None
    for fr in range(1, FRAMES + 1):
        pose_arm(arm, fr)
        for n in ('Arm', 'ForeArm', 'Hand'):
            arm.pose.bones[side_name(-1, n)].keyframe_insert('rotation_quaternion', frame=fr)
        t = 0.0 if fr <= REACH_END else smooth((fr - REACH_END) / (GRIP_END - REACH_END))
        for f, (bones, axes, sgns, angles) in curls.items():
            for b, axis, sgn, a in zip(bones, axes, sgns, angles):
                pb = arm.pose.bones[b]
                pb.rotation_quaternion = Quaternion(axis, sgn * math.radians(a * t))
                pb.keyframe_insert('rotation_quaternion', frame=fr)
        bpy.context.view_layer.update()
        h = arm.pose.bones[side_name(-1, 'Hand')].matrix.copy()
        if fr == GRIP_END:
            hand_grip = h
        m = bar_rest if fr <= GRIP_END else h @ hand_grip.inverted() @ bar_rest
        bar.matrix_world = m
        bar.keyframe_insert('location', frame=fr); bar.keyframe_insert('rotation_quaternion', frame=fr)
    for ob in (arm, bar):
        if ob.animation_data and ob.animation_data.action:
            for fc in getattr(ob.animation_data.action, 'fcurves', []):
                for kp in fc.keyframe_points:
                    kp.interpolation = 'LINEAR'
    sc.frame_set(1)
    print('DOTS_GRAB bar centre %s table top %.3f' % (tuple(round(x, 4) for x in bar_centre), table_top))
    bpy.ops.wm.save_as_mainfile(filepath=str(out / 'grab.blend'))
    # FBX takes from Unity, Mixamo or Cascadeur start at time 0, and Blender's importer puts time 0 on frame 1
    # (anim_offset 1). Write the take from time 0 too, so grab.fbx imports on frames 1-40 like grab.blend.
    for ob in (arm, bar):
        for fc in action_fcurves(ob):
            for kp in fc.keyframe_points:
                kp.co.x -= 1; kp.handle_left.x -= 1; kp.handle_right.x -= 1
    sc.frame_start, sc.frame_end = 0, FRAMES - 1
    bpy.ops.export_scene.fbx(filepath=str(out / 'grab.fbx'), object_types={'ARMATURE', 'MESH'}, add_leaf_bones=False,
                             bake_anim=True, bake_anim_use_all_actions=False, bake_anim_use_nla_strips=False,
                             bake_anim_simplify_factor=0.0, bake_anim_force_startend_keying=True)
    print('DOTS_GRAB_DONE %s' % out)


main()
