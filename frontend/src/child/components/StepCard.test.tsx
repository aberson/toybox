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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Activity, ActivityStep } from "../api";
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

