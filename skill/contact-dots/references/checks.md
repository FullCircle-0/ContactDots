# Check kinds, one worked example each

Every check: `{"id", "kind", ..., "frames": [n, ...]}` (a list; default every measured frame; a
check's frames are measured even when the top-level `frames` leaves them out).
Each reports its number per frame, its worst frame and PASS/FAIL, with the limit beside the
number. `a`/`b` of `seat` and `distance` must be custom dots (`dots`); `hold` and `rest` also
take body dots (`<role>.<dot>`).

## touch: a dot on a surface (mm)
"Her right fingertips on the cup from frame 30 to 45."
```json
{"id": "grip", "kind": "touch", "a": "her.R.hand", "b": "prop:Cup", "frames": [30, 35, 40, 45]}
```
Gap = skin to surface. Off limit (`offMm`) is checked at the listed frames; inside limit
(`insideMm`) on every frame. Result: `worst +1.2 mm f35 (her.R.index.tip on prop:Cup), deepest ...`.

## seat: a part in its socket (mm)
"The magazine is seated at frame 40."
```json
"dots": [{"name": "mag-top", "object": "Magazine", "point": [0, 0, 0.06]},
         {"name": "well-top", "object": "Gun", "group": "mag_well_top"}],
"checks": [{"id": "mag-seated", "kind": "seat", "a": "mag-top", "b": "well-top", "frames": [40], "max": 1}]
```
Number = distance between the two dots; default limit 1 mm.

## rest: lying flat on a surface (mm)
"The box rests flat on the table from frame 50 on."
```json
"dots": [{"name": "box.0", "object": "Box", "point": [-0.1, -0.1, -0.1]},
         {"name": "box.1", "object": "Box", "point": [0.1, -0.1, -0.1]},
         {"name": "box.2", "object": "Box", "point": [-0.1, 0.1, -0.1]},
         {"name": "box.3", "object": "Box", "point": [0.1, 0.1, -0.1]}],
"checks": [{"id": "box-flat", "kind": "rest", "a": ["box.0", "box.1", "box.2", "box.3"],
            "b": "prop:Table", "frames": [50, 60, 70, 80]}]
```
3 or more dots, all within the limits on the same frames: highest dot at most `offMm` off,
lowest at most `insideMm` inside (preset limits unless given). One corner up = FAIL.

## hold: something stays put in a frame of reference (mm)
"The gun stays in his right hand", "the planted left foot does not slide".
```json
"dots": [{"name": "grip", "object": "Gun", "point": [0, -0.05, 0]}],
"checks": [{"id": "gun-in-hand", "kind": "hold", "a": "grip", "b": "him.RightHand", "max": 2},
           {"id": "foot-planted", "kind": "hold", "a": "him.L.heel", "b": "world", "frames": [20, 21, 22, 23, 24]}]
```
`b` = the frame of reference: `world` (default), an object name, `<role>.<canonical bone>`, or
`{"armature", "bone"}`. Number = how far `a` has moved in that frame since the check's first
frame. Default limit = the preset's `offMm`.

## distance: fixed spacing between two dots (mm)
"Both hands stay on the rifle, 30-40 cm apart."
```json
"dots": [{"name": "rh", "bone": "him.RightHand"}, {"name": "lh", "bone": "him.LeftHand"}],
"checks": [{"id": "two-hand-grip", "kind": "distance", "a": "rh", "b": "lh", "min": 300, "max": 400}]
```
Needs `min` and/or `max`.

## align: two directions line up (degrees)
"The palm faces the table at frame 12", "the bolt is lined up with its slot".
```json
{"id": "palm-down", "kind": "align", "a": {"palm": "her.R"}, "b": "-z", "frames": [12], "maxDeg": 15}
{"id": "bolt-in-line", "kind": "align", "a": {"object": "Bolt", "axis": "y"},
 "b": {"object": "Slot", "axis": "y"}, "maxDeg": 2}
```
Number = unsigned angle between the two directions; default limit 5 degrees.

## range: an angle stays inside limits (degrees)
"The right elbow never bends backwards" (forearm against upper arm, signed about the elbow's
hinge axis), "the lid opens 0-110 degrees".
```json
{"id": "elbow", "kind": "range", "a": {"bone": "him.RightForeArm", "axis": "y"},
 "b": {"bone": "him.RightArm", "axis": "y"}, "axis": {"bone": "him.RightArm", "axis": "x"},
 "min": 0, "max": 150}
```
With `axis`: the signed angle from `b` to `a` about that axis (negative = the other way, e.g.
hyperextension); check the sign on one known frame first. Without `axis`: the unsigned angle
(0-180). Needs `min` and/or `max`.

## Directions
| Form | Meaning |
|------|---------|
| `"z"`, `"-x"`, `{"world": "y"}` | world axis |
| `{"bone": "<role>.<canonical bone>", "axis": "y"}` or `{"armature", "bone", "axis"}` | bone axis (y = along the bone; default y) |
| `{"object": "<object>", "axis": "z"}` | object local axis (default z) |
| `{"from": "<dot>", "to": "<dot>"}` | line between two dots |
| `{"normal": "<dot>", "surface": "<surface>"}` | face normal of the surface under the dot (`floor` = +z; default floor) |
| `{"palm": "<role>.R"}` | normal out of the palm |

Axes may be negative (`"-y"`).
