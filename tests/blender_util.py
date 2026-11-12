"""Shared helpers for the tests that need Blender (skipped when none is found)."""
import pytest

import dots


def blender_or_skip():
    try:
        return dots.find_blender()
    except dots.Skipped as why:
        pytest.skip(str(why))


def run(argv):
    lines = []
    code = dots.main(argv, echo=lines.append)
    return code, lines


def make_sample(tmp_path, rig, name='sample.blend'):
    exe = blender_or_skip()
    path = tmp_path / name
    dots.run_blender(exe, dots.MEASURER, ['--make-sample', path, '--rig', rig], 300, 'DOTS_SAMPLE', echo=lambda *_: None)
    return path
