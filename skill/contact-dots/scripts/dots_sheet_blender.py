"""Contact dots close-up sheet. Runs INSIDE Blender (the host script dots.py launches it):
    blender -b --factory-startup --python-exit-code 1 -P dots_sheet_blender.py -- <dots-sheet-job.json>

The job (written by dots.py from a judged dots.json): out (the sheet png), config (the measured config: the scene is
rebuilt from its files, or from its 'builtin' selftest entry), dotsAt (frame -> dot -> position), roles (character
order for the dot colours) and tiles [{name, frame, p, q, ok, caption, mm, dot, surface, da, db}] (metres, unit directions).

Per tile: every mesh in one flat colour per character or prop, every dot of that frame coloured per character (custom dots on objects in one
colour), the measured dot and its surface point larger, the pair line (yellow pass, magenta fail; a ring round a pair too close for a line), for align and range
the two directions as arrows; a translucent disc on the surface at the measured surface point (complementary colour,
outline and normal arrow in front); XYZ triads (X red, Y green, Z light blue) on the palm, wrist, head and pelvis in
view. Measured marks sit toward the camera so the opaque meshes never hide them. An orthographic close-up looks across the pair (or square onto the two arrows) with the
caption at the bottom. Tiles go to dots-sheet-tiles/<name>.png and are composed into the sheet. Reads only; writes
only beside the sheet. Prints DOTS_SHEET_DONE <json>.
"""
import colorsys
import json
import math
import sys
import time
from pathlib import Path

import bpy
import bmesh
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dots_blender  # noqa: E402

TILE = 480
COLS = 3
GAP_PX = 6
ROLE_COLOURS = [(1.0, 0.33, 0.1, 1), (0.1, 0.8, 0.75, 1), (0.6, 0.9, 0.3, 1), (0.9, 0.5, 0.6, 1)]   # far from yellow
COLOURS = {'pass': (1, 1, 0, 1), 'fail': (1, 0, 1, 1), 'object': (0.65, 0.35, 0.95, 1), 'surface': (0.95, 0.95, 0.95, 1),
           'arrowA': (0.6, 0.9, 1.0, 1), 'text': (1, 1, 1, 1), 'floorDisc': (0.8, 0.8, 0.85, 1)}      # never pure R/G/B
# Palette values are display (sRGB) colours; emission() converts them to linear so the Standard view shows them as written.
BODY = [(0.86, 0.68, 0.56, 1), (0.6, 0.66, 0.8, 1), (0.74, 0.8, 0.6, 1), (0.82, 0.66, 0.78, 1)]   # muted, per character
PROP = [(0.5, 0.64, 0.86, 1), (0.66, 0.6, 0.5, 1), (0.6, 0.78, 0.7, 1), (0.78, 0.74, 0.5, 1)]    # per prop object
AXES = [(1, 0.15, 0.15, 1), (0.2, 1, 0.2, 1), (0.25, 0.5, 1, 1)]   # X Y Z: the only near-pure R/G/B on the sheet
MARK_DEPTH = 0.6        # marks drawn in front sit this far before the camera (ortho: same spot on screen)
TRIAD_MM, TRIAD_PX = 30, 40
TRIAD_ZOOM = 1.6        # a tile may widen this much to show whole triads
DISC_ALPHA = 0.65        # disc fill: strong enough to read on its own, the surface still shows through
DISC_R = 0.018          # disc radius (m): wider than a fingertip, so the fill shows round the finger
UP = Vector((0, 0, 1))


def to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def emission(name, rgba, strength=1.0):
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name); m.use_nodes = True; nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    e = nt.nodes.new('ShaderNodeEmission'); e.inputs[0].default_value = [to_linear(c) for c in rgba[:3]] + [rgba[3]]; e.inputs[1].default_value = strength
    o = nt.nodes.new('ShaderNodeOutputMaterial'); nt.links.new(e.outputs[0], o.inputs[0])
    return m


