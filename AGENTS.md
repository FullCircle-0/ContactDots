# AGENTS.md — ContactDots, for agents

Human-facing overview: [README.md](README.md). This file is for an agent that uses or edits the repo.

**Product:** the `contact-dots` skill (`skill/contact-dots/`). It measures, in headless Blender,
whether things that should touch, sit, stay or line up do, on the real meshes (FBX or `.blend`).
It **measures only**: it never edits a source file and writes only into the config's `out` folder.
Load `skill/contact-dots/SKILL.md` first; it is the operating manual.

## Use it

```
python skill/contact-dots/scripts/dots.py selftest [--out DIR]    # known answers; run first on a new machine
python skill/contact-dots/scripts/dots.py bones <file.fbx|.blend> # rig bones + proposed bone map
python skill/contact-dots/scripts/dots.py measure <config.json>   # measure, judge, write dots.json + dots-sheet.png
python skill/contact-dots/scripts/dots.py sheet <dots.json>       # re-render the sheet only
```
Options: `--blender PATH` (else `$BLENDER`, then PATH, then usual install folders), `--timeout S`
(default 600), `--limits finger|limb`, `--no-sheet`.

Exit codes: `0` PASS, `1` FAIL, `2` skipped (no Blender, timeout, Blender error, bad config; the
line says which). **A skipped run is not a result:** fix the reason and rerun. Result line is
`dots: PASS|FAIL ...` or `dots: skipped (<reason>)`.

## Minimal config

```json
{"out": "dots_out",
 "characters": [{"role": "her", "file": "her.fbx"}],
 "frames": [40],
 "checks": [{"id": "palm-on-thigh", "kind": "touch", "a": "her.R.palm", "b": "prop:Table", "frames": [40]}]}
```
Relative paths are relative to the config file. Check kinds: `touch`, `seat`, `rest`, `hold`,
`distance` (mm), `align`, `range` (degrees). Authoritative specs, do not restate from memory:
- `skill/contact-dots/references/config.md`: every config key, dots, surfaces, directions, limits.
- `skill/contact-dots/references/checks.md`: one worked example per check kind.
- `skill/contact-dots/references/results.md`: `dots.json` fields and how to read them.
- Working end-to-end example: `examples/grab/config.json` (expect PASS, 10/10, 0 penetrations).

## Rules when reporting a measurement

1. Open the sheet (`<out>/dots-sheet.png`) before reporting; each tile must show the part you
   meant (right hand, right thigh, right frame). A pass on the wrong dot is a wrong answer.
   Pictures: hand them to a picture-check helper if your environment requires it.
2. With two or more characters, read the printed root positions first. Different FBX
   exports may not share a world frame; if they do not, the pair numbers are meaningless: fix
   the export, never shift the numbers.
3. Gaps are skin to surface in mm (positive apart, negative inside). Touch FAIL with a positive
   worst gap = floating; large negative deepest gap = sinking.
4. Report: the first summary line, each check's number with limit and frame, penetrations over
   the limit, root positions (two characters), what the sheet shows, and that it measured only.
5. Strange `skinMm` (a dot's own skin radius) = poor skin weights or wrong body mesh; that
   dot's gap is rough.

## Repo layout

| Path | What |
|---|---|
| `skill/contact-dots/SKILL.md` | The skill's instructions (frontmatter `name`, `description`). |
| `skill/contact-dots/scripts/dots.py` | Host CLI (stdlib only): config validation, Blender launch, summary. |
| `skill/contact-dots/scripts/dots_blender.py` | The measurer; runs **inside** Blender (`bpy`, `mathutils`). Also holds the selftests. |
| `skill/contact-dots/scripts/dots_sheet_blender.py` | Renders the picture sheet inside Blender. |
| `skill/contact-dots/scripts/bone_maps.py` | Rig bone-name maps (Mixamo, Unity humanoid). |
| `tests/` | pytest; Blender tests are marked `blender` and skip without Blender. `tests/fixtures/real/dots/*.npz` are real mesh samples. |
| `tools/install.py` | Installs the skill, builds `dist/contact-dots.zip` (gitignored). |
| `examples/grab/` | Showcase scene; `build_grab.py` rebuilds it with no outside assets. |
| `docs/images/` | Pictures used by `README.md`, taken from the example's output. |

## Editing rules

- Edit `skill/contact-dots/` only in this repo. **Never edit the installed copy** (`~/.claude/skills/contact-dots`);
  reinstall with `python tools/install.py`, which backs up the old copy outside the skills folder and
  checks the installed hash list equals the source.
- The skill folder must stay self-contained: no repo-only paths, no app names in the shipped text
  (`tests/test_clean_folder.py` enforces it).
- Tests: `BLENDER="<path to blender.exe>" python -m pytest -q` (about 40 s with Blender). Also run
  `python skill/contact-dots/scripts/dots.py selftest`; it must end `DOTS_SELFTEST PASS`.
- Geometry facts that bite: lengths inside Blender are metres, all output is mm. Past `signRangeM`
  (default 0.03 m) a dot behind the nearest face counts as inside only if at least 4 of 6 parity
  rays (three skewed directions and their exact opposites) cross the surface an odd number of
  times; a lone open sheet gives at most 3 by construction. Keep that table symmetric.
- Files may use CRLF in the working tree (`core.autocrlf`); do not rewrite line endings wholesale.
- Regenerate the README pictures after any change to the sheet renderer:
  `cd examples/grab && python ../../skill/contact-dots/scripts/dots.py measure config.json`,
  then copy the tiles into `docs/images/` (see the file names there).
