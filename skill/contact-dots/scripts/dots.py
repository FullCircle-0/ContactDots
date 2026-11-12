#!/usr/bin/env python3
"""contact-dots host script (standard library only). Measures in headless Blender whether things that should touch,
sit, stay or line up really do; it never changes a source file.

  python dots.py selftest [--out DIR]               synthetic scene with known answers (run first on a new machine)
  python dots.py bones <file.fbx|.blend>            the armature's bones and the proposed bone map
  python dots.py measure <config.json>              measure, judge, write dots.json and dots-sheet.png, print the summary
  python dots.py sheet <dots.json>                  render the close-up sheet dots-sheet.png beside dots.json

Options: --blender PATH (else $BLENDER, PATH, the usual install folders), --timeout S (default 600),
         --limits finger|limb (overrides the config's limits).
First output line: `dots: PASS ...` (exit 0), `dots: FAIL ...` (exit 1) or `dots: skipped (<reason>)` (exit 2).
"""
import argparse
import glob
import json
import os
import queue
import re
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEASURER = HERE / 'dots_blender.py'
SHEETER = HERE / 'dots_sheet_blender.py'
PRESETS = {'finger': {'offMm': 3.0, 'insideMm': 2.0}, 'limb': {'offMm': 10.0, 'insideMm': 10.0}}
DEFAULT_PRESET = 'finger'
TIMEOUT_S = 600
PRINT_ROWS = 6
CONFIG_KEYS = {'out', 'characters', 'props', 'frames', 'calibFrame', 'contacts', 'checks', 'dots', 'floorZ', 'signRangeM',
               'observeMm', 'joints', 'closeups', 'limits', 'snapshot', 'name'}


class Skipped(RuntimeError):
    """The run could not happen (no Blender, timeout, Blender error, bad config): exit 2, never a FAIL."""


# ---------------------------------------------------------------- Blender

def _version_key(path):
    nums = re.findall(r'\d+', str(Path(path).parent.name))
    return [int(n) for n in nums] or [0]


def blender_candidates():
    """Usual install folders, newest version first."""
    pats = []
    if os.name == 'nt':
        roots = {os.environ.get(k) for k in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)', 'LOCALAPPDATA')} - {None}
        roots |= {d + ':' + os.sep for d in string.ascii_uppercase if os.path.isdir(d + ':' + os.sep)}
        for r in roots:
            for sub in ('Blender Foundation', os.path.join('Blender', 'Blender Foundation'),
                        os.path.join('Steam', 'steamapps', 'common')):
                pats.append(os.path.join(r, sub, '*', 'blender.exe'))
                pats.append(os.path.join(r, sub, 'Blender', 'blender.exe'))
    elif sys.platform == 'darwin':
        pats += ['/Applications/Blender*.app/Contents/MacOS/Blender',
                 os.path.expanduser('~/Applications/Blender*.app/Contents/MacOS/Blender')]
    else:
        pats += ['/usr/bin/blender', '/usr/local/bin/blender', '/snap/bin/blender', '/opt/blender*/blender',
                 os.path.expanduser('~/blender*/blender')]
    found = {p for pat in pats for p in glob.glob(pat)}
    return sorted(found, key=_version_key, reverse=True)


def find_blender(explicit=None):
    for c in (explicit, os.environ.get('BLENDER')):
        if c:
            if Path(c).is_file():
                return str(Path(c))
            raise Skipped('no Blender at %s' % c)
    on_path = shutil.which('blender')
    if on_path:
        return on_path
    found = blender_candidates()
    if found:
        return found[0]
    raise Skipped('no Blender found: pass --blender PATH or set BLENDER')


