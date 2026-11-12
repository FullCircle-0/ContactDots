"""Install (Plan step 8): the installed folder equals the source (hash list), an existing copy is backed up, and the zip
unpacked in a temp folder passes `dots selftest`."""
import subprocess
import sys
import zipfile
from pathlib import Path

from conftest import ROOT, SKILL
from blender_util import blender_or_skip

sys.path.insert(0, str(ROOT / 'tools'))
import install  # noqa: E402


def test_install_equal_and_backup(tmp_path):
    dest = tmp_path / 'skills' / 'contact-dots'
    lines = []
    assert install.install(dest, lines.append) is None
    assert install.hash_list(dest) == install.hash_list(SKILL)
    (dest / 'stale.txt').write_text('old', encoding='utf-8')
    backup = install.install(dest, lines.append)
    assert backup and (backup / 'stale.txt').is_file() and not (dest / 'stale.txt').exists()
    assert install.hash_list(dest) == install.hash_list(SKILL)
    assert any('moved the existing copy aside' in l for l in lines)
    assert sorted(p.name for p in (tmp_path / 'skills').iterdir()) == ['contact-dots']   # no backup among the skills


def test_zip_passes_selftest(tmp_path):
    path = install.build_zip(tmp_path / 'dist', echo=lambda *_: None)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        assert all(n.startswith('contact-dots/') for n in names) and 'contact-dots/SKILL.md' in names
        z.extractall(tmp_path / 'unzipped')
    unpacked = tmp_path / 'unzipped' / 'contact-dots'
    assert install.hash_list(unpacked) == install.hash_list(SKILL)
    exe = blender_or_skip()
    proc = subprocess.run([sys.executable, str(unpacked / 'scripts' / 'dots.py'), 'selftest', '--out',
                           str(tmp_path / 'out'), '--blender', str(exe)], capture_output=True, text=True, timeout=600,
                          cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1].startswith('dots: PASS selftest')
