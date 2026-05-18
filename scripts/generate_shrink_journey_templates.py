"""Phase M Step M6 — generate ~15 "shrink down" guided-journey templates.

One-shot CLI that appends hand-authored multi-step branching templates
to ``src/toybox/activities/templates/branching/request_story.json``.
Each template patterns on Magic School Bus: the persona (``guide_mentor``,
biased toward Professor Iridia) shrinks the kid down inside an element
or compound, narrates the inside view, then branches on where to go
next (nucleus vs electron shell, surface vs interior, gas vs liquid
state). The activity ends with a Phase L song reward via
``ending_step: {"kind": "song", "auto": true}`` (the runtime ignores
this key per Phase L Step L5 but it is included for spec parity with
M4 / M5).

Coverage (15 total — the plan's sweet-spot count, phase-m §5.6):

Gas phase (5)
    * ``shrink_into_helium_balloon_voyage`` — he-2
    * ``shrink_into_oxygen_lung_dive`` — o-8
    * ``shrink_into_hydrogen_star_nursery`` — h-1
    * ``shrink_into_neon_sign_glow`` — ne-10
    * ``shrink_into_argon_lightbulb_hush`` — ar-18

Liquid phase (3)
    * ``shrink_into_mercury_silver_river`` — hg-80
    * ``shrink_into_water_droplet_journey`` — o-8 (water = H2O; the
      entry step spotlights oxygen, the "heart" of a water molecule,
      since the corpus keys only on single elements)
    * ``shrink_into_liquid_nitrogen_chill`` — n-7

Solid phase (5)
    * ``shrink_into_gold_treasure_chest`` — au-79
    * ``shrink_into_iron_horseshoe`` — fe-26
    * ``shrink_into_carbon_diamond_lattice`` — c-6
    * ``shrink_into_sodium_salt_grain`` — na-11
    * ``shrink_into_lithium_battery_buzz`` — li-3

Famous metals (2)
    * ``shrink_into_silver_spoon_shine`` — ag-47
    * ``shrink_into_copper_penny_warmth`` — cu-29

Authoring style mirrors M5: Python literals + JSON emitter, idempotent,
``--dry-run`` / ``--force`` / ``--validate`` / ``--output`` flags.
Existing ``shrink_into_*`` entries are stripped before the new batch
appends.

Field order matches the 200+ pre-existing branching templates in
``request_story.json`` and the M4 / M5 generators: ``id, title,
buckets, steps, required_roles, optional_roles, recommended_themes,
ending_step``.

Age-appropriate framing
-----------------------

Every template:

* Frames shrinking as a magical, protected ride (Iridia's "magic
  bubble" / "tiny ship" / "soft glove" / "bubble suit"). NO existential
  "vast emptiness" / "you are a speck" language — the inside view is
  wondrous and inhabited, not desolate.
* Mercury template explicitly invokes the magic bubble for safety so
  Child B does not internalize "mercury is fine to touch."
* The hydrogen-star template stays inside a "soft glowing cloud" and
  resolves with stars getting born — never "the cold dark of space."
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Final

_logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT: Final[Path] = Path(
    "src/toybox/activities/templates/branching/request_story.json"
)

# Idempotence prefix: every M6 template id starts with this slug so a
# re-run strips the previous batch before appending. The trailing
# underscore prevents a substring match from accidentally colliding
# with a hypothetical Phase G template named e.g. ``shrink_into_castle``
# (none exist today but the guard is cheap).
_TEMPLATE_PREFIX: Final[str] = "shrink_into_"


# ---------------------------------------------------------------------
# Hand-authored templates
# ---------------------------------------------------------------------
#
# Conventions enforced across every template:
#  * ``required_roles: ["guide_mentor"]`` — biases the persona picker
#    toward Professor Iridia (phase-m §6.9).
#  * ``optional_roles: ["friend"]`` — soft backstop so the K3.2
#    slot-fill has a fall-back when the kid's toy pool has more than
#    one role-eligible toy.
#  * ``recommended_themes: ["adventure", "magic"]`` — both, per
#    phase-m-plan §5.6.
#  * ``ending_step: {"kind": "song", "auto": true}`` — Phase L appends
#    the song reward step. The runtime ignores this field per Phase L
#    Step L5; included for spec parity with M4 / M5 generators.
#  * ``buckets: ["always"]`` — no time-of-day restriction.
#  * ``element_id`` lives ONLY on the entry step (one spotlight per
#    template per phase-m-plan §5.6). HYPHEN format (``"au-79"``); the
#    template id itself uses underscore (``shrink_into_gold_*``).
#  * ``{guide_mentor}`` placeholder used at least once so the K3.2
#    distinct-toy-ceiling gate (``len(required_roles) ≤ ceiling``)
#    is satisfied.
#  * 5-9 steps per template (per spec). Each template has exactly one
#    fork point with 2-3 choices, matching the M5 / Phase G "1 fork
#    point, 2-4 choices per fork" reading of the spec's "2-3 forks"
#    line.

_TEMPLATES: Final[list[dict[str, Any]]] = [
    # =================================================================
    # GAS PHASE (5)
    # =================================================================
    {
        "id": "shrink_into_helium_balloon_voyage",
        "title": "Inside a helium balloon",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} blows up a shiny balloon in {room}. "
                    "\"Ready to shrink down INSIDE the balloon? My magic "
                    "bubble keeps you safe. Hold on tight!\""
                ),
                "action_slot": "pointing",
                "element_id": "he-2",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you are tiny! Helium atoms float all around you "
                    "like soft bouncy beach balls. They don't bump hard; "
                    "they just drift."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "{guide_mentor} smiles. \"Where do you want to look "
                    "first?\""
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Peek at one helium atom up close", "next": "atom"},
                    {"label": "Float to the top of the balloon", "next": "top"},
                    {"label": "Bounce around with the bunch", "next": "bunch"},
                ],
            },
            {
                "id": "atom",
                "text": (
                    "One helium atom drifts close. Two tiny electrons spin "
                    "around a calm little nucleus — {guide_mentor} says they hold on "
                    "tight, so helium never grabs friends."
                ),
                "action_slot": "looking",
                "next": "land",
            },
            {
                "id": "top",
                "text": (
                    "You bob up to the top of the balloon. Helium is so "
                    "light it pushes the whole balloon up into the air!"
                ),
                "action_slot": "jumping",
                "next": "land",
            },
            {
                "id": "bunch",
                "text": (
                    "You bounce off five, ten, twenty helium atoms — boing, "
                    "boing! They tickle but never stick."
                ),
                "action_slot": "cheering",
                "next": "land",
            },
            {
                "id": "land",
                "text": (
                    "{guide_mentor} pops you back to normal size. \"Helium "
                    "is the floaty quiet kid. Now you know what its inside "
                    "feels like.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "shrink_into_oxygen_lung_dive",
        "title": "Riding an oxygen molecule into the lungs",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} taps a sparkly wand. \"Take a deep "
                    "breath. We are shrinking small enough to ride an "
                    "oxygen molecule on the way in!\""
                ),
                "action_slot": "pointing",
                "element_id": "o-8",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — tiny! Two oxygen atoms hold hands right next to "
                    "you. They float into your nose with a soft whoosh."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "The oxygen pair zooms toward the lungs. Where do you "
                    "want to ride along?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Hop onto a red blood cell", "next": "red"},
                    {"label": "Bounce around inside the lung", "next": "lung"},
                ],
            },
            {
                "id": "red",
                "text": (
                    "A round red blood cell scoops up the oxygen — and you "
                    "with it. Off you zoom through a sparkly pink tunnel "
                    "toward the heart!"
                ),
                "action_slot": "jumping",
                "next": "wave",
            },
            {
                "id": "lung",
                "text": (
                    "You bounce around inside a tiny lung pocket. Oxygen "
                    "atoms tap-tap-tap on the walls, then slip through to "
                    "go feed the body."
                ),
                "action_slot": "cheering",
                "next": "wave",
            },
            {
                "id": "wave",
                "text": (
                    "{guide_mentor} pops you back. \"Oxygen is the "
                    "everywhere helper — every breath you take is a tiny "
                    "delivery!\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "shrink_into_hydrogen_star_nursery",
        "title": "Inside a hydrogen star nursery",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} opens a soft glowing cloud in {room}. "
                    "\"This is a baby-star nursery. The cloud is made of "
                    "hydrogen — the very first element! Want to peek "
                    "inside?\""
                ),
                "action_slot": "pointing",
                "element_id": "h-1",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you shrink into the cloud. Hydrogen atoms "
                    "twinkle all around you like fireflies in a pillow fort."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "The atoms drift closer together. \"Where do you want "
                    "to play?\" {guide_mentor} asks."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Cuddle two atoms into a pair", "next": "pair"},
                    {"label": "Watch a brand-new star light up", "next": "star"},
                    {"label": "Float through the cloud", "next": "drift"},
                ],
            },
            {
                "id": "pair",
                "text": (
                    "Two hydrogen atoms hold hands and become H2 — a tiny "
                    "molecule! They wiggle together like a buddy team."
                ),
                "action_slot": "cheering",
                "next": "home",
            },
            {
                "id": "star",
                "text": (
                    "A whole bunch of hydrogen cuddles SUPER close. POP — "
                    "they light up into a brand-new baby star, warm and "
                    "bright!"
                ),
                "action_slot": "cheering",
                "next": "home",
            },
            {
                "id": "drift",
                "text": (
                    "You float gently through the cozy cloud. Every twinkle "
                    "is one hydrogen atom — the simplest atom there is, "
                    "just one electron and one proton."
                ),
                "action_slot": "looking",
                "next": "home",
            },
            {
                "id": "home",
                "text": (
                    "{guide_mentor} catches you in her magic bubble. "
                    "\"Hydrogen built the stars AND it builds water. Pretty "
                    "amazing for such a tiny atom!\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "shrink_into_neon_sign_glow",
        "title": "Inside a glowing neon sign",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} points at a bright sign humming in "
                    "{room}. \"That orange-red glow? That is neon gas. "
                    "Let's shrink inside!\""
                ),
                "action_slot": "pointing",
                "element_id": "ne-10",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you are inside the glass tube! Neon atoms drift "
                    "all around you. Then BZZT — electricity tickles them, "
                    "and they GLOW orange-red."
                ),
                "action_slot": "cheering",
            },
            {
                "id": "fork",
                "text": (
                    "\"Where should we peek?\" {guide_mentor} asks. \"The whole "
                    "place is lit up!\""
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Watch one atom glow up close", "next": "atom"},
                    {"label": "Slide along the sign's curve", "next": "slide"},
                    {"label": "Bounce in the bright middle", "next": "middle"},
                ],
            },
            {
                "id": "atom",
                "text": (
                    "One neon atom soaks up the electricity, then BLINK — "
                    "out comes a tiny burst of orange light. It does that "
                    "over and over!"
                ),
                "action_slot": "looking",
                "next": "exit",
            },
            {
                "id": "slide",
                "text": (
                    "You surf along a glowing curve of the sign. Every "
                    "loop and squiggle is one long tube full of happy "
                    "glowing neon."
                ),
                "action_slot": "jumping",
                "next": "exit",
            },
            {
                "id": "middle",
                "text": (
                    "In the bright middle, millions of neon atoms flash at "
                    "once. It feels like dancing in a sunset."
                ),
                "action_slot": "cheering",
                "next": "exit",
            },
            {
                "id": "exit",
                "text": (
                    "{guide_mentor} pops you out. \"Neon is the quiet kid "
                    "who shines when you give it a little zap.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "shrink_into_argon_lightbulb_hush",
        "title": "Inside a quiet argon lightbulb",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} unscrews a pretend lightbulb. \"Inside "
                    "this bulb is argon gas — it hushes everything so the "
                    "filament can shine forever. Want to peek inside?\""
                ),
                "action_slot": "pointing",
                "element_id": "ar-18",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you are floating inside the bulb! Argon atoms "
                    "drift past you, slow and calm. They do not bump, do "
                    "not grab. It is very quiet."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "In the middle of the bulb, a thin filament wire "
                    "stretches across. \"Where to?\" {guide_mentor} whispers."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Tiptoe near the filament", "next": "filament"},
                    {"label": "Float in the calm corner", "next": "corner"},
                ],
            },
            {
                "id": "filament",
                "text": (
                    "You tiptoe close. The filament glows orange. Argon "
                    "tucks all around it like a soft scarf — no oxygen "
                    "allowed, no burning out."
                ),
                "action_slot": "looking",
                "next": "exit",
            },
            {
                "id": "corner",
                "text": (
                    "You drift to a quiet corner of the bulb. Argon never "
                    "rushes. It just floats and protects."
                ),
                "action_slot": "thinking",
                "next": "exit",
            },
            {
                "id": "exit",
                "text": (
                    "{guide_mentor} flips the switch off and pops you out. "
                    "\"Argon's whole job is to be calm and protect. That "
                    "is a noble gas's superpower.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    # =================================================================
    # LIQUID PHASE (3)
    # =================================================================
    {
        "id": "shrink_into_mercury_silver_river",
        "title": "Skating across a mercury silver river",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} holds up a pretend silver thermometer. "
                    "\"The shiny silver stuff inside is mercury — a metal "
                    "that flows like water. We never touch it for real, but "
                    "my magic bubble keeps us safe to look!\""
                ),
                "action_slot": "pointing",
                "element_id": "hg-80",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you are tiny, inside a glass bubble, floating "
                    "on a shimmery silver river of mercury. The atoms "
                    "slide past each other like fish."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "\"Where do you want to glide?\" {guide_mentor} asks, holding "
                    "the bubble steady."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Look at one mercury atom up close", "next": "atom"},
                    {"label": "Slide along the silver surface", "next": "surface"},
                    {"label": "Watch a droplet roll into a ball", "next": "drop"},
                ],
            },
            {
                "id": "atom",
                "text": (
                    "One mercury atom drifts past — a heavy round ball, "
                    "full of electrons. It does not grab the other atoms "
                    "very tight, which is why mercury flows so easily."
                ),
                "action_slot": "looking",
                "next": "exit",
            },
            {
                "id": "surface",
                "text": (
                    "You glide along the shiny surface. It is so smooth it "
                    "works like a mirror — you see your bubble reflected!"
                ),
                "action_slot": "jumping",
                "next": "exit",
            },
            {
                "id": "drop",
                "text": (
                    "A mercury droplet rolls itself into a perfect silver "
                    "ball, then BOING — bounces on the river without "
                    "splashing. Mercury is weird and wonderful."
                ),
                "action_slot": "cheering",
                "next": "exit",
            },
            {
                "id": "exit",
                "text": (
                    "{guide_mentor} pops the bubble back to {room}. "
                    "\"Mercury is the only metal that is liquid at room "
                    "temperature. Beautiful — but only safe to look at.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "shrink_into_water_droplet_journey",
        "title": "Swimming inside a water droplet",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} catches a raindrop on her finger. "
                    "\"Every water drop is made of TWO things — oxygen and "
                    "hydrogen, holding hands. Shall we shrink inside?\""
                ),
                "action_slot": "pointing",
                "element_id": "o-8",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — splash! You are inside the droplet. Tiny water "
                    "molecules zip past you. Each one is one oxygen atom "
                    "with two hydrogen buddies, like a Mickey-Mouse head."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "\"Where shall we swim?\" {guide_mentor} asks, paddling beside "
                    "you in her own bubble."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Peek at one water molecule", "next": "molecule"},
                    {"label": "Ride a wave inside the drop", "next": "wave"},
                    {"label": "Bump along with the bunch", "next": "bunch"},
                ],
            },
            {
                "id": "molecule",
                "text": (
                    "One water molecule drifts close. The big oxygen atom "
                    "holds two tiny hydrogens at a wide angle — like a "
                    "kid with two pigtails!"
                ),
                "action_slot": "looking",
                "next": "land",
            },
            {
                "id": "wave",
                "text": (
                    "You surf a tiny wave inside the droplet. Water "
                    "molecules slide past each other — that's why water "
                    "flows so smoothly."
                ),
                "action_slot": "jumping",
                "next": "land",
            },
            {
                "id": "bunch",
                "text": (
                    "You bump along with a huge crowd of water molecules. "
                    "They grab each other softly with their hydrogens — "
                    "that grabby team is what makes water sticky!"
                ),
                "action_slot": "cheering",
                "next": "land",
            },
            {
                "id": "land",
                "text": (
                    "{guide_mentor} pops the drop back into the sky. "
                    "\"Water is two of the simplest atoms working as a "
                    "team. Hydrogen and oxygen — best buddies in nature.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "shrink_into_liquid_nitrogen_chill",
        "title": "Skating on liquid nitrogen",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} sets a frosty pretend flask in {room}. "
                    "\"Inside is liquid nitrogen — nitrogen squeezed so "
                    "cold it turned into a pool! My magic bubble keeps "
                    "you snuggly warm.\""
                ),
                "action_slot": "pointing",
                "element_id": "n-7",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you skate onto the surface of the chilly pool. "
                    "Nitrogen molecules huddle in pairs, sliding past each "
                    "other like dancers in fluffy mittens."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "Misty fog curls up off the surface. \"Where to?\" "
                    "{guide_mentor} grins."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Watch a fog cloud puff up", "next": "fog"},
                    {"label": "Skate across the surface", "next": "skate"},
                ],
            },
            {
                "id": "fog",
                "text": (
                    "A puff of nitrogen escapes the pool and turns into "
                    "fog. The liquid is so cold, even normal air gets "
                    "frosty above it!"
                ),
                "action_slot": "cheering",
                "next": "exit",
            },
            {
                "id": "skate",
                "text": (
                    "You glide across the smooth surface. Underneath, "
                    "billions of nitrogen pairs slip and slide — that is "
                    "what makes a liquid a liquid."
                ),
                "action_slot": "jumping",
                "next": "exit",
            },
            {
                "id": "exit",
                "text": (
                    "{guide_mentor} pops you back to {room}. \"Nitrogen is "
                    "everywhere in the air — but get it cold enough and it "
                    "becomes a flowing pool. So cool!\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    # =================================================================
    # SOLID PHASE (5)
    # =================================================================
    {
        "id": "shrink_into_gold_treasure_chest",
        "title": "Inside a gold coin from a treasure chest",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} opens a pretend treasure chest in "
                    "{room}. \"Pirates love gold — but have you ever been "
                    "INSIDE a coin? Shrink with me!\""
                ),
                "action_slot": "pointing",
                "element_id": "au-79",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you stand on top of a glowing yellow city. "
                    "Gold atoms are stacked in neat rows like building "
                    "blocks. Electrons swim between them like fish in a "
                    "shiny sea."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "\"Where to first?\" {guide_mentor} asks, holding up a tiny "
                    "lantern."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Walk among the gold atoms", "next": "atoms"},
                    {"label": "Surf the sea of electrons", "next": "electrons"},
                    {"label": "Peek at the gleaming surface", "next": "surface"},
                ],
            },
            {
                "id": "atoms",
                "text": (
                    "You walk between the stacks. Gold atoms are heavy, "
                    "calm, and lined up in a perfect grid — that's why "
                    "gold feels solid and never rusts."
                ),
                "action_slot": "looking",
                "next": "out",
            },
            {
                "id": "electrons",
                "text": (
                    "WHEEE — you ride a wave of free electrons! They flow "
                    "all over the gold, which is why gold is so shiny and "
                    "conducts electricity."
                ),
                "action_slot": "jumping",
                "next": "out",
            },
            {
                "id": "surface",
                "text": (
                    "You climb to the surface. Light bounces off so well "
                    "you can see your reflection wiggle in the gold!"
                ),
                "action_slot": "cheering",
                "next": "out",
            },
            {
                "id": "out",
                "text": (
                    "{guide_mentor} pops the chest closed. \"Gold is "
                    "stacked atoms with a sea of electrons on top. That's "
                    "why it glitters forever.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "shrink_into_iron_horseshoe",
        "title": "Inside an iron horseshoe magnet",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} sets a chunky iron horseshoe on the "
                    "table. \"Iron is the strongest helper metal. Watch — "
                    "it can be magnetic! Want to shrink inside?\""
                ),
                "action_slot": "pointing",
                "element_id": "fe-26",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you are standing inside the horseshoe! Iron "
                    "atoms tower around you in neat rows. Invisible swirly "
                    "field lines arc from end to end."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "\"Where to first?\" {guide_mentor} twirls in the swirly field."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Ride a magnetic field line", "next": "field"},
                    {"label": "Knock on one iron atom", "next": "knock"},
                    {"label": "Run from end to end", "next": "ends"},
                ],
            },
            {
                "id": "field",
                "text": (
                    "WHOOSH — you grab onto a glowing field line and curve "
                    "from one end of the horseshoe to the other. That is "
                    "the path a paperclip would feel."
                ),
                "action_slot": "jumping",
                "next": "drop",
            },
            {
                "id": "knock",
                "text": (
                    "You tap one iron atom. Its tiny electrons spin in the "
                    "same direction as its neighbours — that team spinning "
                    "is exactly what makes iron magnetic!"
                ),
                "action_slot": "looking",
                "next": "drop",
            },
            {
                "id": "ends",
                "text": (
                    "You sprint from one tip of the horseshoe to the other. "
                    "One tip is North, the other is South — and the pull "
                    "between them is the magnetism!"
                ),
                "action_slot": "running",
                "next": "drop",
            },
            {
                "id": "drop",
                "text": (
                    "{guide_mentor} sets you back on the table. \"Iron's "
                    "atoms can team up to make a magnetic field. Tough AND "
                    "magnetic — what a hero metal!\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "shrink_into_carbon_diamond_lattice",
        "title": "Inside a sparkling diamond",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} holds up a sparkly pretend diamond. "
                    "\"This is carbon — the SAME stuff as pencil lead, but "
                    "stacked differently! Let's shrink inside.\""
                ),
                "action_slot": "pointing",
                "element_id": "c-6",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you stand in a sparkly hall of crystal. Carbon "
                    "atoms hold each other tight in a pattern shaped like "
                    "tiny pyramids — over and over forever!"
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "\"Where do you want to wander?\" {guide_mentor} twinkles."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Climb the crystal pyramid", "next": "climb"},
                    {"label": "Watch light bounce around", "next": "light"},
                    {"label": "Hop atom to atom", "next": "hop"},
                ],
            },
            {
                "id": "climb",
                "text": (
                    "You climb a pyramid of four carbon atoms holding hands. "
                    "Each atom grabs four neighbours — that strong grip is "
                    "why diamond is the HARDEST thing on Earth."
                ),
                "action_slot": "cheering",
                "next": "exit",
            },
            {
                "id": "light",
                "text": (
                    "A sunbeam zooms in and PING — bounces off the carbon "
                    "atoms in a hundred directions. That is the sparkle "
                    "you see from outside!"
                ),
                "action_slot": "looking",
                "next": "exit",
            },
            {
                "id": "hop",
                "text": (
                    "You hop atom to atom like a hopscotch grid that goes "
                    "in every direction at once. Carbon is the king of "
                    "shapes — it builds diamonds AND pencils AND you!"
                ),
                "action_slot": "jumping",
                "next": "exit",
            },
            {
                "id": "exit",
                "text": (
                    "{guide_mentor} pops you out. \"Same atom, different "
                    "stacking — that's all the difference between a pencil "
                    "and a diamond. Carbon is magic.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "shrink_into_sodium_salt_grain",
        "title": "Inside a grain of salt",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} sprinkles a single grain of salt on her "
                    "hand. \"Salt is sodium holding hands with chlorine. "
                    "Today we shrink inside to meet the sodium side!\""
                ),
                "action_slot": "pointing",
                "element_id": "na-11",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you stand inside a tidy salt cube. Sodium "
                    "atoms and chlorine atoms sit in checkerboard rows, "
                    "holding hands tightly: Na, Cl, Na, Cl..."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "\"Where do you want to peek?\" {guide_mentor} asks."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "High-five a sodium atom", "next": "highfive"},
                    {"label": "Walk a checkerboard row", "next": "row"},
                ],
            },
            {
                "id": "highfive",
                "text": (
                    "You walk up to one sodium atom and high-five it. It "
                    "gave away one tiny electron to its chlorine partner — "
                    "that's how the two atoms hold so tight."
                ),
                "action_slot": "cheering",
                "next": "exit",
            },
            {
                "id": "row",
                "text": (
                    "You hop down a row: sodium, chlorine, sodium, chlorine. "
                    "It is the same pattern in every direction — that is "
                    "why salt grains look like little cubes!"
                ),
                "action_slot": "jumping",
                "next": "exit",
            },
            {
                "id": "exit",
                "text": (
                    "{guide_mentor} pops you back to {room}. \"Sodium loves "
                    "to share an electron with chlorine. Together they make "
                    "salt — and salt makes food yummy!\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "shrink_into_lithium_battery_buzz",
        "title": "Inside a buzzing lithium battery",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} unscrews a pretend toy battery. "
                    "\"Inside is lithium — the lightest metal! It is what "
                    "makes your tablet light up. Want to peek?\""
                ),
                "action_slot": "pointing",
                "element_id": "li-3",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you stand in a glowing little factory. Tiny "
                    "lithium atoms zip from one side of the battery to the "
                    "other, dropping electrons off as they go."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "\"Where do you want to ride?\" {guide_mentor} grins."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Hop on a lithium atom", "next": "atom"},
                    {"label": "Watch electrons flow", "next": "flow"},
                    {"label": "Visit the charging side", "next": "charge"},
                ],
            },
            {
                "id": "atom",
                "text": (
                    "You hop on a lithium atom and surf across the battery. "
                    "It drops off one tiny electron at the other end — that "
                    "electron will go power your toy!"
                ),
                "action_slot": "jumping",
                "next": "exit",
            },
            {
                "id": "flow",
                "text": (
                    "WHEEE — a stream of electrons zooms out one end of "
                    "the battery, around a wire, and back in the other. "
                    "That's what makes things light up!"
                ),
                "action_slot": "cheering",
                "next": "exit",
            },
            {
                "id": "charge",
                "text": (
                    "You visit the charging side. When the battery plugs "
                    "in, the lithium atoms zip BACK the other way, getting "
                    "ready to do it all over again."
                ),
                "action_slot": "running",
                "next": "exit",
            },
            {
                "id": "exit",
                "text": (
                    "{guide_mentor} pops you back to {room}. \"Lithium is "
                    "tiny and feathery — and it carries the power that "
                    "runs your favourite gadgets.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    # =================================================================
    # FAMOUS METALS (2)
    # =================================================================
    {
        "id": "shrink_into_silver_spoon_shine",
        "title": "Inside a polished silver spoon",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} polishes a shiny silver spoon. "
                    "\"Silver is the SHINIEST metal of them all. Shall we "
                    "shrink inside?\""
                ),
                "action_slot": "pointing",
                "element_id": "ag-47",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you stand on a glistening silver field. Silver "
                    "atoms are stacked in tidy rows, with extra electrons "
                    "skating across the top like ice dancers."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "\"Where do you want to glide?\" {guide_mentor} asks."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Skate with the electrons", "next": "skate"},
                    {"label": "Watch the light bounce", "next": "light"},
                    {"label": "Walk among silver atoms", "next": "atoms"},
                ],
            },
            {
                "id": "skate",
                "text": (
                    "You join the skating electrons. They glide across the "
                    "whole spoon like one big team — which is why silver "
                    "is even better than gold at carrying electricity!"
                ),
                "action_slot": "cheering",
                "next": "exit",
            },
            {
                "id": "light",
                "text": (
                    "A sunbeam lands on the surface. PING — it bounces off "
                    "almost perfectly! That's why a silver mirror is the "
                    "sharpest mirror of all."
                ),
                "action_slot": "looking",
                "next": "exit",
            },
            {
                "id": "atoms",
                "text": (
                    "You walk between rows of silver atoms. They are "
                    "softer than gold but lined up just as neatly — that "
                    "is why a silversmith can hammer them into any shape."
                ),
                "action_slot": "jumping",
                "next": "exit",
            },
            {
                "id": "exit",
                "text": (
                    "{guide_mentor} pops you out. \"Silver: the shiniest "
                    "and fastest at carrying electricity. A real superstar "
                    "metal.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "shrink_into_copper_penny_warmth",
        "title": "Inside a warm copper penny",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} sets a shiny copper penny on her palm. "
                    "\"Copper is the warmest pinky-orange metal. It carries "
                    "electricity and heat. Want to shrink in?\""
                ),
                "action_slot": "pointing",
                "element_id": "cu-29",
            },
            {
                "id": "shrink",
                "text": (
                    "POOF — you stand in a glowing orange canyon. Copper "
                    "atoms line the walls in cozy rows, and warm electrons "
                    "ripple over them like a sunset on water."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": (
                    "\"Where to first?\" {guide_mentor} rubs her hands warm."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Ride a copper wire", "next": "wire"},
                    {"label": "Feel the warmth pass through", "next": "warm"},
                    {"label": "Look at the orange surface", "next": "surface"},
                ],
            },
            {
                "id": "wire",
                "text": (
                    "WHEEE — you zoom along a copper wire as electrons "
                    "flow around you. Copper is everywhere inside houses, "
                    "carrying power to every lamp and screen!"
                ),
                "action_slot": "jumping",
                "next": "exit",
            },
            {
                "id": "warm",
                "text": (
                    "You hold still and feel warmth wash through. Copper's "
                    "atoms pass heat from one to the next really fast — "
                    "that is why pots and pans like a copper bottom."
                ),
                "action_slot": "thinking",
                "next": "exit",
            },
            {
                "id": "surface",
                "text": (
                    "You climb to the surface. The pinky-orange glow is "
                    "copper's special colour — only copper and gold are "
                    "metals that aren't silvery-grey!"
                ),
                "action_slot": "looking",
                "next": "exit",
            },
            {
                "id": "exit",
                "text": (
                    "{guide_mentor} pops you back to {room}. \"Copper: "
                    "warm, pinky-orange, and the wiring of the whole "
                    "world.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
]


# ---------------------------------------------------------------------
# Load / strip / write helpers (mirror M5)
# ---------------------------------------------------------------------


def _load_existing(path: Path) -> dict[str, Any]:
    """Read the existing intent file. Refuses to overwrite a structurally
    broken file — mirrors :func:`generate_family_pretend_templates._load_existing`."""
    if not path.exists():
        raise FileNotFoundError(
            f"output file {path} does not exist; cannot append shrink-journey "
            f"templates. Run from the worktree root."
        )
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(
            f"output file {path} is not a JSON object (got "
            f"{type(payload).__name__}); refusing to overwrite"
        )
    if payload.get("intent") != "request_story":
        raise ValueError(
            f"output file {path} has intent={payload.get('intent')!r}; "
            f"expected 'request_story'"
        )
    templates = payload.get("templates")
    if not isinstance(templates, list):
        raise ValueError(
            f"output file {path} does not contain a 'templates' list; refusing to overwrite"
        )
    return payload


def _strip_shrink_entries(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the input list with every M6 shrink_into_* entry removed. Idempotence."""
    return [t for t in templates if not str(t.get("id", "")).startswith(_TEMPLATE_PREFIX)]


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    """Persist with the same indent + trailing newline shape as M4 / M5."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _validate_post_write(path: Path, *, expected_shrink: int) -> None:
    """Re-load via the production loader and assert all M6 templates loaded.

    Mirrors :func:`generate_family_pretend_templates._validate_post_write`
    so the same diagnostic surfaces on failure (whole intent file dropped
    on schema failure → delta indicates the offending id range).
    """
    from toybox.activities.generator import (  # type: ignore[import-untyped]
        _load_intent_templates,
        clear_template_cache,
    )

    clear_template_cache()
    templates = _load_intent_templates("request_story")
    loaded_shrink = [t for t in templates if t.id.startswith(_TEMPLATE_PREFIX)]
    if len(loaded_shrink) != expected_shrink:
        raise SystemExit(
            f"--validate: expected {expected_shrink} shrink_into_* templates "
            f"to load, got {len(loaded_shrink)}. The whole intent file may "
            f"have been dropped on schema failure; check {path} and re-run."
        )
    _logger.info(
        "--validate: %d shrink_into_* templates loaded cleanly through "
        "toybox.activities.generator._load_intent_templates",
        len(loaded_shrink),
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append ~15 hand-authored shrink-down guided-journey templates "
            "to request_story.json (Phase M Step M6)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the merged JSON to stdout and exit; do not write the file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=(
            "File to append to. Default: "
            "src/toybox/activities/templates/branching/request_story.json."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Idempotent regeneration is always-on (existing shrink_into_* entries "
            "are stripped before appending); this flag just tags the run in the log."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After writing, re-load via the production template loader and "
            "assert all shrink_into_* templates load cleanly."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    output: Path = args.output

    payload = _load_existing(output)
    existing_templates: list[dict[str, Any]] = list(payload["templates"])
    pre_count = len(existing_templates)

    stripped = _strip_shrink_entries(existing_templates)
    stripped_count = pre_count - len(stripped)

    new_templates = list(_TEMPLATES)
    merged = stripped + new_templates
    payload["templates"] = merged

    post_count = len(merged)
    _logger.info(
        "summary: pre=%d, removed_existing_shrink=%d, generated=%d, post=%d, force=%s",
        pre_count,
        stripped_count,
        len(new_templates),
        post_count,
        args.force,
    )

    if args.dry_run:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0

    _write_payload(output, payload)
    _logger.info("wrote %d templates to %s", post_count, output)

    if args.validate:
        _validate_post_write(output, expected_shrink=len(new_templates))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