def run_blender(exe, script, args, timeout, done_tag, echo=print, show=('DOTS_WARNING', 'DOTS_SELFTEST')):
    """Run a Blender script; returns (seconds, tagged lines). Raises Skipped on a timeout or a Blender error."""
    began = time.time(); lines = []
    cmd = [exe, '-b', '--factory-startup', '--python-exit-code', '1', '-P', str(script), '--'] + [str(a) for a in args]
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    except OSError as e:
        raise Skipped('cannot start Blender: %s' % e)
    feed = queue.Queue()

    def pump():   # own thread, so a silent hang still meets the clock below
        for raw in process.stdout:
            feed.put(raw)
        feed.put(None)
    threading.Thread(target=pump, daemon=True).start()
    try:
        while True:
            left = timeout - (time.time() - began)
            if left <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout)
            try:
                line = feed.get(timeout=min(left, 1.0))
            except queue.Empty:
                continue
            if line is None:
                break
            line = line.rstrip()
            if line.startswith(('DOTS_', 'Error', 'Traceback', 'RuntimeError')) or 'Error:' in line:
                lines.append(line)
                if line.startswith(show):
                    echo('  ' + line.split(' ', 1)[-1])
        process.wait(timeout=max(1, timeout - (time.time() - began)))
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise Skipped('Blender exceeded %d s and was stopped' % timeout)
    if process.returncode != 0 or not any(l.startswith(done_tag) for l in lines):
        errors = [l for l in lines if 'Error' in l]
        why = next((l for l in reversed(errors) if re.match(r'\w+Error: ', l)), errors[-1] if errors else
                   'exit %s' % process.returncode)
        why = re.sub(r'^RuntimeError: ', '', why)
        raise Skipped('Blender failed: %s' % why[:200])
    return round(time.time() - began, 1), lines


# ---------------------------------------------------------------- config

def limits_of(spec):
    """'finger' | 'limb' | {"preset", "offMm", "insideMm", ...} -> {'name', 'offMm', 'insideMm'}."""
    if spec is None:
        spec = DEFAULT_PRESET
    if isinstance(spec, str):
        if spec not in PRESETS:
            raise Skipped('bad config: limits %r (finger, limb or numbers)' % spec)
        return dict(PRESETS[spec], name=spec)
    if isinstance(spec, dict):
        base = spec.get('preset', DEFAULT_PRESET)
        if base not in PRESETS:
            raise Skipped('bad config: limits preset %r' % base)
        out = dict(PRESETS[base], name=base if set(spec) <= {'preset'} else 'custom')
        for k, v in spec.items():
            if k != 'preset':
                if k not in ('offMm', 'insideMm'):
                    raise Skipped('bad config: limits.%s (use preset, offMm, insideMm)' % k)
                if not isinstance(v, (int, float)):
                    raise Skipped('bad config: limits.%s must be a number' % k)
                out[k] = float(v)
        return out
    raise Skipped('bad config: limits must be a preset name or an object')


def _abs(base, p):
    return str((base / p).resolve()) if not Path(p).is_absolute() else str(Path(p))


def validate(cfg, base):
    """Check and resolve a config (paths relative to `base`). Returns a new dict; raises Skipped('bad config: ...')."""
    try:
        return _validate(cfg, base)
    except (TypeError, KeyError, AttributeError, ValueError) as e:   # a value of the wrong type
        raise Skipped('bad config: a value has the wrong type (%s: %s)' % (type(e).__name__, e))