def flat(name, rgba):
    """Opaque unlit colour, a little darker at grazing angles so fingers stay apart."""
    m = bpy.data.materials.new(name); m.use_nodes = True; nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    lw = nt.nodes.new('ShaderNodeLayerWeight'); lw.inputs[0].default_value = 0.5
    mul = nt.nodes.new('ShaderNodeMath'); mul.operation = 'MULTIPLY'; mul.inputs[1].default_value = 0.6
    sub = nt.nodes.new('ShaderNodeMath'); sub.operation = 'SUBTRACT'; sub.inputs[0].default_value = 1.0
    e = nt.nodes.new('ShaderNodeEmission'); e.inputs[0].default_value = [to_linear(c) for c in rgba[:3]] + [1]
    o = nt.nodes.new('ShaderNodeOutputMaterial')
    nt.links.new(lw.outputs['Facing'], mul.inputs[0]); nt.links.new(mul.outputs[0], sub.inputs[1])
    nt.links.new(sub.outputs[0], e.inputs[1]); nt.links.new(e.outputs[0], o.inputs[0])
    return m


def complement(rgba):
    """Opposite hue, saturation capped, value kept in 0.6-0.95 (never pure R/G/B)."""
    h, s, v = colorsys.rgb_to_hsv(*rgba[:3])
    return colorsys.hsv_to_rgb((h + 0.5) % 1.0, min(max(s, 0.45), 0.75), min(max(v, 0.6), 0.95)) + (1,)


def translucent(name, rgba, alpha=0.4):
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name); m.use_nodes = True; nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    e = nt.nodes.new('ShaderNodeEmission'); e.inputs[0].default_value = [to_linear(c) for c in rgba[:3]] + [1]
    t = nt.nodes.new('ShaderNodeBsdfTransparent'); mix = nt.nodes.new('ShaderNodeMixShader')
    mix.inputs[0].default_value = alpha
    o = nt.nodes.new('ShaderNodeOutputMaterial')
    nt.links.new(t.outputs[0], mix.inputs[1]); nt.links.new(e.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], o.inputs[0])
    for attr, value in (('surface_render_method', 'BLENDED'), ('blend_method', 'BLEND'), ('use_backface_culling', False)):
        if hasattr(m, attr):
            try:
                setattr(m, attr, value)
            except (TypeError, ValueError):
                pass
    return m


def build_scene(cfg):
    """Rebuild the measured scene (read only). Every mesh gets one flat colour: per character for skinned meshes,
    per object for props. Returns (characters for the triads, {mesh object: its colour})."""
    chars = []
    if cfg.get('builtin'):
        dots_blender.build_builtin(cfg['builtin'])
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        for t in cfg.get('characters') or []:
            new = dots_blender.load_file(t['file'])
            try:
                chars.append(dots_blender.Character(t['role'], new, t.get('bodyMesh'), t.get('boneMap'), t.get('armature')))
            except RuntimeError:
                pass                                                   # no triads for it; the colours still work
        for p in cfg.get('props') or []:
            dots_blender.load_file(p['file'] if isinstance(p, dict) else p)
    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    colour, props = {}, 0
    for o in sorted((o for o in bpy.data.objects if o.type == 'MESH'), key=lambda o: o.name):
        owner = next((i for i, a in enumerate(arms) if dots_blender.is_skinned_to(o, a)), None)
        if owner is not None:
            rgba = BODY[owner % len(BODY)]
        else:
            rgba = PROP[props % len(PROP)]; props += 1
        colour[o] = rgba
        o.data.materials.clear(); o.data.materials.append(flat('Flat_' + o.name, rgba))
        o.hide_render = False
    return chars, colour


