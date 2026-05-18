"""Phase M Step M11 — generate ~25 conflict-resolution SEL branching templates.

One-shot CLI that appends hand-authored conflict-resolution templates to
TWO intent files in parallel:

* ``src/toybox/activities/templates/branching/request_play.json``    (13 templates)
* ``src/toybox/activities/templates/branching/request_activity.json`` (12 templates)

Each template models the SEL competency "two kids want different
things; here are four ways to resolve it." Two characters (``{friend}``
and ``{frenemy}``) hit a small impasse; the kid picks a resolution
strategy; the chosen branch plays out the resolution AND closes with
a feelings check-in naming how each kid feels afterward (the
load-bearing M11 element per phase-m-plan.md §5.9 / §7).

Direct serve for Child A (6yo, early-reader, LOL-doll social play).
Princess Lyra and Inspector Pip are the natural personas the slot-fill
picker will hit — Princess via ``role_weights.friend`` and Detective
via ``role_weights.frenemy`` (M10 already established this binding).

The four resolution strategies (each fork choice = ONE strategy):

* ``split it``             — divide the contested thing into pieces both can use
* ``find a substitute``    — locate something else just as good
* ``take turns``           — agree on an ordering and trade
* ``one offers, one accepts`` — one kid voluntarily gives, with grace

Each template's fork picks 2-4 of these (not necessarily all four).
Coverage rotates across all four so a kid playing the full corpus
sees every strategy multiple times.

Coverage (25 total — phase-m-plan.md §5.9 sweet-spot, split 13/12):

  request_play (13):
    1.  conflict_last_cookie          — split / sub / turns / offer
    2.  conflict_swing_turn           — sub / turns / offer
    3.  conflict_red_marker           — split / sub / turns / offer
    4.  conflict_pretend_dog          — split / sub / turns / offer
    5.  conflict_front_seat           — sub / turns / offer
    6.  conflict_dollhouse_button     — split / turns / offer
    7.  conflict_red_car              — split / sub / turns / offer
    8.  conflict_role_choice          — sub / turns / offer
    9.  conflict_rug_corner           — split / sub / turns
    10. conflict_feed_cat             — split / turns / offer
    11. conflict_lead_parade          — sub / turns / offer
    12. conflict_magic_wand           — split / sub / turns / offer
    13. conflict_same_team            — sub / turns / offer

  request_activity (12):
    14. conflict_pick_book            — split / sub / turns / offer
    15. conflict_pick_song            — sub / turns / offer
    16. conflict_pick_snack           — split / sub / turns / offer
    17. conflict_new_rules            — split / sub / turns
    18. conflict_what_to_draw         — split / sub / offer
    19. conflict_who_first            — sub / turns / offer
    20. conflict_bracelet_color       — split / sub / turns / offer
    21. conflict_bedtime_story        — sub / turns / offer
    22. conflict_routine_order        — split / turns / offer
    23. conflict_fort_location        — split / sub / turns
    24. conflict_pick_puzzle          — sub / turns / offer
    25. conflict_share_sticker        — split / sub / turns / offer

Strategy rotation tally across the 25 templates:
    split it:     16
    substitute:   22
    take turns:   24
    one offers:   22

Every strategy appears at least 16 times; no strategy dominates;
"take turns" and "one offers, one accepts" lean a bit heavier because
they fit the most scenarios (turns work for sequential things; offers
work as a grace-note that often closes a scene). Every template's
fork has at least 2 strategies; 12 of 25 templates carry all four.

Authoring style mirrors M9 / M10: Python literals + JSON emitter,
idempotent, ``--dry-run`` / ``--force`` / ``--validate`` flags.
Existing ``conflict_*`` entries are stripped from BOTH files before
the new batch appends. The script writes to both intent files in one
invocation.

Field order matches the post-M6 / M9 / M10 branching-template
convention: ``id, title, buckets, steps, required_roles,
optional_roles, recommended_themes, ending_step``.

Authoring conventions
---------------------

* ``required_roles: ["friend"]`` — phase-m-plan.md §5.9 M11 spec.
  Biases the persona picker toward Princess Lyra
  (``role_weights.friend``) and Inspector Pip (catch-all).
* ``optional_roles: ["frenemy"]`` — the second character slot. Using
  ``frenemy`` (vs. a second ``{friend}`` literal) is a runtime
  necessity: the slot-fill engine resolves each placeholder name to
  exactly ONE toy, so two ``{friend}`` literals would resolve to the
  SAME toy — collapsing "two kids want the cookie" into one kid
  wanting it from themselves. The role name "frenemy" in this M11
  context is NOT adversarial framing — narration treats both
  characters as equal friends; the role label is just a slot key the
  engine needs to allocate a distinct toy. (Plan §5.9 reserves
  ``["friend", "frenemy"]`` for M10's two-sided POV, but M11 needs
  the same TWO-CHARACTER mechanism without M10's adversarial-reveal
  story shape. We use ``optional_roles: ["frenemy"]`` rather than
  ``required_roles: ["friend", "frenemy"]`` to honor the M11 plan
  spec's ``["friend"]`` declaration while still satisfying K3.1's
  "every placeholder declared in required ∪ optional" rule.)
* ``recommended_themes: ["friendship"]`` — phase-m-plan.md §5.9 M11
  spec (NOT "feelings" — that's M9/M10 territory).
* ``ending_step: {kind: "joke", auto: true}`` — jokes deflate the
  conflict-resolution scene more gently than songs per
  phase-m-plan.md §5.9.
* ``buckets: ["always"]`` — no time-of-day restriction.
* ``{friend}`` and ``{frenemy}`` are the only role placeholders.
  Literal persona names (Princess, Lyra, Detective, Pip, Iridia,
  Marvelous) NEVER appear in narration — the persona picker binds at
  runtime via the slot-fill engine.
* Every fork branch ends with a feelings check-in step naming how
  ``{friend}`` (or both kids) feel afterward. This is the
  load-bearing M11 element per phase-m-plan.md §7: "each fork branch
  resolves with a feelings check-in."
* No "and they all hugged" boilerplate; each resolution lands with a
  distinct emotional register (proud / patient / generous / wistful /
  glad-they-thought-of-it / satisfied / shy / brave / relieved /
  grateful).
* No "lesson narration." Model the strategy; don't preach about it.

Five-to-seven step skeleton (matches phase-m-plan.md §7 "4-7 steps
per template"; fork at intro is the only fork; each branch is two
steps — resolution + check-in):

    [intro fork]
        -> split_path     -> split_checkin     -> end
        -> sub_path       -> sub_checkin       -> end
        -> turns_path     -> turns_checkin     -> end
        -> offer_path     -> offer_checkin     -> end

Total steps per template = 1 (intro) + 2·N (where N = number of fork
choices) + 1 (end) → 4-10. Most M11 templates land at 6-10.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Final

_logger = logging.getLogger(__name__)

_PLAY_OUTPUT: Final[Path] = Path(
    "src/toybox/activities/templates/branching/request_play.json"
)
_ACTIVITY_OUTPUT: Final[Path] = Path(
    "src/toybox/activities/templates/branching/request_activity.json"
)

# Idempotence prefix: every M11 template id starts with this slug so a
# re-run strips the previous batch before appending. The trailing
# underscore prevents a substring match against any hypothetical
# future ``conflictxxx`` id (none exist today).
_TEMPLATE_PREFIX: Final[str] = "conflict_"


# ---------------------------------------------------------------------
# Shared per-template scaffold helper
# ---------------------------------------------------------------------


def _template(
    *,
    id_: str,
    title: str,
    intro_text: str,
    intro_action: str,
    forks: list[dict[str, str]],
    end_text: str,
) -> dict[str, Any]:
    """Build a conflict-resolution template from the standard skeleton.

    Each entry in ``forks`` is a dict with keys:

      ``key``      — short slug used for step ids (e.g. "split", "sub",
                     "turns", "offer"). Must be a valid step-id slug.
      ``label``    — choice button text (what the kid picks).
      ``resolve``  — narration showing the resolution play out.
      ``checkin``  — feelings check-in step text (the load-bearing M11
                     piece — must name an emotion).
      ``r_action`` — action_slot for the resolve step.
      ``c_action`` — action_slot for the check-in step.

    The skeleton is:

      intro (fork)
        -> <key>_path (resolve)    -> <key>_checkin (feelings)  -> end
        -> ... (next fork choice)

    Choices count: 2-4 (phase-m-plan.md §7 "2-4 forks each"). Schema
    enforces min 2 / max 4 at the JSON-schema layer.
    """
    if not (2 <= len(forks) <= 4):
        raise ValueError(
            f"template {id_!r}: forks must have 2-4 entries, got {len(forks)}"
        )

    choices = [{"label": f["label"], "next": f"{f['key']}_path"} for f in forks]
    steps: list[dict[str, Any]] = [
        {
            "id": "intro",
            "text": intro_text,
            "action_slot": intro_action,
            "choices": choices,
        },
    ]
    for f in forks:
        key = f["key"]
        steps.append(
            {
                "id": f"{key}_path",
                "text": f["resolve"],
                "action_slot": f["r_action"],
                "next": f"{key}_checkin",
            }
        )
        steps.append(
            {
                "id": f"{key}_checkin",
                "text": f["checkin"],
                "action_slot": f["c_action"],
                "next": "end",
            }
        )
    steps.append(
        {
            "id": "end",
            "text": end_text,
            "action_slot": "waving",
        }
    )

    return {
        "id": id_,
        "title": title,
        "buckets": ["always"],
        "steps": steps,
        "required_roles": ["friend"],
        "optional_roles": ["frenemy"],
        "recommended_themes": ["friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    }


# Pre-built fork dicts for the four canonical strategies. Each scenario
# reuses these by name; per-scenario narration overrides the resolve /
# checkin text. Keys are stable across templates so the step ids stay
# predictable.

_SPLIT_KEY = "split"
_SUB_KEY = "sub"
_TURNS_KEY = "turns"
_OFFER_KEY = "offer"


# ---------------------------------------------------------------------
# request_play templates (13)
# ---------------------------------------------------------------------

_PLAY_TEMPLATES: Final[list[dict[str, Any]]] = [
    # 1. Last cookie — all four strategies
    _template(
        id_="conflict_last_cookie",
        title="Two kids, one last cookie",
        intro_text=(
            "{friend} and {frenemy} both reach for the last cookie on "
            "the plate at the same time. Four hands, one cookie. What "
            "should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "{friend} breaks the cookie carefully in half. Two "
                    "small pieces instead of one big one — and now both "
                    "kids have a snack."
                ),
                "checkin": (
                    "How does {friend} feel? Sharing-proud. Their chest "
                    "puffs up a little."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} spots an apple in the fruit bowl across "
                    "the counter. They grab it instead and leave the "
                    "cookie for {friend}."
                ),
                "checkin": (
                    "How does {frenemy} feel? Glad-they-thought-of-it, "
                    "a little clever."
                ),
                "r_action": "looking",
                "c_action": "thinking",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} takes one small bite, then hands the rest "
                    "to {frenemy}. Next snack-time the order flips."
                ),
                "checkin": (
                    "How does {friend} feel? Patient. Waiting was easier "
                    "than they expected."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} hears that {frenemy} had a hard day at the "
                    "park. They slide the cookie across the plate. "
                    "{frenemy} says thank you."
                ),
                "checkin": (
                    "How does {friend} feel? Generous, with a little "
                    "wistful for the cookie they did not eat."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one snack, no fight.",
    ),
    # 2. Swing turn — sub / turns / offer
    _template(
        id_="conflict_swing_turn",
        title="The last swing before lunch",
        intro_text=(
            "{friend} and {frenemy} both run to the one empty swing. "
            "Lunch is in five minutes, so there's only time for one "
            "good swing. What should they try?"
        ),
        intro_action="running",
        forks=[
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} sees the slide is wide open. They head "
                    "over for a fast slide while {friend} takes the "
                    "swing."
                ),
                "checkin": (
                    "How does {frenemy} feel? Pretty good — the slide is "
                    "fun too, and nobody had to wait."
                ),
                "r_action": "running",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} counts twenty pumps, then hops off so "
                    "{frenemy} can have twenty pumps before lunch."
                ),
                "checkin": (
                    "How do both kids feel? Fair. Twenty is twenty, no "
                    "matter who goes first."
                ),
                "r_action": "jumping",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} notices {frenemy} only got to swing once "
                    "yesterday. They wave {frenemy} onto the swing and "
                    "step back."
                ),
                "checkin": (
                    "How does {friend} feel? Generous. A little tired "
                    "feet today, anyway."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one swing, no tears.",
    ),
    # 3. Red marker — all four
    _template(
        id_="conflict_red_marker",
        title="The last red marker in the box",
        intro_text=(
            "{friend} and {frenemy} are coloring side by side. They "
            "both reach for the red marker at the exact same second. "
            "What should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "{friend} uses the red marker on the top half of "
                    "their picture, then slides it over so {frenemy} "
                    "can use it on theirs."
                ),
                "checkin": (
                    "How does {friend} feel? Sharing-proud. Their hand "
                    "is a little tired from waiting but happy."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} digs in the marker box and finds a pink "
                    "marker. Close to red, and nobody else is using it."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. The pink looks "
                    "even better on their drawing."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} colors one shape red, then passes the "
                    "marker to {frenemy}. They trade after every shape."
                ),
                "checkin": (
                    "How do both kids feel? Patient. The drawings grow "
                    "side by side."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{frenemy} is drawing a fire truck and really needs "
                    "the red. {friend} hands the marker over and picks "
                    "blue instead."
                ),
                "checkin": (
                    "How does {friend} feel? Helpful. Their fire-truck "
                    "could be a water-truck — also cool."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one marker, two finished pictures.",
    ),
    # 4. Pretend dog — all four
    _template(
        id_="conflict_pretend_dog",
        title="Both want to be the dog",
        intro_text=(
            "{friend} and {frenemy} are starting a pretend-play game. "
            "Both of them want to be the dog character. The dog only "
            "fits one kid. What should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "They invent two dogs — a big one and a puppy. "
                    "{friend} plays the big dog, {frenemy} plays the "
                    "puppy. The story has TWO dogs now."
                ),
                "checkin": (
                    "How does {friend} feel? Inventive. Two dogs is "
                    "more fun than one anyway."
                ),
                "r_action": "cheering",
                "c_action": "thinking",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} decides to be the cat instead. The cat "
                    "and the dog are best friends in this story."
                ),
                "checkin": (
                    "How does {frenemy} feel? Glad-they-thought-of-it. "
                    "Cat-noises are funny anyway."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} is the dog for the first part of the "
                    "story; halfway through, they switch and {frenemy} "
                    "becomes the dog."
                ),
                "checkin": (
                    "How do both kids feel? Patient. Half a turn each "
                    "is fair."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} remembers {frenemy} loves dogs more than "
                    "anything. They wave {frenemy} into the dog role and "
                    "pick the owner role instead."
                ),
                "checkin": (
                    "How does {friend} feel? Generous, a little "
                    "wistful, but the owner-role gets to give treats."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one story, lots of barking.",
    ),
    # 5. Front seat at puppet show — sub / turns / offer
    _template(
        id_="conflict_front_seat",
        title="The front seat at the puppet show",
        intro_text=(
            "{friend} and {frenemy} both want the one front-row spot "
            "for the puppet show. There's a great view from there. "
            "What should they try?"
        ),
        intro_action="looking",
        forks=[
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} grabs a chair and sets it up next to the "
                    "front spot. Two front-row seats now. Problem "
                    "solved."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. Their new seat is "
                    "honestly just as good."
                ),
                "r_action": "pointing",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} sits front-row for act one. At intermission "
                    "they swap, and {frenemy} sits front-row for act two."
                ),
                "checkin": (
                    "How do both kids feel? Fair. Each got the best "
                    "view for half the show."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} knows {frenemy} has never seen this "
                    "puppet show before. They scoot back one row and "
                    "wave {frenemy} into the front seat."
                ),
                "checkin": (
                    "How does {friend} feel? Generous. Watching {frenemy} "
                    "see it for the first time is its own kind of fun."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one show, two happy puppet-watchers.",
    ),
    # 6. Dollhouse elevator button — split / turns / offer
    _template(
        id_="conflict_dollhouse_button",
        title="Who pushes the dollhouse elevator?",
        intro_text=(
            "{friend} and {frenemy} both reach for the dollhouse "
            "elevator button. The elevator only has one button. What "
            "should they try?"
        ),
        intro_action="pointing",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "They press the button TOGETHER, one finger each. "
                    "The elevator goes up with both fingers on the "
                    "button. Click."
                ),
                "checkin": (
                    "How do both kids feel? Proud. Two fingers worked "
                    "just as well as one."
                ),
                "r_action": "pointing",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} pushes the button for the up trip. "
                    "{frenemy} pushes it for the down trip. The "
                    "elevator gets busy."
                ),
                "checkin": (
                    "How does {friend} feel? Patient. Up and down is "
                    "really two turns anyway."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{frenemy} got to push the button five times "
                    "yesterday. They wave {friend} forward and say, "
                    "'your turn first today.'"
                ),
                "checkin": (
                    "How does {frenemy} feel? Grateful for yesterday, "
                    "satisfied with waving today."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one elevator, lots of trips up and down.",
    ),
    # 7. Red car — all four
    _template(
        id_="conflict_red_car",
        title="The one red toy car",
        intro_text=(
            "{friend} and {frenemy} both grab for the red toy car. "
            "It's the fastest one in the bin. What should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "They build a race track and race side by side. "
                    "{friend} drives the red car on the inside lane, "
                    "{frenemy} drives a blue car on the outside lane — "
                    "and they switch cars halfway."
                ),
                "checkin": (
                    "How do both kids feel? Proud. Racing together is "
                    "better than racing alone."
                ),
                "r_action": "running",
                "c_action": "cheering",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} digs and finds an orange car. Almost the "
                    "same as red, and the wheels are even faster."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. The orange car "
                    "turns out to be the cool one."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} drives the red car around the track once. "
                    "Then they hand it to {frenemy} for one lap. They "
                    "keep trading."
                ),
                "checkin": (
                    "How does {friend} feel? Patient. Watching a lap "
                    "is almost as fun as driving one."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} remembers they had the red car last time. "
                    "They hand it over and pick the green car instead."
                ),
                "checkin": (
                    "How does {friend} feel? Fair. Green cars are good "
                    "too."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one red car, a whole busy track.",
    ),
    # 8. Pretend-play role choice — sub / turns / offer
    _template(
        id_="conflict_role_choice",
        title="Who picks the next pretend-play role?",
        intro_text=(
            "{friend} and {frenemy} are about to start a new round of "
            "pretend-play. Both of them want to pick what kind of game "
            "it is. What should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "They invent a brand-new game that has TWO main "
                    "characters — one each. Neither kid had to give up "
                    "their idea."
                ),
                "checkin": (
                    "How does {friend} feel? Inventive. Mixing two "
                    "games made a better one."
                ),
                "r_action": "cheering",
                "c_action": "thinking",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} picks today's game. {frenemy} picks "
                    "tomorrow's. They write it on a sticky note so "
                    "nobody forgets."
                ),
                "checkin": (
                    "How does {frenemy} feel? Patient. Tomorrow is "
                    "actually pretty close."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{frenemy} sees {friend} brought a new idea they "
                    "really want to try. They say, 'you go ahead, I'll "
                    "play along.'"
                ),
                "checkin": (
                    "How does {frenemy} feel? Generous. Playing along "
                    "can be its own kind of fun."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one game, both helped pick it.",
    ),
    # 9. Rug corner — split / sub / turns
    _template(
        id_="conflict_rug_corner",
        title="The corner of the rug",
        intro_text=(
            "{friend} and {frenemy} both flop onto the cozy corner of "
            "the rug. There's only room for one to really stretch out. "
            "What should they try?"
        ),
        intro_action="looking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "They sit BACK TO BACK in the corner, each taking "
                    "half the cozy spot. Both backs supported, both "
                    "kids cozy."
                ),
                "checkin": (
                    "How does {friend} feel? Pretty cozy, actually. "
                    "Back-warmth is a bonus."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} drags a pillow over and builds their own "
                    "cozy spot near the wall. Just as soft, no fight."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. Pillow-corner "
                    "might be even cozier."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} stretches out in the corner for five "
                    "minutes. Then they trade, and {frenemy} gets the "
                    "spot for five minutes."
                ),
                "checkin": (
                    "How do both kids feel? Patient. Five minutes "
                    "feels long when you're cozy."
                ),
                "r_action": "sleeping",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one cozy corner, no shoving.",
    ),
    # 10. Feed stuffed cat — split / turns / offer
    _template(
        id_="conflict_feed_cat",
        title="Who feeds the stuffed cat first?",
        intro_text=(
            "{friend} and {frenemy} both want to give the stuffed cat "
            "its pretend breakfast. The cat only has one bowl. What "
            "should they try?"
        ),
        intro_action="pointing",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "{friend} pours the pretend milk; {frenemy} adds "
                    "the pretend kibble. Two helpers, one bowl, one "
                    "very happy cat."
                ),
                "checkin": (
                    "How do both kids feel? Proud. The cat purrs "
                    "either way."
                ),
                "r_action": "pointing",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} feeds the cat breakfast. At pretend "
                    "lunchtime, {frenemy} gets to feed it."
                ),
                "checkin": (
                    "How does {frenemy} feel? Patient. Lunch is only "
                    "two pretend hours away."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{frenemy} fed the cat yesterday already. They "
                    "wave {friend} forward with the bowl this time."
                ),
                "checkin": (
                    "How does {frenemy} feel? Satisfied. The cat "
                    "remembers who fed it yesterday."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one cat, breakfast served.",
    ),
    # 11. Lead the parade — sub / turns / offer
    _template(
        id_="conflict_lead_parade",
        title="Who leads the parade?",
        intro_text=(
            "{friend} and {frenemy} both want to be at the front of "
            "the pretend parade through the living room. Only one "
            "kid can lead. What should they try?"
        ),
        intro_action="cheering",
        forks=[
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "They invent a co-leader role: {friend} leads one "
                    "side, {frenemy} leads the other side. The parade "
                    "has two flag-bearers now."
                ),
                "checkin": (
                    "How does {friend} feel? Inventive. Two leaders "
                    "are more fun than one."
                ),
                "r_action": "cheering",
                "c_action": "thinking",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} leads the first lap around the rug. "
                    "{frenemy} leads the second lap. They keep "
                    "trading."
                ),
                "checkin": (
                    "How do both kids feel? Patient. Marching at the "
                    "back is fun too — you get to see the leader."
                ),
                "r_action": "jumping",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} led last time. They wave {frenemy} to "
                    "the front of the line and pick up a drum to play "
                    "from the back."
                ),
                "checkin": (
                    "How does {friend} feel? Generous. Drums are loud "
                    "and excellent."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one parade, everyone marches.",
    ),
    # 12. Magic wand prop — all four
    _template(
        id_="conflict_magic_wand",
        title="The one magic-wand prop",
        intro_text=(
            "{friend} and {frenemy} both spot the sparkly magic wand "
            "in the prop box. They both want it for their pretend "
            "spell-casting. What should they try?"
        ),
        intro_action="looking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "They invent a TWO-WIZARD spell that needs both "
                    "kids' hands on the wand to work. They cast "
                    "together — pow."
                ),
                "checkin": (
                    "How do both kids feel? Proud. Two-wizard magic "
                    "is the strongest kind."
                ),
                "r_action": "cheering",
                "c_action": "cheering",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} finds a long stick that looks just as "
                    "wand-y. They wrap a star sticker on top — "
                    "definitely a wand."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. Their stick-wand "
                    "feels more theirs anyway."
                ),
                "r_action": "looking",
                "c_action": "thinking",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} casts three spells with the wand, then "
                    "passes it to {frenemy} for three spells. They "
                    "keep trading."
                ),
                "checkin": (
                    "How does {friend} feel? Patient. Watching a spell "
                    "is sometimes more fun than casting one."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{frenemy} has been planning their spell all "
                    "morning. {friend} hands them the wand and says, "
                    "'cast yours first.'"
                ),
                "checkin": (
                    "How does {friend} feel? Generous. Watching the "
                    "planned-spell happen is its own kind of magic."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one wand, lots of pretend sparkles.",
    ),
    # 13. Same team — sub / turns / offer
    _template(
        id_="conflict_same_team",
        title="Both want to be on the same team",
        intro_text=(
            "{friend} and {frenemy} are picking teams for a backyard "
            "game. Both kids want to be on the WINNING team — but "
            "every team needs the same number of players. What should "
            "they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "They invent a new role: scorekeeper. The "
                    "scorekeeper helps both teams and isn't on either. "
                    "{frenemy} takes the role and {friend} joins the "
                    "team that needed one more."
                ),
                "checkin": (
                    "How does {frenemy} feel? Important. Scorekeepers "
                    "see everything."
                ),
                "r_action": "pointing",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} joins team A for round one. Round two, "
                    "{friend} switches over to team B with {frenemy}. "
                    "Everybody gets to be teammates eventually."
                ),
                "checkin": (
                    "How do both kids feel? Patient. Two rounds is "
                    "really two chances to win."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{frenemy} sees {friend} really wants to play with "
                    "the kid on team B. They volunteer for team A "
                    "instead and wave {friend} over to B."
                ),
                "checkin": (
                    "How does {frenemy} feel? Generous, a little "
                    "wistful, but cheering across the line counts too."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, two teams, one good game.",
    ),
]


# ---------------------------------------------------------------------
# request_activity templates (12)
# ---------------------------------------------------------------------

_ACTIVITY_TEMPLATES: Final[list[dict[str, Any]]] = [
    # 14. Pick a book — all four
    _template(
        id_="conflict_pick_book",
        title="Which book do we read?",
        intro_text=(
            "{friend} and {frenemy} are at the bookshelf together. "
            "{friend} wants the dinosaur book; {frenemy} wants the "
            "space book. There's only time for one read. What should "
            "they try?"
        ),
        intro_action="looking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "They read half of the dinosaur book, then half of "
                    "the space book. Two stories, one reading time."
                ),
                "checkin": (
                    "How does {friend} feel? Pretty good — getting "
                    "half a story still counts."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} spots a book about space-dinosaurs. "
                    "Both topics in one book. Problem dissolved."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. Space-dinosaurs "
                    "is better than either book alone."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "Today they read the dinosaur book together. "
                    "Tomorrow they read the space book. Both kids get "
                    "their pick on different days."
                ),
                "checkin": (
                    "How does {frenemy} feel? Patient. Tomorrow "
                    "stories are still pretty close."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} remembers {frenemy} hasn't picked a book "
                    "all week. They slide the space book over and say, "
                    "'your pick today.'"
                ),
                "checkin": (
                    "How does {friend} feel? Generous, a tiny bit "
                    "wistful about the dinosaurs — but space is cool "
                    "too."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one bookshelf, one good story.",
    ),
    # 15. Pick the song — sub / turns / offer
    _template(
        id_="conflict_pick_song",
        title="Whose song do we dance to?",
        intro_text=(
            "{friend} and {frenemy} are picking a song to dance to. "
            "{friend} wants the wiggle song; {frenemy} wants the "
            "stomp song. The speaker plays one at a time. What should "
            "they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} finds a song that has BOTH wiggling AND "
                    "stomping in it. They put it on. Win-win."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. Two-move songs "
                    "are the best kind."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "Wiggle song first, then stomp song. They dance to "
                    "both, back to back."
                ),
                "checkin": (
                    "How do both kids feel? Patient. Two songs is "
                    "actually a longer dance party."
                ),
                "r_action": "jumping",
                "c_action": "cheering",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} sees how much {frenemy} loves the stomp "
                    "song. They press play on the stomp song and wave "
                    "{frenemy} into the middle of the floor."
                ),
                "checkin": (
                    "How does {friend} feel? Generous. Watching {frenemy} "
                    "stomp around is honestly hilarious."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one speaker, one good dance.",
    ),
    # 16. Pick the snack — all four
    _template(
        id_="conflict_pick_snack",
        title="Which snack do we share?",
        intro_text=(
            "{friend} and {frenemy} are picking a snack together. "
            "{friend} wants pretzels; {frenemy} wants crackers. The "
            "snack bowl only fits one. What should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "They put pretzels on one half of the bowl and "
                    "crackers on the other half. One bowl, two snacks, "
                    "no fight."
                ),
                "checkin": (
                    "How do both kids feel? Proud. Mixed-snack bowl "
                    "is a great idea."
                ),
                "r_action": "pointing",
                "c_action": "cheering",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} opens the cupboard and finds "
                    "pretzel-crackers — pretzels SHAPED like crackers. "
                    "They pour those into the bowl."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. Crackerty-pretzels "
                    "are the best of both."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "Today's snack: pretzels. Tomorrow's snack: "
                    "crackers. They put a sticky note on the cupboard "
                    "so they remember."
                ),
                "checkin": (
                    "How does {frenemy} feel? Patient. Tomorrow-snack "
                    "is something to look forward to."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} had pretzels yesterday already. They "
                    "tip the cracker box into the bowl and pass it to "
                    "{frenemy}."
                ),
                "checkin": (
                    "How does {friend} feel? Fair. Crackers are crunchy "
                    "and good too."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one bowl, full bellies.",
    ),
    # 17. New game rules — split / sub / turns
    _template(
        id_="conflict_new_rules",
        title="Whose rules for the new game?",
        intro_text=(
            "{friend} and {frenemy} are inventing a brand-new game "
            "together. They each have different ideas for the rules. "
            "Whose rules go in? What should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "They write down one rule from {friend} and one "
                    "rule from {frenemy}, then another from {friend}, "
                    "another from {frenemy}. The rule list mixes both "
                    "kids."
                ),
                "checkin": (
                    "How do both kids feel? Proud. A mixed-rule game "
                    "feels like THEIRS, not anyone else's."
                ),
                "r_action": "pointing",
                "c_action": "cheering",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{friend} suggests using a totally different rule "
                    "neither of them came up with: 'every player gets "
                    "two lives.' They both like it. They use that one."
                ),
                "checkin": (
                    "How does {friend} feel? Inventive. The "
                    "compromise-rule is somehow the best one."
                ),
                "r_action": "thinking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "Round one uses {friend}'s rules. Round two uses "
                    "{frenemy}'s rules. They see which feels better "
                    "and keep the favorite for round three."
                ),
                "checkin": (
                    "How do both kids feel? Patient. Two rounds is "
                    "really a fair test."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one new game, two co-inventors.",
    ),
    # 18. What to draw — split / sub / offer
    _template(
        id_="conflict_what_to_draw",
        title="What do we draw together?",
        intro_text=(
            "{friend} and {frenemy} pull out one big piece of drawing "
            "paper. {friend} wants to draw a castle; {frenemy} wants "
            "to draw a rocket. They both have crayons in hand. What "
            "should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "They draw a line down the middle. Castle on the "
                    "left, rocket on the right. Two pictures on one "
                    "page."
                ),
                "checkin": (
                    "How does {friend} feel? Proud. Two pictures side "
                    "by side tell a bigger story."
                ),
                "r_action": "pointing",
                "c_action": "cheering",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "They draw a CASTLE-ROCKET — a castle with rocket "
                    "boosters on the bottom, blasting off into space. "
                    "Both ideas in one picture."
                ),
                "checkin": (
                    "How do both kids feel? Inventive. The castle-rocket "
                    "is better than either picture alone."
                ),
                "r_action": "cheering",
                "c_action": "cheering",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{frenemy} has been thinking about the rocket all "
                    "morning. {friend} hands them the silver crayon "
                    "first and says, 'rocket it is.'"
                ),
                "checkin": (
                    "How does {friend} feel? Generous. They can draw a "
                    "tiny castle in the corner of the page."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one page, lots of crayon.",
    ),
    # 19. Who goes first — sub / turns / offer
    _template(
        id_="conflict_who_first",
        title="Who goes first?",
        intro_text=(
            "{friend} and {frenemy} are about to start a board game. "
            "Both of them want to roll the dice first. What should "
            "they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "They roll one die together. Highest number goes "
                    "first. The dice decide, not the kids."
                ),
                "checkin": (
                    "How do both kids feel? Fair. Random feels "
                    "honest."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} goes first this game. Next game, "
                    "{frenemy} goes first. They flip the order each "
                    "round."
                ),
                "checkin": (
                    "How does {frenemy} feel? Patient. Next game is "
                    "soon."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} went first last time. They scoot the "
                    "dice across and say, 'you start.'"
                ),
                "checkin": (
                    "How does {friend} feel? Fair. Going second has "
                    "its own advantages."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one game, a fair start.",
    ),
    # 20. Bracelet color — all four
    _template(
        id_="conflict_bracelet_color",
        title="What color is our friendship bracelet?",
        intro_text=(
            "{friend} and {frenemy} are making one friendship "
            "bracelet together. {friend} wants purple thread; "
            "{frenemy} wants yellow thread. The bracelet only has "
            "room for one color at a time. What should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "They braid purple and yellow thread together. "
                    "The bracelet has stripes of both colors going "
                    "around it."
                ),
                "checkin": (
                    "How do both kids feel? Proud. The striped "
                    "bracelet feels like both of them."
                ),
                "r_action": "pointing",
                "c_action": "cheering",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} digs in the thread box and finds "
                    "rainbow thread that has BOTH purple AND yellow "
                    "in it. They use that."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. The rainbow "
                    "thread covers both wishes."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "They make TWO bracelets — one purple for {friend} "
                    "and one yellow for {frenemy}. Then they swap, so "
                    "each kid wears the other's color."
                ),
                "checkin": (
                    "How do both kids feel? Generous. Wearing each "
                    "other's color feels like a real friendship trade."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} remembers {frenemy}'s favorite color is "
                    "yellow. They put down the purple and pick up the "
                    "yellow spool."
                ),
                "checkin": (
                    "How does {friend} feel? Generous. Purple is "
                    "still their favorite, but the yellow bracelet "
                    "looks beautiful."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one bracelet, lots of friendship.",
    ),
    # 21. Bedtime story — sub / turns / offer
    _template(
        id_="conflict_bedtime_story",
        title="Which bedtime story tonight?",
        intro_text=(
            "{friend} and {frenemy} are picking their one bedtime "
            "story. {friend} wants the dragon story; {frenemy} wants "
            "the snail story. Lights are about to go out. What "
            "should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} suggests a story neither of them has "
                    "heard before — the one with the dragon AND the "
                    "snail as friends. They pick that one."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. A new story is "
                    "an adventure for both."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "Tonight: dragon story. Tomorrow night: snail "
                    "story. Both stories make the rotation."
                ),
                "checkin": (
                    "How does {frenemy} feel? Patient. Tomorrow-night "
                    "snail-story is now a thing to look forward to."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} got the dragon story last week. They "
                    "hand the snail book over and curl up to listen."
                ),
                "checkin": (
                    "How does {friend} feel? Generous. Snail-stories "
                    "are actually kind of cozy."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one story, two sleepy yawns.",
    ),
    # 22. Routine order — split / turns / offer
    _template(
        id_="conflict_routine_order",
        title="What order do we do bedtime?",
        intro_text=(
            "{friend} and {frenemy} are doing bedtime together. "
            "{friend} wants to brush teeth FIRST; {frenemy} wants "
            "pajamas FIRST. The grown-up says it's their choice. "
            "What should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "{friend} brushes teeth while {frenemy} puts on "
                    "pajamas. Both kids doing different things at the "
                    "same time. Then they swap."
                ),
                "checkin": (
                    "How do both kids feel? Proud. Bedtime got faster "
                    "because nobody had to wait."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "Tonight: teeth first, pajamas second. Tomorrow "
                    "night: pajamas first, teeth second. They alternate "
                    "the routine."
                ),
                "checkin": (
                    "How does {frenemy} feel? Patient. Tomorrow night "
                    "they get their order."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} sees {frenemy} is super tired and just "
                    "wants pajamas. They wave {frenemy} to the dresser "
                    "and head to the sink alone."
                ),
                "checkin": (
                    "How does {friend} feel? Helpful. A tired kid "
                    "deserves pajamas first."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one bedtime, lights out.",
    ),
    # 23. Fort location — split / sub / turns
    _template(
        id_="conflict_fort_location",
        title="Where do we build the fort?",
        intro_text=(
            "{friend} and {frenemy} both want to build a fort. "
            "{friend} wants it in the living room; {frenemy} wants "
            "it in the bedroom. They have one set of fort-blankets. "
            "What should they try?"
        ),
        intro_action="thinking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "They build a small fort in BOTH rooms — one in "
                    "the living room and a smaller one in the bedroom. "
                    "Connected by a pretend tunnel."
                ),
                "checkin": (
                    "How do both kids feel? Proud. Two forts is "
                    "actually a fort empire."
                ),
                "r_action": "cheering",
                "c_action": "cheering",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "They spot the hallway — neither living room nor "
                    "bedroom — and build the fort there. Compromise "
                    "location."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. The hallway-fort "
                    "is actually the best spot."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "Today's fort: living room. Tomorrow's fort: "
                    "bedroom. They take down today's at bedtime and "
                    "rebuild tomorrow."
                ),
                "checkin": (
                    "How do both kids feel? Patient. Two days of "
                    "fort-building is twice as much fort."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one fort, big imagination.",
    ),
    # 24. Pick the puzzle — sub / turns / offer
    _template(
        id_="conflict_pick_puzzle",
        title="Which puzzle do we do?",
        intro_text=(
            "{friend} and {frenemy} both want to do a puzzle. "
            "{friend} wants the cat puzzle; {frenemy} wants the "
            "rainbow puzzle. They only have time for one. What "
            "should they try?"
        ),
        intro_action="looking",
        forks=[
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} pulls out a puzzle they both forgot "
                    "about — the cat-in-a-rainbow puzzle. Both kids "
                    "get their wish."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. The forgotten "
                    "puzzle turned out perfect."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "Today: cat puzzle. Next puzzle-time: rainbow "
                    "puzzle. They put the rainbow one on top of the "
                    "shelf as a reminder."
                ),
                "checkin": (
                    "How does {frenemy} feel? Patient. The rainbow "
                    "puzzle is waiting."
                ),
                "r_action": "thinking",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} did the cat puzzle yesterday already. "
                    "They slide the rainbow puzzle box to {frenemy} "
                    "and say, 'this one today.'"
                ),
                "checkin": (
                    "How does {friend} feel? Fair. Rainbows are good "
                    "for puzzles too."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one puzzle, lots of clicking pieces.",
    ),
    # 25. Share sticker — all four
    _template(
        id_="conflict_share_sticker",
        title="Who gets the one sparkly sticker?",
        intro_text=(
            "{friend} and {frenemy} are passing a sticker sheet "
            "back and forth. There's only ONE sparkly star sticker "
            "left, and both kids want it. What should they try?"
        ),
        intro_action="looking",
        forks=[
            {
                "key": _SPLIT_KEY,
                "label": "split it",
                "resolve": (
                    "{friend} carefully cuts the star sticker in half. "
                    "{friend} keeps one half, {frenemy} keeps the "
                    "other. Two half-stars instead of one whole."
                ),
                "checkin": (
                    "How do both kids feel? Proud. A half-star is "
                    "still sparkly."
                ),
                "r_action": "pointing",
                "c_action": "cheering",
            },
            {
                "key": _SUB_KEY,
                "label": "find a substitute",
                "resolve": (
                    "{frenemy} flips the page and finds a sparkly "
                    "MOON sticker on the back. Just as good, brand "
                    "new, no fight."
                ),
                "checkin": (
                    "How does {frenemy} feel? Clever. The moon "
                    "sticker might be even cooler."
                ),
                "r_action": "looking",
                "c_action": "cheering",
            },
            {
                "key": _TURNS_KEY,
                "label": "take turns",
                "resolve": (
                    "{friend} sticks it on their notebook for today. "
                    "Next sticker sheet they buy, {frenemy} gets first "
                    "pick. They write it down so they remember."
                ),
                "checkin": (
                    "How does {frenemy} feel? Patient. The first-pick "
                    "I.O.U. feels like a small treasure."
                ),
                "r_action": "pointing",
                "c_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "one offers, one accepts",
                "resolve": (
                    "{friend} sees {frenemy} has been collecting stars "
                    "all week. They peel the sparkly star off and "
                    "stick it on {frenemy}'s notebook."
                ),
                "checkin": (
                    "How does {friend} feel? Generous, a little "
                    "wistful, but proud of the gift."
                ),
                "r_action": "waving",
                "c_action": "thinking",
            },
        ],
        end_text="Two kids, one sticker, lots of sparkle.",
    ),
]


# ---------------------------------------------------------------------
# Load / strip / write helpers (mirror M9 / M10)
# ---------------------------------------------------------------------


def _load_existing(path: Path, *, expected_intent: str) -> dict[str, Any]:
    """Read the existing intent file. Refuses to overwrite a structurally
    broken file — mirrors ``generate_perspective_taking_templates._load_existing``."""
    if not path.exists():
        raise FileNotFoundError(
            f"output file {path} does not exist; cannot append conflict-resolution "
            f"templates. Run from the worktree root."
        )
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(
            f"output file {path} is not a JSON object (got "
            f"{type(payload).__name__}); refusing to overwrite"
        )
    if payload.get("intent") != expected_intent:
        raise ValueError(
            f"output file {path} has intent={payload.get('intent')!r}; "
            f"expected {expected_intent!r}"
        )
    templates = payload.get("templates")
    if not isinstance(templates, list):
        raise ValueError(
            f"output file {path} does not contain a 'templates' list; refusing to overwrite"
        )
    return payload


def _strip_conflict_entries(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the input list with every M11 conflict_* entry removed. Idempotence."""
    return [t for t in templates if not str(t.get("id", "")).startswith(_TEMPLATE_PREFIX)]


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    """Persist with the same indent + trailing newline shape as M9 / M10."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _validate_post_write(
    path: Path, *, intent: str, expected_conflict: int
) -> None:
    """Re-load via the production loader and assert all M11 templates loaded.

    Mirrors ``generate_perspective_taking_templates._validate_post_write``
    so the same diagnostic surfaces on failure (whole intent file dropped
    on schema failure → delta indicates the offending id range).
    """
    from toybox.activities.generator import (  # type: ignore[import-untyped]
        _load_intent_templates,
        clear_template_cache,
    )

    clear_template_cache()
    templates = _load_intent_templates(intent)
    loaded_conflict = [t for t in templates if t.id.startswith(_TEMPLATE_PREFIX)]
    if len(loaded_conflict) != expected_conflict:
        raise SystemExit(
            f"--validate: expected {expected_conflict} conflict_* templates "
            f"to load from {intent}, got {len(loaded_conflict)}. The whole "
            f"intent file may have been dropped on schema failure; check "
            f"{path} and re-run."
        )
    _logger.info(
        "--validate (%s): %d conflict_* templates loaded cleanly via "
        "toybox.activities.generator._load_intent_templates",
        intent,
        len(loaded_conflict),
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append ~25 hand-authored conflict-resolution SEL templates "
            "to request_play.json (13) and request_activity.json (12). "
            "Phase M Step M11."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the merged JSON for both files to stdout and exit; do not write.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Idempotent regeneration is always-on (existing conflict_* entries "
            "are stripped before appending); this flag just tags the run in the log."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After writing, re-load each intent via the production template loader "
            "and assert all conflict_* templates load cleanly."
        ),
    )
    return parser.parse_args(argv)


def _process_intent(
    *,
    path: Path,
    intent: str,
    new_templates: list[dict[str, Any]],
    dry_run: bool,
    do_validate: bool,
    force: bool,
) -> dict[str, Any]:
    """Strip existing conflict_* entries and append the new batch for one intent.

    Returns the merged payload (the same one written to disk, modulo
    dry-run). Logs a per-intent summary line matching the M9/M10
    convention.
    """
    payload = _load_existing(path, expected_intent=intent)
    existing_templates: list[dict[str, Any]] = list(payload["templates"])
    pre_count = len(existing_templates)

    stripped = _strip_conflict_entries(existing_templates)
    stripped_count = pre_count - len(stripped)

    merged = stripped + new_templates
    payload["templates"] = merged
    post_count = len(merged)
    _logger.info(
        "summary (%s): pre=%d, removed_existing_conflict=%d, generated=%d, post=%d, force=%s",
        intent,
        pre_count,
        stripped_count,
        len(new_templates),
        post_count,
        force,
    )

    if not dry_run:
        _write_payload(path, payload)
        _logger.info("wrote %d templates to %s", post_count, path)

        if do_validate:
            _validate_post_write(
                path, intent=intent, expected_conflict=len(new_templates)
            )

    return payload


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)

    play_payload = _process_intent(
        path=_PLAY_OUTPUT,
        intent="request_play",
        new_templates=list(_PLAY_TEMPLATES),
        dry_run=args.dry_run,
        do_validate=args.validate,
        force=args.force,
    )
    activity_payload = _process_intent(
        path=_ACTIVITY_OUTPUT,
        intent="request_activity",
        new_templates=list(_ACTIVITY_TEMPLATES),
        dry_run=args.dry_run,
        do_validate=args.validate,
        force=args.force,
    )

    if args.dry_run:
        sys.stdout.write(
            json.dumps(
                {
                    "request_play": play_payload,
                    "request_activity": activity_payload,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