def _validate(cfg, base):
    if not isinstance(cfg, dict):
        raise Skipped('bad config: not a json object')
    unknown = set(cfg) - CONFIG_KEYS
    if unknown:
        raise Skipped('bad config: unknown keys %s' % sorted(unknown))
    out = dict(cfg)
    if not cfg.get('out'):
        raise Skipped('bad config: no "out" folder')
    out['out'] = _abs(base, cfg['out'])
    roles = set()
    chars = []
    for i, c in enumerate(cfg.get('characters') or []):
        if not isinstance(c, dict) or not c.get('role') or not c.get('file'):
            raise Skipped('bad config: characters[%d] needs "role" and "file"' % i)
        if '.' in c['role'] or c['role'] in roles or c['role'] in ('self', 'floor', 'nearest') or c['role'].startswith('prop:'):
            raise Skipped('bad config: role %r must be unique, without dots, not self/floor/nearest' % c['role'])
        roles.add(c['role'])
        f = _abs(base, c['file'])
        if Path(f).suffix.lower() not in ('.fbx', '.blend'):
            raise Skipped('bad config: %s is not an FBX or .blend' % Path(f).name)
        if not Path(f).is_file():
            raise Skipped('bad config: no file %s' % f)
        chars.append(dict(c, file=f))
    out['characters'] = chars
    props = []
    for p in cfg.get('props') or []:
        f = _abs(base, p['file'] if isinstance(p, dict) else p)
        if not Path(f).is_file():
            raise Skipped('bad config: no prop file %s' % f)
        props.append({'file': f})
    out['props'] = props
    if not chars and not cfg.get('dots') and not props:
        raise Skipped('bad config: nothing to measure (no characters, props or dots)')
    fr = cfg.get('frames', 'all')
    if not (fr == 'all' or (isinstance(fr, list) and all(isinstance(x, int) for x in fr))
            or (isinstance(fr, dict) and isinstance(fr.get('range'), list) and len(fr['range']) == 2)):
        raise Skipped('bad config: frames must be "all", a list of frame numbers or {"range": [first, last]}')
    dot_names = validate_dots(cfg.get('dots'))
    ids = set()
    contacts = [dict(c) if isinstance(c, dict) else c for c in cfg.get('contacts') or []]
    for i, c in enumerate(contacts):
        if not isinstance(c, dict) or not c.get('a') or not c.get('b'):
            raise Skipped('bad config: contacts[%d] needs "a" and "b"' % i)
        c.setdefault('id', 'contact%d' % i)
        validate_touch(c, ids, roles, dot_names)
    checks = []
    for i, c in enumerate(cfg.get('checks') or []):
        if not isinstance(c, dict):
            raise Skipped('bad config: checks[%d] is not an object' % i)
        c = dict(c); c.setdefault('id', 'check%d' % i); c.setdefault('kind', 'touch')
        if c['kind'] == 'touch':
            if not c.get('a') or not c.get('b'):
                raise Skipped('bad config: check %s needs "a" and "b"' % c['id'])
            validate_touch(c, ids, roles, dot_names)
            contacts.append({k: v for k, v in c.items() if k != 'kind'})
            continue
        validate_check(c, ids, dot_names)
        checks.append(c)
    out['contacts'], out['checks'] = contacts, checks
    out['limits'] = limits_of(cfg.get('limits'))
    if cfg.get('snapshot'):
        snap = Path(_abs(Path(out['out']), cfg['snapshot']['path']))
        if Path(out['out']) not in snap.parents:
            raise Skipped('bad config: snapshot.path must be inside the out folder')
        out['snapshot'] = dict(cfg['snapshot'], path=str(snap))
    return out


KINDS = ('touch', 'seat', 'rest', 'hold', 'distance', 'align', 'range')
DIRECTION_KEYS = ('world', 'from', 'normal', 'palm', 'bone', 'object')


def validate_dots(dots):
    names = set()
    for i, d in enumerate(dots or []):
        if not isinstance(d, dict) or not d.get('name'):
            raise Skipped('bad config: dots[%d] needs a "name"' % i)
        if d['name'] in names:
            raise Skipped('bad config: duplicate dot name %r' % d['name'])
        if not d.get('bone') and not d.get('object'):
            raise Skipped('bad config: dot %s needs "bone" or "object"' % d['name'])
        r = d.get('radius', 0)
        if r != 'measure' and not isinstance(r, (int, float)):
            raise Skipped('bad config: dot %s radius must be mm, "measure" or 0' % d['name'])
        names.add(d['name'])
    return names


def _new_id(c, ids):
    if c['id'] in ids:
        raise Skipped('bad config: duplicate id %r' % c['id'])
    ids.add(c['id'])


def validate_touch(c, ids, roles, dot_names):
    _new_id(c, ids)
    if c['a'] not in dot_names and c['a'].split('.')[0] not in roles:
        raise Skipped('bad config: contact %s: "a" must be a custom dot or start with a character role %s' % (
            c['id'], sorted(roles)))