def surface_meshes(surface, dot, colour, chars):
    """The meshes the measured surface label names ('prop:<name>', '<role>[.<bone>]', 'self[.<bone>]'); None when
    the label does not say ('nearest', unknown) and the caller has to guess; [] for the floor."""
    if not surface or surface == 'nearest':
        return None
    if surface == 'floor':
        return []
    if surface.startswith('prop:'):
        o = bpy.data.objects.get(surface[5:])
        return [o] if o in colour else None
    role = surface.partition('.')[0]
    if role == 'self':
        role = (dot or '').partition('.')[0]
    arms = [c.arm for c in chars if c.role == role]
    if not arms:
        every = [o for o in bpy.data.objects if o.type == 'ARMATURE']
        arms = every if len(every) == 1 else []
    found = [o for o in colour if any(dots_blender.is_skinned_to(o, a) for a in arms)]
    return found or None


def surface_at(q, p, colour, only=None):
    """The mesh under the surface point q (of `only` when the label names it, else not the one p sits on) and its
    normal there; None if none within 10 mm."""
    dg = bpy.context.evaluated_depsgraph_get()
    hits = []
    for o in (only if only else colour):
        ev = o.evaluated_get(dg); mw = ev.matrix_world; inv = mw.inverted()
        ok_q, loc_q, nor, _ = ev.closest_point_on_mesh(inv @ q)
        ok_p, loc_p, _, _ = ev.closest_point_on_mesh(inv @ p)
        if not ok_q:
            continue
        n = (mw.to_3x3().inverted().transposed() @ nor).normalized()
        hits.append(((mw @ loc_q - q).length, (mw @ loc_p - p).length if ok_p else 1e9, o, n))
    if not hits:
        return None
    own_p = min(hits, key=lambda h: h[1])[2] if len(hits) > 1 and not only else None
    best = min((h for h in hits if h[2] is not own_p), key=lambda h: h[0], default=None)
    if best is None or best[0] > 0.01:
        return None
    return best[2], best[3]


def frames_of(ch):
    """Semantic frames (name, origin, X, Y, Z) of one character: palm and wrist per hand, head, pelvis.
    X = the body's right (palm: toward the little finger), Y = up / out of the palm, Z = forward / toward the fingers."""
    def at(name):
        if name not in ch.rig_name:
            return None
        return ch.arm.matrix_world @ ch.bone(name).head
    out = []
    for side in ('Left', 'Right'):
        h, i, m, k = (at(side + n) for n in ('Hand', 'HandIndex1', 'HandMiddle1', 'HandPinky1'))
        if None in (h, i, m, k):
            continue
        z = (m - h).normalized(); x = k - i; x = (x - z * x.dot(z)).normalized()
        y = z.cross(x) if side == 'Right' else x.cross(z)
        out += [('%s palm' % side, (h + i + k) / 3, x, y, z), ('%s wrist' % side, h, x, y, z)]
    for name, bone, up_to, l_name, r_name in (('head', 'Head', None, 'LeftArm', 'RightArm'),
                                              ('pelvis', 'Hips', 'Spine', 'LeftUpLeg', 'RightUpLeg')):
        o, l, r = at(bone), at(l_name), at(r_name)
        if None in (o, l, r):
            continue
        top = at(up_to) if up_to else None
        y = ((top if top is not None else ch.arm.matrix_world @ ch.bone(bone).tail) - o).normalized()
        x = r - l; x = (x - y * x.dot(y)).normalized()
        out.append((name, o, x, y, y.cross(x)))
    return out


def on_screen(cam, scale, v, strip=0.0):
    """v inside the tile, clear of its edges and of the caption strip (height `strip`, metres) at the bottom."""
    loc = cam.matrix_world.inverted() @ v
    return abs(loc.x) < scale * 0.45 and scale * 0.45 > loc.y > -scale * 0.45 + strip


