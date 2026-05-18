"""Phase M Step M9 — generate ~20 feelings-naming SEL branching templates.

One-shot CLI that appends hand-authored multi-step branching templates
to ``src/toybox/activities/templates/branching/request_story.json``.

Each template models the SEL competency "name your feeling": the
``{friend}`` character has a small moment, the kid picks which feeling
the moment brought up, and the chosen branch deepens that feeling with
(a) a concrete body cue ("tummy feels heavy", "hands wiggle") and
(b) a coping move ("a hug helps", "a deep breath helps"). Every
template closes with a shared "all feelings are okay" beat and ends
with a Phase L joke reward (jokes deflate emotional weight more
gently than songs per phase-m-plan §5.9).

Direct serve for Child A (6yo, early-reader, LOL-doll social play).
Princess Lyra and Inspector Pip are the natural personas the slot-fill
picker will hit — Princess via ``role_weights.friend = 1.5`` and
Detective via the catch-all default for ``frenemy``-weighted personas
when no frenemy-required template is available.

Coverage (20 total — phase-m-plan §5.9 sweet-spot):

    1.  feelings_lost_blanket           — sad / worried / silly
    2.  feelings_block_tower_falls      — angry / sad / silly / curious
    3.  feelings_friend_with_other_kid  — left-out / jealous / curious
    4.  feelings_unwanted_present       — embarrassed / proud / silly
    5.  feelings_forgot_song_name       — frustrated / embarrassed / silly
    6.  feelings_loud_thunder           — scared / curious / silly
    7.  feelings_tall_ladder            — nervous / proud / scared
    8.  feelings_stuck_jar              — frustrated / angry / proud
    9.  feelings_drawing_didnt_match    — frustrated / sad / proud
    10. feelings_friend_got_treat       — jealous / sad / curious
    11. feelings_new_place              — nervous / curious / shy
    12. feelings_overheard_whisper      — worried / left-out / curious
    13. feelings_picked_last            — left-out / sad / proud
    14. feelings_dropped_ice_cream      — sad / angry / silly
    15. feelings_butterfly_landed       — excited / proud / curious / silly
    16. feelings_confusing_dream        — scared / worried / curious
    17. feelings_long_wait              — frustrated / bored / excited
    18. feelings_favorite_story_again   — excited / cozy / proud
    19. feelings_share_special_toy      — worried / proud / sad
    20. feelings_grown_up_praise        — proud / shy / excited

Feelings vocabulary across the 20 templates (rotation per spec):
    heavy: sad, worried, lonely, scared, embarrassed
    hot:   angry, frustrated, jealous
    light: silly, proud, excited, curious, cozy, bored
    mixed: left-out, nervous, shy

No two templates resolve to the same single feeling; every fork
explores a distinct cluster of 2-4 feelings.

Authoring style mirrors M5 / M6: Python literals + JSON emitter,
idempotent, ``--dry-run`` / ``--force`` / ``--validate`` / ``--output``
flags. Existing ``feelings_*`` entries are stripped before the new
batch appends.

Field order matches the post-M6 ``request_story.json`` convention:
``id, title, buckets, steps, required_roles, optional_roles,
recommended_themes, ending_step``.

Authoring conventions
---------------------

* ``required_roles: ["friend"]`` — biases the persona picker toward
  Princess Lyra (``role_weights.friend = 1.5``) and gives Inspector
  Pip a default-weight shot.
* ``optional_roles: []`` — feelings-naming is a one-character beat;
  adding a second toy dilutes the focus.
* ``recommended_themes: ["feelings"]`` — the new M8 SEL theme.
* ``ending_step: {kind: "joke", auto: true}`` — jokes are lighter
  than songs after emotional content per phase-m-plan §5.9.
* ``buckets: ["always"]`` — no time-of-day restriction.
* ``{friend}`` placeholder used in every step that names the
  character; literal persona names (Princess, Lyra, Detective, Pip,
  Iridia, Marvelous) NEVER appear in narration.
* Each branch text contains (a) a body cue and (b) a coping move —
  modeling, never lecturing ("you SHOULD feel X").
* Each template's fork is 2-4 choices, each choice labeled with a
  single-word feeling so an early reader (Child A, 6yo) can pick by
  sounding it out.
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

# Idempotence prefix: every M9 template id starts with this slug so a
# re-run strips the previous batch before appending. The trailing
# underscore prevents a substring match from colliding with a
# hypothetical future ``feelingsxxx`` id (none exist today).
_TEMPLATE_PREFIX: Final[str] = "feelings_"


# ---------------------------------------------------------------------
# Hand-authored templates
# ---------------------------------------------------------------------

_TEMPLATES: Final[list[dict[str, Any]]] = [
    # -----------------------------------------------------------------
    # 1. Lost blanket — heavy + light cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_lost_blanket",
        "title": "The missing favorite blanket",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} cannot find their favorite blanket anywhere. "
                    "They look under the bed, behind the chair, inside the "
                    "toy bin. Still no blanket."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling does {friend} have right now?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "sad", "next": "sad_path"},
                    {"label": "worried", "next": "worried_path"},
                    {"label": "silly", "next": "silly_path"},
                ],
            },
            {
                "id": "sad_path",
                "text": (
                    "Sad makes {friend}'s tummy feel heavy and their "
                    "shoulders droop. A big hug from a grown-up helps the "
                    "heaviness get smaller."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "worried_path",
                "text": (
                    "Worried makes {friend}'s hands wiggle and their "
                    "chest feel tight. A slow deep breath in, then out, "
                    "helps the tight part loosen up."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "silly_path",
                "text": (
                    "Silly makes {friend}'s belly bubble up with giggles. "
                    "Pretending the blanket grew legs and ran away to "
                    "join a circus helps the search feel like a game."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a brave feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 2. Block tower falls — hot + light cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_block_tower_falls",
        "title": "The tower of blocks crashes",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} stacks blocks higher and higher. Just as the "
                    "tower reaches their chin — CRASH — the whole tower "
                    "tumbles to the floor."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling rushes in for {friend}?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "angry", "next": "angry_path"},
                    {"label": "sad", "next": "sad_path"},
                    {"label": "silly", "next": "silly_path"},
                    {"label": "curious", "next": "curious_path"},
                ],
            },
            {
                "id": "angry_path",
                "text": (
                    "Angry makes {friend}'s face feel hot and their fists "
                    "squeeze tight. Stomping six big stomps helps the hot "
                    "feeling shake loose."
                ),
                "action_slot": "jumping",
                "next": "join",
            },
            {
                "id": "sad_path",
                "text": (
                    "Sad makes {friend}'s lip wobble and their eyes get "
                    "warm. Sitting on the floor and naming the sad out "
                    "loud helps the wobble settle."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "silly_path",
                "text": (
                    "Silly makes {friend}'s belly laugh and their "
                    "shoulders shake. Pretending the blocks did a great "
                    "big dive on purpose helps the moment feel fun."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "curious_path",
                "text": (
                    "Curious makes {friend}'s eyebrows lift and their "
                    "head tilt sideways. Looking at HOW the blocks fell "
                    "helps {friend} learn what to try next time."
                ),
                "action_slot": "looking",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a thoughtful feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 3. Best friend playing with someone else — mixed (left-out) cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_friend_with_other_kid",
        "title": "Best friend, playing with someone new",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} spots their very best friend playing a "
                    "giggling game across the room — with somebody {friend} "
                    "does not know."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling shows up first?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "left-out", "next": "leftout_path"},
                    {"label": "jealous", "next": "jealous_path"},
                    {"label": "curious", "next": "curious_path"},
                ],
            },
            {
                "id": "leftout_path",
                "text": (
                    "Left-out makes {friend}'s tummy feel hollow and "
                    "their feet feel stuck. Walking over and saying "
                    "'can I play too?' helps the hollow get smaller."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "jealous_path",
                "text": (
                    "Jealous makes {friend}'s ears feel warm and their "
                    "jaw squeeze. Naming the jealous out loud — 'I "
                    "wanted that to be me' — helps the warm fade."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "curious_path",
                "text": (
                    "Curious makes {friend}'s eyebrows hop up and their "
                    "feet tip forward. Walking closer to see what game "
                    "they are playing helps the curious lead the way."
                ),
                "action_slot": "looking",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a brave feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 4. Unwanted present — heavy + light cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_unwanted_present",
        "title": "Opening a present that is just okay",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} unwraps a present. Inside is a pair of "
                    "socks with frogs on them. Not at all the toy "
                    "{friend} was hoping for."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling does {friend} have inside?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "embarrassed", "next": "embarrassed_path"},
                    {"label": "proud", "next": "proud_path"},
                    {"label": "silly", "next": "silly_path"},
                ],
            },
            {
                "id": "embarrassed_path",
                "text": (
                    "Embarrassed makes {friend}'s cheeks feel warm and "
                    "their eyes look down. Remembering 'people can't "
                    "read my face perfectly' helps the warm fade out."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "proud_path",
                "text": (
                    "Proud makes {friend}'s chest puff up a tiny bit. "
                    "Saying a clear 'thank you' even for socks helps the "
                    "proud feeling stick around."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "silly_path",
                "text": (
                    "Silly makes {friend}'s mouth twitch into a grin. "
                    "Imagining the frogs hopping off the socks at "
                    "bedtime helps turn just-okay into kind-of-fun."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a kind feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 5. Forgot song name — hot + heavy + light cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_forgot_song_name",
        "title": "The song with the name on the tip of my tongue",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} hums a favorite tune. The song's name is "
                    "RIGHT there on the tip of their tongue — but it "
                    "will not come out."
                ),
                "action_slot": "thinking",
            },
            {
                "id": "fork",
                "text": "What feeling shows up?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "frustrated", "next": "frustrated_path"},
                    {"label": "embarrassed", "next": "embarrassed_path"},
                    {"label": "silly", "next": "silly_path"},
                ],
            },
            {
                "id": "frustrated_path",
                "text": (
                    "Frustrated makes {friend}'s forehead crinkle and "
                    "their hands clench. Singing a different song for a "
                    "minute helps the forgotten name pop back up."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "embarrassed_path",
                "text": (
                    "Embarrassed makes {friend}'s ears feel hot and "
                    "their voice get quieter. Saying 'I forgot, hang on' "
                    "out loud helps the hot fade right away."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "silly_path",
                "text": (
                    "Silly makes {friend}'s shoulders shake with a "
                    "giggle. Making up a brand-new pretend name like "
                    "'The Bouncy Banana Song' helps the moment feel fun."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a thoughtful feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 6. Loud thunder — heavy + light cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_loud_thunder",
        "title": "The loud thunder boom",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "KA-BOOM! A huge thunderclap rattles the window. "
                    "{friend} jumps a little where they stand."
                ),
                "action_slot": "jumping",
            },
            {
                "id": "fork",
                "text": "What feeling rushes in?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "scared", "next": "scared_path"},
                    {"label": "curious", "next": "curious_path"},
                    {"label": "silly", "next": "silly_path"},
                ],
            },
            {
                "id": "scared_path",
                "text": (
                    "Scared makes {friend}'s heart go fast and their "
                    "shoulders scrunch up. Squeezing a cozy pillow tight "
                    "for ten seconds helps the fast heart slow down."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "curious_path",
                "text": (
                    "Curious makes {friend}'s ears perk up and their "
                    "eyes go wide. Counting the seconds until the next "
                    "boom helps the curious feeling lead the way."
                ),
                "action_slot": "looking",
                "next": "join",
            },
            {
                "id": "silly_path",
                "text": (
                    "Silly makes {friend}'s belly bubble with a grin. "
                    "Pretending the thunder is a giant in clompy boots "
                    "looking for snacks helps the big sound feel friendly."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a brave feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 7. Tall ladder — mixed (nervous) cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_tall_ladder",
        "title": "The very tall ladder",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} looks up at a ladder that stretches WAY "
                    "higher than their head. A grown-up is holding the "
                    "bottom steady."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling fills {friend} up?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "nervous", "next": "nervous_path"},
                    {"label": "proud", "next": "proud_path"},
                    {"label": "scared", "next": "scared_path"},
                ],
            },
            {
                "id": "nervous_path",
                "text": (
                    "Nervous makes {friend}'s knees buzz and their tummy "
                    "flip a tiny bit. Climbing just one rung and pausing "
                    "to breathe helps the buzz get smaller."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "proud_path",
                "text": (
                    "Proud makes {friend}'s chin lift and their feet "
                    "feel solid. Saying 'I can do this one step at a "
                    "time' helps the proud carry them up."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "scared_path",
                "text": (
                    "Scared makes {friend}'s breath go shallow and "
                    "their hands grip tight. Asking the grown-up to "
                    "hold the rail too helps the scared feel held."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a brave feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 8. Stuck jar — hot cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_stuck_jar",
        "title": "The jar that will not open",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} tries to twist the lid off a jar of pickles. "
                    "It does not budge. Not one tiny bit."
                ),
                "action_slot": "thinking",
            },
            {
                "id": "fork",
                "text": "What feeling builds inside?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "frustrated", "next": "frustrated_path"},
                    {"label": "angry", "next": "angry_path"},
                    {"label": "proud", "next": "proud_path"},
                ],
            },
            {
                "id": "frustrated_path",
                "text": (
                    "Frustrated makes {friend}'s forehead pinch and "
                    "their hands sweat. Putting the jar down and "
                    "shaking out their arms helps the pinch let go."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "angry_path",
                "text": (
                    "Angry makes {friend}'s face feel hot and their "
                    "feet stomp. Letting out one big growl into a "
                    "pillow helps the hot pour out."
                ),
                "action_slot": "jumping",
                "next": "join",
            },
            {
                "id": "proud_path",
                "text": (
                    "Proud makes {friend}'s chest lift and their grin "
                    "show. Asking a grown-up for help and POP — the lid "
                    "comes off — helps the proud stay big."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a clever feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 9. Drawing didn't match — hot + heavy + light cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_drawing_didnt_match",
        "title": "The drawing that came out different",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} drew a horse. In their head it looked "
                    "majestic. On the paper... it has very long "
                    "spaghetti legs and one giant ear."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling does {friend} have?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "frustrated", "next": "frustrated_path"},
                    {"label": "sad", "next": "sad_path"},
                    {"label": "proud", "next": "proud_path"},
                ],
            },
            {
                "id": "frustrated_path",
                "text": (
                    "Frustrated makes {friend}'s mouth tighten and "
                    "their pencil grip squeeze. Drawing a few wild "
                    "scribbles on a scrap page helps the squeeze unwind."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "sad_path",
                "text": (
                    "Sad makes {friend}'s shoulders dip and their eyes "
                    "get watery. Naming what they LIKED about the "
                    "drawing first helps the dip lift back up."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "proud_path",
                "text": (
                    "Proud makes {friend}'s smile sneak out. Realizing "
                    "the spaghetti legs are kind of charming helps the "
                    "proud stick around like sunshine."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a creative feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 10. Friend got treat — hot + heavy + light cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_friend_got_treat",
        "title": "When a friend gets the cookie",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "A grown-up hands {friend}'s friend the LAST big "
                    "chocolate chip cookie. {friend} watches the cookie "
                    "go by, no cookie for them."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling does {friend} have?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "jealous", "next": "jealous_path"},
                    {"label": "sad", "next": "sad_path"},
                    {"label": "curious", "next": "curious_path"},
                ],
            },
            {
                "id": "jealous_path",
                "text": (
                    "Jealous makes {friend}'s tummy twist and their "
                    "fists tighten. Saying 'I wish I got one too' out "
                    "loud helps the twist relax."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "sad_path",
                "text": (
                    "Sad makes {friend}'s mouth turn down and their "
                    "chest feel hollow. Asking a grown-up for a hug "
                    "helps the hollow fill back up."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "curious_path",
                "text": (
                    "Curious makes {friend}'s head tilt and their "
                    "eyes look around. Wondering if there is fruit or "
                    "crackers nearby helps the curious open new doors."
                ),
                "action_slot": "looking",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a brave feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 11. New place — mixed (shy + nervous) cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_new_place",
        "title": "The first time in a new place",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} steps through the door of a brand-new "
                    "building. Everything looks different — different "
                    "smells, different floor, different voices."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling shows up first?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "nervous", "next": "nervous_path"},
                    {"label": "curious", "next": "curious_path"},
                    {"label": "shy", "next": "shy_path"},
                ],
            },
            {
                "id": "nervous_path",
                "text": (
                    "Nervous makes {friend}'s tummy do a flip and their "
                    "feet feel tingly. Holding a grown-up's hand for the "
                    "first few minutes helps the tingly feeling settle."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "curious_path",
                "text": (
                    "Curious makes {friend}'s eyes go wide and their "
                    "feet step forward. Picking ONE new thing to look "
                    "at first helps the curious take the lead."
                ),
                "action_slot": "looking",
                "next": "join",
            },
            {
                "id": "shy_path",
                "text": (
                    "Shy makes {friend}'s voice get quiet and their "
                    "shoulders dip in. Taking three slow breaths before "
                    "saying hello helps the quiet voice warm up."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a brave feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 12. Overheard whisper — heavy + mixed cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_overheard_whisper",
        "title": "The whisper across the room",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} hears two kids whispering across the "
                    "room. They keep glancing over. {friend} is not "
                    "sure if the whisper is about them or not."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling stirs in {friend}?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "worried", "next": "worried_path"},
                    {"label": "left-out", "next": "leftout_path"},
                    {"label": "curious", "next": "curious_path"},
                ],
            },
            {
                "id": "worried_path",
                "text": (
                    "Worried makes {friend}'s tummy churn and their "
                    "thoughts race. Reminding themselves 'whispers are "
                    "usually not about me' helps the churn slow down."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "leftout_path",
                "text": (
                    "Left-out makes {friend}'s shoulders pull in and "
                    "their cheeks feel heavy. Walking over and asking "
                    "'what's the secret?' helps the heavy lift up."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "curious_path",
                "text": (
                    "Curious makes {friend}'s ears perk and their "
                    "eyebrows lift. Going to ask in a friendly voice "
                    "helps the curious find an answer."
                ),
                "action_slot": "looking",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a kind feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 13. Picked last — heavy + light cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_picked_last",
        "title": "Being the last one picked",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "Teams are getting picked for a game. One by one, "
                    "kids get called over. {friend} is the very last "
                    "one still standing on the line."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling shows up?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "left-out", "next": "leftout_path"},
                    {"label": "sad", "next": "sad_path"},
                    {"label": "proud", "next": "proud_path"},
                ],
            },
            {
                "id": "leftout_path",
                "text": (
                    "Left-out makes {friend}'s feet feel like stones "
                    "and their chest go quiet. Joining the team with a "
                    "loud 'hi everyone' helps the quiet warm up."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "sad_path",
                "text": (
                    "Sad makes {friend}'s eyes get warm and their lip "
                    "wobble. Whispering to themselves 'I am still on "
                    "the team' helps the wobble settle."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "proud_path",
                "text": (
                    "Proud makes {friend}'s chin lift and their feet "
                    "feel ready. Promising themselves 'I am going to "
                    "play my best' helps the proud lead the way."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a strong feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 14. Dropped ice cream — heavy + hot + light cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_dropped_ice_cream",
        "title": "The ice cream that slipped",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "PLOP. {friend}'s scoop of strawberry ice cream "
                    "slides right off the cone and lands upside-down "
                    "on the sidewalk."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling rushes in?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "sad", "next": "sad_path"},
                    {"label": "angry", "next": "angry_path"},
                    {"label": "silly", "next": "silly_path"},
                ],
            },
            {
                "id": "sad_path",
                "text": (
                    "Sad makes {friend}'s shoulders sag and their "
                    "tummy feel hollow. Taking a slow breath and "
                    "asking a grown-up what to do next helps the sag lift."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "angry_path",
                "text": (
                    "Angry makes {friend}'s cheeks burn and their "
                    "fists curl. Stomping three times on the sidewalk "
                    "helps the burn shake out."
                ),
                "action_slot": "jumping",
                "next": "join",
            },
            {
                "id": "silly_path",
                "text": (
                    "Silly makes {friend}'s grin tug sideways. "
                    "Pretending an ant army just won the jackpot helps "
                    "the spilled ice cream feel like a tiny gift."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a brave feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 15. Butterfly landed — light cluster (4 choices)
    # -----------------------------------------------------------------
    {
        "id": "feelings_butterfly_landed",
        "title": "A butterfly on your hand",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} holds very still. A bright orange "
                    "butterfly flutters down and lands right on the "
                    "back of their hand."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling bubbles up?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "excited", "next": "excited_path"},
                    {"label": "proud", "next": "proud_path"},
                    {"label": "curious", "next": "curious_path"},
                    {"label": "silly", "next": "silly_path"},
                ],
            },
            {
                "id": "excited_path",
                "text": (
                    "Excited makes {friend}'s heart skip and their "
                    "fingers tingle. Whispering 'wow' instead of "
                    "shouting helps the excited stay without scaring "
                    "the butterfly off."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "proud_path",
                "text": (
                    "Proud makes {friend}'s chin lift and their "
                    "smile grow. Thinking 'I held still enough for "
                    "this' helps the proud feeling stick around."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "curious_path",
                "text": (
                    "Curious makes {friend}'s eyes go wide and their "
                    "breath go slow. Counting the spots on the wings "
                    "helps the curious soak the moment in."
                ),
                "action_slot": "looking",
                "next": "join",
            },
            {
                "id": "silly_path",
                "text": (
                    "Silly makes {friend}'s belly bubble with a grin. "
                    "Pretending the butterfly came to read the secret "
                    "messages on their freckles helps the moment shine."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a gentle feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 16. Confusing dream — heavy + light cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_confusing_dream",
        "title": "The dream that did not make sense",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} wakes up. The dream had a flying piano, "
                    "a grandma made of clouds, and a cat that spoke "
                    "French. {friend} blinks at the ceiling, trying "
                    "to figure it out."
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling sits with {friend}?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "scared", "next": "scared_path"},
                    {"label": "worried", "next": "worried_path"},
                    {"label": "curious", "next": "curious_path"},
                ],
            },
            {
                "id": "scared_path",
                "text": (
                    "Scared makes {friend}'s breath go quick and the "
                    "blanket feel tight. Naming three things in the "
                    "real room helps the quick breath even out."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "worried_path",
                "text": (
                    "Worried makes {friend}'s tummy buzz and their "
                    "thoughts loop. Telling a grown-up the silliest "
                    "part of the dream helps the loop break open."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "curious_path",
                "text": (
                    "Curious makes {friend}'s eyebrows hop up and "
                    "their head tilt. Drawing the cloud-grandma on a "
                    "scrap paper helps the curious become a story."
                ),
                "action_slot": "looking",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a clever feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 17. Long wait — hot + light cluster (uses 'bored')
    # -----------------------------------------------------------------
    {
        "id": "feelings_long_wait",
        "title": "Waiting for a turn that takes forever",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "{friend} is third in line for the swing. The "
                    "kid swinging now is taking FOREVER. Pump, pump, "
                    "pump, no slowing down."
                ),
                "action_slot": "thinking",
            },
            {
                "id": "fork",
                "text": "What feeling builds up?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "frustrated", "next": "frustrated_path"},
                    {"label": "bored", "next": "bored_path"},
                    {"label": "excited", "next": "excited_path"},
                ],
            },
            {
                "id": "frustrated_path",
                "text": (
                    "Frustrated makes {friend}'s teeth clench and "
                    "their toes tap fast. Wiggling fingers and toes "
                    "in a silly pattern helps the tap turn into a dance."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "bored_path",
                "text": (
                    "Bored makes {friend}'s shoulders slump and their "
                    "brain feel slow. Looking for the prettiest leaf "
                    "near their shoe helps the slow brain wake up."
                ),
                "action_slot": "looking",
                "next": "join",
            },
            {
                "id": "excited_path",
                "text": (
                    "Excited makes {friend}'s feet bounce and their "
                    "grin stretch. Imagining how high the swing will "
                    "go on their turn helps the bounce stay friendly."
                ),
                "action_slot": "jumping",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a patient feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 18. Favorite story again — light cluster (uses 'cozy')
    # -----------------------------------------------------------------
    {
        "id": "feelings_favorite_story_again",
        "title": "The story you have heard a hundred times",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "A grown-up finishes reading {friend}'s favorite "
                    "story for what feels like the hundredth time. "
                    "{friend} immediately wants to hear it AGAIN."
                ),
                "action_slot": "cheering",
            },
            {
                "id": "fork",
                "text": "What feeling glows in {friend}?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "excited", "next": "excited_path"},
                    {"label": "cozy", "next": "cozy_path"},
                    {"label": "proud", "next": "proud_path"},
                ],
            },
            {
                "id": "excited_path",
                "text": (
                    "Excited makes {friend}'s heart skip and their "
                    "hands clap. Asking 'please please please' in a "
                    "kind voice helps the excited share itself nicely."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "cozy_path",
                "text": (
                    "Cozy makes {friend}'s shoulders melt and their "
                    "eyes feel warm. Curling under a blanket for the "
                    "second reading helps the cozy wrap around like a hug."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "proud_path",
                "text": (
                    "Proud makes {friend}'s chest lift. Asking to "
                    "read a few lines along with the grown-up helps "
                    "the proud feeling join the story too."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a joyful feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 19. Share special toy — heavy + light cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_share_special_toy",
        "title": "Sharing the very special toy",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "A grown-up asks {friend} to share their MOST "
                    "favorite toy with a visiting cousin. {friend} "
                    "holds the toy a little tighter."
                ),
                "action_slot": "thinking",
            },
            {
                "id": "fork",
                "text": "What feeling shows up first?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "worried", "next": "worried_path"},
                    {"label": "sad", "next": "sad_path"},
                    {"label": "proud", "next": "proud_path"},
                ],
            },
            {
                "id": "worried_path",
                "text": (
                    "Worried makes {friend}'s grip tighten and their "
                    "tummy flutter. Asking 'will you be careful with "
                    "it?' out loud helps the flutter calm down."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "sad_path",
                "text": (
                    "Sad makes {friend}'s lower lip tuck in and their "
                    "shoulders dip. Picking a timer for sharing — like "
                    "ten minutes — helps the dip feel safer."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "proud_path",
                "text": (
                    "Proud makes {friend}'s chin lift and their hand "
                    "open. Saying 'here, you can hold it' helps the "
                    "proud grow into a kindness."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a generous feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 20. Grown-up praise — light + mixed (shy) cluster
    # -----------------------------------------------------------------
    {
        "id": "feelings_grown_up_praise",
        "title": "A grown-up notices you did great",
        "buckets": ["always"],
        "steps": [
            {
                "id": "intro",
                "text": (
                    "A grown-up looks right at {friend} and says, "
                    "'I noticed how kind you were just now. That was "
                    "really wonderful.'"
                ),
                "action_slot": "looking",
            },
            {
                "id": "fork",
                "text": "What feeling lights up?",
                "action_slot": "thinking",
                "choices": [
                    {"label": "proud", "next": "proud_path"},
                    {"label": "shy", "next": "shy_path"},
                    {"label": "excited", "next": "excited_path"},
                ],
            },
            {
                "id": "proud_path",
                "text": (
                    "Proud makes {friend}'s chest lift and their "
                    "cheeks glow. Looking the grown-up in the eye and "
                    "saying 'thank you' helps the proud stick around."
                ),
                "action_slot": "cheering",
                "next": "join",
            },
            {
                "id": "shy_path",
                "text": (
                    "Shy makes {friend}'s eyes drop and their "
                    "shoulders curl in. Whispering a quiet 'thanks' "
                    "and giving a tiny wave helps the shy share back gently."
                ),
                "action_slot": "thinking",
                "next": "join",
            },
            {
                "id": "excited_path",
                "text": (
                    "Excited makes {friend}'s feet bounce and their "
                    "smile spread wide. Taking one deep breath before "
                    "answering helps the excited come out kind, not loud."
                ),
                "action_slot": "jumping",
                "next": "join",
            },
            {
                "id": "join",
                "text": "All of those feelings are okay to feel.",
                "action_slot": "waving",
                "next": "end",
            },
            {
                "id": "end",
                "text": "What a kind feeling-namer {friend} is.",
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend"],
        "optional_roles": [],
        "recommended_themes": ["feelings"],
        "ending_step": {"kind": "joke", "auto": True},
    },
]


# ---------------------------------------------------------------------
# Load / strip / write helpers (mirror M5 / M6)
# ---------------------------------------------------------------------


def _load_existing(path: Path) -> dict[str, Any]:
    """Read the existing intent file. Refuses to overwrite a structurally
    broken file — mirrors :func:`generate_shrink_journey_templates._load_existing`."""
    if not path.exists():
        raise FileNotFoundError(
            f"output file {path} does not exist; cannot append feelings-naming "
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


def _strip_feelings_entries(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the input list with every M9 feelings_* entry removed. Idempotence."""
    return [t for t in templates if not str(t.get("id", "")).startswith(_TEMPLATE_PREFIX)]


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    """Persist with the same indent + trailing newline shape as M4 / M5 / M6."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _validate_post_write(path: Path, *, expected_feelings: int) -> None:
    """Re-load via the production loader and assert all M9 templates loaded.

    Mirrors :func:`generate_shrink_journey_templates._validate_post_write`
    so the same diagnostic surfaces on failure (whole intent file dropped
    on schema failure → delta indicates the offending id range).
    """
    from toybox.activities.generator import (  # type: ignore[import-untyped]
        _load_intent_templates,
        clear_template_cache,
    )

    clear_template_cache()
    templates = _load_intent_templates("request_story")
    loaded_feelings = [t for t in templates if t.id.startswith(_TEMPLATE_PREFIX)]
    if len(loaded_feelings) != expected_feelings:
        raise SystemExit(
            f"--validate: expected {expected_feelings} feelings_* templates "
            f"to load, got {len(loaded_feelings)}. The whole intent file may "
            f"have been dropped on schema failure; check {path} and re-run."
        )
    _logger.info(
        "--validate: %d feelings_* templates loaded cleanly through "
        "toybox.activities.generator._load_intent_templates",
        len(loaded_feelings),
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append ~20 hand-authored feelings-naming SEL templates "
            "to request_story.json (Phase M Step M9)."
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
            "Idempotent regeneration is always-on (existing feelings_* entries "
            "are stripped before appending); this flag just tags the run in the log."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After writing, re-load via the production template loader and "
            "assert all feelings_* templates load cleanly."
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

    stripped = _strip_feelings_entries(existing_templates)
    stripped_count = pre_count - len(stripped)

    new_templates = list(_TEMPLATES)
    merged = stripped + new_templates
    payload["templates"] = merged

    post_count = len(merged)
    _logger.info(
        "summary: pre=%d, removed_existing_feelings=%d, generated=%d, post=%d, force=%s",
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
        _validate_post_write(output, expected_feelings=len(new_templates))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
