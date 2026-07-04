// ChoiceReadButton component tests — the per-option read-aloud bubble
// (split from the single K9 Read Me button: one bubble for the prompt,
// one per choice).
//
// Same vi.mock pattern as ReadMeButton.test.tsx: replace the K8 TTS
// substrate so the component contract is what's tested.
//
// Coverage:
//   - Render gating: ``enabled=false`` OR an empty label returns null.
//   - Render contract on enable: aria-label names the choice, inline
//     (NOT fixed) positioning, hit target ≥44pt, native <button>.
//   - Tap: ``cancel()`` then ``speak(label, profile)`` ordering; the
//     spoken-text limit truncates the label; rejections swallowed.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { VoiceProfile } from "../tts";
import { ChoiceReadButton } from "./ChoiceReadButton";

vi.mock("../tts", async () => {
  return {
    speak: vi.fn(async () => undefined),
    cancel: vi.fn(),
  };
});

// Phase Z Z5: partial-mock the clip substrate (same seam as
// ReadMeButton.test.tsx) — playClip/stopClip mocked, the pure
// isClipInterrupted / effectiveClipUrl kept real.
vi.mock("../clip-audio", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../clip-audio")>();
  return {
    ...actual,
    playClip: vi.fn(async () => undefined),
    stopClip: vi.fn(),
  };
});

import * as clipAudio from "../clip-audio";
import * as tts from "../tts";

const TEST_PROFILE: VoiceProfile = { rate: 1.0, pitch: 1.0 };

// Interruption rejection built from the substrate's own exported marker
// (one source of truth; the partial mock spreads the actual module, so
// the constant rides through and the REAL isClipInterrupted matches it).
const INTERRUPTED = new Error(clipAudio.CLIP_INTERRUPTED_MESSAGE);

const CLIP_URL = "/api/static/tts/am_puck/1234abcd5678ef00.wav";

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ChoiceReadButton — render gating", () => {
  it("renders null when enabled=false (no button in the DOM)", () => {
    render(
      <ChoiceReadButton
        label="Sneak past the dragon"
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={false}
      />,
    );
    expect(screen.queryByTestId("choice-read-button")).toBeNull();
  });

  it("renders null when the label is empty (nothing to speak)", () => {
    render(
      <ChoiceReadButton
        label=""
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    expect(screen.queryByTestId("choice-read-button")).toBeNull();
  });
});

