# Investigation of fun-facts — top-level summary

Generated 2026-05-18 from six parallel investigation agents. Each agent verified both `fact_a_true` (claimed-true) and `fact_b_false` (claimed-false) for ~20 rows of `_distractors_review.md` (118 rows × 2 statements = 236 facts).

## File layout

Canonical reports (read these):

- [elements_001-020.md](elements_001-020.md) — H → Ca (agent-1)
- [elements_021-040.md](elements_021-040.md) — Sc → Zr (agent-2)
- [elements_041-060.md](elements_041-060.md) — Nb → Nd (agent-3)
- [elements_061-080.md](elements_061-080.md) — Pm → Hg (agent-4)
- [elements_081-100.md](elements_081-100.md) — Tl → Fm (agent-5)
- [elements_101-118.md](elements_101-118.md) — Md → Og (agent-6)

Earlier drafts (`investigation_NNN-NNN.md`) were also written by some agents before finalizing. They cover the same material at slightly different fidelity; keep or delete per preference.

## Top-priority defects to fix in `distractors.json`

These are the rows where verification turned up something more than the expected mis-attribution-of-true-fact-to-wrong-element. Sorted by severity.

### 1. Row #82 (Lead) — `fact_b_false` is actually TRUE (structural label error)

`fact_b_false` says *"Lead paints used to make sunny yellow colors in old artists' paintings."* Lead chromate ("chrome yellow") and lead-tin yellow are real, well-documented historic pigments (Van Gogh's sunflowers, etc.). This row's TRUE/FALSE label is **wrong** — the supposedly-false fact is true. Must edit before microgame ships.

Bonus: row #82 `fact_a_true` says lead was *"used in pencils long ago"* — pencil "lead" has always been graphite. MISLEADING in the same row.

### 2. Verbatim sentence collisions between fact_a_true and fact_b_false

Two cases where the **same exact sentence** appears as the true fact for one element and the false fact for another. The kid has no signal which is the lie:

| True row | False row | Sentence |
|---|---|---|
| #58 Cerium (`fact_a`) | #44 Ruthenium (`fact_b`) | "polishes glass to a mirror finish and is the most common rare-earth metal" |
| #88 Radium (`fact_a`) | #48 Cadmium (`fact_b`) | "glows in the dark and was once painted on old watch dials to read the time" |

Reword one side of each pair.

### 3. Food-association safety vectors

Two rows put a toxic element into food language, risking a literal mislearning that could affect kid behavior around white powders:

- **#81 Thallium** `fact_b`: *"combines with chlorine to make the salt you sprinkle on food."* Thallium salts are genuinely lethal.
- **#94 Plutonium** `fact_b`: *"builds strong bones and teeth and is in milk, cheese, and yogurt."*

Also flagged adjacent in agent-2: **#56 Barium** `fact_b` claims Ba is for cuts/scrapes and in pennies — wound-safe framing of a toxic metal.

Recommended: rephrase to obviously absurd (e.g., "Plutonium is found in jellybeans") rather than plausible-but-misattributed.

### 4. Mixed-truth distractors (the half-true half can anchor the false half)

- **#11 Sodium** `fact_b`: "keeps pools clean" (wrong, that's Cl) + "other half of table salt" (correct for Na). Truthful half makes the false half stick.
- **#12 Magnesium** `fact_b`: "glows white in flash bulbs" (true for Mg, matches fact_a) + "powers ion engines" (xenon).

### 5. fact_a inaccuracies (claimed-true rows that need attention)

| Row | Element | Issue | Severity |
|---|---|---|---|
| #22 | Titanium | "toothpaste uses it" — toothpaste has TiO₂ pigment, not titanium metal | LOW |
| #37 | Rubidium | "named for ruby-red when it burns" — actually named for dark-red spectral lines (flame spectroscopy) | LOW |
| #69 | Thulium | "glows blue in some special lasers" — Tm fluoresces blue, but Tm:YAG lasers emit near-IR | LOW |
| #71 | Lutetium | "rarest and most expensive" — Rhodium is more expensive by mass; "one of" hedge is defensible | LOW |
| #82 | Lead | "used in pencils long ago" — pencil "lead" has always been graphite | MED |

## HIGH-concern fact_b distribution

By design, the table uses real facts about other elements as distractors. That makes most `fact_b_false` rows high mislearning risk if read aloud verbatim to a 4yo. Per-agent HIGH counts:

| Range | HIGH-concern fact_b rows |
|---|---|
| 1–20 | #1 H, #11 Na, #12 Mg |
| 21–40 | #21 Sc, #24 Cr, #27 Co, #28 Ni, #31 Ga, #32 Ge, #35 Br |
| 41–60 | #41 Nb, #44 Ru, #45 Rh, #46 Pd, #48 Cd, #56 Ba |
| 61–80 | 18 of 20 (Pm…Hg — see file for individual cross-refs) |
| 81–100 | #82 Pb (structural), #81 Tl + #94 Pu (food-vector) |
| 101–118 | 14 of 18 (Md…Og — synthetic-element etymology swaps) |

## Structural recommendation surfaced by agent-4

Agent-4 (rows 61–80, the lanthanides) noted that the distractor pattern is structurally risky regardless of which specific rows are HIGH: every `fact_b_false` is a confident-sounding true claim attached to the wrong element, presented alongside the correct attribution. For a 4yo who can't yet read the "this one is fake" framing, both sides land equally.

Two options:

a. **Never read `fact_b_false` aloud** — use it only as a multiple-choice distractor on a screen where the kid sees feedback after picking.
b. **Rewrite all `fact_b_false` strings to be obviously absurd** rather than plausible-but-misattributed (e.g., "Hydrogen tastes like strawberry ice cream"). This trades pedagogical sharpness for safety.

## Method notes

- Each agent verified both statements per row using a mix of internal knowledge and targeted WebSearch on less common claims (rare-earth properties, specific scientist-to-element namings, pigment history). Citations are inside the per-chunk files.
- Verdict scale: TRUE / FALSE / MISLEADING / UNVERIFIABLE.
- Concern scale: NONE / LOW / MED / HIGH for likely 4yo mislearning.
- All six chunk files end with a per-chunk summary listing HIGH-concern rows.
