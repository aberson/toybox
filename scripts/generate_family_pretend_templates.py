"""Phase M Step M5 — generate ~30 element-family pretend-play templates.

One-shot CLI that appends hand-authored multi-step branching templates
to ``src/toybox/activities/templates/branching/request_play.json``.
Each template personifies an element family (per phase-m-plan.md §5.5)
and spotlights one family member via ``element_id`` on the entry step.

Family coverage (30 total — exactly the plan's sweet-spot count):

* ``noble_gas`` — "Quiet kids at the party" (4)
* ``halogen`` — "Friend-makers" (4)
* ``alkali_metal`` — "Go-getters" (4)
* ``alkaline_earth`` — "Shy helpers" (3)
* ``transition_metal`` — "Shiny crafters" (5)
* ``post_transition_metal`` — "The soft ones" (3)
* ``metalloid`` — "In-betweeners" (3)
* ``nonmetal`` — "Everywhere essentials" (4)

Authoring style differs from M4: M4 is programmatic (one shape × 118
elements). M5 is creative — per-family scenarios are too distinct for a
single template to capture. So this script just *holds* the hand-authored
data and merges it into the JSON file. The benefit over directly editing
the JSON is readability (Python string literals + comments per template)
and idempotence (re-running strips the previous ``family_*`` batch
before appending).

Family-name slug rule (load-bearing per phase-m-plan §5.5 done-when):

* ``noble_gas`` → "noble gases"
* ``halogen`` → "halogens"
* ``alkali_metal`` → "alkali metals"
* ``alkaline_earth`` → "alkaline earths"
* ``transition_metal`` → "transition metals"
* ``post_transition_metal`` → "post-transition metals" (hyphenated)
* ``metalloid`` → "metalloids"
* ``nonmetal`` → "nonmetals"

NEVER use synonyms ("rare gases", "inert gases", "soft metals"). The M5
reviewer will grep for each slug-plural.

Field order matches the 250 pre-existing Phase G + Phase K templates
in request_play.json and the M4 meet_element_* templates: id, title,
buckets, steps, required_roles, optional_roles, recommended_themes,
ending_step.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Final

_logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT: Final[Path] = Path("src/toybox/activities/templates/branching/request_play.json")

# Idempotence prefix: every M5 template id starts with one of these
# family slugs (matching the Family StrEnum), and the slug is followed
# by an underscore so a substring match cannot accidentally collide with
# a Phase G template named e.g. ``play_halogenade``.
_FAMILY_PREFIXES: Final[tuple[str, ...]] = (
    "noble_gas_",
    "halogen_",
    "alkali_metal_",
    "alkaline_earth_",
    "transition_metal_",
    "post_transition_metal_",
    "metalloid_",
    "nonmetal_",
)


# ---------------------------------------------------------------------
# Hand-authored templates
# ---------------------------------------------------------------------
#
# Each entry is a complete Pydantic-shaped dict. The structure mirrors
# the Phase G branching templates already in request_play.json (see
# request_play_soak_castle_03 for a 3-choice fork reference) plus the
# M3 element_id field on the entry step.
#
# Conventions enforced across every template:
#  * ``required_roles: ["guide_mentor"]`` — biases the persona picker
#    toward Professor Iridia (phase-m §6.9).
#  * ``optional_roles: ["friend"]`` — backstop so Phase K's slot-fill
#    has a soft fall-back when the kid has more than one toy in pool.
#  * ``ending_step: {kind: "song", auto: true}`` — Phase L appends the
#    song reward. (The runtime ignores this field per L5; included
#    for spec parity with M4's output and forward-compat with future
#    surfaces.)
#  * ``buckets: ["always"]`` — no time-of-day restriction.
#  * Family-name slug-plural appears verbatim in the entry step text.
#  * ``element_id`` lives ONLY on the entry step (one spotlight per
#    template per phase-m §5.5).
#  * ``{guide_mentor}`` placeholder used at least once so the K3.2
#    distinct-toy-ceiling gate (``len(required_roles) ≤ ceiling``)
#    is satisfied.

_TEMPLATES: Final[list[dict[str, Any]]] = [
    # =================================================================
    # noble_gas — "Quiet kids at the party" (4)
    # =================================================================
    {
        "id": "noble_gas_party_floaters",
        "title": "Helium goes to the party",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} sets up a party in {room}. "Noble gases are the '
                    "quiet kids at the party — they don't grab on, they just float and "
                    'glow. Today our helper is Helium!"'
                ),
                "action_slot": "pointing",
                "element_id": "he-2",
            },
            {
                "id": "fork",
                "text": (
                    '{guide_mentor} hands you a Helium balloon. "Where should it float first?"'
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Up to the ceiling", "next": "ceiling"},
                    {"label": "Over to the snack table", "next": "snacks"},
                    {"label": "Hide in the corner", "next": "corner"},
                ],
            },
            {
                "id": "ceiling",
                "text": (
                    "The balloon zooms up and bumps the ceiling. Reach your arms up "
                    "high and wiggle your fingers like Helium dancing on the ceiling."
                ),
                "action_slot": "jumping",
                "next": "join",
            },
            {
                "id": "snacks",
                "text": (
                    "{guide_mentor} guides the Helium balloon above the cupcakes — noble "
                    "gases don't grab on. Tiptoe in a tiny circle, hands behind your back."
                ),
                "action_slot": "looking",
                "next": "join",
            },
            {
                "id": "corner",
                "text": (
                    "{guide_mentor} lets the Helium balloon drift to a quiet corner. "
                    "Crouch down small and rest your chin on your knees like a sleepy balloon."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "join",
                "text": (
                    "{guide_mentor} laughs. \"That's what noble gases do — they show "
                    "up but they don't stick to anyone.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["friendship", "silly"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "noble_gas_neon_sign_shop",
        "title": "Neon lights up the sign shop",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} owns a sign shop in {room}. "Noble gases glow '
                    "different colors when electricity tickles them. Neon glows bright "
                    'orange-red!"'
                ),
                "action_slot": "pointing",
                "element_id": "ne-10",
            },
            {
                "id": "pick",
                "text": (
                    "{guide_mentor} flicks the Neon switch. "
                    "What kind of sign should we light up tonight?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "An ice-cream shop", "next": "icecream"},
                    {"label": "A pizza place", "next": "pizza"},
                    {"label": "A movie theater", "next": "movies"},
                    {"label": "A toy store", "next": "toystore"},
                ],
            },
            {
                "id": "icecream",
                "text": (
                    "The sign blinks ICE CREAM in orange-red letters! Lick an "
                    "imaginary cone slowly."
                ),
                "action_slot": "cheering",
                "next": "close",
            },
            {
                "id": "pizza",
                "text": (
                    "PIZZA flashes bright. Pretend to toss a big pizza dough into "
                    "the air and catch it."
                ),
                "action_slot": "jumping",
                "next": "close",
            },
            {
                "id": "movies",
                "text": (
                    "NOW SHOWING glows above a tiny theater. Make a movie clapper "
                    "with your hands — clap once!"
                ),
                "action_slot": "cheering",
                "next": "close",
            },
            {
                "id": "toystore",
                "text": (
                    "TOYS sparkles in red across the window. Wave at the toys inside "
                    "as if greeting old friends."
                ),
                "action_slot": "waving",
                "next": "close",
            },
            {
                "id": "close",
                "text": (
                    "{guide_mentor} flips the open sign. \"Neon doesn't say much — "
                    'but boy, does it shine."'
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "music"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "noble_gas_argon_lightbulb_lab",
        "title": "Argon keeps the lightbulb cool",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{guide_mentor} pulls a giant pretend lightbulb out of a drawer. "
                    '"Noble gases protect things by NOT reacting. Argon fills lightbulbs '
                    "so they don't burn out.\""
                ),
                "action_slot": "pointing",
                "element_id": "ar-18",
            },
            {
                "id": "task",
                "text": (
                    "The bulb is empty inside. Cup your hands around it gently — "
                    "Argon is going to fill it up."
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Whisper Argon in", "next": "whisper"},
                    {"label": "Blow Argon in", "next": "blow"},
                ],
            },
            {
                "id": "whisper",
                "text": (
                    'You whisper "hush, hush" and pretend Argon tiptoes inside. The '
                    "bulb glows softly and stays cool forever."
                ),
                "action_slot": "thinking",
                "next": "test",
            },
            {
                "id": "blow",
                "text": (
                    "You take a small breath and blow gently. Argon drifts inside "
                    "without a sound. The bulb is safe."
                ),
                "action_slot": "thinking",
                "next": "test",
            },
            {
                "id": "test",
                "text": (
                    "{guide_mentor} flicks the switch. The bulb lights up steady "
                    "and quiet — Argon is doing its job."
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "noble_gas_xenon_flash_photo",
        "title": "Xenon takes the picture",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} holds up a pretend camera. "Noble gases are quiet, '
                    'but Xenon can flash SO bright it freezes a moment forever. Smile!"'
                ),
                "action_slot": "pointing",
                "element_id": "xe-54",
            },
            {
                "id": "pose",
                "text": (
                    "{guide_mentor} loads the Xenon flash bulb. "
                    "What kind of photo should we snap?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A silly face", "next": "silly"},
                    {"label": "A superhero pose", "next": "hero"},
                    {"label": "A sleepy yawn", "next": "yawn"},
                ],
            },
            {
                "id": "silly",
                "text": (
                    "Stick out your tongue and cross your eyes. {guide_mentor} hits "
                    "the Xenon flash — SNAP! Goofiest picture in history."
                ),
                "action_slot": "confused",
                "next": "develop",
            },
            {
                "id": "hero",
                "text": (
                    "Hands on hips, chin up, cape billowing (imaginary). {guide_mentor} "
                    "fires the Xenon flash — SNAP! Frozen mid-power-pose."
                ),
                "action_slot": "cheering",
                "next": "develop",
            },
            {
                "id": "yawn",
                "text": (
                    "Stretch your arms up and yawn HUGE. {guide_mentor} pops the "
                    "Xenon flash — SNAP! Biggest yawn ever taken."
                ),
                "action_slot": "sleeping",
                "next": "develop",
            },
            {
                "id": "develop",
                "text": (
                    '{guide_mentor} pretends to hand you the photo. "Noble gases '
                    "don't talk much — but Xenon sure knows how to make an entrance.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    # =================================================================
    # halogen — "Friend-makers" (4)
    # =================================================================
    {
        "id": "halogen_sodium_chlorine_handshake",
        "title": "Chlorine finds a friend named Sodium",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} sprinkles imaginary salt on the table. "Halogens '
                    "are friend-makers! Chlorine pairs up with Sodium to make every grain "
                    "of salt you've ever eaten.\""
                ),
                "action_slot": "pointing",
                "element_id": "cl-17",
            },
            {
                "id": "search",
                "text": (
                    "Chlorine is wandering around {room} looking for Sodium. How "
                    "does it find a friend?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "Wave hello loudly", "next": "wave"},
                    {"label": "Reach out a hand", "next": "hand"},
                    {"label": "Hum a friendly song", "next": "hum"},
                ],
            },
            {
                "id": "wave",
                "text": (
                    "You wave both arms big. {guide_mentor} spots the Sodium across "
                    "the kitchen and waves it over to join you!"
                ),
                "action_slot": "waving",
                "next": "join",
            },
            {
                "id": "hand",
                "text": (
                    "You stretch one hand out. {guide_mentor} dashes over and grabs "
                    'your hand — the Sodium snaps right onto the Chlorine!'
                ),
                "action_slot": "pointing",
                "next": "join",
            },
            {
                "id": "hum",
                "text": (
                    "Your humming carries across the room. {guide_mentor} brings the "
                    "Sodium over and sets it down beside you."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": (
                    "Snap your fingers together once — CLICK! That's what halogens "
                    "do; they grab hold and make something new."
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["friendship", "food"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "halogen_fluorine_toothbrush_team",
        "title": "Fluorine joins the toothbrush team",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} holds up a pretend toothbrush. "Halogens love '
                    "joining teams. Fluorine teams up with toothpaste to make teeth "
                    'strong and shiny!"'
                ),
                "action_slot": "pointing",
                "element_id": "f-9",
            },
            {
                "id": "task",
                "text": (
                    "{guide_mentor} sets out a tube of toothpaste with Fluorine "
                    "inside. Which tooth should we visit first?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A wiggly loose tooth", "next": "wiggly"},
                    {"label": "A brand-new molar in back", "next": "molar"},
                    {"label": "A sparkly front tooth", "next": "front"},
                ],
            },
            {
                "id": "wiggly",
                "text": (
                    "{guide_mentor} dabs Fluorine on the wiggly tooth — "
                    '"Hang in there, little buddy!" '
                    "Wiggle one finger like a loose tooth."
                ),
                "action_slot": "waving",
                "next": "brush",
            },
            {
                "id": "molar",
                "text": (
                    "{guide_mentor} brushes Fluorine onto the new molar way in the back. "
                    "Open your mouth wide and tap the air with your finger."
                ),
                "action_slot": "cheering",
                "next": "brush",
            },
            {
                "id": "front",
                "text": (
                    "{guide_mentor} polishes the front tooth with Fluorine until it shines. "
                    "Give a HUGE smile and bare your teeth like a friendly tiger."
                ),
                "action_slot": "cheering",
                "next": "brush",
            },
            {
                "id": "brush",
                "text": (
                    "{guide_mentor} cheers. \"That's halogens for you — always "
                    'joining the team that needs them."'
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["friendship", "silly"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "halogen_iodine_salt_helper",
        "title": "Iodine sneaks into the salt shaker",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} unscrews a pretend salt shaker. "Halogens are quiet '
                    'helpers. Iodine hides in your salt and helps your body grow strong."'
                ),
                "action_slot": "pointing",
                "element_id": "i-53",
            },
            {
                "id": "where",
                "text": (
                    "{guide_mentor} dabs a tiny drop of Iodine on a band-aid. "
                    "Where should it ride along today?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "On a pretzel", "next": "pretzel"},
                    {"label": "On scrambled eggs", "next": "eggs"},
                    {"label": "On popcorn", "next": "popcorn"},
                ],
            },
            {
                "id": "pretzel",
                "text": (
                    "Iodine hitches a ride on a salty pretzel. Pretend to take a "
                    "big crunchy bite — CRUNCH!"
                ),
                "action_slot": "cheering",
                "next": "thanks",
            },
            {
                "id": "eggs",
                "text": (
                    "Iodine sprinkles onto warm eggs. Pretend to scoop a forkful "
                    "and blow on it before tasting."
                ),
                "action_slot": "thinking",
                "next": "thanks",
            },
            {
                "id": "popcorn",
                "text": (
                    "Iodine dusts the popcorn. Toss imaginary popcorn up and catch "
                    "it in your mouth — gotcha!"
                ),
                "action_slot": "jumping",
                "next": "thanks",
            },
            {
                "id": "thanks",
                "text": (
                    '{guide_mentor} pats your head. "Halogens are friend-makers — '
                    "even when you can't see them, they're helping.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["food", "friendship"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "halogen_chlorine_pool_patrol",
        "title": "Chlorine patrols the swimming pool",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} tosses a pretend pool noodle. "Halogens keep things '
                    'clean! Chlorine swims around in pools chasing germs away."'
                ),
                "action_slot": "pointing",
                "element_id": "cl-17",
            },
            {
                "id": "stroke",
                "text": (
                    "{guide_mentor} cannonballs into the Chlorine pool "
                    "— pick your swim stroke!"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Freestyle flutter", "next": "free"},
                    {"label": "Big backstroke", "next": "back"},
                    {"label": "Doggy paddle", "next": "dog"},
                ],
            },
            {
                "id": "free",
                "text": (
                    "Pump your arms in circles, kick your feet small and quick — "
                    "the Chlorine swirls down the lane with you!"
                ),
                "action_slot": "running",
                "next": "spot",
            },
            {
                "id": "back",
                "text": (
                    "Lie back, sweep your arms one at a time over your head — slow "
                    "and smooth. The Chlorine ripples around you under the open sky."
                ),
                "action_slot": "looking",
                "next": "spot",
            },
            {
                "id": "dog",
                "text": (
                    "Paws up, splash splash — the Chlorine splashes everywhere as "
                    "you paddle after a tennis ball."
                ),
                "action_slot": "running",
                "next": "spot",
            },
            {
                "id": "spot",
                "text": (
                    '{guide_mentor} blows a lifeguard whistle. "Halogens are friend-makers '
                    'and germ-chasers. The pool is sparkly clean — good job, Chlorine!"'
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["friendship", "silly"],
        "ending_step": {"kind": "song", "auto": True},
    },
    # =================================================================
    # alkali_metal — "Go-getters" (4)
    # =================================================================
    {
        "id": "alkali_metal_lithium_battery_toy",
        "title": "Lithium powers up a toy",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} holds up a pretend battery. "Alkali metals are '
                    "go-getters — they wake everything up. Lithium powers phones, "
                    'tablets, and toys!"'
                ),
                "action_slot": "pointing",
                "element_id": "li-3",
            },
            {
                "id": "pick",
                "text": (
                    "{guide_mentor} loads a fresh Lithium battery. "
                    "Which toy should we power up?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A racecar", "next": "race"},
                    {"label": "A talking robot", "next": "robot"},
                    {"label": "A flying drone", "next": "drone"},
                    {"label": "A musical keyboard", "next": "music"},
                ],
            },
            {
                "id": "race",
                "text": (
                    "VROOM! The racecar zips across the floor. Crouch low and shoot "
                    "your hands forward like a racecar."
                ),
                "action_slot": "running",
                "next": "thanks",
            },
            {
                "id": "robot",
                "text": (
                    "BEEP-BOP! The robot stomps to life. March in place with stiff robot arms."
                ),
                "action_slot": "jumping",
                "next": "thanks",
            },
            {
                "id": "drone",
                "text": (
                    "WHIRR! The drone lifts off. Spin in a slow circle with your "
                    "arms out like helicopter blades."
                ),
                "action_slot": "running",
                "next": "thanks",
            },
            {
                "id": "music",
                "text": (
                    "PLINK PLONK! The keyboard plays itself. Tap your fingers in "
                    "the air like you're tickling the ivories."
                ),
                "action_slot": "cheering",
                "next": "thanks",
            },
            {
                "id": "thanks",
                "text": (
                    '{guide_mentor} bows. "That\'s what alkali metals do — they make things go."'
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "alkali_metal_potassium_banana_run",
        "title": "Potassium and the banana relay",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} peels an imaginary banana. "Alkali metals get '
                    "things moving! Potassium lives inside bananas and helps your "
                    'muscles run."'
                ),
                "action_slot": "pointing",
                "element_id": "k-19",
            },
            {
                "id": "warmup",
                "text": (
                    "{guide_mentor} sips a Potassium banana smoothie. "
                    "Stretch your legs first — what warm-up should we do?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Toe touches", "next": "toes"},
                    {"label": "Big arm circles", "next": "arms"},
                    {"label": "Hop on each foot", "next": "hops"},
                ],
            },
            {
                "id": "toes",
                "text": (
                    "Bend down slowly and reach for your toes. Potassium loosens "
                    "every muscle on the way."
                ),
                "action_slot": "thinking",
                "next": "race",
            },
            {
                "id": "arms",
                "text": (
                    "Big arm circles forward, then backward. Potassium is humming "
                    "in your shoulders now."
                ),
                "action_slot": "waving",
                "next": "race",
            },
            {
                "id": "hops",
                "text": (
                    "Hop three times on the right foot, three times on the left. "
                    "Potassium fizzes through your legs."
                ),
                "action_slot": "jumping",
                "next": "race",
            },
            {
                "id": "race",
                "text": (
                    '{guide_mentor} waves a flag. "Alkali metals are go-getters — '
                    'now go go go!" Run in place as fast as you can for five seconds.'
                ),
                "action_slot": "running",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "animals"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "alkali_metal_sodium_streetlight",
        "title": "Sodium lights the streetlamp",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} points at a pretend streetlamp. "Alkali metals '
                    "wake the world up. Sodium burns golden-yellow in old streetlights, "
                    'lighting the way home."'
                ),
                "action_slot": "pointing",
                "element_id": "na-11",
            },
            {
                "id": "task",
                "text": (
                    "{guide_mentor} flicks on a glowing Sodium streetlamp. "
                    "Who needs the light tonight?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A cat heading home", "next": "cat"},
                    {"label": "A bicycle delivery", "next": "bike"},
                    {"label": "A lost umbrella", "next": "umbrella"},
                ],
            },
            {
                "id": "cat",
                "text": (
                    "The streetlamp clicks on. The cat tiptoes through the golden "
                    'glow. Tiptoe four steps and say "meow."'
                ),
                "action_slot": "looking",
                "next": "shine",
            },
            {
                "id": "bike",
                "text": (
                    "The bike whizzes through the warm yellow circle. Pedal your "
                    "hands in fast circles like wheels."
                ),
                "action_slot": "running",
                "next": "shine",
            },
            {
                "id": "umbrella",
                "text": (
                    "The umbrella sparkles in the golden light. Open imaginary "
                    "umbrella overhead and spin once."
                ),
                "action_slot": "cheering",
                "next": "shine",
            },
            {
                "id": "shine",
                "text": (
                    "{guide_mentor} smiles. \"Sodium's job is done. Alkali metals are "
                    'the wake-up alarms of the periodic table."'
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "alkali_metal_cesium_atomic_clock",
        "title": "Cesium runs the world's clock",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} taps a pretend wristwatch. "Alkali metals can '
                    "be SUPER precise. Cesium runs the most accurate clock in the "
                    'world — it only loses one second every million years!"'
                ),
                "action_slot": "pointing",
                "element_id": "cs-55",
            },
            {
                "id": "tick",
                "text": (
                    "{guide_mentor} sets the Cesium atomic clock. "
                    "How should we count the perfect seconds?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Tap toes one-two-one-two", "next": "toes"},
                    {"label": "Snap fingers slowly", "next": "snap"},
                    {"label": "Nod head left-right", "next": "nod"},
                ],
            },
            {
                "id": "toes",
                "text": (
                    "Tap your right foot, then left, then right, then left. Cesium "
                    "is counting every single tick perfectly."
                ),
                "action_slot": "thinking",
                "next": "bigtime",
            },
            {
                "id": "snap",
                "text": (
                    "Snap your fingers four times, slow and even. Each snap is "
                    "another perfect Cesium second."
                ),
                "action_slot": "thinking",
                "next": "bigtime",
            },
            {
                "id": "nod",
                "text": (
                    "Nod your head left, then right, then left, then right. Cesium "
                    "keeps the rhythm steady."
                ),
                "action_slot": "thinking",
                "next": "bigtime",
            },
            {
                "id": "bigtime",
                "text": (
                    '{guide_mentor} taps the clock face. "Alkali metals make things '
                    'go — and sometimes they make them go EXACTLY right."'
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["magic", "music"],
        "ending_step": {"kind": "song", "auto": True},
    },
    # =================================================================
    # alkaline_earth — "Shy helpers" (3)
    # =================================================================
    {
        "id": "alkaline_earth_calcium_bone_builder",
        "title": "Calcium builds the bone tower",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} flexes a tiny muscle. "Alkaline earths are shy '
                    "helpers — strong but quiet. Calcium builds your bones every day "
                    "while you're not looking!\""
                ),
                "action_slot": "pointing",
                "element_id": "ca-20",
            },
            {
                "id": "snack",
                "text": "{guide_mentor} opens a Calcium snack pack. What should we munch on first?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "A cup of milk", "next": "milk"},
                    {"label": "A slice of cheese", "next": "cheese"},
                    {"label": "A spoon of yogurt", "next": "yogurt"},
                ],
            },
            {
                "id": "milk",
                "text": (
                    "Pretend to drink a glass of milk in three big gulps. Calcium "
                    "rushes to your shin bones."
                ),
                "action_slot": "thinking",
                "next": "tower",
            },
            {
                "id": "cheese",
                "text": (
                    "Nibble a cheese cube — squeak squeak. The Calcium settles into your elbows."
                ),
                "action_slot": "thinking",
                "next": "tower",
            },
            {
                "id": "yogurt",
                "text": (
                    "Scoop yogurt slowly — yum. Calcium drifts into your finger "
                    "bones, then your jaw."
                ),
                "action_slot": "thinking",
                "next": "tower",
            },
            {
                "id": "tower",
                "text": (
                    "{guide_mentor} stacks pretend blocks. \"Alkaline earths don't "
                    "shout, but they do the strong stuff. Stand up tall — that's "
                    'Calcium holding you up."'
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["food", "friendship"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "alkaline_earth_magnesium_flash_camera",
        "title": "Magnesium and the old flash camera",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} picks up a pretend antique camera. "Alkaline '
                    "earths are quiet — but Magnesium can burn with a SUPER bright "
                    'white flash. Old cameras used it for pictures!"'
                ),
                "action_slot": "pointing",
                "element_id": "mg-12",
            },
            {
                "id": "subject",
                "text": (
                    "{guide_mentor} loads the Magnesium camera flash. "
                    "Who should we take a picture of today?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A sleepy puppy", "next": "pup"},
                    {"label": "A big birthday cake", "next": "cake"},
                    {"label": "A jumping frog", "next": "frog"},
                ],
            },
            {
                "id": "pup",
                "text": (
                    "Curl up small and rest your head on your hands. POP! {guide_mentor} "
                    "fires the Magnesium flash — the puppy's eyes shine."
                ),
                "action_slot": "sleeping",
                "next": "develop",
            },
            {
                "id": "cake",
                "text": (
                    "Hold up imaginary candles and puff your cheeks ready to blow. "
                    "POP! {guide_mentor} fires the Magnesium flash and catches your wish."
                ),
                "action_slot": "cheering",
                "next": "develop",
            },
            {
                "id": "frog",
                "text": (
                    "Squat down low, then SPRING up. POP! {guide_mentor} fires the "
                    "Magnesium flash and freezes the highest part of your jump."
                ),
                "action_slot": "jumping",
                "next": "develop",
            },
            {
                "id": "develop",
                "text": (
                    '{guide_mentor} holds up a pretend photo. "Alkaline earths are '
                    'shy helpers — but every now and then, they put on a show."'
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "alkaline_earth_strontium_firework_show",
        "title": "Strontium paints the firework red",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} points up at the imaginary sky. "Alkaline earths '
                    "are quiet during the day — but at night, Strontium paints "
                    'fireworks bright red!"'
                ),
                "action_slot": "pointing",
                "element_id": "sr-38",
            },
            {
                "id": "design",
                "text": (
                    "{guide_mentor} lights a Strontium firework. "
                    "What shape should sparkle across the sky?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A giant heart", "next": "heart"},
                    {"label": "A shooting star", "next": "star"},
                    {"label": "A spiral swirl", "next": "swirl"},
                ],
            },
            {
                "id": "heart",
                "text": (
                    "Draw a heart in the air with both pointer fingers. BOOM! "
                    "{guide_mentor} lights the Strontium and fills it in with red sparks."
                ),
                "action_slot": "pointing",
                "next": "finale",
            },
            {
                "id": "star",
                "text": (
                    "Sweep your arm down like a shooting star. BOOM! {guide_mentor} "
                    "lights the Strontium and a red trail follows behind."
                ),
                "action_slot": "pointing",
                "next": "finale",
            },
            {
                "id": "swirl",
                "text": (
                    "Spin your finger in a big spiral above your head. BOOM! "
                    "{guide_mentor} lights the Strontium and the sky swirls red."
                ),
                "action_slot": "running",
                "next": "finale",
            },
            {
                "id": "finale",
                "text": (
                    '{guide_mentor} claps. "Alkaline earths are shy most of the '
                    'time, but they sure can dress up for a holiday!"'
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    # =================================================================
    # transition_metal — "Shiny crafters" (5)
    # =================================================================
    {
        "id": "transition_metal_blacksmith_iron",
        "title": "Forge a gift with Iron",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} ties on a pretend leather apron. "Transition '
                    "metals are the shiny crafters — strong, shapeable, useful. "
                    "Today we're working with Iron!\""
                ),
                "action_slot": "pointing",
                "element_id": "fe-26",
            },
            {
                "id": "heat",
                "text": (
                    "The forge is glowing. {guide_mentor} grips the hot Iron with tongs. "
                    "What should we shape it into?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A sturdy horseshoe", "next": "horseshoe"},
                    {"label": "A garden gate hinge", "next": "hinge"},
                    {"label": "A frying pan", "next": "pan"},
                    {"label": "A little dinner bell", "next": "bell"},
                ],
            },
            {
                "id": "horseshoe",
                "text": (
                    "BANG BANG BANG! Hammer your fist on your other palm three "
                    "times. The horseshoe takes shape."
                ),
                "action_slot": "jumping",
                "next": "cool",
            },
            {
                "id": "hinge",
                "text": (
                    "Twist your wrist slowly — Iron bends. Make a CREAK sound as "
                    "the new hinge opens and closes."
                ),
                "action_slot": "thinking",
                "next": "cool",
            },
            {
                "id": "pan",
                "text": (
                    "Pat the air with flat hands, shaping a wide round pan. "
                    "Pretend to flip an imaginary pancake — flip!"
                ),
                "action_slot": "cheering",
                "next": "cool",
            },
            {
                "id": "bell",
                "text": (
                    "Cup your hands and pretend to swing a tiny bell — DING DING. "
                    "Iron rings clear and bright."
                ),
                "action_slot": "waving",
                "next": "cool",
            },
            {
                "id": "cool",
                "text": (
                    '{guide_mentor} dunks the piece in water — HISSSS. "Transition '
                    'metals shape the everyday world. Nice work, blacksmith."'
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "silly"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "transition_metal_copper_penny_journey",
        "title": "Copper rolls through the town",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} flips a pretend penny. "Transition metals travel! '
                    "Copper has been a coin, a wire, a pipe, and a pot. Let's roll "
                    'with it."'
                ),
                "action_slot": "pointing",
                "element_id": "cu-29",
            },
            {
                "id": "roll",
                "text": (
                    "{guide_mentor} drops a Copper penny on the floor. "
                    "Where should it roll first?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Into a piggy bank", "next": "piggy"},
                    {"label": "Through a telephone wire", "next": "wire"},
                    {"label": "Down to a kitchen pot", "next": "pot"},
                ],
            },
            {
                "id": "piggy",
                "text": (
                    "Drop the penny in — CLINK! Cup your hands and shake them like "
                    "a piggy bank full of coins."
                ),
                "action_slot": "cheering",
                "next": "shine",
            },
            {
                "id": "wire",
                "text": (
                    "Zip through the wire — BRRZZT! Wiggle your fingers fast like "
                    "electricity zooming home."
                ),
                "action_slot": "running",
                "next": "shine",
            },
            {
                "id": "pot",
                "text": (
                    "Plop into the pot — STIR STIR STIR. Move your hand in a big "
                    "slow circle, stirring imaginary soup."
                ),
                "action_slot": "thinking",
                "next": "shine",
            },
            {
                "id": "shine",
                "text": (
                    "{guide_mentor} polishes the penny on a sleeve. \"That's a "
                    "transition metal — shiny, useful, and always going somewhere "
                    'new."'
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "silly"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "transition_metal_silver_mirror_shop",
        "title": "Silver shines up the mirror",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} unrolls a pretend cloth. "Transition metals shine. '
                    "Silver is the SHINIEST of them all — the king of mirrors and "
                    'the queen of spoons."'
                ),
                "action_slot": "pointing",
                "element_id": "ag-47",
            },
            {
                "id": "polish",
                "text": (
                    "{guide_mentor} grabs a Silver polishing cloth. "
                    "What should we shine up today?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A round hand mirror", "next": "mirror"},
                    {"label": "A soup spoon", "next": "spoon"},
                    {"label": "A holiday ornament", "next": "ornament"},
                ],
            },
            {
                "id": "mirror",
                "text": (
                    "Rub your hand in a big slow circle as if buffing glass. The "
                    "mirror catches your smile."
                ),
                "action_slot": "thinking",
                "next": "show",
            },
            {
                "id": "spoon",
                "text": (
                    "Pretend to polish a long spoon top to bottom, then peer at "
                    "your face in it — upside down!"
                ),
                "action_slot": "looking",
                "next": "show",
            },
            {
                "id": "ornament",
                "text": (
                    "Cup the ornament gently and rub with a thumb. The light "
                    "bounces around the room."
                ),
                "action_slot": "cheering",
                "next": "show",
            },
            {
                "id": "show",
                "text": (
                    '{guide_mentor} holds the piece up to a lamp. "Transition '
                    "metals don't hide. Silver always wants to be seen.\""
                ),
                "action_slot": "pointing",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "magic"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "transition_metal_gold_crown_quest",
        "title": "Gold is forged into a crown",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} bows low. "Today we honor the royal of transition '
                    "metals — Gold! Gold never rusts, never tarnishes, and is the "
                    'softest of the shiny crafters."'
                ),
                "action_slot": "pointing",
                "element_id": "au-79",
            },
            {
                "id": "design",
                "text": (
                    "{guide_mentor} hammers a Gold bar on the anvil. "
                    "Whose crown should we forge tonight?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A queen of dragons", "next": "queen"},
                    {"label": "A pirate captain", "next": "pirate"},
                    {"label": "A garden fairy", "next": "fairy"},
                ],
            },
            {
                "id": "queen",
                "text": (
                    "Twist your fingers in the air — tall spiky points for the "
                    "queen. Stand tall and stretch your chin up royally."
                ),
                "action_slot": "cheering",
                "next": "crown",
            },
            {
                "id": "pirate",
                "text": (
                    "Bend Gold into a low, rough band with a skull on top. Squint "
                    "one eye and growl like a sea captain."
                ),
                "action_slot": "confused",
                "next": "crown",
            },
            {
                "id": "fairy",
                "text": (
                    "Pinch Gold into tiny twirls — leaves and flowers around the "
                    "rim. Tiptoe on your toes like a fairy in the meadow."
                ),
                "action_slot": "running",
                "next": "crown",
            },
            {
                "id": "crown",
                "text": (
                    '{guide_mentor} lifts the crown high. "Transition metals shape '
                    "stories. Today, Gold made a brand-new one. Bow your head and "
                    'be crowned!"'
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "knights"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "transition_metal_titanium_spaceship_build",
        "title": "Titanium builds the spaceship",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} unrolls a blueprint. "Transition metals can be '
                    "tough AND light. Titanium builds airplanes, ships, and even "
                    "rockets — let's make a spaceship!\""
                ),
                "action_slot": "pointing",
                "element_id": "ti-22",
            },
            {
                "id": "part",
                "text": (
                    "{guide_mentor} unrolls the Titanium blueprint. "
                    "Which part of the spaceship should we build first?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "The pointy nose", "next": "nose"},
                    {"label": "The big wings", "next": "wings"},
                    {"label": "The rocket boosters", "next": "boost"},
                    {"label": "The shiny window", "next": "window"},
                ],
            },
            {
                "id": "nose",
                "text": (
                    "Press your palms together in a pointy shape — that's the "
                    "nose cone slicing the sky."
                ),
                "action_slot": "pointing",
                "next": "launch",
            },
            {
                "id": "wings",
                "text": (
                    "Stretch your arms wide and tilt left, then right — the wings "
                    "are testing the wind."
                ),
                "action_slot": "waving",
                "next": "launch",
            },
            {
                "id": "boost",
                "text": ("Stomp twice — BOOM BOOM! The boosters are bolted on tight."),
                "action_slot": "jumping",
                "next": "launch",
            },
            {
                "id": "window",
                "text": (
                    "Make a circle with your hands and peek through it like a porthole into space."
                ),
                "action_slot": "looking",
                "next": "launch",
            },
            {
                "id": "launch",
                "text": (
                    "{guide_mentor} counts down — 3, 2, 1, BLAST OFF! Crouch low "
                    'and leap up. "Transition metals build the stuff that lasts."'
                ),
                "action_slot": "jumping",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["space", "adventure"],
        "ending_step": {"kind": "song", "auto": True},
    },
    # =================================================================
    # post_transition_metal — "The soft ones" (3)
    # =================================================================
    {
        "id": "post_transition_metal_aluminum_can_crusher",
        "title": "Aluminum bends in your hand",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} holds up a pretend soda can. "Post-transition '
                    "metals are the soft ones — bendy, foldable, friendly. Aluminum "
                    'makes soda cans and kitchen foil!"'
                ),
                "action_slot": "pointing",
                "element_id": "al-13",
            },
            {
                "id": "what",
                "text": (
                    "{guide_mentor} pulls a sheet of Aluminum foil. "
                    "What should we shape it into today?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A foil hat", "next": "hat"},
                    {"label": "A crumpled ball", "next": "ball"},
                    {"label": "A long ribbon", "next": "ribbon"},
                ],
            },
            {
                "id": "hat",
                "text": (
                    "Press flat palms to your head and shape an imaginary hat. "
                    "Tip the brim and bow like a foil knight."
                ),
                "action_slot": "thinking",
                "next": "wave",
            },
            {
                "id": "ball",
                "text": (
                    "Crumple imaginary foil in two hands — squish, squish! Now "
                    "toss it up and catch it."
                ),
                "action_slot": "jumping",
                "next": "wave",
            },
            {
                "id": "ribbon",
                "text": (
                    "Pull your hands apart slowly — Aluminum stretches into a "
                    "long ribbon. Wave it side to side like a banner."
                ),
                "action_slot": "waving",
                "next": "wave",
            },
            {
                "id": "wave",
                "text": (
                    '{guide_mentor} pats the foil flat. "Post-transition metals '
                    "are gentle metals — strong enough to hold soup, soft enough "
                    'for tiny hands."'
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "food"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "post_transition_metal_tin_soldier_march",
        "title": "Tin marches the toy soldiers",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} lines up pretend tiny soldiers. "Post-transition '
                    "metals are soft and friendly. Tin used to make toy soldiers and "
                    'still coats the inside of food cans!"'
                ),
                "action_slot": "pointing",
                "element_id": "sn-50",
            },
            {
                "id": "drill",
                "text": "{guide_mentor} lines up the Tin soldiers. What drill should we run today?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "March in a square", "next": "square"},
                    {"label": "Spin and salute", "next": "spin"},
                    {"label": "Tiptoe past a sleeping cat", "next": "cat"},
                ],
            },
            {
                "id": "square",
                "text": (
                    "March four steps forward, four right, four back, four left. "
                    "Tin clinks at every step."
                ),
                "action_slot": "running",
                "next": "rest",
            },
            {
                "id": "spin",
                "text": (
                    "Spin in place once, then snap a salute to your forehead. "
                    "Tin gleams under the lamp."
                ),
                "action_slot": "cheering",
                "next": "rest",
            },
            {
                "id": "cat",
                "text": (
                    "Tiptoe four slow steps, finger on your lips. The cat doesn't wake up. Whew."
                ),
                "action_slot": "looking",
                "next": "rest",
            },
            {
                "id": "rest",
                "text": (
                    '{guide_mentor} blows a tiny pretend trumpet. "Post-transition '
                    "metals don't shout — they march quietly and get the job done.\""
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "adventure"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "post_transition_metal_bismuth_rainbow_castle",
        "title": "Bismuth grows a rainbow castle",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} cradles a pretend crystal. "Post-transition '
                    "metals can be sparkly too. Bismuth grows into rainbow staircase "
                    'crystals that look like teeny castles!"'
                ),
                "action_slot": "pointing",
                "element_id": "bi-83",
            },
            {
                "id": "color",
                "text": (
                    "{guide_mentor} pours hot Bismuth into the mold. "
                    "Which color stair should grow first?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Pink and purple", "next": "pink"},
                    {"label": "Green and blue", "next": "green"},
                    {"label": "Gold and orange", "next": "gold"},
                ],
            },
            {
                "id": "pink",
                "text": (
                    "Step up — UP — UP three little steps. Each step glitters pink and purple."
                ),
                "action_slot": "jumping",
                "next": "tour",
            },
            {
                "id": "green",
                "text": ("Take three slow giant steps. Each step is glassy green and ocean blue."),
                "action_slot": "running",
                "next": "tour",
            },
            {
                "id": "gold",
                "text": (
                    "Tap three times in place — each tap shines gold and orange "
                    "like a tiny sunrise."
                ),
                "action_slot": "cheering",
                "next": "tour",
            },
            {
                "id": "tour",
                "text": (
                    '{guide_mentor} peeks inside. "Post-transition metals are the '
                    'soft ones — but they sure can dress up like a rainbow."'
                ),
                "action_slot": "looking",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["magic", "silly"],
        "ending_step": {"kind": "song", "auto": True},
    },
    # =================================================================
    # metalloid — "In-betweeners" (3)
    # =================================================================
    {
        "id": "metalloid_silicon_chip_workshop",
        "title": "Silicon builds the tiny chip",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} squints into a tiny pretend microscope. "Metalloids '
                    "are in-betweeners — not quite metal, not quite not. Silicon hides "
                    'in beach sand AND every computer chip!"'
                ),
                "action_slot": "pointing",
                "element_id": "si-14",
            },
            {
                "id": "make",
                "text": (
                    "{guide_mentor} taps a Silicon wafer. "
                    "What should we build into the chip today?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A counting circuit", "next": "count"},
                    {"label": "A music circuit", "next": "music"},
                    {"label": "A glowing screen circuit", "next": "screen"},
                ],
            },
            {
                "id": "count",
                "text": (
                    "Hold up fingers — one, two, three, four, five! Silicon's "
                    "circuit can count higher than the stars."
                ),
                "action_slot": "thinking",
                "next": "test",
            },
            {
                "id": "music",
                "text": ("Hum a quick tune — la la LA! Silicon's circuit just learned your song."),
                "action_slot": "cheering",
                "next": "test",
            },
            {
                "id": "screen",
                "text": (
                    "Wiggle all ten fingers in front of your face. Silicon's "
                    "circuit lights up like a million tiny stars."
                ),
                "action_slot": "looking",
                "next": "test",
            },
            {
                "id": "test",
                "text": (
                    "{guide_mentor} slides the chip into place. \"That's a metalloid "
                    "for you — small but mighty, the in-between hero of every "
                    'screen."'
                ),
                "action_slot": "pointing",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["magic", "silly"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "metalloid_boron_sand_castle",
        "title": "Boron strengthens the sand castle",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} digs into a pretend sandbox. "Metalloids are '
                    "in-betweeners — half metal, half something else. Boron makes "
                    'glass and ceramics extra strong!"'
                ),
                "action_slot": "pointing",
                "element_id": "b-5",
            },
            {
                "id": "shape",
                "text": (
                    "{guide_mentor} sprinkles Boron into the sand bucket. "
                    "What should the castle become?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A tall tower", "next": "tower"},
                    {"label": "A wide moat", "next": "moat"},
                    {"label": "A drawbridge", "next": "bridge"},
                ],
            },
            {
                "id": "tower",
                "text": (
                    "Pat your hands flat one on top of the other — the tower "
                    "rises higher and higher. Boron keeps it from crumbling."
                ),
                "action_slot": "pointing",
                "next": "guard",
            },
            {
                "id": "moat",
                "text": (
                    "Sweep your hand in a wide ring around your imaginary castle. "
                    "The water is deep. Boron keeps the walls firm."
                ),
                "action_slot": "thinking",
                "next": "guard",
            },
            {
                "id": "bridge",
                "text": (
                    "Lay your forearm flat — that's the drawbridge. Lift it up, "
                    "then lay it back down. Boron keeps it from cracking."
                ),
                "action_slot": "thinking",
                "next": "guard",
            },
            {
                "id": "guard",
                "text": (
                    '{guide_mentor} pats the castle gently. "In-betweeners hold '
                    'things together. Boron is the secret ingredient."'
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["adventure", "knights"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "metalloid_germanium_first_computer",
        "title": "Germanium runs the first computer",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} dusts off a pretend old computer. "Metalloids '
                    "started the computer age! Germanium was inside the very first "
                    'tiny switches called transistors."'
                ),
                "action_slot": "pointing",
                "element_id": "ge-32",
            },
            {
                "id": "task",
                "text": "{guide_mentor} flips on the Germanium computer. What should it do first?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "Add two big numbers", "next": "add"},
                    {"label": "Print a hello message", "next": "hello"},
                    {"label": "Play a beep song", "next": "beep"},
                ],
            },
            {
                "id": "add",
                "text": (
                    "Hold up three fingers, then four — push them together — that's "
                    "seven! The Germanium transistors hum along to your math."
                ),
                "action_slot": "thinking",
                "next": "shutdown",
            },
            {
                "id": "hello",
                "text": (
                    'Wave at the screen and say "hi there!" {guide_mentor} watches '
                    "the letters appear on the Germanium computer one by one."
                ),
                "action_slot": "waving",
                "next": "shutdown",
            },
            {
                "id": "beep",
                "text": (
                    "Tap an imaginary key three times — BEEP BEEP BOOP. {guide_mentor} "
                    "smiles as the Germanium computer plays a tiny tune."
                ),
                "action_slot": "cheering",
                "next": "shutdown",
            },
            {
                "id": "shutdown",
                "text": (
                    '{guide_mentor} pats the old machine. "Metalloids are the '
                    'in-betweeners that built the modern world. Thanks, Germanium."'
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["magic", "music"],
        "ending_step": {"kind": "song", "auto": True},
    },
    # =================================================================
    # nonmetal — "Everywhere essentials" (4)
    # =================================================================
    {
        "id": "nonmetal_oxygen_breath_adventure",
        "title": "Oxygen rides on every breath",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} takes a big slow breath. "Nonmetals are '
                    "everywhere essentials — invisible but crucial. Oxygen rides "
                    'into your lungs on every breath!"'
                ),
                "action_slot": "pointing",
                "element_id": "o-8",
            },
            {
                "id": "trip",
                "text": "{guide_mentor} fills a fresh Oxygen tank. Where should we ride next?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "Up to a hummingbird's wings", "next": "bird"},
                    {"label": "Into a runner's strong legs", "next": "leg"},
                    {"label": "Through a flame on a candle", "next": "flame"},
                ],
            },
            {
                "id": "bird",
                "text": (
                    "Flutter your hands fast like hummingbird wings. Oxygen keeps "
                    "the wings buzzing!"
                ),
                "action_slot": "waving",
                "next": "thanks",
            },
            {
                "id": "leg",
                "text": (
                    "Run in place faster, faster. Oxygen powers every step. Slow "
                    "down and catch your breath."
                ),
                "action_slot": "running",
                "next": "thanks",
            },
            {
                "id": "flame",
                "text": (
                    "Wave one finger like a flickering flame. Oxygen feeds the "
                    "fire — but stays out of your reach."
                ),
                "action_slot": "thinking",
                "next": "thanks",
            },
            {
                "id": "thanks",
                "text": (
                    '{guide_mentor} bows. "Nonmetals are the everyday helpers. '
                    'Take one big breath together — thanks, Oxygen!"'
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["animals", "adventure"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "nonmetal_carbon_bones_of_life",
        "title": "Carbon visits every living thing",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} sketches a stick figure. "Nonmetals are everywhere '
                    "essentials. Carbon is the building block of every living thing "
                    '— trees, dogs, even you!"'
                ),
                "action_slot": "pointing",
                "element_id": "c-6",
            },
            {
                "id": "visit",
                "text": (
                    "{guide_mentor} draws a smiley with a Carbon pencil. "
                    "Who should we say hello to first?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A leafy tree", "next": "tree"},
                    {"label": "A sleepy turtle", "next": "turtle"},
                    {"label": "A wiggly worm", "next": "worm"},
                    {"label": "A new baby plant sprout", "next": "sprout"},
                ],
            },
            {
                "id": "tree",
                "text": (
                    "Stand tall, stretch your arms out like branches, and sway "
                    "gently. Carbon is in every leaf."
                ),
                "action_slot": "thinking",
                "next": "wonder",
            },
            {
                "id": "turtle",
                "text": (
                    "Tuck your head into your shoulders like a turtle in a shell. "
                    "Carbon makes that shell strong."
                ),
                "action_slot": "thinking",
                "next": "wonder",
            },
            {
                "id": "worm",
                "text": (
                    "Wiggle your whole body side to side like a happy garden worm. "
                    "Carbon zooms through the dirt."
                ),
                "action_slot": "running",
                "next": "wonder",
            },
            {
                "id": "sprout",
                "text": (
                    "Crouch down small, then s-l-o-w-l-y stretch up like a sprout "
                    "reaching for the sun. Carbon builds every leaf."
                ),
                "action_slot": "jumping",
                "next": "wonder",
            },
            {
                "id": "wonder",
                "text": (
                    '{guide_mentor} smiles wide. "Nonmetals quietly hold the world '
                    "together. Carbon is in YOU too — tap your chest twice. Hi, "
                    'Carbon!"'
                ),
                "action_slot": "cheering",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["animals", "friendship"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "nonmetal_sulfur_volcano_sniff",
        "title": "Sulfur and the smelly volcano",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} fans the air dramatically. "Nonmetals can be '
                    "stinky too! Sulfur is the bright yellow stuff near volcanoes — "
                    'and it smells like rotten eggs!"'
                ),
                "action_slot": "pointing",
                "element_id": "s-16",
            },
            {
                "id": "scene",
                "text": (
                    "{guide_mentor} cracks open a Sulfur jar — peeeeyew! "
                    "Where should we stink up today?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "A bubbly hot spring", "next": "spring"},
                    {"label": "A breakfast egg pan", "next": "eggs"},
                    {"label": "A campfire match", "next": "match"},
                ],
            },
            {
                "id": "spring",
                "text": (
                    "Pretend to bubble your fingers up like rising bubbles. Pinch "
                    "your nose — STINKY hot spring!"
                ),
                "action_slot": "confused",
                "next": "fan",
            },
            {
                "id": "eggs",
                "text": (
                    "Pretend to crack an egg, then sniff and make a goofy face. "
                    "Sulfur is the egg smell."
                ),
                "action_slot": "confused",
                "next": "fan",
            },
            {
                "id": "match",
                "text": (
                    "Strike an imaginary match — SCRATCH! Wave it gently. Sulfur "
                    "is what makes the match light."
                ),
                "action_slot": "waving",
                "next": "fan",
            },
            {
                "id": "fan",
                "text": (
                    '{guide_mentor} laughs and fans the room. "Even smelly nonmetals '
                    "are everywhere essentials. Thanks, Sulfur — now go take a "
                    'bath!"'
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["silly", "food"],
        "ending_step": {"kind": "song", "auto": True},
    },
    {
        "id": "nonmetal_nitrogen_air_balloon",
        "title": "Nitrogen fills the whole sky",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    '{guide_mentor} sweeps an arm across the air. "Nonmetals are '
                    "everywhere essentials — most of the air around you is Nitrogen, "
                    'even more than Oxygen!"'
                ),
                "action_slot": "pointing",
                "element_id": "n-7",
            },
            {
                "id": "use",
                "text": (
                    "{guide_mentor} fills a balloon with Nitrogen. "
                    "What should we do with all this puffy air?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "Float a kite", "next": "kite"},
                    {"label": "Push a sailboat", "next": "boat"},
                    {"label": "Fluff up bread dough", "next": "bread"},
                ],
            },
            {
                "id": "kite",
                "text": (
                    "Pretend to hold a kite string and tug it back and forth. "
                    "Nitrogen lifts the kite higher and higher."
                ),
                "action_slot": "looking",
                "next": "rest",
            },
            {
                "id": "boat",
                "text": (
                    "Puff your cheeks and blow — WHOOSH! Nitrogen pushes the boat across the bay."
                ),
                "action_slot": "running",
                "next": "rest",
            },
            {
                "id": "bread",
                "text": (
                    "Push your hands flat down on imaginary dough, then watch it "
                    "rise. Nitrogen makes the bubbles fluffy."
                ),
                "action_slot": "thinking",
                "next": "rest",
            },
            {
                "id": "rest",
                "text": (
                    '{guide_mentor} takes a slow deep breath. "Most of every breath '
                    "is Nitrogen. Nonmetals are quiet — but they're EVERYWHERE.\""
                ),
                "action_slot": "sleeping",
            },
        ],
        "required_roles": ["guide_mentor"],
        "optional_roles": ["friend"],
        "recommended_themes": ["weather", "adventure"],
        "ending_step": {"kind": "song", "auto": True},
    },
]


# ---------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------


def _load_existing(path: Path) -> dict[str, Any]:
    """Read the existing intent file. Refuses to overwrite a structurally
    broken file — mirrors :func:`generate_meet_element_templates._load_existing`."""
    if not path.exists():
        raise FileNotFoundError(
            f"output file {path} does not exist; cannot append family pretend "
            f"templates. Run from the worktree root."
        )
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(
            f"output file {path} is not a JSON object (got "
            f"{type(payload).__name__}); refusing to overwrite"
        )
    if payload.get("intent") != "request_play":
        raise ValueError(
            f"output file {path} has intent={payload.get('intent')!r}; expected 'request_play'"
        )
    templates = payload.get("templates")
    if not isinstance(templates, list):
        raise ValueError(
            f"output file {path} does not contain a 'templates' list; refusing to overwrite"
        )
    return payload


def _strip_family_entries(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the input list with every M5 family_* entry removed. Idempotence."""
    return [
        t
        for t in templates
        if not any(str(t.get("id", "")).startswith(p) for p in _FAMILY_PREFIXES)
    ]


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    """Persist with the same indent + trailing newline shape as M4."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _validate_post_write(path: Path, *, expected_family: int) -> None:
    """Re-load via the production loader and assert all M5 templates loaded.

    Mirrors :func:`generate_meet_element_templates._validate_post_write` so
    the same diagnostic surfaces on failure (whole file dropped → delta
    indicates the offending entry's family + id range).
    """
    from toybox.activities.generator import (  # type: ignore[import-untyped]
        _load_intent_templates,
        clear_template_cache,
    )

    clear_template_cache()
    templates = _load_intent_templates("request_play")
    loaded_family = [t for t in templates if any(t.id.startswith(p) for p in _FAMILY_PREFIXES)]
    if len(loaded_family) != expected_family:
        raise SystemExit(
            f"--validate: expected {expected_family} family_* templates "
            f"to load, got {len(loaded_family)}. The whole intent file may "
            f"have been dropped on schema failure; check {path} and re-run."
        )
    _logger.info(
        "--validate: %d family_* templates loaded cleanly through "
        "toybox.activities.generator._load_intent_templates",
        len(loaded_family),
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append ~30 hand-authored element-family pretend-play templates "
            "to request_play.json (Phase M Step M5)."
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
            "src/toybox/activities/templates/branching/request_play.json."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Idempotent regeneration is always-on (existing family_* entries are "
            "stripped before appending); this flag just tags the run in the log."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After writing, re-load via the production template loader and "
            "assert all family_* templates load cleanly."
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

    stripped = _strip_family_entries(existing_templates)
    stripped_count = pre_count - len(stripped)

    new_templates = list(_TEMPLATES)
    merged = stripped + new_templates
    payload["templates"] = merged

    post_count = len(merged)
    _logger.info(
        "summary: pre=%d, removed_existing_family=%d, generated=%d, post=%d, force=%s",
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
        _validate_post_write(output, expected_family=len(new_templates))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