def _direction_ok(d):
    return isinstance(d, str) or (isinstance(d, dict) and any(k in d for k in DIRECTION_KEYS))


def validate_check(c, ids, dot_names):
    kind = c['kind']
    if kind not in KINDS:
        raise Skipped('bad config: check %s: kind %r (use %s)' % (c['id'], kind, ', '.join(KINDS)))
    _new_id(c, ids)
    for key in ('max', 'min', 'maxDeg', 'offMm', 'insideMm'):
        if key in c and not isinstance(c[key], (int, float)):
            raise Skipped('bad config: check %s: %s must be a number' % (c['id'], key))
    if kind in ('seat', 'distance'):
        for k in ('a', 'b'):
            if c.get(k) not in dot_names:
                raise Skipped('bad config: check %s: %s must be a dot name (custom dots: %s)' % (
                    c['id'], k, sorted(dot_names)[:10]))
        if kind == 'distance' and 'min' not in c and 'max' not in c:
            raise Skipped('bad config: check %s: distance needs "min" and/or "max" (mm)' % c['id'])
    elif kind == 'rest':
        if not isinstance(c.get('a'), list) or len(c['a']) < 3 or not isinstance(c.get('b'), str):
            raise Skipped('bad config: check %s: rest needs "a": 3 or more dots and "b": a surface' % c['id'])
    elif kind == 'hold':
        if not isinstance(c.get('a'), str):
            raise Skipped('bad config: check %s: hold needs "a": a dot' % c['id'])
    else:
        for k in ('a', 'b') + (('axis',) if c.get('axis') is not None else ()):
            if not _direction_ok(c.get(k)):
                raise Skipped('bad config: check %s: %s must be a direction (%s, or a world axis)' % (
                    c['id'], k, ', '.join(DIRECTION_KEYS)))
        if kind == 'range' and 'min' not in c and 'max' not in c:
            raise Skipped('bad config: check %s: range needs "min" and/or "max" (degrees)' % c['id'])


# ---------------------------------------------------------------- judging

INF = float('inf')


def _short(x):
    if isinstance(x, list):
        return '%s+%d' % (x[0], len(x) - 1) if x else '?'
    if isinstance(x, dict):
        return ' '.join(str(v) for v in x.values())
    return str(x)


def judge_check(c, limits):
    """One structural or rotational check -> a summary row; sets ok, worst and limit on the check."""
    kind, vals, unit = c['kind'], c.get('values') or {}, c.get('unit', 'mm')
    what = '%s -> %s' % (_short(c.get('a')), _short(c.get('b')))
    if not vals:
        c['ok'] = False
        return {'id': c['id'], 'kind': kind, 'what': what, 'ok': False, 'reason': 'not measured'}
    hi = max(vals, key=lambda f: vals[f]['v']); lo = min(vals, key=lambda f: vals[f]['v'])
    v_hi, v_lo = vals[hi]['v'], vals[lo]['v']
    if kind == 'rest':
        off, inside = c.get('offMm', limits['offMm']), c.get('insideMm', limits['insideMm'])
        lo = min(vals, key=lambda f: vals[f]['min']); v_lo = vals[lo]['min']
        ok = v_hi <= off and v_lo >= -inside
        limit = 'every dot off <= %g mm, inside <= %g mm' % (off, inside)
        text = 'highest dot %+.1f mm f%s, lowest %+.1f mm f%s' % (v_hi, hi, v_lo, lo)
        frame = lo if v_lo < -inside else hi
    elif kind in ('distance', 'range'):
        mn, mx = c.get('min', -INF), c.get('max', INF)
        ok = v_lo >= mn and v_hi <= mx
        limit = 'inside [%g, %g] %s' % (mn, mx, unit)
        text = 'from %.1f %s f%s to %.1f %s f%s' % (v_lo, unit, lo, v_hi, unit, hi)
        frame = lo if v_lo < mn else hi
    else:
        default = {'seat': 1.0, 'hold': limits['offMm'], 'align': 5.0}[kind]
        mx = c.get('maxDeg' if kind == 'align' else 'max', default)
        ok = v_hi <= mx
        limit = '<= %g %s' % (mx, unit)
        text = 'worst %.1f %s f%s' % (v_hi, unit, hi)
        frame = hi
    c.update(ok=ok, worst=v_hi, worstFrame=int(hi), least=v_lo, leastFrame=int(lo), limit=limit)
    return {'id': c['id'], 'kind': kind, 'what': what, 'ok': ok, 'text': '%s (limit %s)' % (text, limit),
            'frame': int(frame)}

