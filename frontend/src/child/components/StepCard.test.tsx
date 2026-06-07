// Phase F Step F7 — StepCard layout coverage. Asserts the sprite-
// branch logic: the new ``ToyActionSprite`` renders to the LEFT of
// the body text iff the current step has ``action_slot`` set AND the
// activity has a non-empty ``toy_ids``. Otherwise the kiosk renders
// the same body-only layout it shipped with before F7.
//
// Persona-avatar layout is asserted at the App level — StepCard is a
// child of App, and the avatar lives outside StepCard, so the layout
// regression covers ``<StepCard>`` rendering separately from the
// avatar element. (See ``App.test.tsx`` for the kiosk-level shape.)

import type { JSX } from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { currentStepHasElement } from "../App";
import type { Activity, ActivityStep } from "../api";
import { PersonaAvatar } from "./PersonaAvatar";
import { StepCard } from "./StepCard";

// jsdom shim for HTMLMediaElement.play / .pause — needed by the K12
// SongPlayer mount path. Without these the autoplay useEffect throws
// in jsdom (TypeError: el.play is not a function) and the test
// renderer surfaces a hard failure. Re-installed in beforeEach so a
// previous test's vi.clearAllMocks() doesn't leave them stale.
beforeEach(() => {
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    writable: true,
    value: function play(): Promise<void> {
      return Promise.resolve();
    },
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true,
    writable: true,
    value: function pause(): void {
      // jsdom stub — see beforeEach docstring.
    },
  });
});

// Phase K K9: ReadMeButton + ClickableText consume the TTS substrate.
// Mock the substrate so render assertions in this file don't require a
// fake speechSynthesis (substrate has its own coverage in tts.test.ts).
vi.mock("../tts", async () => {
  return {
    speak: vi.fn(async () => undefined),
    cancel: vi.fn(),
  };
});

afterEach(() => {
  cleanup();
});

function fakeStep(overrides: Partial<ActivityStep> = {}): ActivityStep {
  return {
    seq: 1,
    body: "Pretend to be a cat",
    sfx: null,
    expected_action: null,
    current: true,
    action_slot: null,
    ...overrides,
  };
}

function fakeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    id: "act-1",
    state: "running",
    version: 2,
    title: "Cat play",
    summary: null,
    persona_id: null,
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-05-02T10:00:00Z",
    started_at: null,
    ended_at: null,
    steps: [fakeStep()],
    metadata: {},
    ...overrides,
  };
}

describe("StepCard sprite branch", () => {
  it("renders the sprite when the current step has action_slot AND activity has toy_ids", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(sprite.dataset["slot"]).toBe("looking");
    expect(sprite.dataset["toyId"]).toBe("toy-abc");
    // Phase V: initial src is .png during the CSS intro animation phase.
    // Format transitions to .webp only after animationend fires for idle slot.
    expect(sprite.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-abc/looking.png",
    );
  });

  it("renders the sprite with 'idle' fallback when action_slot is null but a toy is present", () => {
    // Post-UAT change: the cast renders on every step (operator: "all
    // toys along for the ride"). Steps without an explicit action_slot
    // default to "idle" so the cast still surfaces.
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: null })],
    });
    render(<StepCard activity={activity} />);
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(sprite.dataset["slot"]).toBe("idle");
    expect(sprite.dataset["toyId"]).toBe("toy-abc");
    // Body text still renders.
    expect(screen.getByTestId("step-text").textContent).toBe(
      "Pretend to be a cat",
    );
  });

  it("hides the sprite when toy_ids is empty", () => {
    const activity = fakeActivity({
      toy_ids: [],
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    expect(screen.queryByTestId("toy-action-sprite")).toBeNull();
  });

  it("hides the sprite when toy_ids is omitted from the wire payload", () => {
    // Pre-F7 / pre-codegen wire shape: backend response omits
    // ``toy_ids`` entirely. The kiosk must treat that the same as
    // an empty array (graceful fallback to the body-only layout).
    const activity = fakeActivity({
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    expect(screen.queryByTestId("toy-action-sprite")).toBeNull();
  });

  it("uses the FIRST toy_id deterministically when multiple are present", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-first", "toy-second", "toy-third"],
      steps: [fakeStep({ action_slot: "jumping" })],
    });
    render(<StepCard activity={activity} />);
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(sprite.dataset["toyId"]).toBe("toy-first");
  });

  it("passes the toy display name through to the sprite when metadata.toys is hydrated", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: "looking" })],
      metadata: {
        toys: [
          { id: "toy-abc", display_name: "Mr. Unicorn" },
          { id: "toy-xyz", display_name: "Other Toy" },
        ],
      },
    });
    render(<StepCard activity={activity} />);
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // alt = "<display_name> <slot>" per F7's a11y contract.
    expect(sprite.alt).toBe("Mr. Unicorn looking");
  });

  it("falls back to bare slot for alt when metadata.toys does not match", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: "idle" })],
      metadata: {
        // Different toy id — the kiosk can't resolve a display name
        // for ``toy-abc``, so the sprite alt falls back to the slot.
        toys: [{ id: "toy-other", display_name: "Some Other Toy" }],
      },
    });
    render(<StepCard activity={activity} />);
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(sprite.alt).toBe("idle");
  });

  it("places the sprite alongside the body text in the row layout", () => {
    // Post-UAT multi-toy refactor: sprite lands in a per-side column
    // (data-testid="step-cast-left" or "step-cast-right") flanking the
    // body text. The side is deterministic per toy_id hash so the kiosk
    // is stable across re-renders.
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    const row = screen.getByTestId("step-body-row");
    const children = Array.from(row.children);
    // Row has 2 children: one side-column + body text.
    expect(children).toHaveLength(2);
    const textIdx = children.findIndex(
      (c) => c.getAttribute("data-testid") === "step-text",
    );
    expect(textIdx).toBeGreaterThanOrEqual(0);
    const spriteCol = children[textIdx === 0 ? 1 : 0];
    expect(
      spriteCol?.getAttribute("data-testid") === "step-cast-left" ||
        spriteCol?.getAttribute("data-testid") === "step-cast-right",
    ).toBe(true);
    expect(spriteCol?.querySelector("[data-testid='toy-action-sprite']")).not.toBeNull();
  });

  it("renders only the body text in the row when the sprite is hidden", () => {
    const activity = fakeActivity({
      toy_ids: [],
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    const row = screen.getByTestId("step-body-row");
    const children = Array.from(row.children);
    expect(children).toHaveLength(1);
    expect(children[0]?.getAttribute("data-testid")).toBe("step-text");
  });
});

