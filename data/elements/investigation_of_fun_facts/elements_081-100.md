# Investigation: Elements 81-100 (Thallium -> Fermium)

Generated 2026-05-18. Reviewer: agent-5.

Scope: rows # 81 through # 100 of `_distractors_review.md`. Each row's `fact_a_true` and `fact_b_false` verified against general science / history knowledge; one borderline claim (actinium pale-blue glow) cross-checked via web. Pedagogical lens: a 4-year-old (Child B) hearing `fact_b_false` and potentially mislearning it.

## Element 81 - Tl - Thallium
- **fact_a_true:** "Thallium is one of the few elements whose name comes from a Greek word for a green twig."
  - Verdict: TRUE
  - Reason: Thallium's name comes from Greek *thallos* meaning "green shoot/twig," after the bright green spectral line by which it was discovered (Crookes, 1861).
  - Concern: NONE
- **fact_b_false:** "Thallium combines with chlorine to make the salt you sprinkle on food."
  - Verdict: FALSE
  - Reason: Table salt is sodium chloride (NaCl). Thallium chloride exists but is highly toxic and is not table salt. Properly false.
  - Concern: MED - if a kid mislearns this and tastes any "salt-looking" chemical thinking it's edible, thallium compounds are genuinely lethal. The fact is structured so a 4yo can hear it as "salt = thallium + chlorine," which is a dangerous mislearning vector.

## Element 82 - Pb - Lead
- **fact_a_true:** "Lead is one of the heaviest common metals and was used in pencils long ago."
  - Verdict: MISLEADING
  - Reason: Lead IS a heavy common metal (TRUE). But "used in pencils long ago" is the classic misconception - pencil "lead" has always been graphite (carbon); the *name* persisted from when Romans used actual lead styluses on papyrus, but modern pencils never contained metallic lead. A 4yo will hear this as "lead = pencil filling," which is exactly the wrong takeaway.
  - Concern: MED - perpetuates the lead/graphite confusion; also could encourage chewing on pencils thinking the "lead" is the metal lead (toxic).
- **fact_b_false:** "Lead paints used to make sunny yellow colors in old artists' paintings."
  - Verdict: TRUE (and thus the row is broken)
  - Reason: Lead chromate ("chrome yellow") and lead-tin yellow were both real, widely-used historic yellow pigments. Van Gogh's sunflowers famously used chrome yellow. This is actually a true fact about lead. The intended false-attribution was probably to cadmium (row #48 fact_a is the cadmium-yellow claim) but lead also legitimately made historic yellows.
  - Concern: HIGH - **fact_b is supposed to be FALSE but is actually TRUE**. The microgame's TRUE/FALSE label is wrong for this row. Row needs fix in `distractors.json`.

## Element 83 - Bi - Bismuth
- **fact_a_true:** "Bismuth grows into beautiful rainbow staircase crystals that look like tiny castles."
  - Verdict: TRUE
  - Reason: Bismuth crystals form hopper-shaped stepped pyramids with iridescent oxide-layer rainbow surfaces. Iconic and well-photographed.
  - Concern: NONE