class Marks:
    """The dots, lines and arrows of one tile (removed before the next)."""

    def __init__(self, scene):
        self.col = bpy.data.collections.new('ContactDots'); scene.collection.children.link(self.col)

    def clear(self):
        for o in list(self.col.objects):
            bpy.data.objects.remove(o)

    def _obj(self, name, build, mat):
        me = bpy.data.meshes.new(name); bm = bmesh.new(); build(bm); bm.to_mesh(me); bm.free()
        me.materials.append(mat); o = bpy.data.objects.new(name, me); self.col.objects.link(o)
        return o

    def sphere(self, p, r, mat):
        o = self._obj('dot', lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=r), mat)
        o.location = p

    def rod(self, p, q, r, mat, r2=None, name='line'):
        v = q - p
        if v.length < 1e-5:
            return
        o = self._obj(name, lambda bm: bmesh.ops.create_cone(bm, cap_ends=True, segments=10, radius1=r,
                                                               radius2=r if r2 is None else r2, depth=v.length), mat)
        o.location = p + v / 2; o.rotation_euler = v.to_track_quat('Z', 'Y').to_euler()

    def ring(self, c, normal, radius, r, mat, n=28):
        """A ring facing the camera: marks a pair whose gap is too short to show a line."""
        a = normal.orthogonal().normalized(); b = normal.cross(a).normalized()
        pts = [c + (a * math.cos(2 * math.pi * i / n) + b * math.sin(2 * math.pi * i / n)) * radius for i in range(n + 1)]
        for u, v in zip(pts, pts[1:]):
            self.rod(u, v, r, mat, name='ring')

    def disc(self, c, normal, radius, mat):
        o = self._obj('disc', lambda bm: bmesh.ops.create_circle(bm, cap_ends=True, segments=32, radius=radius), mat)
        o.location = c + normal * 0.0004; o.rotation_euler = normal.to_track_quat('Z', 'Y').to_euler()

    def triad(self, origin, axes, length, r, lift, rim):
        for d, rgba in zip(axes, AXES):
            self.arrow(origin + lift, d, length, r, emission('Axis_%d_%d_%d' % tuple(int(c * 100) for c in rgba[:3]), rgba), rim)
        self.sphere(origin + lift, min(r * 1.2, length * 0.15), emission('TriadOrigin', (0.95, 0.95, 0.95, 1)))

    def arrow(self, p, d, length, r, mat, rim=None):
        tip = p + d * length; neck = p + d * (length * 0.78)
        if rim is not None:                                       # (dark material, offset away from the camera)
            dark, back = rim
            self.rod(p + back, neck + back, r * 1.8, dark, name='rim'); self.rod(neck + back, tip + back, r * 4.2, dark, r2=0.0, name='rim')
        self.rod(p, neck, r, mat, name='arrow'); self.rod(neck, tip, r * 3.2, mat, r2=0.0, name='head')


def camera(scene):
    data = bpy.data.cameras.new('SheetCam'); data.type = 'ORTHO'; data.clip_start = 0.001; data.clip_end = 100
    cam = bpy.data.objects.new('SheetCam', data); scene.collection.objects.link(cam); scene.camera = cam
    curve = bpy.data.curves.new('Caption', type='FONT'); curve.align_x = 'LEFT'; curve.align_y = 'TOP'
    text = bpy.data.objects.new('Caption', curve); scene.collection.objects.link(text)
    text.parent = cam; curve.materials.append(emission('Caption', COLOURS['text'], 1.0))
    plate = bpy.data.meshes.new('Plate')
    bm = bmesh.new(); bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5); bm.to_mesh(plate); bm.free()
    plate.materials.append(emission('Plate', (0.03, 0.03, 0.04, 1), 1.0))
    back = bpy.data.objects.new('Plate', plate); scene.collection.objects.link(back); back.parent = cam
    return cam, text, back


CAPTION = 0.042         # caption letter height, fraction of the tile
WRAP = 40               # caption characters per line
MARGIN = 1.35           # framed marks span at most 1/MARGIN of the free area


def wrap(caption):
    lines = []
    for line in caption.split('\n'):
        while len(line) > WRAP:
            cut = line.rfind(' ', 0, WRAP)
            cut = cut if cut > 0 else WRAP
            lines.append(line[:cut]); line = '  ' + line[cut:].strip()
        lines.append(line)
    return lines