describe("StepCard multi-toy cast (post-UAT)", () => {
  it("renders every always-visible role's sprite on the step card", () => {
    // Three friend/guide-type roles: all should render regardless of
    // whether the step body names them.
    const activity = fakeActivity({
      toy_ids: ["toy-friend"],
      roles: {
        friend: {
          role_name: "friend",
          toy_id: "toy-friend",
          generic_descriptor: null,
          display_name: "Captain Bear",
        },
        guide_mentor: {
          role_name: "guide_mentor",
          toy_id: "toy-guide",
          generic_descriptor: null,
          display_name: "Wise Owl",
        },
        sidekick: {
          role_name: "sidekick",
          toy_id: "toy-sidekick",
          generic_descriptor: null,
          display_name: "Sparkle Cat",
        },
      },
      steps: [
        fakeStep({
          action_slot: "looking",
          // Step body names only Captain Bear; the other two still
          // should render because they're always-visible roles.
          body: "Captain Bear waves hello.",
        }),
      ],
    });
    render(<StepCard activity={activity} />);
    const sprites = screen.getAllByTestId("toy-action-sprite");
    const toyIds = sprites.map((el) => el.getAttribute("data-toy-id"));
    expect(toyIds).toContain("toy-friend");
    expect(toyIds).toContain("toy-guide");
    expect(toyIds).toContain("toy-sidekick");
    expect(sprites).toHaveLength(3);
  });

  it("hides boss/antagonist roles unless named in the current step body", () => {
    const baseActivity = fakeActivity({
      toy_ids: ["toy-friend"],
      roles: {
        friend: {
          role_name: "friend",
          toy_id: "toy-friend",
          generic_descriptor: null,
          display_name: "Captain Bear",
        },
        big_bad_boss: {
          role_name: "big_bad_boss",
          toy_id: "toy-boss",
          generic_descriptor: null,
          display_name: "Bowser",
        },
      },
    });

    // Step does NOT name Bowser → boss hidden.
    const { unmount } = render(
      <StepCard
        activity={{
          ...baseActivity,
          steps: [fakeStep({ action_slot: "looking", body: "Captain Bear walks." })],
        }}
      />,
    );
    const beforeIds = screen
      .getAllByTestId("toy-action-sprite")
      .map((el) => el.getAttribute("data-toy-id"));
    expect(beforeIds).toContain("toy-friend");
    expect(beforeIds).not.toContain("toy-boss");
    unmount();

    // Step names Bowser → boss appears.
    render(
      <StepCard
        activity={{
          ...baseActivity,
          steps: [
            fakeStep({
              action_slot: "looking",
              body: "Bowser roars at Captain Bear.",
            }),
          ],
        }}
      />,
    );
    const afterIds = screen
      .getAllByTestId("toy-action-sprite")
      .map((el) => el.getAttribute("data-toy-id"));
    expect(afterIds).toContain("toy-friend");
    expect(afterIds).toContain("toy-boss");
  });

  it("splits the cast deterministically into left/right side columns by toy_id", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-a"],
      roles: {
        friend: {
          role_name: "friend",
          toy_id: "toy-a",
          generic_descriptor: null,
          display_name: "A",
        },
        sidekick: {
          role_name: "sidekick",
          toy_id: "toy-b",
          generic_descriptor: null,
          display_name: "B",
        },
        guide_mentor: {
          role_name: "guide_mentor",
          toy_id: "toy-c",
          generic_descriptor: null,
          display_name: "C",
        },
      },
      steps: [fakeStep({ action_slot: "looking", body: "All three are here." })],
    });
    const { rerender } = render(<StepCard activity={activity} />);
    const sprites1 = screen.getAllByTestId("toy-action-sprite");
    const sidesAndIds1 = sprites1.map((el) => {
      const id = el.getAttribute("data-toy-id");
      const parent = el.closest(
        "[data-testid='step-cast-left'], [data-testid='step-cast-right']",
      );
      return { id, side: parent?.getAttribute("data-testid") };
    });

    // Re-render and confirm same side assignment per id (deterministic).
    rerender(<StepCard activity={activity} />);
    const sprites2 = screen.getAllByTestId("toy-action-sprite");
    const sidesAndIds2 = sprites2.map((el) => {
      const id = el.getAttribute("data-toy-id");
      const parent = el.closest(
        "[data-testid='step-cast-left'], [data-testid='step-cast-right']",
      );
      return { id, side: parent?.getAttribute("data-testid") };
    });
    // Sort by id so the comparison ignores DOM order, only side per id matters.
    const sort = (arr: typeof sidesAndIds1) =>
      [...arr].sort((a, b) => (a.id ?? "").localeCompare(b.id ?? ""));
    expect(sort(sidesAndIds1)).toEqual(sort(sidesAndIds2));
  });

  it("uses 'idle' for sprites when step action_slot is null but roles exist", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-friend"],
      roles: {
        friend: {
          role_name: "friend",
          toy_id: "toy-friend",
          generic_descriptor: null,
          display_name: "Captain Bear",
        },
      },
      steps: [fakeStep({ action_slot: null, body: "Captain Bear stands still." })],
    });
    render(<StepCard activity={activity} />);
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(sprite.dataset["slot"]).toBe("idle");
    expect(sprite.dataset["toyId"]).toBe("toy-friend");
  });

  it("falls back to single-sprite layout when activity has no roles map (pre-K)", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-legacy"],
      // roles intentionally omitted to simulate pre-K wire payload.
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    const sprites = screen.getAllByTestId("toy-action-sprite");
    expect(sprites).toHaveLength(1);
    expect(sprites[0]?.getAttribute("data-toy-id")).toBe("toy-legacy");
  });
});

