#!/usr/bin/env python3
"""contact-dots host script (standard library only). Measures in headless Blender whether things that should touch,
sit, stay or line up really do; it never changes a source file.

  python dots.py selftest [--out DIR]               synthetic scene with known answers (run first on a new machine)
  python dots.py bones <file.fbx|.blend>            the armature's bones and the proposed bone map
  python dots.py measure <config.json>              measure, judge, write dots.json, print the summary
  python dots.py sheet <dots.json>                  render the close-up sheet dots-sheet.png beside dots.json

Options: --blender PATH (else $BLENDER, PATH, the usual install folders), --timeout S (default 600),
         --limits finger|limb (overrides the config's limits).
First output line: `dots: PASS ...` (exit 0), `dots: FAIL ...` (exit 1) or `dots: skipped (<reason>)` (exit 2).
"""
import argparse
import glob
import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
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
    try:
        for line in process.stdout:
            line = line.rstrip()
            if line.startswith(('DOTS_', 'Error', 'Traceback', 'RuntimeError')) or 'Error:' in line:
                lines.append(line)
                if line.startswith(show):
                    echo('  ' + line.split(' ', 1)[-1])
            if time.time() - began > timeout:
                raise subprocess.TimeoutExpired(cmd, timeout)
        process.wait(timeout=max(1, timeout - (time.time() - began)))
    except subprocess.TimeoutExpired:
        process.kill()
        raise Skipped('Blender exceeded %d s and was stopped' % timeout)
    if process.returncode != 0 or not any(l.startswith(done_tag) for l in lines):
        why = next((l for l in reversed(lines) if 'Error' in l), 'exit %s' % process.returncode)
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
                if not isinstance(v, (int, float)):
                    raise Skipped('bad config: limits.%s must be a number' % k)
                out[k] = float(v)
        return out
    raise Skipped('bad config: limits must be a preset name or an object')


def _abs(base, p):
    return str((base / p).resolve()) if not Path(p).is_absolute() else str(Path(p))


def validate(cfg, base):
    """Check and resolve a config (paths relative to `base`). Returns a new dict; raises Skipped('bad config: ...')."""
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
    ids = set()
    for i, c in enumerate(cfg.get('contacts') or []):
        if not isinstance(c, dict) or not c.get('a') or not c.get('b'):
            raise Skipped('bad config: contacts[%d] needs "a" and "b"' % i)
        c.setdefault('id', 'contact%d' % i)
        if c['id'] in ids:
            raise Skipped('bad config: duplicate id %r' % c['id'])
        ids.add(c['id'])
        if c['a'].split('.')[0] not in roles:
            raise Skipped('bad config: contact %s: "a" must start with a character role %s' % (c['id'], sorted(roles)))
    out['limits'] = limits_of(cfg.get('limits'))
    if cfg.get('snapshot'):
        out['snapshot'] = dict(cfg['snapshot'], path=_abs(base, cfg['snapshot']['path']))
    return out


# ---------------------------------------------------------------- judging

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
                     'text': 'worst %+.1f mm f%d (%s on %s), deepest %+.1f mm f%d' % (
                         gaps[key_at]['mm'], int(key_at), gaps[key_at]['dot'], gaps[key_at]['surface'],
                         gaps[deep_at]['mm'], int(deep_at)),
                     'frame': int(deep_at) if gaps[deep_at]['mm'] < -inside else int(key_at)})
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
    for line in lines:
        echo(line)
    return 0 if payload['verdict']['status'] == 'PASS' else 1


def cmd_selftest(args, echo=print):
    out = Path(args.out or os.path.join(tempfile.gettempdir(), 'contact-dots-selftest')).resolve()
    try:
        exe = find_blender(args.blender)
        out.mkdir(parents=True, exist_ok=True)
        seconds, lines = run_blender(exe, MEASURER, ['--selftest', out], args.timeout, 'DOTS_SELFTEST', echo,
                                     show=('DOTS_SELFTEST',))
    except Skipped as why:
        echo(skipped(str(why))[0])
        return 2
    ok = any(l.strip() == 'DOTS_SELFTEST PASS' for l in lines)
    echo('dots: %s selftest (known gaps within 0.1 mm) | %s s | %s' % ('PASS' if ok else 'FAIL', seconds, out))
    return 0 if ok else 1


def cmd_bones(args, echo=print):
    echo(skipped('bones: not built yet')[0])
    return 2


def cmd_sheet(args, echo=print):
    echo(skipped('sheet: not built yet')[0])
    return 2


def main(argv=None, echo=print):
    ap = argparse.ArgumentParser(prog='dots', description=__doc__.split('\n')[1])
    sub = ap.add_subparsers(dest='command', required=True)
    for name in ('selftest', 'bones', 'measure', 'sheet'):
        p = sub.add_parser(name)
        p.add_argument('--blender')
        p.add_argument('--timeout', type=float, default=TIMEOUT_S)
        if name == 'selftest':
            p.add_argument('--out')
        if name == 'bones':
            p.add_argument('file')
        if name == 'measure':
            p.add_argument('config')
            p.add_argument('--limits', choices=sorted(PRESETS))
        if name == 'sheet':
            p.add_argument('dots_json')
    args = ap.parse_args(argv)
    return {'selftest': cmd_selftest, 'bones': cmd_bones, 'measure': cmd_measure, 'sheet': cmd_sheet}[args.command](args, echo)


if __name__ == '__main__':
    sys.exit(main())
