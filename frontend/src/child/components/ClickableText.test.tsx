// Phase K Step K9 — ClickableText component tests.
//
// Pattern: ``vi.mock("../tts")`` replaces the K8 substrate so each test
// can spy on ``speak`` / ``cancel`` without staging a fake
// ``window.speechSynthesis``. The component contract is what we're
// testing — the substrate has its own coverage in ``tts.test.ts``.
//
// Coverage matches the K9 plan requirements:
//   - Gating: when ``enabled`` is false the component renders a plain
//     ``<span>`` (no word spans, no spy calls).
//   - Word tokenization: each non-whitespace run becomes one tappable
//     word span; whitespace tokens render as plain text so the visible
//     string is byte-identical to the input.
//   - Tap: ``cancel()`` fires BEFORE ``speak(word, profile)`` so a
//     mid-utterance tap on a new word interrupts the prior word.
//   - stopPropagation: a word click does NOT bubble to a clickable
//     ancestor — required for the ChoiceButton mount.
//   - Rejected promises are swallowed (no unhandled rejection escapes).

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { VoiceProfile } from "../tts";
import { ClickableText } from "./ClickableText";

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

describe("ClickableText — disabled (flag off)", () => {
  it("renders a plain span when enabled=false; no word spans, no spy calls", () => {
    render(
      <ClickableText text="hello world" profile={TEST_PROFILE} enabled={false} />,
    );
    const wrapper = screen.getByTestId("clickable-text");
    expect(wrapper.getAttribute("data-clickable")).toBe("false");
    expect(wrapper.textContent).toBe("hello world");
    // No clickable-word spans exist on the disabled render.
    expect(screen.queryAllByTestId("clickable-word")).toHaveLength(0);
    // Clicking the plain span is a no-op (no word spans to receive it
    // and bubble — and even if a parent test wrapper added one, the
    // disabled mode bypasses the handler entirely).
    fireEvent.click(wrapper);
    expect(tts.speak).not.toHaveBeenCalled();
    expect(tts.cancel).not.toHaveBeenCalled();
  });
});

describe("ClickableText — enabled (flag on)", () => {
  it("renders one tappable word span per non-whitespace run", () => {
    render(
      <ClickableText
        text="the quick brown fox"
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const words = screen.getAllByTestId("clickable-word");
    expect(words).toHaveLength(4);
    expect(words.map((w) => w.textContent)).toEqual([
      "the",
      "quick",
      "brown",
      "fox",
    ]);
  });

  it("preserves whitespace structure in the visible textContent", () => {
    // Multiple spaces + tab + leading/trailing whitespace should survive
    // round-trip rendering. Tokenizer preserves the source by emitting
    // separator tokens between word tokens.
    const input = "  one\t two  three ";
    render(
      <ClickableText text={input} profile={TEST_PROFILE} enabled={true} />,
    );
    const wrapper = screen.getByTestId("clickable-text");
    expect(wrapper.textContent).toBe(input);
  });

  it("calls cancel() before speak() on word tap (interrupt + speak)", () => {
    render(
      <ClickableText
        text="hello world"
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const [firstWord] = screen.getAllByTestId("clickable-word");
    fireEvent.click(firstWord!);
    expect(tts.cancel).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledWith("hello", TEST_PROFILE);
    // Cancel's invocation order must precede speak's so an in-flight
    // utterance is interrupted before the new word starts. The mock
    // call records are ordered, so a numeric comparison of the
    // invocationCallOrder field captures the relationship.
    const cancelMock = tts.cancel as unknown as { mock: { invocationCallOrder: number[] } };
    const speakMock = tts.speak as unknown as { mock: { invocationCallOrder: number[] } };
    expect(cancelMock.mock.invocationCallOrder[0]!).toBeLessThan(
      speakMock.mock.invocationCallOrder[0]!,
    );
  });

  it("passes the tapped word (not the whole text) to speak()", () => {
    render(
      <ClickableText
        text="alpha beta gamma"
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const words = screen.getAllByTestId("clickable-word");
    fireEvent.click(words[1]!); // "beta"
    expect(tts.speak).toHaveBeenCalledWith("beta", TEST_PROFILE);
    fireEvent.click(words[2]!); // "gamma"
    expect(tts.speak).toHaveBeenCalledWith("gamma", TEST_PROFILE);
  });

  it("stops event propagation so a clickable ancestor does NOT fire", () => {
    // Mount inside an ancestor handler. The ChoiceButton case is the
    // production motivation for stopPropagation; this isolates that
    // behavior from the rest of the K9 surface.
    const parentClick = vi.fn();
    render(
      <div onClick={parentClick} data-testid="parent">
        <ClickableText
          text="tap me"
          profile={TEST_PROFILE}
          enabled={true}
        />
      </div>,
    );
    const [first] = screen.getAllByTestId("clickable-word");
    fireEvent.click(first!);
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(parentClick).not.toHaveBeenCalled();
  });

  it("swallows promise rejections from speak() (no unhandled rejection)", async () => {
    // Re-stub speak to reject; the component must not propagate the
    // rejection. We verify by mocking the rejection-handler chain
    // returns without throwing. ``catch (() => {})`` in the component
    // is what's under test.
    (tts.speak as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("engine error"),
    );
    render(
      <ClickableText
        text="error path"
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    const [first] = screen.getAllByTestId("clickable-word");
    // The click handler does not await — we just verify the click
    // doesn't throw synchronously, then flush the microtask queue and
    // assert nothing escaped (no unhandled rejection would surface as
    // a Vitest failure on the next tick).
    expect(() => fireEvent.click(first!)).not.toThrow();
    // Await a microtask so the rejected promise's catch handler runs.
    await Promise.resolve();
    // The catch handler is registered — no further assertion needed.
    expect(tts.speak).toHaveBeenCalledTimes(1);
  });

  it("renders empty when text is the empty string (no word spans, no crash)", () => {
    render(<ClickableText text="" profile={TEST_PROFILE} enabled={true} />);
    expect(screen.queryAllByTestId("clickable-word")).toHaveLength(0);
    // Still renders the wrapper so data-clickable surfaces for tests
    // that inspect the gated state.
    expect(screen.getByTestId("clickable-text").textContent).toBe("");
  });
});
