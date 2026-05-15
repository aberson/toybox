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

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Activity, ActivityStep } from "../api";
import { StepCard } from "./StepCard";

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
    // URL composes from the toy_id + slot — the worker writes sprites
    // under ``data/images/toy_actions/<toy_id>/<slot>.png`` and the
    // backend's static-files mount lives at ``/api/static/images``.
    expect(sprite.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-abc/looking.png",
    );
  });

  it("hides the sprite when action_slot is null", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: null })],
    });
    render(<StepCard activity={activity} />);
    expect(screen.queryByTestId("toy-action-sprite")).toBeNull();
    // Body text still renders — sprite-absence does not regress the
    // kiosk's primary readable surface.
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

  it("places the sprite to the LEFT of the body text in the row layout", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    const row = screen.getByTestId("step-body-row");
    const children = Array.from(row.children);
    // Two children: sprite first (left), body text second.
    expect(children).toHaveLength(2);
    expect(children[0]?.getAttribute("data-testid")).toBe("toy-action-sprite");
    expect(children[1]?.getAttribute("data-testid")).toBe("step-text");
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
    "mounts ReadMeButton for step.kind=%s",
    (kind) => {
      // ``kind`` is read defensively off the previewStep object; the
      // fake spreads it into the step body. The cast to ActivityStep
      // narrows past the not-yet-declared field.
      const activity = fakeActivity({
        steps: [
          { ...fakeStep({ body: "x" }), kind } as ActivityStep,
        ],
      });
      render(
        <StepCard
          activity={activity}
          readMeButtonEnabled={true}
        />,
      );
      expect(screen.getByTestId("read-me-button")).not.toBeNull();
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

  it("sets position:relative on the step-card container (K9 positioning contract)", () => {
    // ReadMeButton renders position:absolute and pins to bottom-left;
    // that contract requires the parent to be a positioning context.
    // Guarding the contract here means a future StepCard refactor
    // can't silently break the watermark placement.
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