// Phase G G4: branching render path. StepCard is now responsible for
// the bottom action button row; it renders ``<NextStepButton>`` on
// linear steps and a vertical stack of ``<ChoiceButton>``s when the
// current step has ``choices``.

describe("StepCard branching action row", () => {
  it.each<{ name: string; choices: ActivityStep["choices"] }>([
    { name: "omitted", choices: undefined },
    { name: "null", choices: null },
    { name: "empty array", choices: [] },
  ])(
    "renders NextStepButton when step.choices is falsy or empty ($name)",
    ({ choices }) => {
      // Backend emits ``null`` on linear steps, but pre-G3 wire shapes
      // omit the field and a defensive guard treats ``[]`` the same.
      // All three render paths must collapse to the linear NextStepButton.
      const stepOverrides =
        choices === undefined ? {} : ({ choices } as Partial<ActivityStep>);
      const activity = fakeActivity({
        steps: [fakeStep(stepOverrides)],
      });
      render(
        <StepCard
          activity={activity}
          onAdvance={vi.fn()}
          onChoose={vi.fn()}
          advanceBusy={false}
        />,
      );
      expect(screen.getByTestId("next-step-button")).not.toBeNull();
      expect(screen.queryByTestId("choice-button-stack")).toBeNull();
      expect(screen.queryAllByTestId("choice-button")).toHaveLength(0);
    },
  );

  it("renders N ChoiceButtons when step.choices has N entries", () => {
    const activity = fakeActivity({
      steps: [
        fakeStep({
          choices: [
            { label: "Sneak past Penguin", choice_index: 0 },
            { label: "Charge in bravely", choice_index: 1 },
            { label: "Run away laughing", choice_index: 2 },
          ],
        }),
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={vi.fn()}
        onChoose={vi.fn()}
        advanceBusy={false}
      />,
    );
    // No NextStepButton on the choice path.
    expect(screen.queryByTestId("next-step-button")).toBeNull();
    // Three ChoiceButtons in the stack.
    const buttons = screen.getAllByTestId("choice-button");
    expect(buttons).toHaveLength(3);
    expect(screen.getByTestId("choice-button-stack")).not.toBeNull();
  });

  it("passes the correct label and choiceIndex to each ChoiceButton", () => {
    const activity = fakeActivity({
      steps: [
        fakeStep({
          choices: [
            { label: "Path A", choice_index: 0 },
            { label: "Path B", choice_index: 1 },
          ],
        }),
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={vi.fn()}
        onChoose={vi.fn()}
        advanceBusy={false}
      />,
    );
    const buttons = screen.getAllByTestId("choice-button");
    expect(buttons[0]?.dataset["choiceIndex"]).toBe("0");
    expect(buttons[0]?.textContent).toContain("Path A");
    expect(buttons[1]?.dataset["choiceIndex"]).toBe("1");
    expect(buttons[1]?.textContent).toContain("Path B");
  });

  it("uses the CURRENT step's choices, not steps[0]'s choices", () => {
    // When seq=2 is current and seq=1 is in history, the kiosk must
    // render the choices on the current step (seq=2), not on the
    // first step in the array. Today's "preview the first step when
    // none are current" behavior should NOT extend to choice rendering.
    const activity = fakeActivity({
      steps: [
        fakeStep({
          seq: 1,
          current: false,
          // Past step — its choices were already chosen, kiosk should
          // not re-render buttons for it.
          choices: [
            { label: "Old A", choice_index: 0 },
            { label: "Old B", choice_index: 1 },
          ],
        }),
        fakeStep({
          seq: 2,
          current: true,
          body: "Now what?",
          choices: [
            { label: "New A", choice_index: 0 },
            { label: "New B", choice_index: 1 },
            { label: "New C", choice_index: 2 },
          ],
        }),
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={vi.fn()}
        onChoose={vi.fn()}
        advanceBusy={false}
      />,
    );
    const buttons = screen.getAllByTestId("choice-button");
    // Three buttons (the current step's choices), not two.
    expect(buttons).toHaveLength(3);
    expect(buttons[0]?.textContent).toContain("New A");
  });

  it("disables ALL ChoiceButtons when choosingIndex is set (sibling lock-out)", () => {
    // Phase G G4 sibling double-tap fix: when the App passes
    // ``choosingIndex`` (one button has a POST in flight), every
    // ChoiceButton — including the in-flight one and its siblings —
    // renders disabled so a kid tapping "Choice A" then "Choice B"
    // can't fire two competing POSTs (the second would 409 on
    // version, and the FIRST tap would win — opposite of UX
    // expectation). The disable signal is uniform across the stack.
    const activity = fakeActivity({
      steps: [
        fakeStep({
          choices: [
            { label: "A", choice_index: 0 },
            { label: "B", choice_index: 1 },
            { label: "C", choice_index: 2 },
          ],
        }),
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={vi.fn()}
        // Mock onChoose that never resolves — represents an in-flight
        // POST. The actual call doesn't matter for this test; we only
        // assert that prop drilling lights up the disabled state on
        // every sibling.
        onChoose={vi.fn(() => new Promise(() => {}))}
        advanceBusy={false}
        choosingIndex={0}
      />,
    );
    const buttons = screen.getAllByTestId(
      "choice-button",
    ) as HTMLButtonElement[];
    expect(buttons).toHaveLength(3);
    // ALL buttons disabled — including the in-flight one (index 0).
    // App-level gating is uniform, the in-flight button's own ``busy``
    // state covers the spinner; ``disabled`` covers the click gate.
    for (const btn of buttons) {
      expect(btn.disabled).toBe(true);
    }
  });

  it("drops the 'of N' progress denominator (Phase G — variable step count)", () => {
    // Phase G G4 explicitly drops the "of N" suffix because the total
    // step count is no longer a meaningful target on a branched
    // playthrough. Asserting the absence here so a future regression
    // (e.g. someone reverting the StepCard change) is caught.
    // Body text is set explicitly so the "of" check below isn't
    // contaminated by the default fixture body containing the word.
    const activity = fakeActivity({
      steps: [
        fakeStep({ seq: 1, current: false, body: "first" }),
        fakeStep({ seq: 2, current: true, body: "second" }),
      ],
    });
    render(<StepCard activity={activity} />);
    // The step-card section's progress hint is the immediate text node;
    // it should read "Step 2" with no "of N" trailing.
    const card = screen.getByTestId("step-card");
    // Match "Step 2" not followed by "of" anywhere in the card text.
    expect(card.textContent).toContain("Step 2");
    expect(card.textContent).not.toMatch(/Step\s+\d+\s+of/);
  });
});

// Phase K K9: ReadMeButton mounting rules + clickable-words flag
// threading. The kiosk's K9 affordance is gated per-flag; the
// StepCard is the chokepoint that selects which step kinds get a
// Read Me button and threads the words flag into ChoiceButton.

describe("StepCard K9 — ReadMeButton mounting", () => {
  it("mounts ReadMeButton when readMeButtonEnabled=true and kind is implicit text", () => {
    // No ``kind`` on the wire defaults to "text" per the K9 contract
    // (K12 lands the new kinds; pre-K12 wire payloads stay valid).
    const activity = fakeActivity({
      steps: [fakeStep({ body: "Look at the stars." })],
    });
    render(
      <StepCard
        activity={activity}
        readMeButtonEnabled={true}
        clickableWordsEnabled={false}
      />,
    );
    expect(screen.getByTestId("read-me-button")).not.toBeNull();
  });

  it("does NOT mount ReadMeButton when readMeButtonEnabled=false", () => {
    const activity = fakeActivity({
      steps: [fakeStep({ body: "Look at the stars." })],
    });
    render(
      <StepCard
        activity={activity}
        readMeButtonEnabled={false}
        clickableWordsEnabled={false}
      />,
    );
    expect(screen.queryByTestId("read-me-button")).toBeNull();
  });

  it.each(["text", "fork", "joke"])(
    "mounts ReadMeButton for step.kind=%s with position:fixed (#137)",
    (kind) => {
      // ``kind`` is read defensively off the previewStep object; the
      // fake spreads it into the step body. The cast to ActivityStep
      // narrows past the not-yet-declared field.
      //
      // #137: assert ``position: fixed`` (viewport-anchored) across
      // every step kind, including ``fork`` whose choice-button stack
      // would have drifted the button mid-screen under the old
      // ``position: absolute`` contract. Joke steps use the inline
      // JOKE_READ_ME_STYLE in StepCard.tsx, not the K9 ReadMeButton
      // component, so this cross-kind assertion catches drift in
      // either spot.
      const fork: ActivityStep["choices"] = kind === "fork"
        ? [
            { label: "A", choice_index: 0 },
            { label: "B", choice_index: 1 },
          ]
        : null;
      const activity = fakeActivity({
        steps: [
          {
            ...fakeStep({ body: "x" }),
            kind,
            ...(fork !== null ? { choices: fork } : {}),
          } as ActivityStep,
        ],
      });
      render(
        <StepCard
          activity={activity}
          readMeButtonEnabled={true}
        />,
      );
      const btn = screen.getByTestId("read-me-button") as HTMLButtonElement;
      expect(btn.style.position).toBe("fixed");
      expect(btn.style.bottom).toBe("16px");
      expect(btn.style.left).toBe("16px");
    },
  );

  it("does NOT mount ReadMeButton for step.kind=song (audio surface owned by K12)", () => {
    const activity = fakeActivity({
      steps: [
        { ...fakeStep({ body: "x" }), kind: "song" } as ActivityStep,
      ],
    });
    render(
      <StepCard
        activity={activity}
        readMeButtonEnabled={true}
      />,
    );
    expect(screen.queryByTestId("read-me-button")).toBeNull();
  });

  it("keeps position:relative on the step-card container (post-#137 invariant)", () => {
    // #137 moved both Read Me variants to ``position: fixed`` (viewport-
    // anchored), so the section's ``position: relative`` is no longer
    // load-bearing for ReadMeButton's pinning. It's retained as a
    // harmless stacking-context isolator. This test guards against
    // accidental removal — if a refactor drops it AND a new consumer
    // re-introduces an absolute-positioned descendant, the same
    // mid-screen-drift class of bug returns.
    const activity = fakeActivity({
      steps: [fakeStep({ body: "Look at the stars." })],
    });
    render(
      <StepCard activity={activity} readMeButtonEnabled={true} />,
    );
    const card = screen.getByTestId("step-card") as HTMLElement;
    expect(card.style.position).toBe("relative");
  });
});

describe("StepCard K9 — ClickableText threading", () => {
  it("wraps the step body in ClickableText when clickableWordsEnabled=true", () => {
    const activity = fakeActivity({
      steps: [fakeStep({ body: "Pretend to be a cat" })],
    });
    render(
      <StepCard activity={activity} clickableWordsEnabled={true} />,
    );
    const wrapper = screen.getByTestId("clickable-text");
    expect(wrapper.getAttribute("data-clickable")).toBe("true");
    expect(wrapper.textContent).toBe("Pretend to be a cat");
    // 5 word spans (one per non-whitespace token).
    expect(screen.getAllByTestId("clickable-word")).toHaveLength(5);
  });

  it("renders plain text (data-clickable=false) when clickableWordsEnabled=false", () => {
    const activity = fakeActivity({
      steps: [fakeStep({ body: "Pretend to be a cat" })],
    });
    render(
      <StepCard activity={activity} clickableWordsEnabled={false} />,
    );
    const wrapper = screen.getByTestId("clickable-text");
    expect(wrapper.getAttribute("data-clickable")).toBe("false");
    expect(wrapper.textContent).toBe("Pretend to be a cat");
    expect(screen.queryAllByTestId("clickable-word")).toHaveLength(0);
  });

  it("threads clickable-words flag through to ChoiceButton labels", () => {
    const activity = fakeActivity({
      steps: [
        fakeStep({
          choices: [
            { label: "Sneak past Penguin", choice_index: 0 },
            { label: "Charge in bravely", choice_index: 1 },
          ],
        }),
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={vi.fn()}
        onChoose={vi.fn()}
        advanceBusy={false}
        clickableWordsEnabled={true}
      />,
    );
    // Each choice label becomes a ClickableText. We expect both the
    // step body's ClickableText AND each choice's ClickableText (3
    // wrappers total: 1 body + 2 choices).
    const wrappers = screen.getAllByTestId("clickable-text");
    expect(wrappers.length).toBeGreaterThanOrEqual(3);
    // The choice labels' words are tappable too.
    const wordTexts = screen
      .getAllByTestId("clickable-word")
      .map((w) => w.textContent);
    expect(wordTexts).toContain("Sneak");
    expect(wordTexts).toContain("Penguin");
    expect(wordTexts).toContain("Charge");
  });
});

// Phase K K12: kind dispatch + auto-advance. StepCard switches its
// inner body on ``step.kind``: song → SongPlayer, joke → JokeStep,
// text/fork → existing path. Auto-advance fires when a song/joke
// step's content-master flag is OFF and ``currentStep`` is non-null.

describe("StepCard K12 — song step dispatch", () => {
  it("renders SongPlayer for kind=song and skips the step-body-row + NextStepButton", () => {
    const onAdvance = vi.fn();
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({ body: "Rocket Launch Countdown" }),
          kind: "song",
          metadata: { audio_url: "/api/static/songs/audio/rocket.mp3" },
        } as ActivityStep,
      ],
    });
    render(<StepCard activity={activity} onAdvance={onAdvance} />);
    // SongPlayer mounts.
    expect(screen.getByTestId("song-player")).not.toBeNull();
    expect(screen.getByTestId("song-player-title").textContent).toBe(
      "Rocket Launch Countdown",
    );
    const audio = screen.getByTestId(
      "song-player-audio",
    ) as HTMLAudioElement;
    expect(audio.getAttribute("src")).toBe(
      "/api/static/songs/audio/rocket.mp3",
    );
    // The default text body-row is NOT rendered (song owns the layout).
    expect(screen.queryByTestId("step-body-row")).toBeNull();
    // No linear NextStepButton — SongPlayer's internal next is the
    // sole advance affordance on song steps.
    expect(screen.queryByTestId("next-step-button")).toBeNull();
  });

  it("falls back to /api/static/songs/audio/<id>.mp3 when only song_id is in metadata", () => {
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({ body: "Moon Stroll" }),
          kind: "song",
          metadata: { song_id: "moon-stroll-lullaby" },
        } as ActivityStep,
      ],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    const audio = screen.getByTestId(
      "song-player-audio",
    ) as HTMLAudioElement;
    expect(audio.getAttribute("src")).toBe(
      "/api/static/songs/audio/moon-stroll-lullaby.mp3",
    );
  });

  it("does NOT mount a ReadMeButton on a song step", () => {
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({ body: "Song" }),
          kind: "song",
          metadata: { audio_url: "/x.mp3" },
        } as ActivityStep,
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={vi.fn()}
        readMeButtonEnabled={true}
      />,
    );
    expect(screen.queryByTestId("read-me-button")).toBeNull();
  });
});

describe("StepCard K12 — joke step dispatch", () => {
  it("renders JokeStep for kind=joke and keeps the linear NextStepButton", () => {
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({ body: "Why did the chicken cross the road?" }),
          kind: "joke",
          metadata: { punchline: "To get to the other side." },
        } as ActivityStep,
      ],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    expect(screen.getByTestId("joke-step")).not.toBeNull();
    expect(screen.getByTestId("joke-setup").textContent).toBe(
      "Why did the chicken cross the road?",
    );
    // Linear NextStepButton is rendered — the punchline reveal is
    // time-gated, but advance is still kid-triggered.
    expect(screen.getByTestId("next-step-button")).not.toBeNull();
    // The default body-row is NOT rendered for joke kind.
    expect(screen.queryByTestId("step-body-row")).toBeNull();
  });

  it("mounts a ReadMeButton variant on joke steps (replays both lines)", () => {
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({ body: "Setup" }),
          kind: "joke",
          metadata: { punchline: "Punchline" },
        } as ActivityStep,
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={vi.fn()}
        readMeButtonEnabled={true}
      />,
    );
    const button = screen.getByTestId("read-me-button") as HTMLButtonElement;
    expect(button).not.toBeNull();
    // The joke variant carries the data-read-me-variant attr so a
    // selector can distinguish it from the K9 stock ReadMeButton.
    expect(button.dataset["readMeVariant"]).toBe("joke");
  });
});

