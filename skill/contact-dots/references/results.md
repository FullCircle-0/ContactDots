# Results reference

`measure` writes into the config's `out` folder: `dots.json`, `dots-sheet.png`,
`dots-sheet-tiles/` (one PNG per tile) and `dots-sheet-job.json` (what the sheet drew).
Lengths in `dots.json` are mm, angles degrees, positions metres (world), frames plain numbers
(keys of per-frame tables are strings).

## Summary on stdout
```
dots: FAIL limits finger (off <= 3 mm, inside <= 2 mm): checks 1/2 pass, 1 penetration(s) deeper than 2 mm | 14.2 s
  touch palm-on-thigh her.R.palm -> him.LeftUpLeg: worst +0.8 mm f40 (her.R.palm on him.LeftUpLeg), deepest +0.8 mm f40 (limit off <= 3, inside <= 2 mm)  PASS
  seat mag-seated mag-top -> well-top: worst 3.4 mm f40 (limit <= 1 mm)  FAIL
  inside her.L.index.tip in him -4.6 mm f42
  world check f40 roots (m): her (0.02, 0.31, 0.95), him (0.00, -0.28, 0.98)  <- confirm the characters stand where expected
```
At most 6 rows per list; the rest are in `dots.json`. FAIL if any check fails or any
penetration is deeper than `insideMm`. With no checks: a line of observed contacts.

## dots.json
| Key | Content |
|-----|---------|
| `verdict` | `status` (PASS/FAIL), `limits`, `checks` (the summary rows: id, kind, what, ok, text, frame), `penetrationsOverLimit` |
| `contacts` | touch entries: `gaps` {frame: {mm, dot, surface, p, q, parity when ray-tested}}, `worstMm`/`worstFrame` (most off at its frames), `deepestMm`/`deepestFrame`, `ok`, `limit` |
| `checks` | other kinds: `values` {frame: {v, p, q, ...}}, `unit`, `worst`/`worstFrame`, `least`/`leastFrame`, `ok`, `limit`. `rest` adds `min` and per-dot `gaps`; `align`/`range` add the directions `da`, `db` |
| `penetrations` | every dot inside any surface on any frame: {dot, surface, group, mm, frame, parity when ray-tested}, deepest first |
| `observed` | dots within `observeMm` of a surface (shown when no checks are declared) |
| `skinMm` | each body dot's skin radius (0 for snapped dots: palm, heel, pelvis front/back) |
| `dots` | custom dots with their `radiusMm` |
| `roots` | each character's root (hips) position per frame: the world-frame check |
| `roles` | per character: armature, body mesh, bone preset used, bone matches, vertex and region counts |
| `warnings` | dots dropped for missing bones |
| `frames`, `calibFrame`, `floorZ`, `blender`, `seconds`, `config` | what was measured and how |
| `joints` | canonical bone positions per frame, when the config asked |

`p` = the dot's position, `q` = the nearest surface point (or the other dot): the pair line on
the sheet.

## Reading the numbers
- Gap: skin to surface. `+` apart, `-` inside. A touch at its frames is good at about 0 to
  +`offMm`; small negatives (skin pressing in) are fine down to -`insideMm`.
- Floating contact: touch FAIL with a positive worst gap.
- Sinking: a penetration row, or a touch with a deepest gap below -`insideMm`.
- Deep dots: past `signRangeM` a dot behind the nearest face is inside only if most of six rays
  (three directions and their opposites) cross its whole closed surface an odd number of times;
  `parity` holds those votes (0-6, 4+ = inside). A lone open sheet, however large, gives at most
  3, so it never reads deep inside; a mesh with holes usually reads outside too. A large positive
  gap with `parity` 0-3 on such a surface is worth a look on the sheet.
- `hold`: movement since the check's first frame in its frame of reference; a steady climb
  means sliding, a jump means a pop.
- `range` with `axis`: signed; a value below `min` on an elbow or knee is hyperextension.
- A skin radius far off the others on the same limb (or 0 where it should not be) means poor
  skin weights or the wrong body mesh: that dot's gaps are rough.
- `warnings` not empty: some dots do not exist (missing bones). A check that names one of them
  stops the run with `dots: skipped (Blender failed: ...)`: drop or rename that check, run again.

## The sheet
One tile per check, at its verdict frame (the failing frame, else the worst), and one per
penetration deeper than `insideMm` (deepest first, at most `closeups`). Each mesh in one flat colour per character or prop (a little darker at grazing angles) on a dark
background, dots coloured per character (custom dots on objects in one colour), the measured dot
larger, the pair line yellow (pass) or magenta (fail), a ring round a near-zero gap, angle checks
as two arrows with dark rims. A translucent disc lies on the surface at the measured surface point,
on the surface the check names, in the complement of that mesh's colour (grey on the floor), with its outline and normal arrow (out of the surface;
on an inside tile the dot is behind it). A disc whose colour is close to
the mesh behind it is faint: look for the outline. XYZ triads (X red, Y green,
Z light blue; the only pure R/G/B on the sheet) show the palm, wrist, head and pelvis frames in close
views, never on top of the contact. The measured pair, arrows, disc outline and triads are drawn in front of the meshes; the small dots are not. Caption: id, value, limit, PASS/FAIL. Always look:
it shows whether the right parts were measured.