def judge(payload, limits):
    """Apply the limits. Adds 'verdict', and 'worstMm'/'ok' per contact; returns the verdict."""
    off, inside = limits['offMm'], limits['insideMm']
    rows = []
    for c in payload.get('contacts', []):
        gaps = c.get('gaps') or {}
        keys = [str(f) for f in (c.get('keys') or c.get('frames') or []) if str(f) in gaps] or list(gaps)
        if not keys:
            c['ok'] = False
            rows.append({'id': c['id'], 'kind': 'touch', 'what': '%s -> %s' % (c['a'], c['b']), 'ok': False,
                         'reason': 'not measured'})
            continue
        key_at = max(keys, key=lambda f: gaps[f]['mm'])
        deep_at = min(gaps, key=lambda f: gaps[f]['mm'])
        ok = gaps[key_at]['mm'] <= off and gaps[deep_at]['mm'] >= -inside
        c.update(worstMm=gaps[key_at]['mm'], worstFrame=int(key_at), deepestMm=gaps[deep_at]['mm'],
                 deepestFrame=int(deep_at), ok=ok, limit='off <= %g mm, inside <= %g mm' % (off, inside))
        rows.append({'id': c['id'], 'kind': 'touch', 'what': '%s -> %s' % (c['a'], c['b']), 'ok': ok,
                     'text': 'worst %+.1f mm f%d (%s on %s), deepest %+.1f mm f%d (limit off <= %g, inside <= %g mm)' % (
                         gaps[key_at]['mm'], int(key_at), gaps[key_at]['dot'], gaps[key_at]['surface'],
                         gaps[deep_at]['mm'], int(deep_at), off, inside),
                     'frame': int(deep_at) if gaps[deep_at]['mm'] < -inside else int(key_at)})
    for c in payload.get('checks', []):
        rows.append(judge_check(c, limits))
    pens = [p for p in payload.get('penetrations', []) if p['mm'] < -inside]
    verdict = {'limits': limits, 'checks': rows, 'penetrationsOverLimit': len(pens),
               'status': 'PASS' if all(r['ok'] for r in rows) and not pens else 'FAIL'}
    payload['verdict'] = verdict
    return verdict


def describe(payload, name=''):
    v = payload['verdict']; lim = v['limits']
    rows, pens = v['checks'], [p for p in payload.get('penetrations', []) if p['mm'] < -lim['insideMm']]
    seconds = payload.get('wallSeconds', payload.get('seconds'))
    head = 'dots: %s %s limits %s (off <= %g mm, inside <= %g mm): checks %d/%d pass, %d penetration(s) deeper than %g mm | %s s' % (
        v['status'], name, lim.get('name', 'custom'), lim['offMm'], lim['insideMm'], sum(r['ok'] for r in rows), len(rows),
        len(pens), lim['insideMm'], seconds)
    lines = [' '.join(head.split())]
    for r in rows[:PRINT_ROWS]:
        lines.append('  %s %s %s: %s  %s' % (r['kind'], r['id'], r['what'], r.get('text', r.get('reason', '')),
                                             'PASS' if r['ok'] else 'FAIL'))
    if len(rows) > PRINT_ROWS:
        lines.append('  ... %d more checks in dots.json' % (len(rows) - PRINT_ROWS))
    for p in pens[:PRINT_ROWS]:
        lines.append('  inside %s in %s %+.1f mm f%d' % (p['dot'], p['surface'], p['mm'], p['frame']))
    if len(pens) > PRINT_ROWS:
        lines.append('  ... %d more penetrations in dots.json' % (len(pens) - PRINT_ROWS))
    if not rows:
        near = [o for o in payload.get('observed', []) if o['mm'] >= -lim['insideMm']][:PRINT_ROWS]
        lines.append('  no checks declared; observed within %g mm: %s' % (
            payload.get('observeMm', 30.0),
            ', '.join('%s~%s %+.1f f%d' % (o['dot'], o['surface'], o['mm'], o['frame']) for o in near) or 'none'))
    roots = payload.get('roots') or {}
    if len(roots) > 1:
        first = str(payload['frames'][0])
        lines.append('  world check f%s roots (m): %s  <- confirm the characters stand where expected' % (
            first, ', '.join('%s (%s)' % (r, ', '.join('%.2f' % x for x in fr.get(first, []))) for r, fr in roots.items())))
    if payload.get('warnings'):
        lines.append('  %d dot(s) dropped for missing bones (dots.json warnings)' % len(payload['warnings']))
    return lines


