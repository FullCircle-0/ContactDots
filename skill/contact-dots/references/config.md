# Config reference

One json object. Relative paths are relative to the config file. Unknown keys are refused
(`dots: skipped (bad config: unknown keys ...)`). Lengths in the config are metres for scene
positions (`floorZ`, `point`, `offset`, `signRangeM`) and mm for limits; angles in degrees.

| Key | Meaning | Default |
|-----|---------|---------|
| `out` | output folder (required) | - |
| `name` | label in the summary line | none |
| `characters` | `[{"role", "file", "armature"?, "bodyMesh"?, "boneMap"?}]` | none |
| `props` | `[{"file"}]` or `["file", ...]`: FBX or .blend; every mesh is surface `prop:<object name>` | none |
| `frames` | `[n, ...]`, `{"range": [first, last]}` or `"all"` (plain scene frame numbers) | `"all"` |
| `calibFrame` | frame where skin radii are taken | first measured frame |
| `contacts` | touch entries (same as `checks` of kind `touch`) | none |
| `checks` | `[{"id", "kind", ...}]`, see `checks.md` | none |
| `dots` | custom dots, see below | none |
| `floorZ` | floor height in metres; `null` = no floor surface | 0 |
| `limits` | `"finger"`, `"limb"` or `{"preset"?, "offMm"?, "insideMm"?}` | `"finger"` |
| `observeMm` | with no checks, surfaces nearer than this are listed as observed | 30 |
| `closeups` | how many worst penetrations get a sheet tile | 6 |
| `joints` | canonical bone names whose world positions are written per frame | none |
| `signRangeM` | a dot is trusted to be inside a surface only within this distance | 0.03 |

FBX frames: Blender's importer puts FBX time 0 on scene frame 1 (Unity, Mixamo and Cascadeur takes start at 0).
An FBX exported from Blender with keys from frame 1 therefore lands on frames 2 onward; check the range first.

Need at least one of `characters`, `props`, `dots`.

## Characters
- `role`: short unique name without dots; not `self`, `floor`, `nearest`, nor starting `prop:`.
- `file`: FBX or `.blend` holding the armature and its skinned body mesh.
- `armature`: the armature object's name, when the file has more than one.
- `bodyMesh`: the body mesh name (or part of it), when several meshes are skinned to the rig.
- `boneMap`: `{canonical bone: rig bone}`. Canonical names are Mixamo-style without a prefix:
  `Hips`, `Spine`, `Spine1`, `Spine2`, `Neck`, `Head`, then per side (`Left`/`Right`)
  `Shoulder`, `Arm`, `ForeArm`, `Hand`, `UpLeg`, `Leg`, `Foot`, `ToeBase`, `Toe_End`,
  `Hand<Finger>1-4` (Thumb, Index, Middle, Ring, Pinky). Mixamo and Unity humanoid names (with
  any common prefix) map on their own; a hand-written entry always wins. `dots.py bones <file>`
  prints the rig's bones and the proposed map. A missing bone drops only its own dots, with a
  warning in `dots.json`.

Meshes in a character file that are not skinned to its armature are props (`prop:<name>`).

## Limits
- `finger`: a contact is at most 3 mm off at its frames, at most 2 mm inside on any frame.
- `limb`: 10 mm / 10 mm.
- Own numbers: `{"preset": "limb", "insideMm": 5}`.
- `--limits` on the command line overrides the config.
- Per check: `seat`/`hold`/`distance` take `max`/`min` (mm), `align` takes `maxDeg`,
  `range` takes `min`/`max` (degrees), `rest` takes `offMm`/`insideMm`.

## Custom dots (`dots`)
A dot on anything; `name` must be unique and is used as written (no role prefix).

| Form | Fields |
|------|--------|
| on a character bone | `{"name", "bone": "<role>.<canonical bone>", "offset": [x, y, z]}` (offset in the bone's space, metres) |
| on any armature bone | `{"name", "armature": "<object>", "bone": "<bone>", "offset"}` |
| on an object | `{"name", "object": "<object>", "point": [x, y, z]}` (object-local, metres; default its origin) |
| on a mesh region | `{"name", "object": "<mesh>", "group": "<vertex group>"}` or `"regionBone": "<bone>"` (vertices that bone dominates), `"at": "centre"` (default) or `"nearest"` (the region's point nearest the surface) |

Optional: `"radius"`: mm, `"measure"` (from the dot's own mesh) or `0` (a surface point; the
default). `"penetration": false` leaves the dot out of the penetration scan (markers floating
in the air or inside a part on purpose).

## Touch entries
`{"id", "a": "<role>.<dot or group>" or a custom dot, "b": surface, "frames": [...]}`.
`frames` defaults to every measured frame. With a group, the dot with the smallest gap counts.
Surfaces: `<role>`, `<role>.<canonical bone>`, `self`, `self.<canonical bone>`,
`prop:<object>`, `floor`, `nearest`. On `<role>` or `self` for a dot on its own body, the dot's
own part and its chain neighbours are left out (a fingertip is not "touching" its own finger).
