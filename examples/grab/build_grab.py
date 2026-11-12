"""Build the contact-dots showcase scene: a featureless mannequin reaches for a bar, grips it and lifts it.

  blender -b --factory-startup -P build_grab.py -- <out folder>

Writes grab.blend and grab.fbx. No outside assets: the skeleton uses Mixamo bone names (65 bones), the body is one
grey mesh of rounded segments rigidly skinned to it, the bar (3 cm x 40 cm) stands on a block table. Frames 1-15
reach, 16-20 the fingers close (each joint curls until its segment touches the bar), 21-40 the hand lifts the bar
20 cm; the bar follows the hand rigidly from frame 20.
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
BAR_Y = 0.075                   # bar axis along the hand bone (m from the wrist)
BAR_ABOVE = 0.06                # bar centre sits this far below the grip line (grip on the upper half)
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
THUMB_BASE = (-0.755, -0.035, 1.42)
THUMB_DIR = Vector((-0.5, -0.85, -0.15)).normalized()
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
        assign(_box(bm, foot @ Vector((0, 0.09, 0)), (0.09, 0.24, 0.07), foot.to_3x3()), side_name(s, 'Foot'))
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
    mat = bpy.data.materials.new('Mannequin_Grey'); mat.diffuse_color = (0.55, 0.55, 0.55, 1)
    mesh.materials.append(mat)
    return body


def mesh_object(name, build, colour):
    bm = bmesh.new(); build(bm)
    mesh = bpy.data.meshes.new(name); bm.to_mesh(mesh); bm.free()
    ob = bpy.data.objects.new(name, mesh); bpy.context.scene.collection.objects.link(ob)
    mat = bpy.data.materials.new(name + '_mat'); mat.diffuse_color = colour
    mesh.materials.append(mat)
    return ob


# ---------------------------------------------------------------- posing
def frame_rot(y, z):
    """Armature-space rotation whose bone y and z axes are the given directions."""
    y = Vector(y).normalized(); z = Vector(z).normalized()
    x = y.cross(z)
    return Matrix((x, y, z)).transposed()


def smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def arm_targets(f):
    """Armature-space rotations of RightArm, RightForeArm, RightHand at frame f."""
    down, fwd, med = Vector((0, 0, -1)), Vector((0, -1, 0)), Vector((-1, 0, 0))
    rest = [frame_rot(down, med), frame_rot(down, med), frame_rot(down, med)]
    grip = [frame_rot(down, med), frame_rot(fwd, med), frame_rot(fwd, med)]
    if f <= REACH_END:
        t = smooth((f - 1) / (REACH_END - 1))
        return [a.to_quaternion().slerp(b.to_quaternion(), t).to_matrix() for a, b in zip(rest, grip)]
    if f <= GRIP_END:
        return grip
    th = math.radians(LIFT_DEG) * smooth((f - GRIP_END) / (FRAMES - GRIP_END))
    up = Vector((0, -math.sin(th), -math.cos(th)))
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


def curl_sign(arm, bone, axis, toward):
    """+1 if a positive turn about the bone's local axis moves its tail toward `toward` (armature space)."""
    pb = arm.pose.bones[bone]
    pb.rotation_quaternion = Quaternion(); bpy.context.view_layer.update()
    t0 = pb.tail.copy()
    pb.rotation_quaternion = Quaternion(axis, math.radians(10)); bpy.context.view_layer.update()
    s = 1 if (pb.tail - t0).dot(toward) > 0 else -1
    pb.rotation_quaternion = Quaternion(); bpy.context.view_layer.update()
    return s


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
        axis, toward = Vector((1, 0, 0)), -hz         # curl toward the palm about the bone's x
        sgn = curl_sign(arm, bones[0], axis, toward)
        angles = []
        for k in range(3):
            angles.append(solve_joint(arm, bones, k, axis, sgn, r, c, hx))
            if f == 'Thumb':
                break
        curls[f] = (bones, axis, sgn, angles)
        gap = chain_gap(arm, bones, r, c, hx)
        print('DOTS_GRAB finger %s curls %s gap %.2f mm' % (f, ['%.1f' % a for a in angles], gap * 1000))

    # left arm hangs at the side for the whole take
    for n in ('Arm', 'ForeArm', 'Hand'):
        set_matrix(arm, side_name(1, n), frame_rot(Vector((0, 0, -1)), Vector((1, 0, 0))))
        arm.pose.bones[side_name(1, n)].keyframe_insert('rotation_quaternion', frame=1)

    # keys
    hand_grip = None
    for fr in range(1, FRAMES + 1):
        pose_arm(arm, fr)
        for n in ('Arm', 'ForeArm', 'Hand'):
            arm.pose.bones[side_name(-1, n)].keyframe_insert('rotation_quaternion', frame=fr)
        t = 0.0 if fr <= REACH_END else smooth((fr - REACH_END) / (GRIP_END - REACH_END))
        for f, (bones, axis, sgn, angles) in curls.items():
            for b, a in zip(bones, angles + [0.0] * (3 - len(angles))):
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
    bpy.ops.export_scene.fbx(filepath=str(out / 'grab.fbx'), object_types={'ARMATURE', 'MESH'}, add_leaf_bones=False,
                             bake_anim=True, bake_anim_use_all_actions=False, bake_anim_use_nla_strips=False,
                             bake_anim_simplify_factor=0.0, bake_anim_force_startend_keying=True)
    print('DOTS_GRAB_DONE %s' % out)


main()