describe("StepCard K12 — text + fork kinds unchanged", () => {
  it("renders the body-row + NextStepButton for kind=text (explicit)", () => {
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({ body: "Plain text step" }),
          kind: "text",
        } as ActivityStep,
      ],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    expect(screen.getByTestId("step-body-row")).not.toBeNull();
    expect(screen.getByTestId("next-step-button")).not.toBeNull();
    expect(screen.queryByTestId("song-player")).toBeNull();
    expect(screen.queryByTestId("joke-step")).toBeNull();
  });

  it("renders the body-row + ChoiceButtons for kind=fork", () => {
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({
            body: "Branch?",
            choices: [
              { label: "A", choice_index: 0 },
              { label: "B", choice_index: 1 },
            ],
          }),
          kind: "fork",
        } as ActivityStep,
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={vi.fn()}
        onChoose={vi.fn()}
      />,
    );
    expect(screen.getByTestId("step-body-row")).not.toBeNull();
    expect(screen.getAllByTestId("choice-button")).toHaveLength(2);
    expect(screen.queryByTestId("song-player")).toBeNull();
    expect(screen.queryByTestId("joke-step")).toBeNull();
  });
});

describe("StepCard K12 — auto-advance on disabled content master", () => {
  it("auto-advances past a song step when songsEnabled=false", () => {
    const onAdvance = vi.fn();
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({ body: "Song" }),
          kind: "song",
          metadata: { audio_url: "/x.mp3" },
        } as ActivityStep,
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={onAdvance}
        songsEnabled={false}
      />,
    );
    // onAdvance fires once during the mount effect (silent skip).
    expect(onAdvance).toHaveBeenCalledTimes(1);
    // The step card renders the auto-advance sentinel — no SongPlayer,
    // no body row, no buttons.
    const card = screen.getByTestId("step-card");
    expect(card.dataset["autoAdvance"]).toBe("true");
    expect(screen.queryByTestId("song-player")).toBeNull();
    expect(screen.queryByTestId("next-step-button")).toBeNull();
  });

  it("auto-advances past a joke step when jokesEnabled=false", () => {
    const onAdvance = vi.fn();
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({ body: "Joke setup" }),
          kind: "joke",
          metadata: { punchline: "Joke punchline" },
        } as ActivityStep,
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={onAdvance}
        jokesEnabled={false}
      />,
    );
    expect(onAdvance).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("joke-step")).toBeNull();
  });

  it("does NOT auto-advance when the disabled-content step is only a preview (currentStep is null)", () => {
    // When the activity is in approved-but-not-started state, no step
    // has current=true. The kiosk previews steps[0] as a "ready" hint
    // — auto-advancing through it would defeat the kid's "I'm Ready"
    // tap and silently consume the step before the kid sees it.
    const onAdvance = vi.fn();
    const activity = fakeActivity({
      state: "approved",
      steps: [
        {
          ...fakeStep({ body: "Song" }),
          // current=false → no current step on the activity.
          current: false,
          kind: "song",
          metadata: { audio_url: "/x.mp3" },
        } as ActivityStep,
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={onAdvance}
        songsEnabled={false}
      />,
    );
    // No auto-advance call — the preview is a "Ready" hint, not a
    // running step.
    expect(onAdvance).not.toHaveBeenCalled();
  });

  it("renders the SongPlayer normally when songsEnabled=true", () => {
    const onAdvance = vi.fn();
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({ body: "Song" }),
          kind: "song",
          metadata: { audio_url: "/x.mp3" },
        } as ActivityStep,
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={onAdvance}
        songsEnabled={true}
      />,
    );
    expect(screen.getByTestId("song-player")).not.toBeNull();
    // No auto-advance — kid is supposed to hear the song.
    expect(onAdvance).not.toHaveBeenCalled();
  });

  it("does NOT auto-advance non-song / non-joke kinds even when both flags are false", () => {
    // Defensive: text + fork kinds should never auto-advance based
    // on the K12 flags. The flags are content-master gates, not a
    // general-purpose skip.
    const onAdvance = vi.fn();
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({ body: "Plain step" }),
          kind: "text",
        } as ActivityStep,
      ],
    });
    render(
      <StepCard
        activity={activity}
        onAdvance={onAdvance}
        songsEnabled={false}
        jokesEnabled={false}
      />,
    );
    expect(onAdvance).not.toHaveBeenCalled();
    expect(screen.getByTestId("step-body-row")).not.toBeNull();
  });
});

