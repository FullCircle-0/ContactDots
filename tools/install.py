#!/usr/bin/env python3
"""Install the contact-dots skill (Plan step 8) and build the shareable zip.

  python tools/install.py [--dest DIR] [--dist DIR] [--zip-only]

Copies skill/contact-dots to DIR (default: the user skills folder, ~/.claude/skills/contact-dots). An existing copy is
moved aside first to skill-backups/contact-dots.bak-<UTC time> beside the skills folder (never inside it, or it would
load as a second skill); the installed copy is never edited in place. Always writes
dist/contact-dots.zip (one top folder contact-dots/). Prints what it replaced and the file hash list summary.
"""
import argparse
import hashlib
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skill' / 'contact-dots'
NAME = 'contact-dots'
SKIP = shutil.ignore_patterns('__pycache__', '*.pyc')


def files_of(folder):
    return sorted(p for p in Path(folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')


def hash_list(folder):
    """{relative posix path: sha256} of a skill folder (line endings as stored)."""
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files_of(folder)}


def install(dest, echo=print):
    dest = Path(dest)
    replaced = None
    if dest.exists():
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        # outside the skills folder: a copy left in it would load as a second skill
        backups = dest.parent.parent / 'skill-backups'
        backups.mkdir(parents=True, exist_ok=True)
        replaced = backups / ('%s.bak-%s' % (dest.name, stamp))
        n = 1
        while replaced.exists():
            n += 1
            replaced = backups / ('%s.bak-%s-%d' % (dest.name, stamp, n))
        shutil.move(str(dest), str(replaced))
        echo('install: moved the existing copy aside to %s' % replaced)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILL, dest, ignore=SKIP)
    if hash_list(dest) != hash_list(SKILL):
        raise SystemExit('install: the installed copy differs from the source (hash list)')
    echo('install: %s (%d files, hash list equal to the source)%s' % (
        dest, len(files_of(dest)), '' if replaced else '; nothing replaced'))
    return replaced


def build_zip(dist, echo=print):
    dist = Path(dist); dist.mkdir(parents=True, exist_ok=True)
    path = dist / (NAME + '.zip')
    tmp = path.with_suffix('.zip.tmp')
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in files_of(SKILL):
            z.write(p, '%s/%s' % (NAME, p.relative_to(SKILL).as_posix()))
    tmp.replace(path)
    echo('install: zip %s (%d files)' % (path, len(files_of(SKILL))))
    return path


def main(argv=None, echo=print):
    ap = argparse.ArgumentParser(prog='install', description=__doc__.split('\n')[0])
    ap.add_argument('--dest', default=str(Path.home() / '.claude' / 'skills' / NAME))
    ap.add_argument('--dist', default=str(ROOT / 'dist'))
    ap.add_argument('--zip-only', action='store_true')
    a = ap.parse_args(argv)
    if not (SKILL / 'SKILL.md').is_file():
        raise SystemExit('install: no skill at %s' % SKILL)
    if not a.zip_only:
        install(a.dest, echo)
    build_zip(a.dist, echo)
    return 0


if __name__ == '__main__':
    sys.exit(main())