def skipped(reason):
    return ['dots: skipped (%s)' % reason]


# ---------------------------------------------------------------- commands

def cmd_measure(args, echo=print):
    path = Path(args.config).resolve()
    try:
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as e:
            raise Skipped('bad config: %s' % e)
        cfg = validate(raw, path.parent)
        if args.limits:
            cfg['limits'] = limits_of(args.limits)
        out = Path(cfg['out']); out.mkdir(parents=True, exist_ok=True)
        resolved = out / 'dots-config.json'
        resolved.write_text(json.dumps(cfg, indent=1), encoding='utf-8')
        (out / 'dots.json').unlink(missing_ok=True)
        exe = find_blender(args.blender)
        seconds, _ = run_blender(exe, MEASURER, [resolved], args.timeout, 'DOTS_DONE', echo)
        payload = json.loads((out / 'dots.json').read_text(encoding='utf-8'))
    except Skipped as why:
        for line in skipped(str(why)):
            echo(line)
        return 2
    payload['wallSeconds'] = seconds
    judge(payload, cfg['limits'])
    lines = describe(payload, cfg.get('name') or path.stem)
    payload['summary'] = lines
    (out / 'dots.json').write_text(json.dumps(payload, indent=1), encoding='utf-8')
    if not args.no_sheet:
        try:
            png, tiles = make_sheet(out / 'dots.json', exe, args.timeout, echo)
            lines.append('  sheet %s (%d close-ups; LOOK at it)' % (png, len(tiles)) if png else
                         '  sheet: nothing to show')
        except Skipped as why:
            lines.append('  sheet skipped (%s); the numbers stand' % why)
        payload['summary'] = lines
        (out / 'dots.json').write_text(json.dumps(payload, indent=1), encoding='utf-8')
    for line in lines:
        echo(line)
    return 0 if payload['verdict']['status'] == 'PASS' else 1


def cmd_selftest(args, echo=print):
    out = Path(args.out or os.path.join(tempfile.gettempdir(), 'contact-dots-selftest')).resolve()
    try:
        exe = find_blender(args.blender)
        out.mkdir(parents=True, exist_ok=True)
        seconds, lines = run_blender(exe, MEASURER, ['--selftest', out, '--rig', args.rig], args.timeout, 'DOTS_SELFTEST', echo,
                                     show=('DOTS_SELFTEST',))
    except Skipped as why:
        echo(skipped(str(why))[0])
        return 2
    ok = any(l.strip() == 'DOTS_SELFTEST PASS' for l in lines)
    ok &= judge_rigid(out / 'rigid' / 'dots.json', echo)
    echo('dots: %s selftest (known gaps and angles within 0.1, rigid checks judged as expected) | %s s | %s' % (
        'PASS' if ok else 'FAIL', seconds, out))
    return 0 if ok else 1