// Phase L L10: StepCard mounts RewardStep when step.kind === "reward"
// and skips the body-row + linear NextStepButton (RewardStep owns
// its own surface, like SongPlayer does on kind="song").

describe("StepCard L10 — reward step dispatch", () => {
  it("mounts RewardStep for kind=reward and skips the body-row + NextStepButton", () => {
    const activity = fakeActivity({
      steps: [
        {
          ...fakeStep({ body: "Gold Star" }),
          kind: "reward",
          metadata: {
            reward_kind: "picture",
            reward_id: "gold-star",
            image_url: "/api/static/images/rewards/gold-star.png",
            animation: "shine",
            audio_url: null,
            body: "Gold Star",
            setup: null,
            punchline: null,
          },
        } as ActivityStep,
      ],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    // RewardStep mounts and surfaces its picture-variant child.
    const card = screen.getByTestId("reward-step");
    expect(card.getAttribute("data-reward-kind")).toBe("picture");
    expect(screen.getByTestId("reward-picture-image")).not.toBeNull();
    // The default text body-row is NOT rendered — RewardStep owns
    // the entire kiosk surface for the reward beat.
    expect(screen.queryByTestId("step-body-row")).toBeNull();
    // No linear NextStepButton — the picture variant uses its own
    // 6s timer + tap-to-advance.
    expect(screen.queryByTestId("next-step-button")).toBeNull();
  });
});

// Phase M Step M3 — ElementCard wiring into StepCard.
//
// Coverage:
//   - When step.element_id is non-null, ElementCard renders above the
//     step body row (text and fork step kinds).
//   - When step.element_id is null or absent, ElementCard does NOT
//     render — the kiosk's body row layout is byte-identical to pre-M3.
//   - Denormalized element fields (element_symbol / element_name /
//     element_atomic_number) carried in step.metadata reach the
//     rendered card.

describe("StepCard ElementCard branch (Phase M M3)", () => {
  it("renders the ElementCard above the body row when step.element_id is present", () => {
    const activity = fakeActivity({
      steps: [
        fakeStep({
          element_id: "au-79",
          metadata: {
            element_id: "au-79",
            element_symbol: "Au",
            element_name: "Gold",
            element_atomic_number: 79,
          },
        }),
      ],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    const card = screen.getByTestId("element-card");
    expect(card.getAttribute("data-element-id")).toBe("au-79");
    expect(screen.getByTestId("element-card-symbol").textContent).toBe("Au");
    expect(screen.getByTestId("element-card-name").textContent).toBe("Gold");
    expect(screen.getByTestId("element-card-atomic-number").textContent).toBe(
      "#79",
    );
    // The body row also renders (existing text step content) — the
    // element card sits ABOVE it, not in place of it.
    expect(screen.getByTestId("step-body-row")).not.toBeNull();
  });

  it("does NOT render the ElementCard when step.element_id is null", () => {
    const activity = fakeActivity({
      steps: [fakeStep({ element_id: null })],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    expect(screen.queryByTestId("element-card")).toBeNull();
    // Body row still renders the step text — the no-element_id path
    // must be byte-identical to pre-M3 StepCard rendering.
    expect(screen.getByTestId("step-body-row")).not.toBeNull();
  });

  it("does NOT render the ElementCard when step.element_id is absent from the wire", () => {
    // Pre-M3 wire shape: backend response omits ``element_id`` entirely.
    // The kiosk must treat that the same as null (graceful fallback to
    // pre-M3 layout).
    const activity = fakeActivity({
      steps: [fakeStep()],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    expect(screen.queryByTestId("element-card")).toBeNull();
  });
});

// Phase N Step N0 — D2 fix. UAT defect from Phase M row #4
// (``shrink_into_helium_balloon_voyage``): the kiosk's persona-letter
// avatar overlapped the NextStepButton hit zone on element-bearing
// steps, blocking operator advance. The fix lives in ``App.tsx``
// (which is where ``PersonaAvatar`` is mounted, as a sibling above the
// StepCard) and conditionally suppresses the avatar when the current
// step has a non-empty ``element_id`` — the ElementCard sprite itself
// encodes the periodic-table persona identity for these cards, so the
// letter badge is redundant + a blocker.
//
// These tests cover the production composition (``PersonaAvatar`` +
// ``StepCard`` mounted as siblings in a flex column, matching App.tsx)
// rather than StepCard in isolation. The wrapper calls the production
// ``currentStepHasElement`` helper from App.tsx directly — if a future
// dev flips the predicate, removes the guard, or mishandles the
// empty-string case, these tests fail (no producer/consumer drift in a
// separately-defined wrapper copy).
function KioskComposition(props: { activity: Activity }): JSX.Element {
  const hasElement = currentStepHasElement(props.activity);
  return (
    <main
      style={{ display: "flex", flexDirection: "column", alignItems: "center" }}
    >
      {!hasElement && <PersonaAvatar letter="M" size={240} />}
      <StepCard activity={props.activity} onAdvance={vi.fn()} />
    </main>
  );
}

describe("StepCard N0 — persona-letter hidden on element steps (D2 fix)", () => {
  it("renders the persona letter on a regular (non-element) step", () => {
    // Baseline: a plain text step with no ``element_id`` keeps the
    // production composition unchanged — PersonaAvatar sits above the
    // StepCard like it always has.
    const activity = fakeActivity({
      steps: [fakeStep({ body: "Pretend to be a cat" })],
    });
    render(<KioskComposition activity={activity} />);
    const avatar = screen.getByTestId("persona-avatar");
    expect(avatar).not.toBeNull();
    // Letter-mode avatar (no image path) — the visible letter is "M"
    // (from the wrapper's hard-coded ``letter="M"`` prop, mirroring
    // App.tsx's avatarLetter() output).
    expect(avatar.getAttribute("data-avatar-mode")).toBe("letter");
    expect(avatar.textContent).toBe("M");
  });

  it("hides the persona letter when the current step has element_id set", () => {
    // Element-bearing step: the ElementCard renders, the persona letter
    // does NOT. The element sprite + symbol/name/number block is the
    // sole persona surface on these cards (and the redundant avatar
    // was blocking the Next button hit zone — UAT D2).
    const activity = fakeActivity({
      steps: [
        fakeStep({
          element_id: "he-2",
          metadata: {
            element_id: "he-2",
            element_symbol: "He",
            element_name: "Helium",
            element_atomic_number: 2,
          },
        }),
      ],
    });
    render(<KioskComposition activity={activity} />);
    expect(screen.queryByTestId("persona-avatar")).toBeNull();
    // ElementCard does mount — sanity check that the composition is
    // surfacing the periodic-table card (the only "persona" the kiosk
    // shows on this step).
    expect(screen.getByTestId("element-card")).not.toBeNull();
  });

  it("keeps the Next button reachable on the element-card variant", () => {
    // The defect: the persona-letter avatar (240px square at the top
    // of the column) sat on top of the NextStepButton hit zone on
    // element-bearing steps, blocking operator advance. With the
    // avatar suppressed, the Next button is the bottom-most actionable
    // surface and ``getByRole`` can find it AND it is clickable in
    // jsdom (no aria-hidden, no pointer-events: none from a sibling).
    const activity = fakeActivity({
      steps: [
        fakeStep({
          element_id: "he-2",
          metadata: {
            element_id: "he-2",
            element_symbol: "He",
            element_name: "Helium",
            element_atomic_number: 2,
          },
        }),
      ],
    });
    render(<KioskComposition activity={activity} />);
    const nextBtn = screen.getByRole("button", { name: /next/i });
    expect(nextBtn).not.toBeNull();
    // Defensive: the button itself is reachable to the accessibility
    // tree (not aria-hidden) and not disabled at rest.
    expect(nextBtn.getAttribute("aria-hidden")).not.toBe("true");
    expect((nextBtn as HTMLButtonElement).disabled).toBe(false);
    // (Persona-avatar absence is asserted in the prior test — this test
    // focuses exclusively on Next-button reachability semantics.)
    // The Next button's parent chain has no ``pointer-events: none``
    // inline style — jsdom can't reason about visual stacking, but the
    // pointer-events check guards against the obvious "sibling steals
    // clicks via CSS" regression class.
    let node: HTMLElement | null = nextBtn as HTMLElement;
    while (node !== null) {
      expect(node.style.pointerEvents).not.toBe("none");
      node = node.parentElement;
    }
  });
});

// ---------------------------------------------------------------------------
// Phase R Step R3: Q&A gating — StepCard renders question text and replaces
// the Next button with "Waiting for parent…" when question_pending is true.
// ---------------------------------------------------------------------------

describe("StepCard Q&A gating", () => {
  it("renders question text when the current step has a question string", () => {
    const activity = fakeActivity({
      steps: [
        fakeStep({
          question: "What is your favourite colour?",
          question_pending: true,
        }),
      ],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    const questionEl = screen.getByTestId("step-question");
    expect(questionEl.textContent).toContain("What is your favourite colour?");
  });

  it("renders 'Waiting for parent…' instead of the Next button when question_pending is true", () => {
    const activity = fakeActivity({
      steps: [
        fakeStep({
          question: "Name an element!",
          question_pending: true,
        }),
      ],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    expect(screen.getByTestId("waiting-for-parent")).toBeTruthy();
    // Next button must not be present while waiting.
    expect(screen.queryByRole("button", { name: /next/i })).toBeNull();
  });

  it("renders the Next button (not 'Waiting') when question_pending is false", () => {
    // question resolved — question_pending=false means the parent already approved/skipped.
    const activity = fakeActivity({
      steps: [
        fakeStep({
          question: "What animal?",
          question_pending: false,
        }),
      ],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    expect(screen.queryByTestId("waiting-for-parent")).toBeNull();
    expect(screen.getByRole("button", { name: /next/i })).toBeTruthy();
  });

  it("renders the Next button when the step has no question at all", () => {
    const activity = fakeActivity({
      steps: [fakeStep()],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    expect(screen.queryByTestId("step-question")).toBeNull();
    expect(screen.queryByTestId("waiting-for-parent")).toBeNull();
    expect(screen.getByRole("button", { name: /next/i })).toBeTruthy();
  });

  it("does not render question text when question is an empty string", () => {
    // The backend won't emit an empty string (question is NULL or non-empty),
    // but defensive guard: empty string must NOT render the question block.
    const activity = fakeActivity({
      steps: [
        fakeStep({
          question: "",
          question_pending: false,
        }),
      ],
    });
    render(<StepCard activity={activity} onAdvance={vi.fn()} />);
    expect(screen.queryByTestId("step-question")).toBeNull();
  });
});

