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

import * as tts from "../tts";

const TEST_PROFILE: VoiceProfile = { rate: 1.0, pitch: 1.0 };

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

  it("applies the spoken-text limit to the label (word-boundary + '…')", () => {
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
    // Slice "Hello wo" → lastSpace at 5 → "Hello" + "…" (same rule as
    // ReadMeButton — one shared truncateAtWordBoundary).
    expect(tts.speak).toHaveBeenCalledWith("Hello…", TEST_PROFILE);
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
});
