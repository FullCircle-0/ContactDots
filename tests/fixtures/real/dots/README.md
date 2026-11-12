# Contact dots real samples (Spec A2)

Single-frame mesh snapshots written by the measurer's `snapshot` config option from solved takes of a private project
(the source files were read only, never changed; they are not part of this repo). Each `.npz` holds, for a few dots at
one frame: the dot centres, each dot's own skin triangles and the surface triangles within 25 cm (stored once per
character, float32), the sign range, and the measurer's skin radius and gap when the sample was written.

`tests/dots_reference.py` recomputes every gap by brute force (numpy, every triangle, no BVH, no code shared with the
measurer). `tests/test_dots_real.py` requires agreement within 0.5 mm, both for the stored answer and for today's gap
rule run in Blender on the stored triangles (`tests/regap_blender.py`).

Character A is a female rig, character B a male rig of a different skeleton.

| File | Frame | Dots -> surface | What it proves |
|---|---|---|---|
| cowgirl_handsthighs_R | 0 | A right hand (11) -> A right thigh | self contact, fingers inside the thigh (down to -13 mm) |
| cowgirl_handsthighs_L | 24 | A left hand (11) -> A left thigh | the left side, deepest -23 mm (pinky mid) |
| cowgirl_halfkneel_hands | 0 | A right hand -> A's own body minus the arm chain | self surface with exclusions |
| doggy_main_arms | 0 | A forearms, upper arms, palms -> A's own body | far gaps and big skin radii |
| bj2_pair_hands_his | 0 | A both hands (22) -> B whole body | two files in one scene, hands on the other's thighs |
| bj2_pair_his_thighs | 0 | B thighs and knees -> A whole body | the other character's dots, inside by about 20 mm |
| bj2_his_arms_self | 0 | B upper arms -> B's own body | second rig, own body, 3-6 mm from the chest |

Result when written (2026-11-11): every dot within 0.0005 mm of the reference (the measurer rounds to 0.001 mm).
