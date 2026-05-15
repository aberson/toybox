// Phase G Step G4 — ChoiceButton component tests.
//
// Covers the state transitions called out in the plan:
//   - idle: renders the label, calls onChoose(choiceIndex) on click.
//   - in-flight: button disabled until onChoose settles.
//   - error: onChoose rejects → button re-enables AND inline error
//     indicator surfaces; the next successful click clears it. (4xx
//     and 5xx go through the same catch branch — one test covers both.)
//   - 409: onChoose resolves with "conflict" → button re-enables and
//     no error indicator (the parent's withConflictHandler already
//     refetched activity state).
//   - synchronous double-tap: two clicks in one frame fire onChoose
//     once (busyRef latch beats the React commit boundary).
//
// Test style mirrors ``StepCard.test.tsx`` and ``KioskPinPrompt.test.tsx``:
// happy-dom + @testing-library/react + Vitest, no Playwright. UI
// verification is bundled into G6's iPad UAT per
// ``feedback_autonomous_build_bundled_ui.md``.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChoiceButton } from "./ChoiceButton";
import type { ChoiceResult } from "./ChoiceButton";
import type { VoiceProfile } from "../tts";

// Phase K K9: when ``clickableWordsEnabled`` + a ``voiceProfile`` are
// supplied, the label is rendered via ClickableText which speaks the
// word on tap. Mock the substrate so the tests below can assert
// stopPropagation + tap dispatch without staging speechSynthesis.
vi.mock("../tts", async () => {
  return {
    speak: vi.fn(async () => undefined),
    cancel: vi.fn(),
  };
});

import * as tts from "../tts";

const K9_TEST_PROFILE: VoiceProfile = { rate: 1.0, pitch: 1.0 };

afterEach(() => {
  cleanup();
  // K9: tts spies persist across tests via the module-level vi.mock.
  // Clear call records so a previous test's word-tap doesn't leak into
  // a later test's "speak should not have been called" assertion.
  vi.clearAllMocks();
});

// Helper: a deferred promise so the test can hold ``onChoose`` open
// to assert the in-flight state, then resolve it to assert the
// re-enable. Avoids the brittleness of waitFor with no synchronization.
function deferred<T>(): {
  promise: Promise<T>;
  resolve: (v: T) => void;
  reject: (e: unknown) => void;
} {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("ChoiceButton — idle state", () => {
  it("renders the label and choice index", () => {
    render(
      <ChoiceButton
        label="Sneak past the dragon"
        choiceIndex={0}
        onChoose={async () => "ok"}
      />,
    );
    const btn = screen.getByTestId("choice-button");
    expect(btn.textContent).toContain("Sneak past the dragon");
    expect(btn.dataset["choiceIndex"]).toBe("0");
    expect(btn.dataset["busy"]).toBe("false");
    expect(btn.dataset["errored"]).toBe("false");
    expect((btn as HTMLButtonElement).disabled).toBe(false);
  });

  it("calls onChoose with the choiceIndex on click", async () => {
    const onChoose = vi.fn<[number], Promise<ChoiceResult>>().mockResolvedValue("ok");
    render(
      <ChoiceButton label="Charge in" choiceIndex={2} onChoose={onChoose} />,
    );
    fireEvent.click(screen.getByTestId("choice-button"));
    await waitFor(() => {
      expect(onChoose).toHaveBeenCalledTimes(1);
    });
    expect(onChoose).toHaveBeenCalledWith(2);
  });
});

describe("ChoiceButton — externally disabled", () => {
  it("does not call onChoose when disabled is true", () => {
    const onChoose = vi.fn<[number], Promise<ChoiceResult>>().mockResolvedValue("ok");
    render(
      <ChoiceButton
        label="Disabled choice"
        choiceIndex={1}
        disabled={true}
        onChoose={onChoose}
      />,
    );
    const btn = screen.getByTestId("choice-button") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);
    expect(onChoose).not.toHaveBeenCalled();
  });
});

