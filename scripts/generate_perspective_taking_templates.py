"""Phase M Step M10 — generate ~20 perspective-taking SEL branching templates.

One-shot CLI that appends hand-authored two-act perspective-taking
templates to
``src/toybox/activities/templates/branching/request_play.json``.

Each template models the SEL competency "see the other side": act 1
plays a small conflict from ``{friend}``'s view (where ``{frenemy}``
looks selfish, careless, or rude); act 2 replays the **same scene**
from ``{frenemy}``'s view, revealing the real reason ``{frenemy}`` did
what they did. The reveal is always a benign reason — never a moral
failing (phase-m-plan.md §7 explicit: ``{frenemy}`` has a REAL reason,
not a "secretly the villain" framing).

Direct serve for Child A (6yo, early-reader, LOL-doll social play).
Princess Lyra and Inspector Pip are the natural personas the slot-fill
picker will hit — Princess via ``role_weights.friend`` and Detective
via ``role_weights.frenemy = 1.3`` (the Detective persona is the
load-bearing M10 anchor per phase-m-plan.md §6.5).

Two-act structure (mirrors phase-m-plan.md §5.9 / §7 spec):

* ``act1_intro``         — set up the conflict from {friend}'s view +
                           act-1 fork
* ``act1_branch_a/b``    — {friend}'s interpretation deepens (still
                           one-sided)
* ``act1_end``           — {friend} has decided how they feel about
                           {frenemy}
* ``act2_intro``         — explicit reset: "Now let's see the same
                           scene from {frenemy}'s side." + act-2 fork
* ``act2_branch_a/b``    — {frenemy} reveals the REAL reason they did
                           what they did (the load-bearing reveal —
                           must be NEW information, not a restatement)
* ``act2_end``           — both characters had real reasons; no
                           winner, no villain

Coverage (20 total — phase-m-plan.md §5.9 sweet-spot):

    1.  perspective_toy_taken           — wheel repair
    2.  perspective_line_skipped        — thought they'd left
    3.  perspective_last_cookie         — thought theirs was eaten
    4.  perspective_wont_play           — waiting to be asked
    5.  perspective_hide_and_seek       — fair hiding spot
    6.  perspective_ignored_drawing     — busy looking at colors
    7.  perspective_laughed_dance       — smiled because they liked it
    8.  perspective_broken_tower        — thought it was part of game
    9.  perspective_quiet_song          — singing along quietly
    10. perspective_took_seat           — thought the spot was open
    11. perspective_copied_idea         — expanding a great idea
    12. perspective_hidden_markers      — helping clean up
    13. perspective_took_too_long       — making sure to do it right
    14. perspective_smaller_piece       — thought {friend} preferred it
    15. perspective_too_loud            — excited and didn't notice
    16. perspective_swing_hog           — waiting for turn pattern
    17. perspective_not_included        — focused on the rules
    18. perspective_lunch_curious       — curious about the food
    19. perspective_sat_on_drawing      — didn't see it there
    20. perspective_no_wave             — didn't see the wave

Act-2 reveal taxonomy:

    fixing/helping:    1 (wheel), 8 (tower), 12 (markers)
    timing mistake:    2 (line), 4 (waiting), 16 (swing pattern)
    misread state:     3 (cookie), 10 (seat), 14 (smaller piece)
    didn't-see/notice: 6 (drawing), 15 (loud), 19 (sat on drawing),
                       20 (wave)
    fair-and-thought:  5 (hide spot)
    positive misread:  7 (smile = like), 9 (quiet = liking),
                       11 (copy = expand), 18 (curious about lunch)
    focused-on:        13 (right-not-fast), 17 (rules)

Each reveal type is something a 6yo can recognize from their own life,
and none frame {frenemy} as malicious.

Authoring style mirrors M5 / M6 / M9: Python literals + JSON emitter,
idempotent, ``--dry-run`` / ``--force`` / ``--validate`` / ``--output``
flags. Existing ``perspective_*`` entries are stripped before the new
batch appends.

Field order matches the post-M6 / M9 branching-template convention:
``id, title, buckets, steps, required_roles, optional_roles,
recommended_themes, ending_step``.

Authoring conventions
---------------------

* ``required_roles: ["friend", "frenemy"]`` — phase-m-plan.md §5.9
  M10 two-sided POV. Biases the persona picker toward Detective
  (Inspector Pip; ``role_weights.frenemy = 1.3``) and Princess Lyra
  (``role_weights.friend``). Both placeholders MUST appear in step
  text — K3.2 gate is ``len(required_roles) ≤ distinct role
  placeholder count`` so 2 ≤ 2.
* ``optional_roles: []`` — a third toy would dilute the two-sided
  focus; perspective-taking is fundamentally a duet.
* ``recommended_themes: ["feelings", "friendship"]`` — feelings is
  the M8 SEL theme, friendship covers the relational repair.
* ``ending_step: {kind: "joke", auto: true}`` — jokes deflate
  emotional weight after a perspective-shift reveal per
  phase-m-plan.md §5.9. Auto-mode lets the Phase L reward picker pull
  a friendship-or-feelings-themed joke.
* ``buckets: ["always"]`` — no time-of-day restriction.
* ``{friend}`` and ``{frenemy}`` are the only role placeholders in
  narration. Literal persona names (Princess, Lyra, Detective, Pip,
  Iridia, Marvelous) NEVER appear in narration — the persona picker
  binds at runtime via the slot-fill engine.
* Act 1 NEVER reveals {frenemy}'s reasoning — {friend}'s view stays
  one-sided. Act 2 ALWAYS reveals something act 1 did not. This is
  the load-bearing M10 quality bar per phase-m-plan.md §7.
* No "lesson narration." The story models the reveal; it does not
  preach about it (per phase-m-plan.md §8 risks: "Lean on Daniel
  Tiger / Bluey's lighter touch: model the behavior, don't narrate
  the lesson").

Eight-step skeleton (one fork per act → 2 fork points; matches
phase-m-plan.md §5.9 "1-3 forks per act"):

    [act1_intro fork] -> act1_branch_a -> act1_end -> [act2_intro fork]
        -> act2_branch_a -> act2_end
    [act1_intro fork] -> act1_branch_b -> act1_end -> [act2_intro fork]
        -> act2_branch_b -> act2_end

Each branch in act 1 explores a different way {friend} could
interpret what they saw (sad / mad / etc.); each branch in act 2
reveals a different *aspect* of {frenemy}'s real reason (so both
choices still land on "{frenemy} had a real reason," but the kid
hears two different facets of it).
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
    "src/toybox/activities/templates/branching/request_play.json"
)

# Idempotence prefix: every M10 template id starts with this slug so a
# re-run strips the previous batch before appending. The trailing
# underscore prevents a substring match against any hypothetical
# future ``perspectivexxx`` id (none exist today).
_TEMPLATE_PREFIX: Final[str] = "perspective_"


# ---------------------------------------------------------------------
# Hand-authored templates
# ---------------------------------------------------------------------

_TEMPLATES: Final[list[dict[str, Any]]] = [
    # -----------------------------------------------------------------
    # 1. Toy taken — wheel repair reveal (fixing/helping cluster)
    # -----------------------------------------------------------------
    {
        "id": "perspective_toy_taken",
        "title": "The car that walked away",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} sets their red car down to grab a snack. "
                    "When they look back — the car is gone. Across the "
                    "room, {frenemy} is holding it. What does {friend} "
                    "think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they took it on purpose", "next": "act1_branch_a"},
                    {"label": "they don't even ask", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s shoulders go stiff. From here it looks "
                    "like {frenemy} grabbed the car the second {friend} "
                    "turned away. {friend} feels their chest squeeze."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s eyebrows pull down. {frenemy} did not "
                    "ask once. From here that looks like {frenemy} "
                    "thinks {friend}'s things are also theirs."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} did something wrong. "
                    "That is how the scene looks from {friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What was {frenemy} actually doing with the red car?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "fixing it", "next": "act2_branch_a"},
                    {"label": "saving it", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} saw a wheel wobble loose as {friend} walked "
                    "away. They picked it up to push the wheel back on "
                    "before {friend} came back to a broken car."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} saw the dog walking right toward the car. "
                    "They scooped it up fast so the dog would not chew the "
                    "wheel. They were going to bring it right back."
                ),
                "action_slot": "running",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought one thing. {frenemy} was doing "
                    "another. Both had real reasons inside their head."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 2. Line skipped — timing mistake reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_line_skipped",
        "title": "Who is next in line?",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} is in line for the slide. They step aside "
                    "for one second to fix their shoe. When they look "
                    "up — {frenemy} has climbed onto the ladder ahead of "
                    "them. What does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they snuck in", "next": "act1_branch_a"},
                    {"label": "they don't care", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s jaw goes tight. From here it looks like "
                    "{frenemy} waited for {friend} to look down, then "
                    "snuck past on purpose."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s feet feel stuck. From here it looks like "
                    "{frenemy} did not even notice {friend} was there. "
                    "That feels worse than the skipping."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} cut in line. That is "
                    "how the scene looks from {friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What did {frenemy} see when they walked up to the "
                    "ladder?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "an empty line", "next": "act2_branch_a"},
                    {"label": "a moving line", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} walked up and saw nobody on the ladder, "
                    "nobody at the bottom. They thought {friend} had "
                    "gone to the water fountain and given up their spot."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} saw the kid in front move on, and nobody "
                    "stepping up. They thought the line had ended and "
                    "they were the next one to go."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} saw a line skip. {frenemy} saw an empty "
                    "ladder. Both were really there at the same time."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 3. Last cookie — misread state reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_last_cookie",
        "title": "The cookie on the plate",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "Two cookies sit on a plate at snack time. {friend} "
                    "saves their cookie for later. When {friend} comes "
                    "back, the plate is empty and {frenemy} is wiping "
                    "crumbs off their chin. What does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they stole it", "next": "act1_branch_a"},
                    {"label": "they grabbed both", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s tummy drops. From here it looks like "
                    "{frenemy} knew that second cookie belonged to "
                    "{friend} and ate it anyway."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s eyes sting. From here it looks like "
                    "{frenemy} doubled up — one for them, one for them "
                    "too. {friend} feels small."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} took what was theirs. "
                    "That is how the scene looks from {friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "When {frenemy} walked back to the plate, what did "
                    "they think they were seeing?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "one cookie left", "next": "act2_branch_a"},
                    {"label": "their cookie", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} remembered eating their cookie earlier. "
                    "Coming back to the plate, they saw one cookie left "
                    "and thought {friend} had already eaten theirs and "
                    "this leftover was up for grabs."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} got mixed up about which cookie was which. "
                    "They saw the one closer to their chair and thought "
                    "it was the one they had set aside for themself."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} saw a stolen cookie. {frenemy} saw a "
                    "leftover. Same plate, two stories."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 4. Won't play — waiting-to-be-asked reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_wont_play",
        "title": "The kid who won't come over",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} sets up a tea party. They wave at {frenemy} "
                    "across the playroom. {frenemy} just sits there "
                    "watching, not coming over. What does {friend} think?"
                ),
                "action_slot": "waving",
                "choices": [
                    {"label": "they don't want to", "next": "act1_branch_a"},
                    {"label": "they don't like me", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s shoulders slump. From here it looks like "
                    "{frenemy} could come over but is choosing not to. "
                    "{friend} feels left alone with the teapot."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s heart pinches. From here it looks like "
                    "{frenemy} is staying away because they do not want "
                    "to be friends today. That stings."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} is shutting them out. "
                    "That is how the scene looks from {friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What was going on inside {frenemy}'s head as they "
                    "watched the tea party?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "waiting for an invite", "next": "act2_branch_a"},
                    {"label": "shy to walk over", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} thought the tea party was {friend}'s "
                    "special game. They were waiting for {friend} to "
                    "say 'come play' out loud, because a wave alone did "
                    "not feel like enough of an invitation."
                ),
                "action_slot": "looking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} wanted to come over so badly. But their "
                    "feet felt shy and stuck. They were hoping {friend} "
                    "would walk halfway, so the last few steps would "
                    "feel easier."
                ),
                "action_slot": "looking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought {frenemy} did not want to play. "
                    "{frenemy} was actually waiting for a real invite."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 5. Hide-and-seek — fair-and-thought reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_hide_and_seek",
        "title": "The hide-and-seek spot",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} is counting to twenty for hide-and-seek. At "
                    "twenty, they look up — {frenemy} is hiding behind "
                    "the couch, but {friend} can see one whole foot "
                    "sticking out. What does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they cheated", "next": "act1_branch_a"},
                    {"label": "they barely tried", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s mouth opens. From here it looks like "
                    "{frenemy} picked a spot they KNEW had a peeking gap, "
                    "to make the round end fast on purpose."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s breath huffs out. From here it looks like "
                    "{frenemy} did not even try to hide well. That "
                    "feels like they did not want to play right."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} broke the game. That "
                    "is how the scene looks from {friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "Why did {frenemy} pick behind the couch?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "a fair-looking spot", "next": "act2_branch_a"},
                    {"label": "the best spot they knew", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "From where {frenemy} crouched, the couch looked HUGE. "
                    "They could not see their own foot. They thought "
                    "behind-the-couch was a totally fair hiding place."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} had played here once before and remembered "
                    "the couch as their best spot. They tucked in fast "
                    "and never thought to check what {friend} would see "
                    "from the other side of the room."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought it was cheating. {frenemy} thought "
                    "it was just hiding. They were looking from two "
                    "different angles."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 6. Ignored drawing — didn't-see/notice reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_ignored_drawing",
        "title": "The drawing nobody talked about",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} draws their best-ever rainbow and slides it "
                    "across the table to {frenemy}. {frenemy} looks at it "
                    "for a long time and says — nothing. What does "
                    "{friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they hate it", "next": "act1_branch_a"},
                    {"label": "they're ignoring me", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s smile drops. From here it looks like "
                    "{frenemy} thinks the rainbow is bad and is being "
                    "too nice to say it out loud."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s eyes prick. From here it looks like "
                    "{frenemy} does not even care enough to talk about "
                    "{friend}'s drawing. That feels like being invisible."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} doesn't like the "
                    "drawing. That is how the scene looks from {friend}'s "
                    "side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What was {frenemy} actually doing while looking at "
                    "the rainbow?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "studying the colors", "next": "act2_branch_a"},
                    {"label": "memorizing it", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} got stuck on the purple stripe. They were "
                    "wondering HOW {friend} made the purple line so "
                    "smooth. Their mouth was open to ask but their brain "
                    "was still busy looking."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} was trying to memorize every color in "
                    "order so they could try the same rainbow at home "
                    "later. They forgot that {friend} was waiting for "
                    "words."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought silence meant bad. {frenemy}'s "
                    "silence actually meant 'wow, I am still looking.'"
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 7. Laughed at dance — positive misread reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_laughed_dance",
        "title": "The dance and the smile",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} makes up a wiggly dance in the living room. "
                    "While they spin, {friend} sees {frenemy} watching "
                    "with a huge open-mouth grin. What does {friend} "
                    "think the grin means?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they're laughing at me", "next": "act1_branch_a"},
                    {"label": "they think it's silly", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s feet stop. From here that grin looks like "
                    "{frenemy} is laughing because the dance is "
                    "embarrassing. {friend} wants to disappear."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s cheeks burn. From here that grin looks "
                    "like {frenemy} thinks the dance is goofy and a "
                    "little bit weird."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} is laughing at them. "
                    "That is how the scene looks from {friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What was making {frenemy} grin so wide?"
                ),
                "action_slot": "cheering",
                "choices": [
                    {"label": "they loved the dance", "next": "act2_branch_a"},
                    {"label": "they wanted to join", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} thought the wiggly dance was the best "
                    "thing they had seen all day. Their grin was the "
                    "kind of grin you can't keep in. They were not "
                    "laughing at all — they were happy-smiling."
                ),
                "action_slot": "cheering",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy}'s grin was the 'I want to do that too' "
                    "kind. Their feet were already bouncing along under "
                    "the table. They were getting ready to ask if they "
                    "could learn the dance."
                ),
                "action_slot": "jumping",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought the smile meant 'laughing at.' It "
                    "actually meant 'I like this.' Same face, two readings."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 8. Broken tower — part-of-game reveal (fixing/helping cluster)
    # -----------------------------------------------------------------
    {
        "id": "perspective_broken_tower",
        "title": "The tower that fell over",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} builds a block tower as tall as their chin. "
                    "They walk to the bathroom for one minute. They come "
                    "back to find {frenemy} pushing the top block — "
                    "CRASH. What does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they wrecked it on purpose", "next": "act1_branch_a"},
                    {"label": "they were being mean", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s eyes get hot. From here it looks like "
                    "{frenemy} chose the exact moment {friend} was away "
                    "to knock the whole thing down."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s hands clench. From here it looks like "
                    "{frenemy} thought wrecking the tower would be funny "
                    "without asking if {friend} would think so too."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} broke it on purpose. "
                    "That is how the scene looks from {friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What did {frenemy} think the tower was for?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "the final big crash", "next": "act2_branch_a"},
                    {"label": "a game waiting to be played", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} thought every block tower ends with one "
                    "big satisfying CRASH — that was the WHOLE fun. They "
                    "thought {friend} had walked away because they were "
                    "ready for the crash part."
                ),
                "action_slot": "cheering",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} thought the tower was the START of a "
                    "game — like a fort to attack with paper airplanes. "
                    "They thought pushing the top block was step one of "
                    "playing together."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} saw their building wrecked. {frenemy} thought "
                    "they were starting the next game. No bad guys here."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 9. Quiet song — positive misread reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_quiet_song",
        "title": "The favorite song nobody sang along to",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} plays their favorite song out loud. They "
                    "look over at {frenemy} expecting big singing — but "
                    "{frenemy} sits very quietly with a small face. What "
                    "does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they hate the song", "next": "act1_branch_a"},
                    {"label": "they think it's babyish", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s smile fades. From here {frenemy}'s quiet "
                    "face looks like 'this song is bad and I am suffering "
                    "through it.'"
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s cheeks go warm. From here {frenemy}'s "
                    "quiet face looks like 'this song is for little "
                    "kids and I am too old for it.'"
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} does not like the "
                    "song. That is how the scene looks from {friend}'s "
                    "side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What was happening inside {frenemy} during that song?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "singing in their head", "next": "act2_branch_a"},
                    {"label": "really really listening", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} was singing every single word — but only "
                    "inside their head. Out loud they were too quiet to "
                    "hear, just lips barely moving. They actually love "
                    "this song."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} closed up because they wanted to hear "
                    "every note carefully. They had a small face because "
                    "all their feelings were busy listening. Loving a "
                    "song doesn't always look big."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought quiet meant 'bad song.' {frenemy}'s "
                    "quiet actually meant 'I love it so much.'"
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 10. Took seat — misread state reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_took_seat",
        "title": "The spot at the table",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} has been sitting in the blue chair at the "
                    "craft table all morning. They run to grab more "
                    "paper. When they come back, {frenemy} is sitting in "
                    "the blue chair. What does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they stole my spot", "next": "act1_branch_a"},
                    {"label": "they planned it", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s ears turn warm. From here it looks like "
                    "{frenemy} watched {friend} leave and grabbed the "
                    "chair right away. That feels unfair."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s arms feel tight. From here it looks like "
                    "{frenemy} had been waiting for the chair the whole "
                    "time, watching for any chance."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} took their spot. "
                    "That is how the scene looks from {friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What did {frenemy} see when they walked up to the "
                    "blue chair?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "no markers, no paper", "next": "act2_branch_a"},
                    {"label": "an open chair", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} saw the blue chair with NOTHING in front "
                    "of it — no paper, no markers, no in-progress drawing. "
                    "They thought {friend} had cleaned up and finished. "
                    "An empty desk meant an empty chair."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} did not even know {friend} had been there. "
                    "From their angle the chair just looked open, the way "
                    "any empty chair looks open. They sat down without a "
                    "second thought."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought {frenemy} stole the spot. {frenemy} "
                    "thought the spot was free. The chair did not tell "
                    "either of them the whole story."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 11. Copied idea — positive misread reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_copied_idea",
        "title": "The idea that got borrowed",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} announces a great idea: a pillow fort with "
                    "a SECRET door. Five minutes later, {frenemy} starts "
                    "building a pillow fort with a SECRET door of their "
                    "own. What does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they're stealing my idea", "next": "act1_branch_a"},
                    {"label": "they can't think for themself", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s shoulders pull up. From here it looks "
                    "like {frenemy} grabbed {friend}'s idea and pretended "
                    "it was theirs. That feels like a copy without credit."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s jaw goes tight. From here it looks like "
                    "{frenemy} could not come up with anything of their "
                    "own and had to use {friend}'s idea."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} copied them. That "
                    "is how the scene looks from {friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What was going through {frenemy}'s head when they "
                    "heard the secret-door idea?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "this is the best idea ever", "next": "act2_branch_a"},
                    {"label": "let's make TWO forts", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} thought the secret-door idea was the "
                    "coolest thing they had ever heard. To {frenemy}, "
                    "building one too was a way of saying 'this idea is "
                    "AMAZING' — not stealing it."
                ),
                "action_slot": "cheering",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} pictured two pillow forts side by side "
                    "with two secret doors that could MEET in the middle. "
                    "They were trying to expand {friend}'s idea into a "
                    "bigger game for both of them."
                ),
                "action_slot": "pointing",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought {frenemy} was stealing. {frenemy} "
                    "thought they were saying 'yes!' to a great idea."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 12. Hidden markers — fixing/helping reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_hidden_markers",
        "title": "The markers that disappeared",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} leaves all their markers spread out on the "
                    "floor. They come back from snack and every single "
                    "marker is GONE. {frenemy} is the only one in the "
                    "room. What does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they hid them", "next": "act1_branch_a"},
                    {"label": "they took them", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s hands open and close. From here it looks "
                    "like {frenemy} stashed the markers somewhere to "
                    "tease {friend} or play a trick."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s stomach knots. From here it looks like "
                    "{frenemy} carried the markers off because they "
                    "wanted them for themself."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} did something with "
                    "the markers. That is how the scene looks from "
                    "{friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What did {frenemy} decide to do when they saw the "
                    "markers everywhere?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "tidy up before the dog", "next": "act2_branch_a"},
                    {"label": "be helpful before clean-up time", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} saw the dog wandering over and worried "
                    "the markers would get chewed. They grabbed every "
                    "marker fast and put them all back in the marker "
                    "bin to keep them safe."
                ),
                "action_slot": "running",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} heard a grown-up say 'clean-up time in "
                    "five minutes' and wanted to help {friend} not get "
                    "in trouble. They scooped the markers into the bin "
                    "before {friend} got back."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought the markers were taken. {frenemy} "
                    "thought they were being a helper. The bin had them "
                    "the whole time."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 13. Took too long — focused-on reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_took_too_long",
        "title": "The turn that took forever",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} and {frenemy} are taking turns putting "
                    "puzzle pieces in. It is {frenemy}'s turn and they "
                    "have been staring at one piece for what feels like "
                    "FOREVER. What does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they're hogging the turn", "next": "act1_branch_a"},
                    {"label": "they're spacing out", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s feet bounce. From here it looks like "
                    "{frenemy} is taking forever ON PURPOSE because they "
                    "like making {friend} wait."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s hands fidget. From here it looks like "
                    "{frenemy} is not even paying attention to the puzzle "
                    "and is just zoning out."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} is wasting their "
                    "turn. That is how the scene looks from {friend}'s "
                    "side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What was {frenemy} doing as they stared at that "
                    "piece?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "checking every edge", "next": "act2_branch_a"},
                    {"label": "trying to get it perfect", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} was matching the piece's edges to every "
                    "open spot, one by one. They wanted to be sure it "
                    "really fit before pushing it down — not slow on "
                    "purpose, just careful."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} could see the piece almost fit, but the "
                    "picture on it was upside down. They were turning it "
                    "in their head, trying to get it RIGHT so the puzzle "
                    "would look perfect for both of them."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought slow meant 'hogging.' {frenemy}'s "
                    "slow meant 'making sure.' Both wanted to play."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 14. Smaller piece — misread state reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_smaller_piece",
        "title": "The pieces of cake",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{frenemy} cuts a piece of cake in two and hands the "
                    "smaller half to {friend}. {friend} stares at their "
                    "plate, then at {frenemy}'s bigger piece. What does "
                    "{friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they kept the bigger one", "next": "act1_branch_a"},
                    {"label": "they don't share fairly", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s mouth pulls down. From here it looks "
                    "like {frenemy} did the cutting so THEY would get "
                    "the bigger piece and {friend} would get the small "
                    "one."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s arms cross. From here it looks like "
                    "{frenemy} just does not care about being fair, even "
                    "with cake."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} took the bigger "
                    "piece on purpose. That is how the scene looks from "
                    "{friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "Why did {frenemy} hand over the smaller piece?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "they thought it was the favor", "next": "act2_branch_a"},
                    {"label": "they remembered last time", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} remembered that {friend} usually says "
                    "they 'can't finish a whole big piece.' They thought "
                    "giving {friend} the smaller half was being kind — "
                    "less waste, less full belly."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "Last time they shared, {friend} had picked the "
                    "smaller piece on purpose. {frenemy} thought {friend} "
                    "PREFERRED smaller, so they handed over the small "
                    "half thinking it was {friend}'s favorite."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought {frenemy} kept the big one. "
                    "{frenemy} thought small was the kind gift. Cake "
                    "looks different from each plate."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 15. Too loud — didn't-see/notice reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_too_loud",
        "title": "The talking that filled the room",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} is trying to read a book on the couch. "
                    "{frenemy} is across the room talking at full volume "
                    "about their new sticker pack — for a long, long, "
                    "long time. What does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they're being rude", "next": "act1_branch_a"},
                    {"label": "they don't care I'm reading", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s ears pinch. From here it looks like "
                    "{frenemy} is using a loud voice ON PURPOSE because "
                    "they want to make {friend} stop reading."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s shoulders rise. From here it looks like "
                    "{frenemy} can see the book and just does not care "
                    "that {friend} is trying to concentrate."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} is being too loud "
                    "on purpose. That is how the scene looks from "
                    "{friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What did {frenemy} notice about how loud they were "
                    "being?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "nothing — they had no idea", "next": "act2_branch_a"},
                    {"label": "they didn't see the book", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} was SO excited about the sticker pack "
                    "their volume turned up without them even hearing it "
                    "rise. Inside their head it felt like a normal voice "
                    "the whole time."
                ),
                "action_slot": "cheering",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} could not see {friend}'s book from where "
                    "they stood — only the back of the couch. They "
                    "thought {friend} was just resting. A resting person "
                    "doesn't need quiet."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought 'loud on purpose.' {frenemy} thought "
                    "'normal voice, nobody reading.' Two ears, two "
                    "stories."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 16. Swing hog — timing mistake reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_swing_hog",
        "title": "The swing that nobody got off",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "There is only ONE swing left at the park. {frenemy} "
                    "is on it. {friend} stands close by, waiting. The "
                    "swinging goes on and on. What does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they'll never get off", "next": "act1_branch_a"},
                    {"label": "they don't see me", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s feet stomp once. From here it looks like "
                    "{frenemy} plans to stay on the swing until "
                    "{friend} gives up and walks away."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s chest hurts. From here it looks like "
                    "{frenemy} hasn't even looked at {friend} once. "
                    "{friend} feels invisible."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} is hogging the "
                    "swing. That is how the scene looks from {friend}'s "
                    "side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What rule about swings was {frenemy} using inside "
                    "their head?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "wait until asked", "next": "act2_branch_a"},
                    {"label": "count to one hundred", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} thought the rule was 'if someone wants a "
                    "turn they have to come ASK.' They were waiting for "
                    "{friend} to say 'my turn?' so they would know "
                    "{friend} wanted on."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "At {frenemy}'s house, swing turns were measured by "
                    "counting to one hundred. They were almost done with "
                    "their count. They thought it was a fair pattern, "
                    "not a forever."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought 'hogging.' {frenemy} thought "
                    "'waiting for the ask.' Different rules in two heads."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 17. Not included — focused-on-rules reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_not_included",
        "title": "The game that started without them",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{frenemy} starts a tag game with two other kids in "
                    "the yard. {friend} is standing right by the gate. "
                    "{frenemy} doesn't call them over. What does {friend} "
                    "think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they don't want me", "next": "act1_branch_a"},
                    {"label": "they forgot on purpose", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s eyes get warm. From here it looks like "
                    "{frenemy} picked the other kids and is leaving "
                    "{friend} out on purpose."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s feet feel heavy. From here it looks "
                    "like {frenemy} is pretending not to see {friend} "
                    "by the gate so they have an excuse."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} doesn't want them "
                    "in the game. That is how the scene looks from "
                    "{friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What was {frenemy} so focused on that they didn't "
                    "call {friend}?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "explaining the rules", "next": "act2_branch_a"},
                    {"label": "picking who is It", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} was busy teaching the two other kids a "
                    "tricky new tag rule about safe-bases. Their whole "
                    "brain was on getting the rule right. They were "
                    "about to wave {friend} in once it stuck."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} was deep in the 'how do we pick who is "
                    "It' part. They had a counting rhyme going and could "
                    "not stop in the middle. Inviting {friend} was the "
                    "VERY next step, but the rhyme was first."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought 'they don't want me.' {frenemy} "
                    "was so focused on rules they hadn't gotten to the "
                    "inviting part yet."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 18. Lunch curious — positive misread reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_lunch_curious",
        "title": "The look at the lunchbox",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} opens their lunchbox. There are tiny "
                    "seaweed crackers their grandma made. {frenemy} "
                    "stares at the crackers with a funny scrunched-up "
                    "face. What does {friend} think the face means?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they think it's gross", "next": "act1_branch_a"},
                    {"label": "they're judging my lunch", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s cheeks heat up. From here that scrunched "
                    "face looks like 'ew, that lunch is so weird.' "
                    "{friend} wants to close the box."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s tummy tightens. From here that face "
                    "looks like {frenemy} is silently making fun of "
                    "{friend}'s grandma's cooking."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} thinks the lunch "
                    "is gross. That is how the scene looks from "
                    "{friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What was that scrunched face actually about?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "trying to figure it out", "next": "act2_branch_a"},
                    {"label": "wishing for a bite", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "{frenemy} had never seen seaweed crackers before. "
                    "Their scrunched face was a 'wait, what ARE those, "
                    "they look so cool' face. Inside their head they "
                    "were already wondering if they tasted salty."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} squinted at the crackers because the "
                    "color reminded them of their cousin's favorite "
                    "snack. They were quietly hoping {friend} might "
                    "share a taste so they could try one too."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought the face meant 'gross.' {frenemy}'s "
                    "face meant 'curious.' New foods make funny faces."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 19. Sat on drawing — didn't-see/notice reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_sat_on_drawing",
        "title": "The drawing under the cushion",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} sets their best dragon drawing on the "
                    "couch. They turn around for a second. When they "
                    "turn back, {frenemy} is sitting RIGHT on top of "
                    "the drawing. What does {friend} think?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "they ruined it on purpose", "next": "act1_branch_a"},
                    {"label": "they don't respect my stuff", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s breath catches. From here it looks like "
                    "{frenemy} aimed for the drawing on purpose, to "
                    "wreck it."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s hands shake. From here it looks like "
                    "{frenemy} just doesn't care what is on the couch "
                    "before they plop down."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} squashed the dragon "
                    "drawing. That is how the scene looks from {friend}'s "
                    "side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What did {frenemy} see when they walked up to the "
                    "couch?"
                ),
                "action_slot": "looking",
                "choices": [
                    {"label": "an empty couch", "next": "act2_branch_a"},
                    {"label": "the cushion only", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "The drawing was the same color as the cushion. From "
                    "where {frenemy} stood, the couch looked totally "
                    "empty. They sat down without ever seeing the paper "
                    "was there."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} was rushing to sit and start a new game "
                    "with {friend}. Their eyes were on the toy bin, not "
                    "the cushion. They never looked down before sitting."
                ),
                "action_slot": "running",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought the squashing was on purpose. "
                    "{frenemy} never saw the dragon at all. Same couch, "
                    "two views."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
    # -----------------------------------------------------------------
    # 20. No wave back — didn't-see/notice reveal
    # -----------------------------------------------------------------
    {
        "id": "perspective_no_wave",
        "title": "The wave that wasn't waved back",
        "buckets": ["always"],
        "steps": [
            {
                "id": "act1_intro",
                "text": (
                    "{friend} spots {frenemy} across the playground and "
                    "waves BIG. {frenemy} walks right past, no wave back, "
                    "no smile, like they aren't there at all. What does "
                    "{friend} think?"
                ),
                "action_slot": "waving",
                "choices": [
                    {"label": "they're ignoring me", "next": "act1_branch_a"},
                    {"label": "they're being cold", "next": "act1_branch_b"},
                ],
            },
            {
                "id": "act1_branch_a",
                "text": (
                    "{friend}'s arm drops. From here it looks like "
                    "{frenemy} saw the wave perfectly and chose to "
                    "ignore it on purpose."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_branch_b",
                "text": (
                    "{friend}'s chest pinches. From here it looks like "
                    "{frenemy} does not want to be friends today and "
                    "is walking past on purpose."
                ),
                "action_slot": "thinking",
                "next": "act1_end",
            },
            {
                "id": "act1_end",
                "text": (
                    "{friend} has decided: {frenemy} ignored the wave. "
                    "That is how the scene looks from {friend}'s side."
                ),
                "action_slot": "idle",
                "next": "act2_intro",
            },
            {
                "id": "act2_intro",
                "text": (
                    "Now let's see the same scene from {frenemy}'s side. "
                    "What was happening for {frenemy} as they walked "
                    "across the playground?"
                ),
                "action_slot": "thinking",
                "choices": [
                    {"label": "the sun in their eyes", "next": "act2_branch_a"},
                    {"label": "deep in a daydream", "next": "act2_branch_b"},
                ],
            },
            {
                "id": "act2_branch_a",
                "text": (
                    "The sun was right in {frenemy}'s eyes. They could "
                    "see almost nothing but bright squiggles. They walked "
                    "past {friend} without seeing the wave, the smile, "
                    "or {friend} themself."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_branch_b",
                "text": (
                    "{frenemy} was deep in a daydream about a story they "
                    "wanted to tell {friend} later. Their eyes were "
                    "looking forward but their brain was a million miles "
                    "away. The wave never reached them."
                ),
                "action_slot": "thinking",
                "next": "act2_end",
            },
            {
                "id": "act2_end",
                "text": (
                    "{friend} thought 'ignored.' {frenemy} never saw the "
                    "wave. Eyes can be open and still not looking."
                ),
                "action_slot": "waving",
            },
        ],
        "required_roles": ["friend", "frenemy"],
        "optional_roles": [],
        "recommended_themes": ["feelings", "friendship"],
        "ending_step": {"kind": "joke", "auto": True},
    },
]


# ---------------------------------------------------------------------
# Load / strip / write helpers (mirror M5 / M6 / M9)
# ---------------------------------------------------------------------


def _load_existing(path: Path) -> dict[str, Any]:
    """Read the existing intent file. Refuses to overwrite a structurally
    broken file — mirrors ``generate_feelings_naming_templates._load_existing``."""
    if not path.exists():
        raise FileNotFoundError(
            f"output file {path} does not exist; cannot append perspective-taking "
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
            f"output file {path} has intent={payload.get('intent')!r}; "
            f"expected 'request_play'"
        )
    templates = payload.get("templates")
    if not isinstance(templates, list):
        raise ValueError(
            f"output file {path} does not contain a 'templates' list; refusing to overwrite"
        )
    return payload


def _strip_perspective_entries(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the input list with every M10 perspective_* entry removed. Idempotence."""
    return [t for t in templates if not str(t.get("id", "")).startswith(_TEMPLATE_PREFIX)]


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    """Persist with the same indent + trailing newline shape as M4 / M5 / M6 / M9."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _validate_post_write(path: Path, *, expected_perspective: int) -> None:
    """Re-load via the production loader and assert all M10 templates loaded.

    Mirrors ``generate_feelings_naming_templates._validate_post_write`` so
    the same diagnostic surfaces on failure (whole intent file dropped
    on schema failure → delta indicates the offending id range).
    """
    from toybox.activities.generator import (  # type: ignore[import-untyped]
        _load_intent_templates,
        clear_template_cache,
    )

    clear_template_cache()
    templates = _load_intent_templates("request_play")
    loaded_perspective = [t for t in templates if t.id.startswith(_TEMPLATE_PREFIX)]
    if len(loaded_perspective) != expected_perspective:
        raise SystemExit(
            f"--validate: expected {expected_perspective} perspective_* templates "
            f"to load, got {len(loaded_perspective)}. The whole intent file may "
            f"have been dropped on schema failure; check {path} and re-run."
        )
    _logger.info(
        "--validate: %d perspective_* templates loaded cleanly through "
        "toybox.activities.generator._load_intent_templates",
        len(loaded_perspective),
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append ~20 hand-authored perspective-taking SEL templates "
            "to request_play.json (Phase M Step M10)."
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
            "Idempotent regeneration is always-on (existing perspective_* entries "
            "are stripped before appending); this flag just tags the run in the log."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After writing, re-load via the production template loader and "
            "assert all perspective_* templates load cleanly."
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

    stripped = _strip_perspective_entries(existing_templates)
    stripped_count = pre_count - len(stripped)

    new_templates = list(_TEMPLATES)
    merged = stripped + new_templates
    payload["templates"] = merged

    post_count = len(merged)
    _logger.info(
        "summary: pre=%d, removed_existing_perspective=%d, generated=%d, post=%d, force=%s",
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
        _validate_post_write(output, expected_perspective=len(new_templates))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
