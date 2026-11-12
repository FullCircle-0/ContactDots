"""Clean skill folder (Plan step 7, Spec A5): no pipeline, project or machine names in the shipped folder, the skill
text is complete, and the skill runs from a copy in a temp folder."""
import re
import shutil
import subprocess
import sys

import pytest

from conftest import SKILL
from blender_util import blender_or_skip

FORBIDDEN = [r'cascpipe', r'cascadeur', r'plan\.json', r'titans', r'glock', r'raven', r'terrific', r'session.?sphere',
             r'theseus', r'autoposing', r'\b[A-Za-z]:[\\/]', r'\\\\[A-Za-z0-9_.-]+\\', r'/Users/', r'/home/']
TEXT = ('.py', '.md', '.json', '.txt')


def shipped_files():
    return [p for p in SKILL.rglob('*') if p.is_file() and '__pycache__' not in p.parts]


def test_no_forbidden_terms():
    hits = []
    for p in shipped_files():
        rel = p.relative_to(SKILL).as_posix()
        for pat in FORBIDDEN:
            if re.search(pat, rel, re.I):
                hits.append('%s: name matches %s' % (rel, pat))
        if p.suffix.lower() in TEXT:
            for n, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
                for pat in FORBIDDEN:
                    if re.search(pat, line, re.I):
                        hits.append('%s:%d matches %s: %s' % (rel, n, pat, line.strip()[:80]))
    assert not hits, '\n'.join(hits)


def test_only_skill_files():
    allowed = {'.py', '.md'}
    odd = [p.relative_to(SKILL).as_posix() for p in shipped_files() if p.suffix.lower() not in allowed]
    assert not odd, odd


def test_skill_text():
    text = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
    assert len(text.splitlines()) < 150
    head = re.match(r'---\nname: contact-dots\ndescription: (.+?)\n---\n', text, re.S)
    assert head and len(head.group(1)) <= 1024
    for ref in ('references/config.md', 'references/checks.md', 'references/results.md'):
        assert ref in text and (SKILL / ref).is_file(), ref
    for cmd in ('selftest', 'bones', 'measure', 'sheet'):
        assert 'dots.py %s' % cmd in text, cmd
    for must in ('LOOK', 'World frame', 'dots: skipped'):
        assert must in text, must
    checks = (SKILL / 'references' / 'checks.md').read_text(encoding='utf-8')
    for kind in ('touch', 'seat', 'rest', 'hold', 'distance', 'align', 'range'):
        assert '## %s:' % kind in checks, kind


def test_examples_are_valid_json_and_configs(tmp_path):
    """Every full config block in the skill text validates against the host script's checker."""
    import dots
    for name in ('SKILL.md', 'references/checks.md'):
        text = (SKILL / name).read_text(encoding='utf-8')
        for block in re.findall(r'```json\n(.*?)```', text, re.S):
            body = block.strip()
            if not body.startswith('{"out"'):
                continue
            import json
            cfg = json.loads(body)
            for c in cfg.get('characters', []):
                (tmp_path / c['file']).write_bytes(b'')
            dots.validate(cfg, tmp_path)


def test_runs_from_temp_copy(tmp_path):
    exe = blender_or_skip()
    copy = tmp_path / 'contact-dots'
    shutil.copytree(SKILL, copy, ignore=shutil.ignore_patterns('__pycache__'))
    out = tmp_path / 'out'
    proc = subprocess.run([sys.executable, str(copy / 'scripts' / 'dots.py'), 'selftest', '--out', str(out),
                           '--blender', str(exe)], capture_output=True, text=True, timeout=600, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1].startswith('dots: PASS selftest'), proc.stdout[-1000:]
    assert (out / 'dots.json').is_file()