describe("ChoiceButton — in-flight state", () => {
  it("disables itself until onChoose settles", async () => {
    const def = deferred<ChoiceResult>();
    const onChoose = vi
      .fn<[number], Promise<ChoiceResult>>()
      .mockReturnValue(def.promise);
    render(
      <ChoiceButton label="Slow path" choiceIndex={0} onChoose={onChoose} />,
    );
    const btn = screen.getByTestId("choice-button") as HTMLButtonElement;
    fireEvent.click(btn);
    // After click the button flips to busy=true synchronously (the
    // handler sets state immediately, before awaiting the promise).
    await waitFor(() => {
      expect(btn.disabled).toBe(true);
      expect(btn.dataset["busy"]).toBe("true");
    });
    // A second click while in-flight must NOT fire onChoose again
    // (debounce; prevents the double-tap problem the plan calls out).
    fireEvent.click(btn);
    expect(onChoose).toHaveBeenCalledTimes(1);
    // Resolve and assert the button re-enables.
    def.resolve("ok");
    await waitFor(() => {
      expect(btn.disabled).toBe(false);
      expect(btn.dataset["busy"]).toBe("false");
    });
  });
});

describe("ChoiceButton — error path", () => {
  it("shows error on rejection, clears on subsequent successful click", async () => {
    // Combined coverage for the error-indicator lifecycle: a 4xx
    // rejection lights the indicator, the next click clears it on
    // its way to a successful onChoose. The 5xx path is the same
    // catch branch in the component, so one test suffices for both.
    let calls = 0;
    const onChoose = vi
      .fn<[number], Promise<ChoiceResult>>()
      .mockImplementation(async () => {
        calls += 1;
        if (calls === 1) {
          throw new Error("api error 400");
        }
        return "ok";
      });
    render(
      <ChoiceButton label="Retry me" choiceIndex={0} onChoose={onChoose} />,
    );
    const btn = screen.getByTestId("choice-button") as HTMLButtonElement;
    fireEvent.click(btn);
    // First tap rejects: button re-enables, error indicator surfaces.
    await waitFor(() => {
      expect(btn.disabled).toBe(false);
      expect(btn.dataset["errored"]).toBe("true");
    });
    expect(screen.getByTestId("choice-button-error")).not.toBeNull();
    // Second tap resolves: error indicator clears.
    fireEvent.click(btn);
    await waitFor(() => {
      expect(btn.dataset["errored"]).toBe("false");
      expect(screen.queryByTestId("choice-button-error")).toBeNull();
    });
    expect(onChoose).toHaveBeenCalledTimes(2);
  });
});

describe("ChoiceButton — 409 conflict path", () => {
  it("re-enables without error indicator when onChoose resolves with 'conflict'", async () => {
    // The parent's withConflictHandler swallows a 409, fires the
    // refetch, and resolves the promise with "conflict". The button
    // should treat this as a successful tap from the user's POV and
    // NOT show an error indicator.
    const onChoose = vi
      .fn<[number], Promise<ChoiceResult>>()
      .mockResolvedValue("conflict");
    render(
      <ChoiceButton label="Stale" choiceIndex={1} onChoose={onChoose} />,
    );
    const btn = screen.getByTestId("choice-button") as HTMLButtonElement;
    fireEvent.click(btn);
    await waitFor(() => {
      expect(btn.disabled).toBe(false);
      expect(btn.dataset["busy"]).toBe("false");
    });
    expect(btn.dataset["errored"]).toBe("false");
    expect(screen.queryByTestId("choice-button-error")).toBeNull();
    // The refetch was triggered by the parent (whose withConflictHandler
    // ran the refetch callback before resolving). Tested at App level.
    expect(onChoose).toHaveBeenCalledTimes(1);
    expect(onChoose).toHaveBeenCalledWith(1);
  });
});

