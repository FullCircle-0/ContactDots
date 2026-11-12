---
name: contact-dots
description: Measure in headless Blender whether things that should touch, sit, stay or line up really do, on the real meshes of an animation (FBX or .blend). Body contact in mm (a hand on a thigh, fingers on a prop, feet on the floor), structural checks (a part seated in its socket, a box resting flat, a held object not sliding, a fixed spacing) and rotational checks in degrees (a part lined up, a hinge or joint inside its range), plus every penetration, with a close-up picture sheet. Use when asked to check, verify or measure contacts, penetrations, seating, sliding or angles in an animation or pose. Measures only; it corrects nothing.
---

# contact-dots

Puts dots on the hands, limbs, feet and pelvis of each character (and on anything else you name),
then measures, frame by frame in headless Blender, the gap from each dot to a surface, the
distance between dots and the angle between directions. Writes `dots.json`, prints a short
summary and renders `dots-sheet.png` (one close-up per check and per worst penetration).

It never changes a source file and writes only into the output folder you name.

## When to use
- "Is her right palm really on his left thigh at frame 40?" (body contact, `touch`)
- "Does any finger go inside the cup?" (penetrations are always checked)
- "The magazine is seated at frame 40", "the box rests flat", "the gun stays in the hand",
  "the planted foot does not slide" (structural: `seat`, `rest`, `hold`, `distance`)
- "The elbow never bends backwards", "the lid opens 0-110 degrees", "the palm faces the table"
  (rotational: `range`, `align`)

Not for: fixing poses, physics or balance, mouth or cloth contact, mesh-against-mesh overlap.

## Needs
Blender (any recent version; its own Python is used inside) and Python 3 standard library for
the host script. Blender is found from `--blender PATH`, then `$BLENDER`, then PATH, then the
usual install folders.

## Commands
All through `scripts/dots.py` in this skill folder:

```
python scripts/dots.py selftest [--out DIR]       # known answers; run first on a new machine
python scripts/dots.py bones <file.fbx|.blend>    # the rig's bones and the proposed bone map
python scripts/dots.py measure <config.json>      # measure, judge, write dots.json + dots-sheet.png
python scripts/dots.py sheet <dots.json>          # re-render the sheet only
```
Options: `--blender PATH`, `--timeout S` (default 600), `--limits finger|limb` (measure),
`--no-sheet` (measure, numbers only).

First output line, always: `dots: PASS ...` (exit 0), `dots: FAIL ...` (exit 1) or
`dots: skipped (<reason>)` (exit 2: no Blender, timeout, Blender error or a bad config;
the reason says which). A skipped run is not a result: fix the reason and run again.

## From a sentence to a config
1. Run `dots.py bones <file>` for each character file if you are unsure the rig maps (Mixamo
   and Unity humanoid names map on their own; otherwise write a `boneMap`).
2. Give each character a short `role` (`her`, `him`, `a`, `b`): the dots are `<role>.<dot>`.
3. Turn each thing to check into one entry of `checks`, with the frames it should hold on.
4. Name an `out` folder (relative paths are relative to the config file).

"Her right palm on his left thigh at frame 40":
```json
{"out": "dots_out",
 "characters": [{"role": "her", "file": "her.fbx"}, {"role": "him", "file": "him.fbx"}],
 "frames": [40],
 "checks": [{"id": "palm-on-thigh", "kind": "touch", "a": "her.R.palm", "b": "him.LeftUpLeg", "frames": [40]}]}
```
"Her right hand on his left thigh" (any part of the hand): `"a": "her.R.hand"` (the nearest
dot of the group counts).

Body dots (S = R or L): `S.<finger>.tip`, `S.<finger>.mid` (thumb, index, middle, ring,
pinky), `S.palm`, `S.forearm.1-3`, `S.upperarm.1-3`, `S.thigh.1-3`, `S.shin.1-3`, `S.knee`,
`S.heel`, `S.ball`, `S.toe`, `pelvis.front`, `pelvis.back`. Groups: `S.hand`, `S.foot`,
`S.forearm`, `S.upperarm`, `S.thigh`, `S.shin`, `pelvis`.

Surfaces (`b` of a touch): `<role>` (whole body), `<role>.<canonical bone>` (one body part,
e.g. `him.LeftUpLeg`, `him.Spine1`, `him.Head`), `self`, `self.<bone>`, `prop:<object name>`,
`floor`, `nearest`.

Structural and rotational checks use custom dots (on a bone, an object, or a mesh region) and
directions (bone or object axis, a line between dots, a surface normal, a palm normal, a world
axis). Full config: `references/config.md`. One worked example per check kind:
`references/checks.md`.

Limits: preset `finger` (default: a contact may be at most 3 mm off at its frames and at
most 2 mm inside on any frame) or `limb` (10 mm / 10 mm), or your own numbers.

## Reading the results
The summary lists each check with its number, worst frame, the limit and PASS/FAIL, then
penetrations deeper than the limit. Gaps are skin to surface in mm: positive = apart,
negative = inside. Details of `dots.json`: `references/results.md`.

- A touch FAIL with a positive worst gap = not touching (floating); with a large negative
  deepest gap = sinking in.
- `skinMm` in `dots.json` is each dot's own skin radius; a strange radius means poor skin
  weights or a wrong body mesh, and the gap of that dot is rough.
- No checks declared: the summary lists observed contacts (dots within 30 mm) instead.

## World frame warning
Two FBX files do not always share one world frame (different export roots or offsets). With
two or more characters the summary prints each character's root position at the first frame.
Confirm they stand where they should, relative to each other, before trusting any pair number.
If they do not, the numbers are meaningless: fix the export, do not shift the numbers.

## LOOK at the sheet
Numbers alone are not enough. Open `dots-sheet.png` (in the `out` folder) before reporting:
bodies see-through grey, small dots per character, the measured pair joined by a line
(yellow = pass, magenta = fail), arrows for angle checks, a caption per tile (id, value,
limit, PASS/FAIL). Check that each tile shows the parts you meant (right hand, right thigh,
right frame). A pass on the wrong dot is a wrong answer.

## Report back
The first summary line, each check's number with its limit and frame, penetrations over the
limit, the root positions if two characters, and what the sheet shows. Say it measured only.
