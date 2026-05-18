"""Phase M Step M12 — generate ~15 friendship/repair SEL branching templates.

One-shot CLI that appends hand-authored friendship/repair templates to:

* ``src/toybox/activities/templates/branching/request_play.json``  (15 templates)

Each template models the SEL competency "rupture happened; here are ways to
repair it." Two characters (``{friend}`` and ``{frenemy}``) are friends;
one accidentally causes a small social rupture (knocked over a tower,
forgot to invite, said something mean). The kid picks a REPAIR STRATEGY
as a fork choice; the chosen branch plays out the attempt at repair.

The load-bearing M12 quality bar (phase-m-plan.md §5.9 / §7 / done-when):

    *At least one fork in every template must depict a "first try fails,
     second try works" recovery.*

Repair takes effort. The model shows the kid trying once, the move not
landing yet, then trying again with more specificity, persistence, or a
different angle — and the second try works. NO "repair = instant
forgiveness" shortcuts.

Direct serve for Child A (6yo, early-reader, LOL-doll social play).
Princess Lyra is the natural persona the slot-fill picker hits via
``role_weights.friend``; Inspector Pip via the ``frenemy`` slot
(established by M10 / extended by M11).

The four repair strategies (each fork choice = ONE strategy):

* ``apologize``               — say sorry; second try names the specific wrong
* ``help fix it``             — actively repair the damage; second try invites in
* ``acknowledge feelings``    — name what {frenemy} is feeling
* ``offer something``         — a gesture (give a turn, share, propose new game)

Each template picks 2-3 of these (per plan §7). Coverage rotates across
all four so a kid playing the corpus sees every strategy multiple times.

Coverage (15 total — phase-m-plan.md §7 sweet-spot 13-17):

  request_play (15):
     1. repair_block_tower         — apologize / fix / feelings
     2. repair_forgot_invite       — apologize / feelings / offer
     3. repair_mean_drawing        — apologize / feelings / offer
     4. repair_laughed_fall        — apologize / feelings / offer
     5. repair_took_marker         — apologize / fix / offer
     6. repair_broken_crayon       — apologize / fix / offer
     7. repair_interrupted_story   — apologize / feelings / offer
     8. repair_ignored_wave        — apologize / feelings / offer
     9. repair_weird_outfit        — apologize / feelings / offer
    10. repair_team_list           — apologize / fix / offer
    11. repair_stepped_puzzle      — apologize / fix / feelings
    12. repair_skipped_turn        — apologize / fix / offer
    13. repair_closed_book         — apologize / fix / feelings
    14. repair_hid_teddy           — apologize / fix / feelings
    15. repair_loud_song           — apologize / feelings / offer

Strategy rotation tally across the 15 templates:
    apologize:          15
    help fix it:         8
    acknowledge feelings:10
    offer something:    10

"Apologize" appears in every template — it's the floor move; the other
strategies layer on top to teach there's MORE than one way to repair. No
single strategy dominates the rest.

The "first try fails, second try works" required pattern — implemented
via a 2-step path per fork (``*_path`` shows the first try not landing;
``*_second`` shows the recovery). EVERY template uses this two-step
recovery shape in EVERY fork (not just one) — exceeding the plan-floor
of "at least one fork." Rationale: the load-bearing M12 lesson is that
repair takes more than one try; modeling that uniformly across forks
reinforces the lesson and gives the kid a richer corpus.

Authoring style mirrors M11: Python literals + JSON emitter,
idempotent, ``--dry-run`` / ``--force`` / ``--validate`` flags.
Existing ``repair_*`` entries are stripped before the new batch
appends.

Field order matches the post-M6 / M9 / M10 / M11 branching-template
convention: ``id, title, buckets, steps, required_roles,
optional_roles, recommended_themes, ending_step``.

Authoring conventions
---------------------

* ``required_roles: ["friend"]`` — phase-m-plan.md §5.9 M12 spec.
  Biases the persona picker toward Princess Lyra
  (``role_weights.friend``).
* ``optional_roles: ["frenemy"]`` — the second character slot, exact
  M11 reuse. ``frenemy`` is a SLOT KEY only, NOT adversarial framing.
  Narration treats {friend} and {frenemy} as equal friends — the role
  label is just what the slot-fill engine needs to allocate a distinct
  toy for each placeholder (two ``{friend}`` literals would collapse
  to one toy).
* ``recommended_themes: ["friendship"]`` — phase-m-plan.md §5.9 M12
  spec.
* ``ending_step: {kind: "joke", auto: true}`` — jokes deflate the
  emotional scene more gently than songs.
* ``buckets: ["always"]`` — no time-of-day restriction.
* ``{friend}`` and ``{frenemy}`` are the only role placeholders.
  Literal persona names (Princess, Lyra, Detective, Pip, Iridia,
  Marvelous) NEVER appear in narration.
* No "lesson narration." Model the repair; don't preach about it.

Step skeleton (every template, 5-8 steps total — within plan §7):

    rupture (fork)
      -> <key>_path     (first try; doesn't land yet)  -> <key>_second
      -> <key>_second   (recovery; lands)              -> end
      -> ... (next fork choice)
    end (joins all branches)

  N forks → 1 (rupture) + 2·N (path + second per fork) + 1 (end) steps:
    2 forks = 6 steps
    3 forks = 8 steps
  All 15 templates use 3 forks → 8 steps each.
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

# Idempotence prefix: every M12 template id starts with this slug so a
# re-run strips the previous batch before appending. The trailing
# underscore prevents a substring match against any hypothetical
# future ``repairxxx`` id (none exist today).
_TEMPLATE_PREFIX: Final[str] = "repair_"


# Stable per-strategy step-id slugs so step ids stay predictable across
# templates and so the strip-prefix idempotence operates on whole
# templates, not branches.
_APOLOGIZE_KEY = "apologize"
_FIX_KEY = "fix"
_FEELINGS_KEY = "feelings"
_OFFER_KEY = "offer"


# ---------------------------------------------------------------------
# Shared per-template scaffold helper
# ---------------------------------------------------------------------


def _template(
    *,
    id_: str,
    title: str,
    rupture_text: str,
    rupture_action: str,
    forks: list[dict[str, str]],
    end_text: str,
) -> dict[str, Any]:
    """Build a friendship/repair template from the standard skeleton.

    Each entry in ``forks`` is a dict with keys:

      ``key``        — short slug used for step ids ("apologize", "fix",
                       "feelings", "offer"). Must be a valid step-id slug.
      ``label``      — choice button text (what the kid picks).
      ``first_try``  — narration showing the first repair attempt and
                       that it did NOT land yet ({frenemy} stays quiet /
                       still tense / pulls away / shakes head).
      ``second_try`` — narration showing the kid trying again with more
                       specificity / persistence / a different angle,
                       and {frenemy} responding (nods / opens hands /
                       sits closer / brightens).
      ``f_action``   — action_slot for the first_try step.
      ``s_action``   — action_slot for the second_try step.

    The skeleton wires each fork as a 2-step branch:

      rupture (fork)
        -> <key>_path (first_try)   -> <key>_second (second_try)  -> end
        -> ... (next fork choice)

    Choices count: 2-3 (phase-m-plan.md §7 M12 "2-3 forks"). Schema
    enforces min 2 / max 4 at the JSON-schema layer; M12 stays at 2-3
    per the plan spec.
    """
    if not (2 <= len(forks) <= 3):
        raise ValueError(
            f"template {id_!r}: forks must have 2-3 entries (M12 spec), got {len(forks)}"
        )

    choices = [{"label": f["label"], "next": f"{f['key']}_path"} for f in forks]
    steps: list[dict[str, Any]] = [
        {
            "id": "rupture",
            "text": rupture_text,
            "action_slot": rupture_action,
            "choices": choices,
        },
    ]
    for f in forks:
        key = f["key"]
        steps.append(
            {
                "id": f"{key}_path",
                "text": f["first_try"],
                "action_slot": f["f_action"],
                "next": f"{key}_second",
            }
        )
        steps.append(
            {
                "id": f"{key}_second",
                "text": f["second_try"],
                "action_slot": f["s_action"],
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


# ---------------------------------------------------------------------
# request_play templates (15)
# ---------------------------------------------------------------------

_PLAY_TEMPLATES: Final[list[dict[str, Any]]] = [
    # 1. Block tower — apologize / fix / feelings
    _template(
        id_="repair_block_tower",
        title="Knocked over the block tower",
        rupture_text=(
            "{friend} runs past the rug and accidentally knocks over "
            "{frenemy}'s tall block tower. The blocks scatter. "
            "{frenemy} looks scared, hands frozen. What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} says, \"sorry!\" really fast and looks "
                    "down. {frenemy} stays quiet — the apology was too "
                    "quick to land."
                ),
                "second_try": (
                    "{friend} tries again, slower: \"I'm sorry for "
                    "knocking over your tower. I was running too fast.\" "
                    "{frenemy} nods."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _FIX_KEY,
                "label": "help fix it",
                "first_try": (
                    "{friend} kneels and starts re-stacking the blocks "
                    "alone. It feels lonely. {frenemy}'s hands stay in "
                    "their lap."
                ),
                "second_try": (
                    "{friend} stops, looks up, and asks, \"want to "
                    "rebuild it together?\" {frenemy} reaches for a "
                    "block. They build side by side."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _FEELINGS_KEY,
                "label": "name the feelings",
                "first_try": (
                    "{friend} says, \"don't be sad.\" {frenemy}'s "
                    "shoulders stay tense — being told what to feel "
                    "didn't help."
                ),
                "second_try": (
                    "{friend} waits a beat and tries again: \"you look "
                    "scared. I'd be scared too if my tower fell.\" "
                    "{frenemy}'s shoulders drop."
                ),
                "f_action": "looking",
                "s_action": "thinking",
            },
        ],
        end_text="Repair takes more than one try. They keep playing.",
    ),
    # 2. Forgot to invite — apologize / feelings / offer
    _template(
        id_="repair_forgot_invite",
        title="Forgot to invite a friend",
        rupture_text=(
            "{friend} started a tag game with two other kids and forgot "
            "to invite {frenemy}, who was right there. {frenemy} stands "
            "alone at the edge, watching. What does {friend} do?"
        ),
        rupture_action="looking",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} jogs over: \"sorry, sorry!\" {frenemy} "
                    "shrugs — a fast sorry doesn't tell them what "
                    "{friend} is sorry FOR."
                ),
                "second_try": (
                    "{friend} stops, breathes, and says, \"I'm sorry I "
                    "didn't invite you. I should have looked around "
                    "first.\" {frenemy} nods."
                ),
                "f_action": "running",
                "s_action": "thinking",
            },
            {
                "key": _FEELINGS_KEY,
                "label": "name the feelings",
                "first_try": (
                    "{friend} says, \"it's not a big deal.\" {frenemy} "
                    "looks away — being told their feeling isn't a big "
                    "deal made it bigger."
                ),
                "second_try": (
                    "{friend} tries again: \"you look left out. That "
                    "would hurt me too.\" {frenemy} meets their eyes "
                    "and steps closer."
                ),
                "f_action": "thinking",
                "s_action": "looking",
            },
            {
                "key": _OFFER_KEY,
                "label": "offer something",
                "first_try": (
                    "{friend} waves: \"come play later, okay?\" "
                    "{frenemy} doesn't move — \"later\" feels like "
                    "another way of saying no-thanks."
                ),
                "second_try": (
                    "{friend} walks over and says, \"come play right "
                    "now. You can be It first.\" {frenemy} grins and "
                    "joins the game."
                ),
                "f_action": "waving",
                "s_action": "running",
            },
        ],
        end_text="A late invite is still an invite. They run together.",
    ),
    # 3. Mean about a drawing — apologize / feelings / offer
    _template(
        id_="repair_mean_drawing",
        title="Said something mean about a drawing",
        rupture_text=(
            "{friend} looked at {frenemy}'s drawing and said, \"that "
            "doesn't look like a horse.\" {frenemy}'s face crumples and "
            "they cover the picture. What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} says \"sorry, I didn't mean it!\" but "
                    "{frenemy} keeps the paper covered — \"didn't mean "
                    "it\" doesn't undo the words."
                ),
                "second_try": (
                    "{friend} tries again: \"I'm sorry I said that "
                    "about your drawing. It wasn't kind, and I'm the "
                    "one who was wrong.\" {frenemy} uncovers the paper."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _FEELINGS_KEY,
                "label": "name the feelings",
                "first_try": (
                    "{friend} says, \"don't cry.\" {frenemy}'s shoulders "
                    "stay hunched — being told not to feel something "
                    "made the feeling stick."
                ),
                "second_try": (
                    "{friend} sits down next to them: \"my words hurt "
                    "your feelings. I'd be hurt too.\" {frenemy} wipes "
                    "their eyes and nods."
                ),
                "f_action": "thinking",
                "s_action": "looking",
            },
            {
                "key": _OFFER_KEY,
                "label": "offer something",
                "first_try": (
                    "{friend} says, \"want to draw with me?\" {frenemy} "
                    "shakes their head — being asked to draw right "
                    "after the comment feels like more pressure."
                ),
                "second_try": (
                    "{friend} tries again: \"want me to draw a horse "
                    "next to yours? Yours can be the parent and mine "
                    "the baby.\" {frenemy} slides the paper over."
                ),
                "f_action": "waving",
                "s_action": "pointing",
            },
        ],
        end_text="The drawing now has two horses. They both color it in.",
    ),
    # 4. Laughed at a fall — apologize / feelings / offer
    _template(
        id_="repair_laughed_fall",
        title="Laughed when a friend fell",
        rupture_text=(
            "{frenemy} tripped on the rug and landed on their knees. "
            "{friend} laughed — just for a second, but {frenemy} heard "
            "it and turned red. What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} says, \"sorry, it just slipped out!\" "
                    "{frenemy} looks at the floor — blaming it on "
                    "\"slipping out\" sounds like {friend} won't be "
                    "careful next time."
                ),
                "second_try": (
                    "{friend} kneels next to them: \"I'm sorry I "
                    "laughed when you fell. That wasn't kind. Are you "
                    "okay?\" {frenemy} nods, small."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _FEELINGS_KEY,
                "label": "name the feelings",
                "first_try": (
                    "{friend} says, \"don't be embarrassed, it's "
                    "nothing.\" {frenemy} stays curled up — being told "
                    "their feeling is \"nothing\" made it heavier."
                ),
                "second_try": (
                    "{friend} sits beside them: \"you look "
                    "embarrassed. I'd feel embarrassed too if someone "
                    "laughed at me.\" {frenemy}'s shoulders soften."
                ),
                "f_action": "thinking",
                "s_action": "looking",
            },
            {
                "key": _OFFER_KEY,
                "label": "offer something",
                "first_try": (
                    "{friend} reaches out a hand. {frenemy} doesn't "
                    "take it — being helped up by the kid who laughed "
                    "feels like the wrong order."
                ),
                "second_try": (
                    "{friend} lowers the hand and waits. \"I can sit "
                    "with you until you're ready.\" After a moment, "
                    "{frenemy} takes the hand."
                ),
                "f_action": "pointing",
                "s_action": "waving",
            },
        ],
        end_text="They both stand up. The rug is the only thing still on the floor.",
    ),
    # 5. Took the last marker — apologize / fix / offer
    _template(
        id_="repair_took_marker",
        title="Took the last marker without asking",
        rupture_text=(
            "{friend} grabbed the last blue marker out of the box right "
            "as {frenemy} was reaching for it. {frenemy} pulls their "
            "hand back, eyes wide. What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} mutters, \"sorry.\" {frenemy} stays quiet "
                    "— a quick sorry while still holding the marker "
                    "doesn't change anything."
                ),
                "second_try": (
                    "{friend} sets the marker on the table: \"I'm "
                    "sorry I grabbed the marker. I saw you reaching.\" "
                    "{frenemy} nods."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _FIX_KEY,
                "label": "help fix it",
                "first_try": (
                    "{friend} hands the marker over. {frenemy} takes it "
                    "but their face is still tight — getting it now "
                    "doesn't undo the grab."
                ),
                "second_try": (
                    "{friend} digs through the box: \"let me find you "
                    "another color you like too.\" They pull out a "
                    "teal. {frenemy} brightens."
                ),
                "f_action": "pointing",
                "s_action": "looking",
            },
            {
                "key": _OFFER_KEY,
                "label": "offer something",
                "first_try": (
                    "{friend} offers a different marker — a yellow one. "
                    "{frenemy} shakes their head; they wanted BLUE for "
                    "the sky."
                ),
                "second_try": (
                    "{friend} thinks, then offers a trade: \"I'll use "
                    "yellow on my picture. You take the blue.\" "
                    "{frenemy} accepts."
                ),
                "f_action": "pointing",
                "s_action": "thinking",
            },
        ],
        end_text="One marker, two pictures. The sky is blue.",
    ),
    # 6. Broke a favorite crayon — apologize / fix / offer
    _template(
        id_="repair_broken_crayon",
        title="Broke a favorite crayon",
        rupture_text=(
            "{friend} pressed too hard and snapped {frenemy}'s favorite "
            "purple crayon in half. {frenemy} stares at the two pieces. "
            "What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} says, \"sorry, it was old anyway.\" "
                    "{frenemy} frowns — calling it \"old\" makes it "
                    "sound like the loss doesn't count."
                ),
                "second_try": (
                    "{friend} picks up both halves and tries again: "
                    "\"I'm sorry I broke your purple. I know it was "
                    "your favorite.\" {frenemy}'s frown softens."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _FIX_KEY,
                "label": "help fix it",
                "first_try": (
                    "{friend} grabs the tape and tries to stick the "
                    "two halves together. The tape doesn't hold; the "
                    "crayon won't draw. {frenemy} sighs."
                ),
                "second_try": (
                    "{friend} thinks again: \"two halves means two "
                    "crayons we can both use at once. Want to color "
                    "the same picture together?\" {frenemy} picks up "
                    "one half and slides over."
                ),
                "f_action": "pointing",
                "s_action": "cheering",
            },
            {
                "key": _OFFER_KEY,
                "label": "offer something",
                "first_try": (
                    "{friend} offers their own purple crayon. "
                    "{frenemy} looks at it but doesn't take it — "
                    "a swap doesn't make the favorite one whole."
                ),
                "second_try": (
                    "{friend} sits down: \"you can keep this one, and "
                    "I'll save my next purple for you too.\" {frenemy} "
                    "accepts and tucks it into their tin."
                ),
                "f_action": "pointing",
                "s_action": "waving",
            },
        ],
        end_text="Crayons can break. Friendships are bendier than that.",
    ),
    # 7. Interrupted a pretend-play story — apologize / feelings / offer
    _template(
        id_="repair_interrupted_story",
        title="Interrupted the pretend story",
        rupture_text=(
            "{frenemy} was in the middle of telling a long pretend-play "
            "story about dragons. {friend} cut in to add their own part "
            "and the whole thread got lost. {frenemy} stops talking. "
            "What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} says, \"sorry, keep going!\" {frenemy} "
                    "stays quiet — they don't remember where they were "
                    "anymore."
                ),
                "second_try": (
                    "{friend} tries again: \"I'm sorry I cut in. Your "
                    "story was good. Can you start that part over so I "
                    "can hear it?\" {frenemy} thinks, then begins again."
                ),
                "f_action": "waving",
                "s_action": "pointing",
            },
            {
                "key": _FEELINGS_KEY,
                "label": "name the feelings",
                "first_try": (
                    "{friend} says, \"don't be mad.\" {frenemy} crosses "
                    "their arms — being told not to be mad made them "
                    "feel madder."
                ),
                "second_try": (
                    "{friend} tries again: \"you look frustrated. I'd "
                    "be frustrated too if someone cut me off in the "
                    "middle.\" {frenemy} uncrosses their arms."
                ),
                "f_action": "thinking",
                "s_action": "looking",
            },
            {
                "key": _OFFER_KEY,
                "label": "offer something",
                "first_try": (
                    "{friend} offers, \"want me to be a quiet listener "
                    "now?\" {frenemy} hesitates — the offer sounds "
                    "like {friend} wants this to be over fast."
                ),
                "second_try": (
                    "{friend} sits down cross-legged: \"I'll listen all "
                    "the way to the end. The dragons can take as long "
                    "as they need.\" {frenemy} smiles and continues."
                ),
                "f_action": "pointing",
                "s_action": "thinking",
            },
        ],
        end_text="The dragons get their full story. Both kids cheer at the end.",
    ),
    # 8. Ignored a wave — apologize / feelings / offer
    _template(
        id_="repair_ignored_wave",
        title="Didn't see the wave",
        rupture_text=(
            "{frenemy} waved from across the room and {friend} walked "
            "right past, eyes on the floor. {frenemy}'s hand drops; "
            "they look down. {friend} realizes a minute later. What "
            "does {friend} do?"
        ),
        rupture_action="looking",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} calls from across the room: \"sorry, I "
                    "didn't see you!\" {frenemy} nods but stays sat "
                    "down — a shout-apology from far away doesn't quite "
                    "reach."
                ),
                "second_try": (
                    "{friend} walks over and crouches: \"I'm sorry I "
                    "walked past. I was looking at the floor and didn't "
                    "see your wave.\" {frenemy} smiles a little."
                ),
                "f_action": "waving",
                "s_action": "pointing",
            },
            {
                "key": _FEELINGS_KEY,
                "label": "name the feelings",
                "first_try": (
                    "{friend} says, \"it's no big deal!\" {frenemy} "
                    "shrugs — being told the moment was nothing made it "
                    "feel like THEY were nothing."
                ),
                "second_try": (
                    "{friend} tries again: \"you look ignored. That "
                    "would hurt me too.\" {frenemy} looks up and "
                    "scoots over to make room."
                ),
                "f_action": "thinking",
                "s_action": "looking",
            },
            {
                "key": _OFFER_KEY,
                "label": "offer something",
                "first_try": (
                    "{friend} waves big: \"hi now! hi!\" {frenemy} "
                    "manages a small wave back — the loud catch-up "
                    "wave feels a little late."
                ),
                "second_try": (
                    "{friend} sits down next to them: \"want to do "
                    "something together? Just us, right now.\" "
                    "{frenemy} nods and stands up."
                ),
                "f_action": "waving",
                "s_action": "pointing",
            },
        ],
        end_text="One missed wave doesn't end a friendship. They walk off together.",
    ),
    # 9. Called outfit weird — apologize / feelings / offer
    _template(
        id_="repair_weird_outfit",
        title="Called the outfit weird",
        rupture_text=(
            "{frenemy} showed off their rainbow-striped pants and "
            "polka-dot shirt. {friend} said, \"that's weird.\" "
            "{frenemy}'s smile drops. What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} says, \"sorry, weird is good!\" {frenemy} "
                    "doesn't smile back — \"weird is good\" is still "
                    "weird, and it sounds like a save."
                ),
                "second_try": (
                    "{friend} tries again: \"I'm sorry I said weird. "
                    "That came out wrong. Your outfit is bright and I "
                    "like the stripes.\" {frenemy}'s smile peeks back."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _FEELINGS_KEY,
                "label": "name the feelings",
                "first_try": (
                    "{friend} says, \"don't be sad about your clothes!\" "
                    "{frenemy} looks down — being told not to be sad "
                    "didn't make the sadness leave."
                ),
                "second_try": (
                    "{friend} tries again: \"you look hurt. If someone "
                    "called my favorite outfit weird, I'd feel hurt "
                    "too.\" {frenemy} looks up."
                ),
                "f_action": "thinking",
                "s_action": "looking",
            },
            {
                "key": _OFFER_KEY,
                "label": "offer something",
                "first_try": (
                    "{friend} offers, \"want to wear one of MY shirts?\" "
                    "{frenemy} shakes their head — they liked THIS "
                    "outfit and the offer felt like a swap-out."
                ),
                "second_try": (
                    "{friend} thinks again: \"can I find something "
                    "stripy of mine so we match?\" {frenemy} brightens "
                    "and nods."
                ),
                "f_action": "pointing",
                "s_action": "cheering",
            },
        ],
        end_text="They walk off, matching in stripes. Weird is good after all.",
    ),
    # 10. Team list — apologize / fix / offer
    _template(
        id_="repair_team_list",
        title="Left a name off the team list",
        rupture_text=(
            "{friend} wrote down the team list for the pretend game and "
            "forgot to put {frenemy} on it. {frenemy} reads the list "
            "twice, hoping. What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} says, \"oh, sorry!\" and keeps walking. "
                    "{frenemy} stares at the paper — a sorry-on-the-go "
                    "didn't change the list."
                ),
                "second_try": (
                    "{friend} comes back and looks at the paper: \"I'm "
                    "sorry I left your name off. That was a real "
                    "mistake, not just a typo.\" {frenemy} hands them "
                    "the pen."
                ),
                "f_action": "running",
                "s_action": "pointing",
            },
            {
                "key": _FIX_KEY,
                "label": "help fix it",
                "first_try": (
                    "{friend} scribbles {frenemy}'s name in the margin "
                    "in tiny letters. {frenemy} sees it — squeezed in "
                    "the corner doesn't feel like being on the team."
                ),
                "second_try": (
                    "{friend} flips the paper over and re-writes the "
                    "WHOLE list with {frenemy}'s name first this time. "
                    "{frenemy} smiles big."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _OFFER_KEY,
                "label": "offer something",
                "first_try": (
                    "{friend} offers, \"you can be our cheerleader.\" "
                    "{frenemy} shakes their head — they wanted to "
                    "PLAY, not cheer."
                ),
                "second_try": (
                    "{friend} thinks: \"you can be on my team and we "
                    "both go first.\" {frenemy} grins and joins the "
                    "lineup."
                ),
                "f_action": "waving",
                "s_action": "cheering",
            },
        ],
        end_text="A team list with everyone on it. The game begins.",
    ),
    # 11. Stepped on puzzle piece — apologize / fix / feelings
    _template(
        id_="repair_stepped_puzzle",
        title="Stepped on a puzzle piece",
        rupture_text=(
            "{friend} walked across the rug and stepped right on "
            "{frenemy}'s last puzzle piece. It bent. {frenemy} holds "
            "up the crinkled piece. What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} says, \"sorry, didn't see it.\" {frenemy} "
                    "doesn't look up — \"didn't see\" doesn't fix the "
                    "bent piece."
                ),
                "second_try": (
                    "{friend} kneels down: \"I'm sorry I stepped on "
                    "your piece. I should have looked where I was "
                    "walking around your puzzle.\" {frenemy} hands "
                    "them the piece."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _FIX_KEY,
                "label": "help fix it",
                "first_try": (
                    "{friend} tries to flatten the piece against the "
                    "floor. It stays bent. {frenemy} sighs."
                ),
                "second_try": (
                    "{friend} tries again: \"let me press it under a "
                    "book for a few minutes — that fixed mine once.\" "
                    "They slide the piece under a board book together."
                ),
                "f_action": "pointing",
                "s_action": "cheering",
            },
            {
                "key": _FEELINGS_KEY,
                "label": "name the feelings",
                "first_try": (
                    "{friend} says, \"don't be upset over a puzzle.\" "
                    "{frenemy}'s shoulders go tight — telling them the "
                    "feeling is too big made it bigger."
                ),
                "second_try": (
                    "{friend} sits down: \"you look frustrated. I'd be "
                    "frustrated too if someone bent my last piece.\" "
                    "{frenemy}'s shoulders loosen."
                ),
                "f_action": "thinking",
                "s_action": "looking",
            },
        ],
        end_text="A bent piece still fits. The puzzle finishes.",
    ),
    # 12. Skipped a turn — apologize / fix / offer
    _template(
        id_="repair_skipped_turn",
        title="Skipped a turn in the game",
        rupture_text=(
            "{friend} was so excited that they took two turns in a row "
            "and skipped {frenemy} completely. {frenemy} sets down "
            "their game piece. What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} says, \"sorry, my bad!\" and rolls again. "
                    "{frenemy} watches — saying sorry while taking ANOTHER "
                    "turn doesn't count."
                ),
                "second_try": (
                    "{friend} puts the dice down: \"I'm sorry I skipped "
                    "your turn. I got carried away. Your turn now — "
                    "twice, to make it even.\" {frenemy} picks up the "
                    "dice."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _FIX_KEY,
                "label": "help fix it",
                "first_try": (
                    "{friend} undoes their second move on the board. "
                    "{frenemy} watches but their face stays still — "
                    "undoing the move doesn't undo the missed turn."
                ),
                "second_try": (
                    "{friend} also slides {frenemy}'s piece forward to "
                    "where it would have been, and offers an extra "
                    "roll: \"your turn now, plus the one I missed.\" "
                    "{frenemy} nods."
                ),
                "f_action": "pointing",
                "s_action": "thinking",
            },
            {
                "key": _OFFER_KEY,
                "label": "offer something",
                "first_try": (
                    "{friend} offers a swap: \"you go first next "
                    "round.\" {frenemy} doesn't reach for the dice — "
                    "\"next round\" still feels like waiting."
                ),
                "second_try": (
                    "{friend} thinks again: \"actually, you go right "
                    "now and I'll skip MY next turn to make it even.\" "
                    "{frenemy} smiles and rolls."
                ),
                "f_action": "waving",
                "s_action": "cheering",
            },
        ],
        end_text="The game keeps going. Both kids have the same number of turns now.",
    ),
    # 13. Closed the book early — apologize / fix / feelings
    _template(
        id_="repair_closed_book",
        title="Closed the book before they were done",
        rupture_text=(
            "{friend} closed the picture book while {frenemy} was still "
            "looking at the last page. {frenemy}'s finger was still on "
            "the page. What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} says, \"sorry, didn't know!\" {frenemy} "
                    "stays quiet — they had been pointing at the page "
                    "for a while."
                ),
                "second_try": (
                    "{friend} opens the book again: \"I'm sorry I "
                    "closed it. I should have checked if you were "
                    "still looking.\" {frenemy} points back to the page."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _FIX_KEY,
                "label": "help fix it",
                "first_try": (
                    "{friend} flips back to the page and pushes the "
                    "book toward {frenemy}. {frenemy} looks at it but "
                    "their finger doesn't go back down — the moment "
                    "got interrupted."
                ),
                "second_try": (
                    "{friend} sits closer and asks, \"what were you "
                    "looking at? Show me.\" {frenemy} points to a "
                    "tiny mouse in the corner of the picture. They "
                    "study it together."
                ),
                "f_action": "pointing",
                "s_action": "looking",
            },
            {
                "key": _FEELINGS_KEY,
                "label": "name the feelings",
                "first_try": (
                    "{friend} says, \"don't be upset, it's just a "
                    "book.\" {frenemy} pulls their hand into their lap "
                    "— calling the book \"just\" made the feeling "
                    "smaller and bigger at once."
                ),
                "second_try": (
                    "{friend} tries again: \"you look interrupted. I'd "
                    "be frustrated too if someone closed my book.\" "
                    "{frenemy} nods."
                ),
                "f_action": "thinking",
                "s_action": "looking",
            },
        ],
        end_text="The book opens again. They find the mouse on every page now.",
    ),
    # 14. Hid the teddy bear — apologize / fix / feelings
    _template(
        id_="repair_hid_teddy",
        title="Hid the teddy bear as a joke",
        rupture_text=(
            "{friend} hid {frenemy}'s teddy bear behind the curtain as "
            "a joke. {frenemy} got scared and started looking "
            "everywhere. The joke didn't land. What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} pulls the teddy out: \"haha, sorry, "
                    "joke!\" {frenemy} grabs the teddy and holds it "
                    "tight — calling it a joke makes the scare feel "
                    "less serious to {friend}, but not to {frenemy}."
                ),
                "second_try": (
                    "{friend} sits down: \"I'm sorry I hid Teddy. I "
                    "thought it would be funny but it scared you. I "
                    "won't hide your stuff again.\" {frenemy} hugs "
                    "the bear and nods."
                ),
                "f_action": "pointing",
                "s_action": "thinking",
            },
            {
                "key": _FIX_KEY,
                "label": "help fix it",
                "first_try": (
                    "{friend} hands the teddy back. {frenemy} takes it "
                    "but won't put it down — getting it back doesn't "
                    "undo the lost-feeling."
                ),
                "second_try": (
                    "{friend} thinks: \"let's make Teddy a safe spot "
                    "where I PROMISE I won't move them.\" They build a "
                    "small pillow nest together."
                ),
                "f_action": "pointing",
                "s_action": "cheering",
            },
            {
                "key": _FEELINGS_KEY,
                "label": "name the feelings",
                "first_try": (
                    "{friend} says, \"it was just a joke!\" {frenemy} "
                    "squeezes the teddy harder — \"just a joke\" makes "
                    "their scared-feeling feel wrong."
                ),
                "second_try": (
                    "{friend} tries again: \"you got really scared. I'd "
                    "be scared too if my favorite went missing.\" "
                    "{frenemy} nods, eyes wet but soft."
                ),
                "f_action": "thinking",
                "s_action": "looking",
            },
        ],
        end_text="Teddy gets a nest. Jokes work better when both kids laugh.",
    ),
    # 15. Played a loud song after asking not to — apologize / feelings / offer
    _template(
        id_="repair_loud_song",
        title="Kept playing the song that was too loud",
        rupture_text=(
            "{frenemy} said the song was too loud for their ears, but "
            "{friend} kept playing it. {frenemy} covered their ears "
            "and turned away. What does {friend} do?"
        ),
        rupture_action="confused",
        forks=[
            {
                "key": _APOLOGIZE_KEY,
                "label": "say sorry",
                "first_try": (
                    "{friend} pauses the music: \"sorry, I forgot.\" "
                    "{frenemy} keeps their hands near their ears — "
                    "\"forgot\" sounds like it could happen again."
                ),
                "second_try": (
                    "{friend} tries again: \"I'm sorry I kept playing "
                    "it after you said it was too loud. I should have "
                    "listened the first time.\" {frenemy} lowers their "
                    "hands."
                ),
                "f_action": "thinking",
                "s_action": "pointing",
            },
            {
                "key": _FEELINGS_KEY,
                "label": "name the feelings",
                "first_try": (
                    "{friend} says, \"don't be upset, it's a fun song.\" "
                    "{frenemy} stays turned away — being told the song "
                    "is fun doesn't change that it was too loud."
                ),
                "second_try": (
                    "{friend} tries again: \"your ears were "
                    "overwhelmed. Loud sounds hurt mine too sometimes.\" "
                    "{frenemy} turns back."
                ),
                "f_action": "thinking",
                "s_action": "looking",
            },
            {
                "key": _OFFER_KEY,
                "label": "offer something",
                "first_try": (
                    "{friend} offers, \"I'll skip this song.\" "
                    "{frenemy} shrugs — skipping it sounds like the "
                    "song just won today, not like {friend} understood."
                ),
                "second_try": (
                    "{friend} thinks again: \"you pick the volume. I'll "
                    "wait until you say it's good.\" {frenemy} turns "
                    "the dial down themselves and presses play."
                ),
                "f_action": "pointing",
                "s_action": "waving",
            },
        ],
        end_text="A quieter song works for both of them. They dance, ears happy.",
    ),
]


# ---------------------------------------------------------------------
# Load / strip / write helpers (mirror M11)
# ---------------------------------------------------------------------


def _load_existing(path: Path, *, expected_intent: str) -> dict[str, Any]:
    """Read the existing intent file. Refuses to overwrite a structurally
    broken file — mirrors ``generate_conflict_resolution_templates._load_existing``."""
    if not path.exists():
        raise FileNotFoundError(
            f"output file {path} does not exist; cannot append friendship/repair "
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


def _strip_repair_entries(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the input list with every M12 repair_* entry removed. Idempotence."""
    return [t for t in templates if not str(t.get("id", "")).startswith(_TEMPLATE_PREFIX)]


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    """Persist with the same indent + trailing newline shape as M11."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _validate_post_write(
    path: Path, *, intent: str, expected_repair: int
) -> None:
    """Re-load via the production loader and assert all M12 templates loaded.

    Mirrors ``generate_conflict_resolution_templates._validate_post_write``
    so the same diagnostic surfaces on failure (whole intent file dropped
    on schema failure → delta indicates the offending id range).
    """
    from toybox.activities.generator import (  # type: ignore[import-untyped]
        _load_intent_templates,
        clear_template_cache,
    )

    clear_template_cache()
    templates = _load_intent_templates(intent)
    loaded_repair = [t for t in templates if t.id.startswith(_TEMPLATE_PREFIX)]
    if len(loaded_repair) != expected_repair:
        raise SystemExit(
            f"--validate: expected {expected_repair} repair_* templates "
            f"to load from {intent}, got {len(loaded_repair)}. The whole "
            f"intent file may have been dropped on schema failure; check "
            f"{path} and re-run."
        )
    _logger.info(
        "--validate (%s): %d repair_* templates loaded cleanly via "
        "toybox.activities.generator._load_intent_templates",
        intent,
        len(loaded_repair),
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append ~15 hand-authored friendship/repair SEL templates "
            "to request_play.json. Phase M Step M12."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the merged JSON to stdout and exit; do not write.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Idempotent regeneration is always-on (existing repair_* entries "
            "are stripped before appending); this flag just tags the run in the log."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "After writing, re-load the intent via the production template loader "
            "and assert all repair_* templates load cleanly."
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
    """Strip existing repair_* entries and append the new batch for one intent.

    Returns the merged payload (the same one written to disk, modulo
    dry-run). Logs a per-intent summary line matching the M11 convention.
    """
    payload = _load_existing(path, expected_intent=intent)
    existing_templates: list[dict[str, Any]] = list(payload["templates"])
    pre_count = len(existing_templates)

    stripped = _strip_repair_entries(existing_templates)
    stripped_count = pre_count - len(stripped)

    merged = stripped + new_templates
    payload["templates"] = merged
    post_count = len(merged)
    _logger.info(
        "summary (%s): pre=%d, removed_existing_repair=%d, generated=%d, post=%d, force=%s",
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
                path, intent=intent, expected_repair=len(new_templates)
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

    if args.dry_run:
        sys.stdout.write(
            json.dumps(play_payload, indent=2, ensure_ascii=False)
        )
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
