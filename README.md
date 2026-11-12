# ContactDots

Source repo of the `contact-dots` Claude skill: measures, in headless Blender, whether
things that should touch, sit, stay or line up really do (body contact, structural
contact, rotational checks) on the real meshes. It measures only; it corrects nothing.

- `skill/contact-dots/` is the product: a standard Claude skill folder (`SKILL.md`,
  `scripts/`, `references/`). Install it or hand it over as one zip.
- `tests/` holds the tests, including real mesh samples kept outside the skill folder.
- `tools/install.py` copies the skill into the user skills folder and builds
  `dist/contact-dots.zip`.

Needs a Blender install (its own Python) and the system Python standard library.
Tests need `pytest` (and `numpy` for the real-sample tests).

```
python -m pytest -q
python skill/contact-dots/scripts/dots.py selftest
```