- **fact_b_false:** "Bismuth is so rare that less than a teaspoonful exists on the whole planet."
  - Verdict: FALSE
  - Reason: Bismuth is mined commercially in thousands of tonnes per year; this description fits astatine (see row #85). Properly false.
  - Concern: LOW - undersells bismuth's abundance, but no safety/learning risk for a 4yo.

## Element 84 - Po - Polonium
- **fact_a_true:** "Polonium was named after Poland by Marie Curie, who discovered it."
  - Verdict: TRUE
  - Reason: Marie Curie (with Pierre) discovered polonium in 1898 and named it after her native Poland. Well-established history.
  - Concern: NONE
- **fact_b_false:** "Polonium is a strong, light metal that helps build airplanes and spaceships."
  - Verdict: FALSE
  - Reason: This describes beryllium (or titanium). Polonium is extremely radioactive, has no structural use, and is not "light." Properly false.
  - Concern: LOW - if anything, this oversells polonium as safe/useful, but a 4yo isn't going to encounter polonium.

## Element 85 - At - Astatine
- **fact_a_true:** "Astatine is so rare that less than a teaspoonful exists on the whole planet."
  - Verdict: TRUE
  - Reason: Standard estimates put naturally-occurring astatine in Earth's crust at under 30 grams at any moment - well under a teaspoon. Commonly cited as "rarest naturally-occurring element."
  - Concern: NONE
- **fact_b_false:** "Astatine helps make super-fast magnets that lift trains off the tracks."
  - Verdict: FALSE
  - Reason: Maglev train magnets use niobium (superconductors) or neodymium (permanent magnets). Astatine is too radioactive and rare for any structural use. Properly false.
  - Concern: NONE

## Element 86 - Rn - Radon
- **fact_a_true:** "Radon is a gas that seeps up from the ground and is checked for in basements."
  - Verdict: TRUE
  - Reason: Radon is a noble gas produced by uranium decay in soil/rock; basement radon testing is standard home-safety practice.
  - Concern: NONE
- **fact_b_false:** "Radon is so rare that even less of it exists than gold, and it makes fancy wedding rings."
  - Verdict: FALSE
  - Reason: Describes platinum. Radon is a radioactive gas and cannot be made into jewelry. Properly false; also absurd enough a kid would likely catch it.
  - Concern: NONE

## Element 87 - Fr - Francium
- **fact_a_true:** "Francium is so rare that there are only a few atoms of it on Earth at any time."
  - Verdict: MISLEADING (but acceptable)
  - Reason: Actual estimate is ~20-30 grams of francium in Earth's crust at any moment - that's ~10^22 atoms, not "a few." However, "a few atoms" is a common kid-friendly poeticism and gets the directional point across (extraordinarily rare). Compared to astatine (less than a teaspoon, ~30 g) francium is similarly rare. For a 4yo, the takeaway "francium is super rare" is correct.
  - Concern: LOW - factually loose but pedagogically reasonable.
- **fact_b_false:** "Francium is named after Moscow, the capital city of Russia."
  - Verdict: FALSE
  - Reason: Francium is named after France (discovered by Marguerite Perey, 1939, at the Curie Institute in Paris). Moscow is the namesake of moscovium (#115). Properly false.
  - Concern: LOW

## Element 88 - Ra - Radium
- **fact_a_true:** "Radium glows in the dark and was once painted on old watch dials to read the time."
  - Verdict: TRUE
  - Reason: Radium-226 mixed with zinc sulfide produced glow-in-the-dark watch dials early-to-mid 20th century, famously causing the "Radium Girls" poisoning cases.
  - Concern: NONE
- **fact_b_false:** "Radium helps photocopiers see and copy pictures on paper."
  - Verdict: FALSE
  - Reason: Photocopier drums use selenium (row #34). Radium has no role. Properly false.
  - Concern: NONE

## Element 89 - Ac - Actinium
- **fact_a_true:** "Actinium glows a pretty pale blue in the dark and starts the actinide family of elements."
  - Verdict: TRUE
  - Reason: Verified - actinium's intense radioactivity (~150x radium) ionizes surrounding air, producing a pale blue glow. It is the namesake/first member of the actinide series.
  - Concern: NONE
- **fact_b_false:** "Actinium is one of the rarest and most expensive metals to dig out of the ground."
  - Verdict: MISLEADING
  - Reason: Actinium IS extremely rare in Earth's crust (~0.0005 ppm in uranium ores). But it's not "dug out" - it's recovered from uranium decay chains or made synthetically. The fact is meant to describe lutetium (row #71). The undersell vs. lutetium is the intended false-attribution, but actinium's rarity claim happens to also be defensible.
  - Concern: LOW - kid would likely accept "rare and expensive" for either actinium or lutetium; not safety-relevant.

## Element 90 - Th - Thorium
- **fact_a_true:** "Thorium is named after Thor, the Norse god of thunder."
  - Verdict: TRUE
  - Reason: Berzelius (1828) named thorium after Thor. Standard chemistry history.
  - Concern: NONE
- **fact_b_false:** "Thorium is named after the German state of Hesse, where scientists made it."
  - Verdict: FALSE
  - Reason: Hesse (Hassia) is the namesake of hassium (#108). Thorium is named after Thor. Properly false.
  - Concern: NONE

## Element 91 - Pa - Protactinium
- **fact_a_true:** "Protactinium's name means it comes before actinium when it breaks down."
  - Verdict: TRUE
  - Reason: From Greek *protos* (first/before) + actinium; protactinium-231 decays to actinium-227 via alpha decay. The name literally encodes that decay relationship.
  - Concern: NONE
- **fact_b_false:** "Protactinium makes the deep blue color in old pottery and stained-glass windows."
  - Verdict: FALSE
  - Reason: Describes cobalt (row #27). Protactinium is too rare/radioactive for pigment use. Properly false.
  - Concern: NONE

## Element 92 - U - Uranium
- **fact_a_true:** "Uranium is named after the planet Uranus and powers nuclear reactors that make electricity."
  - Verdict: TRUE
  - Reason: Klaproth (1789) named uranium after the then-newly-discovered planet Uranus (1781). U-235 fission powers commercial reactors.
  - Concern: NONE
- **fact_b_false:** "Uranium helps doctors take clearer pictures inside the body with MRI scans."
  - Verdict: FALSE
  - Reason: MRI contrast uses gadolinium (row #64). Uranium has no diagnostic-imaging role. Properly false.
  - Concern: LOW - if a kid mislearned this, no immediate safety risk (they aren't going to handle uranium), but it would muddle the gadolinium/MRI association.

## Element 93 - Np - Neptunium
- **fact_a_true:** "Neptunium is named after the planet Neptune, just past Uranus in our solar system."
  - Verdict: TRUE
  - Reason: McMillan & Abelson (1940) named it after Neptune to continue the uranium-after-Uranus sequence. Astronomy detail also TRUE: Neptune is the next planet beyond Uranus.
  - Concern: NONE
- **fact_b_false:** "Neptunium is named after the scientist who first organized the periodic table."
  - Verdict: FALSE
  - Reason: That description fits mendelevium (#101, after Mendeleev). Properly false.
  - Concern: NONE

## Element 94 - Pu - Plutonium
- **fact_a_true:** "Plutonium is named after the dwarf planet Pluto and powers some space probes."
  - Verdict: TRUE
  - Reason: Seaborg et al. (1940) named it after Pluto (then a planet, now dwarf planet, so phrasing is current-correct). Pu-238 RTGs power Voyager, Cassini, New Horizons, Perseverance, etc.
  - Concern: NONE
- **fact_b_false:** "Plutonium builds strong bones and teeth and is in milk, cheese, and yogurt."
  - Verdict: FALSE
  - Reason: Describes calcium (row #20). Plutonium is extremely toxic and radioactive; ingesting it is dangerous, not nutritious. Properly false.
  - Concern: MED - the structure "plutonium is in milk/cheese/yogurt" is exactly the kind of false-attribution a 4yo could remember out of context. If Child B later hears "plutonium" anywhere and recalls "that's in yogurt," it's a pedagogically backwards association. However, fact A's nuclear-space-probe framing already cues "this is rare/special," which helps.

## Element 95 - Am - Americium
- **fact_a_true:** "Americium is in many home smoke detectors and helps keep families safe from fires."
  - Verdict: TRUE
  - Reason: Am-241 alpha source in ionization-type smoke detectors. Very widely cited and accurate.
  - Concern: NONE
- **fact_b_false:** "Americium makes camera lenses sharper so pictures look crisp and clear."
  - Verdict: FALSE
  - Reason: Lanthanum (row #57) is the lens-clarifier. Americium has no optical role. Properly false.
  - Concern: NONE

## Element 96 - Cm - Curium
- **fact_a_true:** "Curium is named after Marie and Pierre Curie, two famous element-discovering scientists."
  - Verdict: TRUE
  - Reason: Seaborg et al. (1944) named it after the Curies. Standard.
  - Concern: NONE
- **fact_b_false:** "Curium is named after the dwarf planet Pluto and powers some space probes."
  - Verdict: FALSE (naming part) / partly TRUE (powering part)
  - Reason: Naming claim is false - that's plutonium (row #94). However, curium-244 IS also used in some RTGs and as an alpha source on Mars rover instruments (APXS on Spirit/Opportunity/Curiosity). The compound claim is properly false because of the Pluto-naming half.
  - Concern: LOW - kid won't parse the compound claim that finely; the obvious-wrong "named after Pluto" makes the whole statement read as false. Acceptable distractor.

## Element 97 - Bk - Berkelium
- **fact_a_true:** "Berkelium is named after Berkeley, California, where scientists first made it."
  - Verdict: TRUE
  - Reason: Synthesized at UC Berkeley, 1949, by Thompson, Ghiorso, Seaborg. Named after the city.
  - Concern: NONE
- **fact_b_false:** "Berkelium is the light, shiny metal in soda cans and kitchen foil."
  - Verdict: FALSE
  - Reason: Describes aluminum (row #13). Berkelium is a synthetic actinide produced in microgram quantities. Properly false.
  - Concern: LOW - absurd enough (and "berkelium" sounds nothing like "aluminum") that mislearning risk is small.

## Element 98 - Cf - Californium
- **fact_a_true:** "Californium helps find gold deep underground and start some nuclear reactors."
  - Verdict: TRUE
  - Reason: Cf-252 is a strong neutron emitter used in neutron-activation well-logging (oil/gold/mineral prospecting) and as a neutron source to start reactor cores. Both applications are documented.
  - Concern: NONE
- **fact_b_false:** "Californium coats steel cans so the soup or beans inside don't rust the can."
  - Verdict: FALSE
  - Reason: Describes tin (row #50). Californium is far too rare/radioactive to coat anything. Properly false.
  - Concern: NONE

## Element 99 - Es - Einsteinium
- **fact_a_true:** "Einsteinium is named after Albert Einstein, the famous scientist who imagined time bending."
  - Verdict: TRUE
  - Reason: Discovered in 1952 debris of the "Ivy Mike" thermonuclear test; named after Einstein. "Imagined time bending" is a kid-friendly relativity reference - acceptable.
  - Concern: NONE
- **fact_b_false:** "Einsteinium is so rare that there are only a few atoms of it on Earth at any time."
  - Verdict: MISLEADING (potentially TRUE in spirit)
  - Reason: Einsteinium occurs *naturally* in vanishingly small amounts (synthetically produced in microgram quantities; only nanograms are routinely available for research). "Few atoms" is poetic, but einsteinium really is one of the rarest synthesizable elements. The intended false-attribution is to francium (row #87 fact_a) but the claim defensibly applies to einsteinium too.
  - Concern: LOW - directional truth carries; row is weak as a "clearly false" distractor but not actively harmful.

## Element 100 - Fm - Fermium
- **fact_a_true:** "Fermium is named after Enrico Fermi, a scientist who built the first nuclear reactor."
  - Verdict: TRUE
  - Reason: Co-discovered with einsteinium in Ivy Mike debris (1952), named after Fermi. Fermi did lead Chicago Pile-1, the first artificial nuclear reactor (1942).
  - Concern: NONE
- **fact_b_false:** "Fermium is in every living thing on Earth, from trees to people to your pet dog."
  - Verdict: FALSE
  - Reason: Describes carbon (row #6). Fermium is synthetic, extremely radioactive, and not biologically present. Properly false.
  - Concern: LOW - absurd enough to a kid who already accepts "fermium is rare/special."

---

## Summary

### HIGH-concern rows (fact A wrong, or fact B accidentally true, or high mislearning risk)

- **Row #82 (Lead).**
  - `fact_b_false` "Lead paints used to make sunny yellow colors in old artists' paintings" is actually TRUE - lead chromate ("chrome yellow") and lead-tin yellow are well-documented historic pigments (Van Gogh, etc.). The microgame TRUE/FALSE assignment is broken for this row. **Action: edit `distractors.json` to replace fact_b with a clearly-false claim** (e.g. swap in a description of bismuth's rainbow crystals, or magnesium's flash flame).
  - `fact_a_true` "Lead ... used in pencils long ago" is MISLEADING; pencil "lead" has always been graphite. **Action: consider rewording fact_a** to drop the pencils claim - replace with something like "Lead is so heavy it was used to make weights for fishing lines and old church-window frames." Pencils confusion is the kid-mislearn risk you flagged in the brief.

### MED-concern rows (safety-adjacent mislearning)

- **Row #81 (Thallium).** `fact_b_false` says thallium chloride is table salt. If a kid generalizes "any salt-shaped white powder = sprinkle on food" they're at real harm risk. Consider rephrasing to make the false-ness more obviously absurd (e.g. "Thallium is the orange fruit growing on lemon trees"). The current phrasing is dangerously plausible.
- **Row #94 (Plutonium).** `fact_b_false` puts plutonium in milk/cheese/yogurt. Same structural risk as #81 (food + dangerous element). The fact_a "powers space probes" framing helps cue rarity, so risk is lower than #81, but consider rephrasing fact_b to something more obviously absurd (e.g. "Plutonium is the bright yellow stuff in lemons").

### LOW-concern rows worth noting (factually loose but acceptable)

- Row #87 (Francium): "few atoms" undersells ~30g, but directionally fine for a 4yo.
- Row #89 (Actinium): the "rare/expensive" distractor is defensibly true of actinium too; weak distractor but harmless.
- Row #96 (Curium): curium IS used in some RTGs/space instruments, but the "named after Pluto" half makes the compound claim cleanly false.
- Row #99 (Einsteinium): "few atoms exist" claim is poetic and arguably true of einsteinium; weak as a distractor.

### Rows where everything checks out (no action)

#83 Bismuth, #84 Polonium, #85 Astatine, #86 Radon, #88 Radium, #90 Thorium, #91 Protactinium, #92 Uranium, #93 Neptunium, #95 Americium, #97 Berkelium, #98 Californium, #100 Fermium.

### Recommended priority

1. **Fix row #82 (Lead)** - this is the only row where the microgame's TRUE/FALSE label is structurally wrong. Child B would be marked wrong for picking the correct answer, or learn that fact_b is false when it's actually true.
2. **Reword row #81 (Thallium)** and **row #94 (Plutonium)** fact_b strings to remove the food-association mislearning vector.
3. Optionally reword row #82 fact_a to drop the lead/pencils confusion.
