"""Contact dots close-up sheet. Runs INSIDE Blender (the host script dots.py launches it):
    blender -b --factory-startup --python-exit-code 1 -P dots_sheet_blender.py -- <dots-sheet-job.json>

The job (written by dots.py from a judged dots.json): out (the sheet png), config (the measured config: the scene is
rebuilt from its files, or from its 'builtin' selftest entry), dotsAt (frame -> dot -> position), roles (character
order for the dot colours) and tiles [{name, frame, p, q, ok, caption, da, db}] (metres, unit directions).

Per tile: the bodies see-through, every dot of that frame coloured per character (custom dots on objects in one
colour), the measured dot and its surface point larger, the pair line (yellow pass, magenta fail; a ring round a pair too close for a line), for align and range
the two directions as arrows; an orthographic close-up looks across the pair (or square onto the two arrows) with the
caption at the bottom. Tiles go to dots-sheet-tiles/<name>.png and are composed into the sheet. Reads only; writes
only beside the sheet. Prints DOTS_SHEET_DONE <json>.
"""
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
           'arrowA': (0.45, 0.65, 1.0, 1), 'text': (1, 1, 1, 1)}      # never pure R/G/B
# Palette values are display (sRGB) colours; emission() converts them to linear so the Standard view shows them as written.
SKIN = [(0.92, 0.66, 0.48, 1), (0.48, 0.58, 0.8, 1)]   # warm / cool by turns per mesh, apart from the background
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


def see_through(name, rgba, alpha=0.5):
    m = bpy.data.materials.new(name); m.use_nodes = True
    bsdf = m.node_tree.nodes['Principled BSDF']
    bsdf.inputs['Base Color'].default_value = rgba; bsdf.inputs['Alpha'].default_value = alpha
    for attr, value in (('surface_render_method', 'BLENDED'), ('blend_method', 'BLEND'), ('use_backface_culling', False)):
        if hasattr(m, attr):
            try:
                setattr(m, attr, value)
            except (TypeError, ValueError):
                pass
    return m


def build_scene(cfg):
    """Rebuild the measured scene (read only). Every mesh becomes see-through."""
    if cfg.get('builtin'):
        dots_blender.build_builtin(cfg['builtin'])
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        for t in cfg.get('characters') or []:
            dots_blender.load_file(t['file'])
        for p in cfg.get('props') or []:
            dots_blender.load_file(p['file'] if isinstance(p, dict) else p)
    for i, o in enumerate(o for o in bpy.data.objects if o.type == 'MESH'):
        mat = see_through('Skin_%d' % i, SKIN[i % 2])
        o.data.materials.clear(); o.data.materials.append(mat)
        o.hide_render = False


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

    def arrow(self, p, d, length, r, mat):
        tip = p + d * length; neck = p + d * (length * 0.78)
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
    return (side * math.cos(math.radians(15)) - UP * math.sin(math.radians(15))).normalized()


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


def frame_tile(cam, text, back, tile):
    """Fit p, q and the arrow tips inside the tile above the caption strip. Returns the ortho scale."""
    look = view_of(tile)
    rot = look.to_track_quat('-Z', 'Y')
    right, up = rot @ Vector((1, 0, 0)), rot @ Vector((0, 1, 0))
    p, q = Vector(tile['p']), Vector(tile['q'])
    pts = [p, q]
    if 'da' in tile and 'db' in tile:
        L = arrow_length(tile)
        pa, pb = arrow_bases(tile)
        pts += [pb, pa + Vector(tile['da']).normalized() * L, pb + Vector(tile['db']).normalized() * L]
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
    build_scene(cfg)
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
        scale = frame_tile(cam, text, back, tile)
        r = scale * 0.016
        arrows = 'da' in tile and 'db' in tile
        for name, pos in (job.get('dotsAt', {}).get(str(tile['frame'])) or {}).items():
            role = name.split('.')[0]
            rgba = ROLE_COLOURS[roles[role] % len(ROLE_COLOURS)] if role in roles else COLOURS['object']
            marks.sphere(Vector(pos), r * (0.3 if arrows else 0.75), emission('Dot_%s' % (role if role in roles else 'object'), rgba))
        verdict = emission('Verdict_' + ('pass' if tile['ok'] else 'fail'), COLOURS['pass' if tile['ok'] else 'fail'])
        p, q = Vector(tile['p']), Vector(tile['q'])
        lift = -view_of(tile) * 0.15        # measured marks sit toward the camera, in front of the see-through meshes;
        p, q = p + lift, q + lift           # the camera is ortho, so they keep their spot on screen
        if arrows:
            length = arrow_length(tile)
            pa, pb = arrow_bases(tile)
            pa, pb = pa + lift, pb + lift
            marks.arrow(pa, Vector(tile['da']).normalized(), length, r * 0.7, emission('ArrowA', COLOURS['arrowA']))
            marks.arrow(pb, Vector(tile['db']).normalized(), length, r * 0.7, verdict)
            marks.sphere(pa, r * 1.3, emission('ArrowA', COLOURS['arrowA'])); marks.sphere(pb, r * 1.3, verdict)
            if (pb - q).length > 1e-6:
                marks.rod(q, pb, r * 0.25, verdict)                # thin tie back to the measured point
        else:
            marks.sphere(p, r * 1.7, verdict); marks.sphere(q, r * 1.4, emission('Surface', COLOURS['surface']))
            marks.rod(p, q, r * 0.45, verdict)
            if (p - q).length < r * 4:
                marks.ring((p + q) / 2, view_of(tile), r * 4, r * 0.6, verdict)
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