describe("ChoiceButton K9 — clickable-words label", () => {
  it("renders the label as plain text when clickableWordsEnabled is omitted", () => {
    // Default behavior is the pre-K9 string render — preserves the G4
    // layout fixtures + the F7 sprite-row contract.
    render(
      <ChoiceButton
        label="Path A"
        choiceIndex={0}
        onChoose={async () => "ok"}
      />,
    );
    expect(screen.queryByTestId("clickable-text")).toBeNull();
    expect(screen.getByTestId("choice-button").textContent).toContain("Path A");
  });

  it("renders the label wrapped in ClickableText when flag + profile are supplied", () => {
    render(
      <ChoiceButton
        label="Sneak past Penguin"
        choiceIndex={0}
        onChoose={async () => "ok"}
        voiceProfile={K9_TEST_PROFILE}
        clickableWordsEnabled={true}
      />,
    );
    const wrapper = screen.getByTestId("clickable-text");
    expect(wrapper.getAttribute("data-clickable")).toBe("true");
    // Three words → three tappable spans.
    expect(screen.getAllByTestId("clickable-word")).toHaveLength(3);
  });

  it("word tap calls speak() and does NOT fire onChoose (stopPropagation)", () => {
    const onChoose = vi.fn<[number], Promise<ChoiceResult>>().mockResolvedValue("ok");
    render(
      <ChoiceButton
        label="Sneak past"
        choiceIndex={2}
        onChoose={onChoose}
        voiceProfile={K9_TEST_PROFILE}
        clickableWordsEnabled={true}
      />,
    );
    const [firstWord] = screen.getAllByTestId("clickable-word");
    fireEvent.click(firstWord!);
    // Word tap reaches the TTS substrate.
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledWith("Sneak", K9_TEST_PROFILE);
    // Choice submit did NOT fire — stopPropagation in ClickableText.
    expect(onChoose).not.toHaveBeenCalled();
  });

  it("whole-button click still fires onChoose (outside the word spans)", () => {
    const onChoose = vi.fn<[number], Promise<ChoiceResult>>().mockResolvedValue("ok");
    render(
      <ChoiceButton
        label="Charge in"
        choiceIndex={5}
        onChoose={onChoose}
        voiceProfile={K9_TEST_PROFILE}
        clickableWordsEnabled={true}
      />,
    );
    // Click the button element directly — not on a child word span.
    // fireEvent.click on the button (not a word child) does not bubble
    // up through a child handler; only the button's own onClick fires.
    const btn = screen.getByTestId("choice-button");
    fireEvent.click(btn);
    expect(onChoose).toHaveBeenCalledTimes(1);
    expect(onChoose).toHaveBeenCalledWith(5);
    // The button-level click also did not fire a word tts call.
    expect(tts.speak).not.toHaveBeenCalled();
  });

  it("flag false + profile supplied → plain label (no ClickableText)", () => {
    // The flag is the gate; passing a profile alone must not enable
    // word taps. Mirrors the App-level flag-off path.
    render(
      <ChoiceButton
        label="No taps"
        choiceIndex={0}
        onChoose={async () => "ok"}
        voiceProfile={K9_TEST_PROFILE}
        clickableWordsEnabled={false}
      />,
    );
    expect(screen.queryByTestId("clickable-text")).toBeNull();
    expect(screen.getByTestId("choice-button").textContent).toContain("No taps");
  });
});

describe("ChoiceButton — synchronous double-tap", () => {
  it("fires onChoose only once on two clicks in immediate succession", () => {
    // Phase G G4 fix for the same-button double-tap race: two pointer
    // events fired in the same frame BOTH read ``busy=false`` from
    // the render closure (setBusy is async w.r.t. the closure's view).
    // The synchronous ``busyRef`` latch — set BEFORE the await — gates
    // the second click without waiting for a React commit. We do NOT
    // await between the two fireEvent.click calls (no waitFor flush),
    // mimicking the real-iPad case where two taps land within one frame.
    const def = deferred<ChoiceResult>();
    const onChoose = vi
      .fn<[number], Promise<ChoiceResult>>()
      .mockReturnValue(def.promise);
    render(
      <ChoiceButton label="Tap me" choiceIndex={0} onChoose={onChoose} />,
    );
    const btn = screen.getByTestId("choice-button") as HTMLButtonElement;
    fireEvent.click(btn);
    fireEvent.click(btn);
    // Even without any waitFor or render flush between the two clicks,
    // only the first click's onChoose should have fired — the ref is
    // synchronous, no React commit required.
    expect(onChoose).toHaveBeenCalledTimes(1);
    // Resolve so the test doesn't leave a dangling promise.
    def.resolve("ok");
  });
});