def judge_rigid(path, echo=print):
    """The rigid selftest's checks, judged with the default limits, must pass or fail as each one's 'expect' says."""
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        echo('  rigid selftest: no %s' % path)
        return False
    judge(payload, limits_of(None))
    good = True
    for c in payload['checks']:
        right = c['ok'] == (c.get('expect') == 'pass')
        good &= right
        if not right:
            echo('  rigid %s judged %s, expected %s (%s)' % (c['id'], 'pass' if c['ok'] else 'fail', c.get('expect'),
                                                             c.get('limit')))
    payload['summary'] = describe(payload, 'rigid selftest')
    Path(path).write_text(json.dumps(payload, indent=1), encoding='utf-8')
    return good


def describe_bones(path, arms):
    if not arms:
        return ['dots: FAIL bones %s: no armature in the file' % Path(path).name]
    good = any(a['usable'] for a in arms)
    lines = ['dots: %s bones %s: %d armature(s)' % ('PASS' if good else 'FAIL', Path(path).name, len(arms))]
    for a in arms:
        lines.append('  armature %s: %d bones, preset %s (%d matched; scores %s) %s' % (
            a['armature'], len(a['bones']), a['preset'], len(a['map']),
            ', '.join('%s %d' % kv for kv in a['scores'].items()),
            'usable' if a['usable'] else 'NO BONE MAP: add "boneMap": {canonical: rig bone} to the character'))
        lines.append('  map: ' + ', '.join('%s=%s' % kv for kv in a['map'].items()))
        lines.append('  unmatched: ' + (', '.join(a['unmatched']) or 'none'))
        lines.append('  skinned meshes: ' + (', '.join(a['skinnedMeshes']) or 'none'))
    return lines


def cmd_bones(args, echo=print):
    path = Path(args.file).resolve()
    try:
        if not path.is_file():
            raise Skipped('no file %s' % path)
        exe = find_blender(args.blender)
        _, lines = run_blender(exe, MEASURER, ['--bones', path], args.timeout, 'DOTS_BONES', echo)
    except Skipped as why:
        echo(skipped(str(why))[0])
        return 2
    arms = json.loads(next(l for l in lines if l.startswith('DOTS_BONES')).split(' ', 1)[1])
    out = describe_bones(path, arms)
    for line in out:
        echo(line)
    return 0 if out[0].startswith('dots: PASS') else 1


CLOSEUPS = 6


def _caption(head, value, limit, ok):
    return '%s\n%s  (limit %s)  %s' % (head, value, limit, 'PASS' if ok else 'FAIL')


def sheet_tiles(payload):
    """The close-ups of a judged dots.json: one per declared contact or check (at its verdict frame) and one per
    penetration deeper than the inside limit (worst first, at most `closeups`). Each tile: name, frame, p, q (metres),
    ok, caption, mm (signed gap) for touch and inside, and da/db (unit directions) for align and range."""
    v = payload['verdict']; rows = {r['id']: r for r in v['checks']}
    tiles, used = [], set()

    def add(name, frame, ok, caption, p, q, **extra):
        base, i = re.sub(r'[^\w.-]+', '_', name)[:60] or 'tile', 1
        while base + ('' if i == 1 else '-%d' % i) in used:
            i += 1
        name = base + ('' if i == 1 else '-%d' % i); used.add(name)
        tiles.append(dict({'name': name, 'frame': int(frame), 'ok': bool(ok), 'caption': caption, 'p': p, 'q': q}, **extra))

    for c in payload.get('contacts', []):
        r = rows.get(c['id'])
        if not r or 'frame' not in r:
            continue
        g = c['gaps'][str(r['frame'])]
        add('touch-' + c['id'], r['frame'], r['ok'], _caption(
            'touch %s  f%d  %s on %s' % (c['id'], r['frame'], g['dot'], g['surface']), 'gap %+.1f mm' % g['mm'],
            c.get('limit', ''), r['ok']), g['p'], g['q'], mm=g['mm'])
    for c in payload.get('checks', []):
        r = rows.get(c['id'])
        if not r or 'frame' not in r:
            continue
        val = c['values'][str(r['frame'])]
        unit = c.get('unit', 'mm')
        extra = {k: val[k] for k in ('da', 'db') if k in val}
        add('%s-%s' % (c['kind'], c['id']), r['frame'], r['ok'], _caption(
            '%s %s  f%d  %s' % (c['kind'], c['id'], r['frame'], r['what']),
            '%.1f %s' % (val['v'], 'deg' if unit.startswith('deg') else unit), c.get('limit', ''), r['ok']),
            val['p'], val['q'], **extra)
    inside = v['limits']['insideMm']
    cap = int((payload.get('config') or {}).get('closeups', CLOSEUPS))
    for pen in [x for x in payload.get('penetrations', []) if x['mm'] < -inside][:cap]:
        add('inside-%s-f%d' % (pen['dot'], pen['frame']), pen['frame'], False, _caption(
            'inside  f%d  %s in %s' % (pen['frame'], pen['dot'], pen['surface']), 'gap %+.1f mm' % pen['mm'],
            'inside <= %g mm' % inside, False), pen['p'], pen['q'], mm=pen['mm'])
    return tiles