describe("ChoiceReadButton — enabled render", () => {
  it("renders a native button whose aria-label names the choice", () => {
    render(
      <ChoiceReadButton
        label="Charge in bravely"
        choiceIndex={1}
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const btn = screen.getByTestId("choice-read-button");
    expect(btn.tagName).toBe("BUTTON");
    expect(btn.getAttribute("aria-label")).toBe(
      "Read choice: Charge in bravely",
    );
    expect(btn.getAttribute("data-choice-index")).toBe("1");
  });

  it("is laid out inline — NOT position:fixed (must travel with its option)", () => {
    render(
      <ChoiceReadButton
        label="anything"
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const btn = screen.getByTestId("choice-read-button") as HTMLButtonElement;
    // The prompt's ReadMeButton is viewport-fixed (#137); this bubble
    // must NOT be — it sits in the choice row's flex flow.
    expect(btn.style.position).toBe("");
    expect(btn.style.flexShrink).toBe("0");
  });

  it("hit target ≥44pt — width/height >= 44px on the element style", () => {
    render(
      <ChoiceReadButton
        label="anything"
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const btn = screen.getByTestId("choice-read-button") as HTMLButtonElement;
    const widthPx = Number.parseInt(btn.style.width, 10);
    const heightPx = Number.parseInt(btn.style.height, 10);
    expect(widthPx).toBeGreaterThanOrEqual(44);
    expect(heightPx).toBeGreaterThanOrEqual(44);
  });

  it("keeps tab-reachability (no tabIndex=-1) + the scoped opacity class", () => {
    render(
      <ChoiceReadButton
        label="x"
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const btn = screen.getByTestId("choice-read-button") as HTMLButtonElement;
    expect(btn.tabIndex).toBeGreaterThanOrEqual(0);
    expect(btn.className).toContain("kiosk-choice-read-button");
  });
});

describe("ChoiceReadButton — click handler", () => {
  it("calls cancel() before speak() with the label + profile", () => {
    render(
      <ChoiceReadButton
        label="Take the mountain pass"
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    fireEvent.click(screen.getByTestId("choice-read-button"));
    expect(tts.cancel).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledWith(
      "Take the mountain pass",
      TEST_PROFILE,
    );
    // Strict ordering: cancel must come first so a prior in-flight
    // utterance is interrupted before this one starts.
    const cancelMock = tts.cancel as unknown as {
      mock: { invocationCallOrder: number[] };
    };
    const speakMock = tts.speak as unknown as {
      mock: { invocationCallOrder: number[] };
    };
    expect(cancelMock.mock.invocationCallOrder[0]!).toBeLessThan(
      speakMock.mock.invocationCallOrder[0]!,
    );
  });

  it("applies the spoken-text limit to the label (word-boundary fallback + '…')", () => {
    render(
      <ChoiceReadButton
        label="Hello world, this is a long choice."
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
        limit={8}
      />,
    );
    fireEvent.click(screen.getByTestId("choice-read-button"));
    // No sentence terminator at or below limit 8 → word-boundary
    // fallback: slice "Hello wo" → lastSpace at 5 → "Hello" + "…"
    // (same rule as ReadMeButton — one shared truncateSpokenText).
    expect(tts.speak).toHaveBeenCalledWith("Hello…", TEST_PROFILE);
  });

  it("cuts the label at the last sentence boundary below the limit (Phase Z Z2)", () => {
    render(
      <ChoiceReadButton
        label="Go left. Then run to the big red door."
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
        limit={14}
      />,
    );
    fireEvent.click(screen.getByTestId("choice-read-button"));
    // Same input/limit as ReadMeButton's sentence-boundary component
    // test — pins that BOTH bubbles share the ONE sentence-aware
    // truncation function (truncateSpokenText, exported from
    // ReadMeButton): the "." at index 7 is the only terminator at or
    // below 14, so the spoken label is the complete first sentence.
    // The fixture discriminates from the old word-boundary rule, which
    // would cut at the space at index 13 and speak "Go left. Then…" —
    // a fork or revert of the shared truncation logic fails here.
    expect(tts.speak).toHaveBeenCalledWith("Go left.…", TEST_PROFILE);
  });

  it("limit=0 (off): speak() receives the full label unchanged", () => {
    const label = "The quick brown fox jumps over the lazy dog.";
    render(
      <ChoiceReadButton
        label={label}
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
        limit={0}
      />,
    );
    fireEvent.click(screen.getByTestId("choice-read-button"));
    expect(tts.speak).toHaveBeenCalledWith(label, TEST_PROFILE);
  });

  it("swallows speak() rejections (no unhandled rejection)", async () => {
    (tts.speak as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("engine error"),
    );
    render(
      <ChoiceReadButton
        label="something"
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    expect(() =>
      fireEvent.click(screen.getByTestId("choice-read-button")),
    ).not.toThrow();
    await Promise.resolve();
    expect(tts.speak).toHaveBeenCalledTimes(1);
  });

  it("does not attempt a clip when no clipUrl is threaded (pre-Z5 speak path)", () => {
    render(
      <ChoiceReadButton
        label="anything"
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    fireEvent.click(screen.getByTestId("choice-read-button"));
    expect(clipAudio.playClip).not.toHaveBeenCalled();
    expect(tts.speak).toHaveBeenCalledTimes(1);
  });
});

// Phase Z Z5 — clip-first with Web Speech fallback (mirrors the
// ReadMeButton matrix; the label is this surface's "full text").
describe("ChoiceReadButton — Z5 neural clip path", () => {
  it("prefers the clip when clipUrl is present and the gate is on — full label, no truncation", () => {
    render(
      <ChoiceReadButton
        label="Go left. Then run to the big red door."
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
        // A limit that WOULD truncate on the speech path — proving the
        // clip path ignores it (server clips render the full label).
        limit={14}
        clipUrl={CLIP_URL}
      />,
    );
    fireEvent.click(screen.getByTestId("choice-read-button"));
    expect(clipAudio.playClip).toHaveBeenCalledTimes(1);
    expect(clipAudio.playClip).toHaveBeenCalledWith(CLIP_URL);
    expect(tts.speak).not.toHaveBeenCalled();
  });

  it("falls back to TRUNCATED Web Speech when the clip rejects (404-until-rendered)", async () => {
    vi.mocked(clipAudio.playClip).mockRejectedValueOnce(
      new Error("clip-audio: load or decode error"),
    );
    render(
      <ChoiceReadButton
        label="Go left. Then run to the big red door."
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
        limit={14}
        clipUrl={CLIP_URL}
      />,
    );
    fireEvent.click(screen.getByTestId("choice-read-button"));
    await flush();
    // Z2 sentence-aware truncation applies on the fallback ONLY.
    expect(tts.speak).toHaveBeenCalledWith("Go left.…", TEST_PROFILE);
    expect(clipAudio.stopClip).toHaveBeenCalled();
    expect(tts.cancel).toHaveBeenCalled();
  });

  it("does NOT fall back when the clip rejection is an interruption", async () => {
    vi.mocked(clipAudio.playClip).mockRejectedValueOnce(INTERRUPTED);
    render(
      <ChoiceReadButton
        label="anything"
        choiceIndex={0}
        profile={TEST_PROFILE}
        enabled={true}
        clipUrl={CLIP_URL}
      />,
    );
    fireEvent.click(screen.getByTestId("choice-read-button"));
    await flush();
    expect(tts.speak).not.toHaveBeenCalled();
  });

  it("neuralVoiceEnabled=false routes straight to Web Speech — no clip attempt", () => {
    render(
      <ChoiceReadButton
        label="Take the mountain pass"
        choiceIndex={1}
        profile={TEST_PROFILE}
        enabled={true}
        clipUrl={CLIP_URL}
        neuralVoiceEnabled={false}
      />,
    );
    fireEvent.click(screen.getByTestId("choice-read-button"));
    expect(clipAudio.playClip).not.toHaveBeenCalled();
    expect(tts.speak).toHaveBeenCalledWith(
      "Take the mountain pass",
      TEST_PROFILE,
    );
  });
});