def view_of(tile):
    """Look square onto the two arrows (align, range), else across the pair, slightly from above."""
    p, q = Vector(tile['p']), Vector(tile['q'])
    if 'da' in tile and 'db' in tile:
        n = Vector(tile['da']).cross(Vector(tile['db']))
        if n.length > 1e-3:
            return -n.normalized() if n.dot(UP) > 0 else n.normalized()
        da = Vector(tile['da'])                       # parallel arrows: look square onto da
        if da.length > 1e-6:
            da.normalize()
            n = da.cross(UP)
            if n.length < 1e-3:
                n = da.orthogonal()
            n.normalize()
            return -n if n.dot(UP) > 0 else n
    gap = q - p
    side = gap.cross(UP) if gap.length > 1e-4 else Vector((0, 0, 0))
    if side.length < 1e-6:
        side = Vector((1, -1, 0))
    side.normalize()
    look = (side * math.cos(math.radians(15)) - UP * math.sin(math.radians(15))).normalized()
    if 'n' in tile:                                   # a disc on the surface: look at it from 45 deg, from outside
        n = Vector(tile['n']).normalized()
        across = look - n * look.dot(n)
        look = (across.normalized() - n).normalized() if across.length > 1e-3 else -n
    return look


def arrow_length(tile):
    return max(0.06, (Vector(tile['p']) - Vector(tile['q'])).length * 0.8)


def arrow_bases(tile):
    """Where the two arrows start. Parallel arrows from (nearly) the same point would hide each other,
    so the second one moves aside, across the view, by half an arrow length."""
    p, q = Vector(tile['p']), Vector(tile['q'])
    L = arrow_length(tile)
    look = view_of(tile)
    gap = (q - p) - look * look.dot(q - p)                     # p->q as seen on screen
    if gap.length >= 0.35 * L:
        return p, q
    db = Vector(tile['db']).normalized()
    side = look.cross(db)
    if side.length < 1e-6:
        side = look.orthogonal()
    return p, q + side.normalized() * 0.5 * L


