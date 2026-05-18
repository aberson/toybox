# Element corpus — factoid sources and attribution

`elements.json` ships 118 entries covering atomic numbers 1..118. The
factoids (`fun_fact`, `story_seed_hooks`, `color_description`,
`discovered_era`, `phase_at_room_temp`, `family`, `atomic_mass`) are
common-knowledge chemistry verified against the public sources listed
below. Where a fact is borderline or could plausibly be wrong, the
entry uses a family-based generic line that is safe for kids.

## Sources

### NIST — National Institute of Standards and Technology

- **URL:** https://physics.nist.gov/PhysRefData/ASD/Html/help.html and
  https://www.nist.gov/pml/atomic-weights-and-isotopic-compositions-relative-atomic-masses
- **Used for:** atomic numbers, element symbols, atomic masses (rounded
  to 1dp per phase-m-plan §5.1).
- **License:** U.S. federal-government work, public domain. No
  attribution legally required; cited here for traceability.

### Royal Society of Chemistry — Periodic Table

- **URL:** https://www.rsc.org/periodic-table/
- **Used for:** kid-friendly element descriptions, discovery years,
  phase at room temperature, color descriptions, common-use facts
  (e.g. "lithium powers tablet batteries", "tungsten survives the
  highest heat").
- **License:** Mixed. Most RSC element-page content is editorial. Used
  as a fact-checking reference, not copied verbatim. Every `fun_fact`
  in `elements.json` is paraphrased in the author's own words and is
  ≤ 25 words.

### U.S. Department of Energy and national-lab science overviews

- **URL:** https://www.energy.gov/science/doe-explains, plus
  individual national-lab outreach pages (LBL, ORNL, LLNL).
- **Used for:** synthetic-element history (technetium, plutonium,
  californium, livermorium), nuclear-medicine applications
  (technetium for body imaging), and rare-earth uses.
- **License:** U.S. federal-government work, public domain.

### Wikipedia (CC BY-SA 4.0)

- **Used for:** secondary fact-check on ~30 element-page reads,
  especially super-heavy elements (Nh, Fl, Mc, Lv, Ts, Og) and
  lanthanides where RSC coverage was thin. No paragraphs copied
  verbatim; facts are rephrased in original kid-friendly prose.
- **Attribution:** Per CC BY-SA 4.0, Wikipedia is credited here as a
  reference work consulted. Article slugs consulted (representative
  list, not exhaustive): `Hydrogen`, `Helium`, `Carbon`, `Gold`,
  `Mercury_(element)`, `Mendelevium`, `Praseodymium`, `Dysprosium`,
  `Roentgenium`, `Oganesson`. Revision dates: read on 2026-05-18.

## Author judgment calls

A few facts required disambiguation; the choices made:

- **Hydrogen `discovered_era`:** RSC and NIST attribute the discovery
  to Henry Cavendish in 1766; the corpus uses `"1766"` rather than
  `"ancient"` for that reason (hydrogen was *isolated* in 1766;
  earlier alchemists observed it without identifying it as an
  element).
- **Oganesson `phase_at_room_temp`:** Group-18 placement implies "gas",
  but theoretical calculations suggest Og may be a solid at room
  temperature. Since only a handful of Og atoms have ever existed
  and none long enough to measure phase, we follow the RSC/Wikipedia
  pedagogical convention and mark Og as `"gas"` to match its
  group-18 peers. Kids learning the table see the group-18 column
  as "the noble gases", and the corpus matches that mental model.
- **Astatine and Tennessine `phase`:** Marked `"solid"` and `"solid"`
  respectively. Astatine is widely described as a halogen "solid" in
  pedagogy (though its true phase is unmeasured); Tennessine likewise.
- **Lanthanide / actinide vs. transition_metal placement:** The 10
  Family slugs in `Family(StrEnum)` give lanthanides and actinides
  their OWN families rather than rolling them into `transition_metal`.
  Atomic numbers 57-71 (La..Lu) are `lanthanide`; 89-103 (Ac..Lr) are
  `actinide`. Group 3 elements Sc (21) and Y (39) are
  `transition_metal`. Cn (112) and Rf-Cn (104-112) are
  `transition_metal`. Nh-Og (113-118) split per main-group analogy:
  Nh / Fl / Mc / Lv are `post_transition_metal`; Ts is `halogen`; Og
  is `noble_gas`.

## Safety review

Every `fun_fact` was reviewed against the M1 kid-safety rule: no
danger framing. Notably:

- Mercury: "the only metal that's liquid at room temperature" — no
  mention of toxicity. M-plan content-team judgment: toxicity is a
  parent's later conversation, not a primary-school activity fact.
- Uranium / plutonium / radium: framed by use ("powers nuclear
  reactors", "powers space probes", "glows in the dark") rather than
  danger. The kiosk audience is 3-12; the goal is curiosity.
- Phosphorus, fluorine, chlorine: framed by everyday use (matches,
  toothpaste, swimming pools).
- Polonium, astatine, francium: framed by discovery / rarity rather
  than radioactivity.

## File history

- 2026-05-18 — initial 118-entry ship (Phase M Step M1).
