// Phase K Step K9 — ReadMeButton component tests.
//
// Same vi.mock pattern as ClickableText.test.tsx: replace the K8 TTS
// substrate so the component contract is what's tested.
//
// Coverage:
//   - Render gating: ``enabled=false`` returns null (no button in DOM).
//   - Render contract on enable: aria-label "Read Me", absolute
//     positioning at bottom-left, hit target ≥44pt.
//   - Tap: ``cancel()`` then ``speak(text, profile)`` invocation
//     ordering. Promise rejection swallowed.
//   - Tab-reachable: native ``<button>`` element (not a div with
//     onClick), no tabIndex={-1}.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { VoiceProfile } from "../tts";
import { ReadMeButton } from "./ReadMeButton";

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

describe("ReadMeButton — disabled", () => {
  it("renders null when enabled=false (no button in the DOM)", () => {
    const { container } = render(
      <ReadMeButton
        text="Once upon a time"
        profile={TEST_PROFILE}
        enabled={false}
      />,
    );
    expect(screen.queryByTestId("read-me-button")).toBeNull();
    // Component returns null — the React fragment renders no DOM nodes
    // at the root of the test render. The container retains only the
    // injected base wrapper.
    expect(container.querySelector("[data-testid='read-me-button']")).toBeNull();
  });
});

describe("ReadMeButton — enabled render", () => {
  it("renders a button with aria-label='Read Me'", () => {
    render(
      <ReadMeButton
        text="Once upon a time"
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const btn = screen.getByTestId("read-me-button");
    expect(btn.tagName).toBe("BUTTON");
    expect(btn.getAttribute("aria-label")).toBe("Read Me");
  });

  it("uses fixed positioning at the viewport's bottom-left (#137)", () => {
    render(
      <ReadMeButton
        text="anything"
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const btn = screen.getByTestId("read-me-button") as HTMLButtonElement;
    // Inline style — happy-dom exposes the literal style strings we set.
    // ``position: fixed`` (not ``absolute``) anchors to the viewport so
    // the affordance doesn't drift mid-screen when a parent section's
    // intrinsic height varies (e.g. fork steps with a tall choice
    // button stack vs. linear text steps).
    expect(btn.style.position).toBe("fixed");
    expect(btn.style.bottom).toBe("16px");
    expect(btn.style.left).toBe("16px");
  });

  it("hit target ≥44pt — width/height >= 44px on the element style", () => {
    render(
      <ReadMeButton
        text="anything"
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const btn = screen.getByTestId("read-me-button") as HTMLButtonElement;
    // happy-dom does not compute CSS layout; inline-style width/height
    // are the values we set on the component. The numeric extraction
    // here is the durable signal of "hit-target sized per HIG".
    const widthPx = Number.parseInt(btn.style.width, 10);
    const heightPx = Number.parseInt(btn.style.height, 10);
    expect(widthPx).toBeGreaterThanOrEqual(44);
    expect(heightPx).toBeGreaterThanOrEqual(44);
  });

  it("native button element keeps tab-reachability (no tabIndex=-1)", () => {
    render(
      <ReadMeButton
        text="anything"
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const btn = screen.getByTestId("read-me-button") as HTMLButtonElement;
    expect(btn.tagName).toBe("BUTTON");
    // tabIndex defaults to 0 on a button; we just guard against a
    // future regression where someone sets it to -1 (which would
    // strip the button from the tab order).
    expect(btn.tabIndex).toBeGreaterThanOrEqual(0);
  });

  it("scoped CSS class — kiosk-read-me-button (for hover/focus opacity)", () => {
    render(
      <ReadMeButton text="x" profile={TEST_PROFILE} enabled={true} />,
    );
    const btn = screen.getByTestId("read-me-button");
    expect(btn.className).toContain("kiosk-read-me-button");
  });
});

describe("ReadMeButton — click handler", () => {
  it("calls cancel() before speak() with the full text + profile", () => {
    const stepText = "The brave knight set out for the dragon's lair.";
    render(
      <ReadMeButton
        text={stepText}
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    fireEvent.click(screen.getByTestId("read-me-button"));
    expect(tts.cancel).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledWith(stepText, TEST_PROFILE);
    // Strict ordering: cancel must come first so a prior in-flight
    // utterance is interrupted before this one starts.
    const cancelMock = tts.cancel as unknown as { mock: { invocationCallOrder: number[] } };
    const speakMock = tts.speak as unknown as { mock: { invocationCallOrder: number[] } };
    expect(cancelMock.mock.invocationCallOrder[0]!).toBeLessThan(
      speakMock.mock.invocationCallOrder[0]!,
    );
  });

  it("swallows speak() rejections (no unhandled rejection)", async () => {
    (tts.speak as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("engine error"),
    );
    render(
      <ReadMeButton
        text="something"
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    expect(() =>
      fireEvent.click(screen.getByTestId("read-me-button")),
    ).not.toThrow();
    await Promise.resolve();
    expect(tts.speak).toHaveBeenCalledTimes(1);
  });
});
