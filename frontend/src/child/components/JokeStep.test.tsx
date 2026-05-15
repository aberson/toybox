// Phase K Step K12 — JokeStep + replayJoke tests.
//
// Coverage:
//   - Setup renders immediately; punchline hidden until reveal.
//   - Auto-play: speak() called for setup on mount; speak() called for
//     punchline after 1.5s reveal.
//   - Empty punchline: setup-only render (defensive against pre-K13
//     wire payloads).
//   - clickableWordsEnabled: ClickableText wrappers carry the
//     expected data-clickable attribute.
//   - replayJoke: cancels then speaks both lines back-to-back.

import { cleanup, render, screen, act } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import type { VoiceProfile } from "../tts";
import { JokeStep, replayJoke } from "./JokeStep";

// Mock the TTS substrate so the test isolates JokeStep's render +
// timer wiring from the actual Web Speech API. Same pattern as
// ClickableText / ReadMeButton tests.
vi.mock("../tts", async () => {
  return {
    speak: vi.fn(async () => undefined),
    cancel: vi.fn(),
  };
});

import * as tts from "../tts";

const TEST_PROFILE: VoiceProfile = { rate: 1.0, pitch: 1.0 };

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("JokeStep — initial render", () => {
  it("renders the setup line immediately, with revealed=false", () => {
    render(
      <JokeStep
        setup="Why did the chicken cross the road?"
        punchline="To get to the other side."
        profile={TEST_PROFILE}
        clickableWordsEnabled={false}
      />,
    );
    expect(screen.getByTestId("joke-setup").textContent).toBe(
      "Why did the chicken cross the road?",
    );
    expect(screen.getByTestId("joke-step").dataset["revealed"]).toBe("false");
    // Punchline DOM is gated on the reveal effect — not yet present.
    expect(screen.queryByTestId("joke-punchline")).toBeNull();
  });

  it("auto-speaks the setup on mount", () => {
    render(
      <JokeStep
        setup="Why did the chicken cross the road?"
        punchline="To get to the other side."
        profile={TEST_PROFILE}
        clickableWordsEnabled={false}
      />,
    );
    expect(tts.speak).toHaveBeenCalledWith(
      "Why did the chicken cross the road?",
      TEST_PROFILE,
    );
  });
});

describe("JokeStep — punchline reveal after 1.5s", () => {
  it("reveals the punchline DOM after 1.5s", () => {
    render(
      <JokeStep
        setup="setup line"
        punchline="punchline line"
        profile={TEST_PROFILE}
        clickableWordsEnabled={false}
      />,
    );
    // Before timer: no punchline.
    expect(screen.queryByTestId("joke-punchline")).toBeNull();
    // Advance just past 1.5s.
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(screen.getByTestId("joke-punchline").textContent).toBe(
      "punchline line",
    );
    expect(screen.getByTestId("joke-step").dataset["revealed"]).toBe("true");
  });

  it("auto-speaks the punchline at the reveal point", () => {
    render(
      <JokeStep
        setup="setup"
        punchline="punchline"
        profile={TEST_PROFILE}
        clickableWordsEnabled={false}
      />,
    );
    // First speak call: the setup.
    expect(tts.speak).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    // Second speak call: the punchline.
    expect(tts.speak).toHaveBeenCalledTimes(2);
    expect(tts.speak).toHaveBeenNthCalledWith(2, "punchline", TEST_PROFILE);
  });
});

describe("JokeStep — defensive empty-punchline path", () => {
  it("renders setup-only when punchline is empty (no reveal, no second speak)", () => {
    render(
      <JokeStep
        setup="setup"
        punchline=""
        profile={TEST_PROFILE}
        clickableWordsEnabled={false}
      />,
    );
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    // Reveal flips but the punchline DOM gate also requires
    // non-empty punchline text → still null.
    expect(screen.queryByTestId("joke-punchline")).toBeNull();
    // Only the setup speak fired.
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledWith("setup", TEST_PROFILE);
  });
});

describe("JokeStep — ClickableText threading", () => {
  it("wraps both lines in ClickableText with clickable=true when flag is on", () => {
    render(
      <JokeStep
        setup="alpha beta"
        punchline="gamma delta"
        profile={TEST_PROFILE}
        clickableWordsEnabled={true}
      />,
    );
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    const wrappers = screen.getAllByTestId("clickable-text");
    // One wrapper per visible line (setup + punchline).
    expect(wrappers.length).toBe(2);
    for (const w of wrappers) {
      expect(w.getAttribute("data-clickable")).toBe("true");
    }
  });

  it("renders plain spans when flag is off", () => {
    render(
      <JokeStep
        setup="alpha beta"
        punchline="gamma delta"
        profile={TEST_PROFILE}
        clickableWordsEnabled={false}
      />,
    );
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    const wrappers = screen.getAllByTestId("clickable-text");
    expect(wrappers.length).toBe(2);
    for (const w of wrappers) {
      expect(w.getAttribute("data-clickable")).toBe("false");
    }
  });
});

describe("replayJoke helper", () => {
  it("cancels in-flight speech then speaks both lines", () => {
    replayJoke("setup line", "punchline line", TEST_PROFILE);
    // Cancel is called first to ensure a clean restart even if
    // something is mid-utterance.
    expect(tts.cancel).toHaveBeenCalledTimes(1);
    // Both lines are queued via speak() in order.
    expect(tts.speak).toHaveBeenNthCalledWith(1, "setup line", TEST_PROFILE);
    expect(tts.speak).toHaveBeenNthCalledWith(
      2,
      "punchline line",
      TEST_PROFILE,
    );
  });

  it("skips the punchline speak when punchline is empty", () => {
    replayJoke("setup only", "", TEST_PROFILE);
    expect(tts.cancel).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledWith("setup only", TEST_PROFILE);
  });
});