def make_sheet(dots_path, exe, timeout, echo=print):
    """Render dots-sheet.png beside a dots.json (judging it first if the host has not). Returns (png, tiles)."""
    dots_path = Path(dots_path).resolve()
    try:
        payload = json.loads(dots_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise Skipped('cannot read %s: %s' % (dots_path, e))
    if 'config' not in payload:
        raise Skipped('%s has no config (measure it again with this version)' % dots_path.name)
    if 'verdict' not in payload:
        judge(payload, limits_of(payload['config'].get('limits')))
    tiles = sheet_tiles(payload)
    png = dots_path.parent / 'dots-sheet.png'
    png.unlink(missing_ok=True)
    if not tiles:
        return None, tiles
    roles = sorted({d.split('.')[0] for f in payload.get('dotsAt', {}).values() for d in f
                    if d.split('.')[0] in (payload.get('roles') or {})})
    job = {'dots': str(dots_path), 'out': str(png), 'tiles': tiles, 'roles': roles,
           'config': payload['config'], 'dotsAt': payload.get('dotsAt', {})}
    job_path = dots_path.parent / 'dots-sheet-job.json'
    job_path.write_text(json.dumps(job, indent=1), encoding='utf-8')
    run_blender(exe, SHEETER, [job_path], timeout, 'DOTS_SHEET_DONE', echo)
    if not png.is_file():
        raise Skipped('the sheet script wrote no %s' % png.name)
    return png, tiles


def cmd_sheet(args, echo=print):
    try:
        exe = find_blender(args.blender)
        png, tiles = make_sheet(args.dots_json, exe, args.timeout, echo)
    except Skipped as why:
        echo(skipped(str(why))[0])
        return 2
    if png is None:
        echo('dots: sheet: nothing to show (no checks declared, no penetration over the limit)')
        return 0
    echo('dots: sheet %s (%d close-ups; LOOK at it before reporting)' % (png, len(tiles)))
    return 0


def main(argv=None, echo=print):
    ap = argparse.ArgumentParser(prog='dots', description=__doc__.split('\n')[1])
    sub = ap.add_subparsers(dest='command', required=True)
    for name in ('selftest', 'bones', 'measure', 'sheet'):
        p = sub.add_parser(name)
        p.add_argument('--blender')
        p.add_argument('--timeout', type=float, default=TIMEOUT_S)
        if name == 'selftest':
            p.add_argument('--out')
            p.add_argument('--rig', default='plain', choices=('plain', 'mixamo', 'unity', 'unknown'),
                           help='bone names of the synthetic character (bone map test)')
        if name == 'bones':
            p.add_argument('file')
        if name == 'measure':
            p.add_argument('config')
            p.add_argument('--limits', choices=sorted(PRESETS))
            p.add_argument('--no-sheet', action='store_true', help='numbers only, no dots-sheet.png')
        if name == 'sheet':
            p.add_argument('dots_json')
    args = ap.parse_args(argv)
    return {'selftest': cmd_selftest, 'bones': cmd_bones, 'measure': cmd_measure, 'sheet': cmd_sheet}[args.command](args, echo)


if __name__ == '__main__':
    sys.exit(main())