def frame_tile(cam, text, back, tile, extra=()):
    """Fit p, q, the arrow tips and `extra` (triad tips) inside the tile above the caption strip. Returns the ortho scale."""
    look = view_of(tile)
    rot = look.to_track_quat('-Z', 'Y')
    right, up = rot @ Vector((1, 0, 0)), rot @ Vector((0, 1, 0))
    p, q = Vector(tile['p']), Vector(tile['q'])
    pts = [p, q]
    if 'da' in tile and 'db' in tile:
        L = arrow_length(tile)
        pa, pb = arrow_bases(tile)
        pts += [pb, pa + Vector(tile['da']).normalized() * L, pb + Vector(tile['db']).normalized() * L]
    pts += list(extra)
    xs = [v.dot(right) for v in pts]; ys = [v.dot(up) for v in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    lines = wrap(tile['caption'])
    strip = CAPTION * (1.25 * len(lines) + 0.7)                               # fraction of the tile
    least = 0.15 if 'da' in tile else 0.12
    scale = max(least, w * MARGIN, h * MARGIN / (1 - strip))
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    centre = right * cx + up * (cy - scale * strip / 2) + look * look.dot((p + q) / 2)
    cam.data.ortho_scale = scale
    cam.location = centre - look * 3.0
    cam.rotation_euler = rot.to_euler()
    size = scale * CAPTION; strip_h = scale * strip
    text.data.body = '\n'.join(lines); text.data.size = size
    text.location = (-scale * 0.48, -scale * 0.5 + strip_h - size * 0.35, -0.01)
    back.scale = (scale, strip_h, 1); back.location = (0, -scale * 0.5 + strip_h / 2, -0.012)
    return scale


def compose(paths, out):
    imgs = []
    for path in paths:
        im = bpy.data.images.load(str(path)); w, h = im.size
        px = np.empty(w * h * 4, dtype=np.float32); im.pixels.foreach_get(px)
        imgs.append(px.reshape(h, w, 4)); bpy.data.images.remove(im)
    cols = min(COLS, len(imgs)); rows = (len(imgs) + cols - 1) // cols
    W = cols * TILE + (cols + 1) * GAP_PX; H = rows * TILE + (rows + 1) * GAP_PX
    sheet = np.zeros((H, W, 4), dtype=np.float32); sheet[..., :3] = 0.08; sheet[..., 3] = 1
    for i, px in enumerate(imgs):
        r, c = divmod(i, cols)
        x = GAP_PX + c * (TILE + GAP_PX); y = H - (r + 1) * (TILE + GAP_PX)        # pixel rows run bottom-up
        sheet[y:y + px.shape[0], x:x + px.shape[1]] = px
    im = bpy.data.images.new('dots-sheet', W, H, alpha=True)
    im.pixels.foreach_set(sheet.ravel()); im.filepath_raw = str(out); im.file_format = 'PNG'; im.save()
    return W, H


def main(job_path):
    began = time.time()
    job = json.loads(Path(job_path).read_text(encoding='utf-8'))
    cfg, out = job['config'], Path(job['out'])
    tiles_dir = out.parent / 'dots-sheet-tiles'; tiles_dir.mkdir(parents=True, exist_ok=True)
    for old in tiles_dir.glob('*.png'):
        old.unlink()
    chars, colour = build_scene(cfg)
    scene = bpy.context.scene
    world = bpy.data.worlds.new('World'); scene.world = world; world.use_nodes = True
    world.node_tree.nodes['Background'].inputs['Color'].default_value = (0.035, 0.038, 0.05, 1)
    bpy.ops.object.light_add(type='SUN', rotation=(math.radians(50), 0, math.radians(30))); bpy.context.object.data.energy = 3.0
    for engine in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    scene.render.resolution_x = scene.render.resolution_y = TILE; scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = 'Standard'
    image = scene.render.image_settings
    if hasattr(image, 'media_type'):
        image.media_type = 'IMAGE'
    image.file_format = 'PNG'; image.color_mode = 'RGBA'
    cam, text, back = camera(scene)
    marks = Marks(scene)
    roles = {r: i for i, r in enumerate(job.get('roles') or [])}
    paths = []
    for tile in job['tiles']:
        marks.clear()
        scene.frame_set(int(tile['frame']))
        arrows = 'da' in tile and 'db' in tile
        p, q = Vector(tile['p']), Vector(tile['q'])
        only = None if arrows else surface_meshes(tile.get('surface'), tile.get('dot'), colour, chars)
        if only == []:                                        # the floor: no mesh, a disc on the floor plane
            hit = (None, UP.copy())
        else:
            hit = None if arrows else surface_at(q, p, colour, only)
        tile.pop('n', None)
        if hit:
            n = hit[1]                                        # the face normal: out of the surface
            outside = tile.get('mm', 1.0) >= 0                # an inside dot sits behind the surface: keep it outward
            if (p - q).length > 1e-4 and (n.dot(p - q) < 0) == outside:
                n = -n                                        # only a reversed face normal gets turned round
            tile['n'] = list(n)
        scale = frame_tile(cam, text, back, tile)
        bpy.context.view_layer.update()                       # the camera just moved: on_screen() reads its matrix
        look = view_of(tile)
        arm_len = max(TRIAD_MM / 1000.0, TRIAD_PX / TILE * scale)
        triads = []
        if arm_len <= 3 * TRIAD_MM / 1000.0:
            for ch in chars:
                for _name, origin, x, y, z in frames_of(ch):
                    near_p = (origin - p) - look * look.dot(origin - p)
                    if near_p.length > 0.6 * arm_len and on_screen(cam, scale, origin, back.scale[1]):  # not on the contact
                        triads.append((origin, (x, y, z)))
        if triads:                                            # widen the view so whole triads clear the caption and edges
            tips = [o for o, _ in triads] + [o + d * arm_len * 1.15 for o, axes in triads for d in axes]
            wide = frame_tile(cam, text, back, tile, tips)
            if wide > TRIAD_ZOOM * scale:                     # too far out for the contact: keep the close view
                frame_tile(cam, text, back, tile)
            else:
                scale = wide
            bpy.context.view_layer.update()
        r = scale * 0.016
        base = look.dot((p + q) / 2 - cam.location)

        def lift(depth):                                      # moves a mark to `depth` metres before the camera
            return -look * (base - depth)
        dark = emission('Rim', (0.05, 0.05, 0.06, 1)); rim = (dark, look * 0.02)
        for name, pos in (job.get('dotsAt', {}).get(str(tile['frame'])) or {}).items():
            role = name.split('.')[0]
            rgba = ROLE_COLOURS[roles[role] % len(ROLE_COLOURS)] if role in roles else COLOURS['object']
            marks.sphere(Vector(pos), r * (0.3 if arrows else 0.75), emission('Dot_%s' % (role if role in roles else 'object'), rgba))
        verdict = emission('Verdict_' + ('pass' if tile['ok'] else 'fail'), COLOURS['pass' if tile['ok'] else 'fail'])
        if hit:                             # the disc stays on the real surface (depth-tested); edge and normal in front
            obj = hit[0]; n = Vector(tile['n']); key = obj.name if obj else 'floor'
            tint = complement(colour[obj]) if obj else COLOURS['floorDisc']; edge = emission('DiscEdge_' + key, tint)
            marks.disc(q, n, DISC_R, translucent('Disc_' + key, tint, DISC_ALPHA))
            marks.ring(q + lift(MARK_DEPTH + 0.3), n, DISC_R, r * 0.3, edge)
            marks.arrow(q + lift(MARK_DEPTH + 0.3), n, 0.025, r * 0.4, edge, rim)
        for origin, (x, y, z) in triads:
            for k in (1.0, 0.8, 0.6):                         # shorter arms until the whole triad (cone tips too)
                if all(on_screen(cam, scale, origin + d * arm_len * k * 1.15, back.scale[1]) for d in (x, y, z)):
                    marks.triad(origin, (x, y, z), arm_len * k, r * 0.45, lift(MARK_DEPTH + 0.15), rim)
                    break                                     # (none fits: no triad rather than one under the caption)
        p, q = p + lift(MARK_DEPTH), q + lift(MARK_DEPTH)
        if arrows:
            length = arrow_length(tile)
            pa, pb = arrow_bases(tile)
            pa, pb = pa + lift(MARK_DEPTH), pb + lift(MARK_DEPTH)
            marks.arrow(pa, Vector(tile['da']).normalized(), length, r * 0.7, emission('ArrowA', COLOURS['arrowA']), rim)
            marks.arrow(pb, Vector(tile['db']).normalized(), length, r * 0.7, verdict, rim)
            marks.sphere(pa, r * 1.3, emission('ArrowA', COLOURS['arrowA'])); marks.sphere(pb, r * 1.3, verdict)
            if (pb - q).length > 1e-6:
                marks.rod(q, pb, r * 0.25, verdict)                # thin tie back to the measured point
        else:
            marks.sphere(p, r * 1.7, verdict); marks.sphere(q, r * 1.4, emission('Surface', COLOURS['surface']))
            marks.rod(p + rim[1], q + rim[1], r * 0.8, dark, name='rim'); marks.rod(p, q, r * 0.45, verdict)
            marks.sphere(p + rim[1], r * 2.0, dark)
            if (p - q).length < r * 4:
                marks.ring((p + q) / 2 + rim[1], look, r * 4, r * 0.95, dark)
                marks.ring((p + q) / 2, look, r * 4, r * 0.6, verdict)
        path = tiles_dir / ('%s.png' % tile['name'])
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        paths.append(path)
        print('DOTS_SHEET_TILE', tile['name'], round(time.time() - began, 1), flush=True)
    w, h = compose(paths, out)
    print('DOTS_SHEET_DONE', json.dumps({'tiles': len(paths), 'size': [w, h], 'seconds': round(time.time() - began, 1)}),
          flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[sys.argv.index('--') + 1]))
