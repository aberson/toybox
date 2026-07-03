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
//   - Phase Z Z2: sentence-boundary-aware spoken-text truncation —
//     direct unit tests of ``truncateSpokenText`` plus the operator's
//     157-char "What does Miss Maple think?" regression (#4).

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { VoiceProfile } from "../tts";
import { ReadMeButton, truncateSpokenText } from "./ReadMeButton";

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

describe("ReadMeButton — spoken text truncation (limit prop)", () => {
  it("limit=0 (off): speak() receives the full text unchanged", () => {
    const stepText = "The quick brown fox jumps over the lazy dog.";
    render(
      <ReadMeButton
        text={stepText}
        profile={TEST_PROFILE}
        enabled={true}
        limit={0}
      />,
    );
    fireEvent.click(screen.getByTestId("read-me-button"));
    expect(tts.speak).toHaveBeenCalledWith(stepText, TEST_PROFILE);
  });

  it("limit omitted: speak() receives the full text (no truncation)", () => {
    const stepText = "Short text.";
    render(
      <ReadMeButton
        text={stepText}
        profile={TEST_PROFILE}
        enabled={true}
      />,
    );
    fireEvent.click(screen.getByTestId("read-me-button"));
    expect(tts.speak).toHaveBeenCalledWith(stepText, TEST_PROFILE);
  });

  it("text shorter than limit: speak() receives the full text (no truncation)", () => {
    const stepText = "Hi there!";
    render(
      <ReadMeButton
        text={stepText}
        profile={TEST_PROFILE}
        enabled={true}
        limit={100}
      />,
    );
    fireEvent.click(screen.getByTestId("read-me-button"));
    // text.length (9) < limit (100) — no truncation.
    expect(tts.speak).toHaveBeenCalledWith(stepText, TEST_PROFILE);
  });

  it("first sentence exceeds the limit: speak() falls back to a word-boundary cut + '…'", () => {
    // Phase Z Z2: no sentence terminator at or below limit 8 (the only
    // "." sits at the very end) → word-boundary fallback. Slice
    // "Hello wo" → lastSpace at 5 → "Hello" + "…".
    const stepText = "Hello world, this is a long sentence.";
    render(
      <ReadMeButton
        text={stepText}
        profile={TEST_PROFILE}
        enabled={true}
        limit={8}
      />,
    );
    fireEvent.click(screen.getByTestId("read-me-button"));
    expect(tts.speak).toHaveBeenCalledWith("Hello…", TEST_PROFILE);
  });

  it("text with no spaces within limit: speak() hard-cuts at limit + '…'", () => {
    // A single long word with no spaces — the fallback path uses hard cut.
    const stepText = "supercalifragilistic";
    render(
      <ReadMeButton
        text={stepText}
        profile={TEST_PROFILE}
        enabled={true}
        limit={5}
      />,
    );
    fireEvent.click(screen.getByTestId("read-me-button"));
    // No space in "super" (slice to 5 chars = "super"), lastSpace = -1
    // → fallback to hard cut at 5 → "super" + "…"
    expect(tts.speak).toHaveBeenCalledWith("super…", TEST_PROFILE);
  });

  it("multi-sentence text over the limit: speak() cuts at the last sentence boundary", () => {
    // Phase Z Z2 through the component: the "." at index 7 is the only
    // terminator at or below limit 14 → the cut keeps the complete
    // first sentence. Fixture chosen to DISCRIMINATE from the old
    // word-boundary rule: slice(0, 14) = "Go left. Then " has its last
    // space at index 13, so a revert-to-word-boundary mutation would
    // speak "Go left. Then…" and fail this assertion.
    const stepText = "Go left. Then run to the big red door.";
    render(
      <ReadMeButton
        text={stepText}
        profile={TEST_PROFILE}
        enabled={true}
        limit={14}
      />,
    );
    fireEvent.click(screen.getByTestId("read-me-button"));
    expect(tts.speak).toHaveBeenCalledWith("Go left.…", TEST_PROFILE);
  });
});

describe("ReadMeButton — Z2 regression: operator's mid-sentence cutoff (#4)", () => {
  // Phase Z Z2 (#4): the operator heard a 157-char step body spoken as
  // "…What does Miss" — limit 150 landed inside the final question
  // "What does Miss Maple think?" and the old word-boundary rule cut
  // mid-sentence. Reconstruct that exact class of failure and assert
  // the spoken text now ends at the PREVIOUS sentence boundary.
  const OPERATOR_BODY =
    "Miss Maple the wise old owl lands on the branch beside you. " +
    "She hoots softly and points her wing at the puzzle pieces on the rug. " +
    "What does Miss Maple think?";

  it("157-char body at limit 150: spoken text ends at the previous sentence boundary", () => {
    // Pin the regression shape: exactly 157 chars, limit 150 lands
    // mid-way through the final question.
    expect(OPERATOR_BODY.length).toBe(157);
    render(
      <ReadMeButton
        text={OPERATOR_BODY}
        profile={TEST_PROFILE}
        enabled={true}
        limit={150}
      />,
    );
    fireEvent.click(screen.getByTestId("read-me-button"));
    const spoken = (tts.speak as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0]![0] as string;
    // Ends with a sentence terminator + "…" — never a dangling clause.
    expect(spoken).toMatch(/[.!?]…$/);
    // The operator's observed fragment must be gone entirely.
    expect(spoken).not.toContain("What does Miss");
    expect(spoken).toBe(
      "Miss Maple the wise old owl lands on the branch beside you. " +
        "She hoots softly and points her wing at the puzzle pieces on the rug.…",
    );
  });
});

describe("truncateSpokenText — sentence-boundary contract (Phase Z Z2)", () => {
  // Direct unit tests of the shared truncation function (exported from
  // ReadMeButton, imported by ChoiceReadButton — one source of truth).

  it("limit=0 (off): returns the text unchanged", () => {
    expect(truncateSpokenText("One. Two. Three.", 0)).toBe(
      "One. Two. Three.",
    );
  });

  it("text.length <= limit: returns the text unchanged (even with terminators)", () => {
    // Terminator exactly at the limit AND length == limit — this is the
    // passthrough branch, so no "…" is appended.
    expect(truncateSpokenText("Hi there!", 9)).toBe("Hi there!");
  });

  it("text.length == limit+1: truncates (passthrough boundary is <= limit exactly)", () => {
    // Off-by-one pin: "Go home. Now" is 12 chars, limit 11 — one char
    // over MUST truncate. A `text.length <= limit + 1` mutation would
    // return the text unchanged and fail here. The cut lands at the
    // "." at index 7.
    expect(truncateSpokenText("Go home. Now", 11)).toBe("Go home.…");
  });

  it("empty string: returned unchanged for any limit", () => {
    expect(truncateSpokenText("", 10)).toBe("");
    expect(truncateSpokenText("", 0)).toBe("");
  });

  it("terminator exactly at the limit: kept in the spoken text", () => {
    // "Hi there." is 9 chars — the "." occupies the limit-th character
    // ("at or below the limit" is inclusive).
    expect(truncateSpokenText("Hi there. More text follows here.", 9)).toBe(
      "Hi there.…",
    );
  });

  it("terminator just PAST the limit (0-based index == limit): excluded", () => {
    // "Hi there. More": the "." sits at 0-based index 8 — the first
    // character beyond slice(0, 8), i.e. the (limit+1)-th character —
    // so it is NOT an eligible cut. No terminator within the limit →
    // word-boundary fallback at the space (index 2).
    expect(truncateSpokenText("Hi there. More", 8)).toBe("Hi…");
  });

  it("multiple terminators below the limit: cuts at the LAST one", () => {
    // "." at 5, "!" at 12, "?" at 21 are all within limit 25 — the cut
    // must keep everything through the "?" (mixed terminator kinds).
    expect(truncateSpokenText("A one. B two! C three? D four.", 25)).toBe(
      "A one. B two! C three?…",
    );
  });

  it("terminator followed by a closing quote: quote is dropped (documented)", () => {
    // Simple last-terminator rule: the cut lands right after "!", so
    // the closing '"' is not carried into the spoken text. Punctuation
    // isn't voiced, so the omission is inaudible — accepted behavior.
    expect(
      truncateSpokenText(
        'She said "Stop!" and then kept running down the long hallway.',
        30,
      ),
    ).toBe('She said "Stop!…');
  });

  it("abbreviation-like period counts as a terminator (documented, no NLP)", () => {
    // "Dr." is the only "." at or below limit 20 → the cut lands there.
    // Accepted trade-off: an abbreviation cut still ends on a natural
    // spoken pause, which beats a mid-clause stop.
    expect(
      truncateSpokenText(
        "Dr. Maple examined the clue very carefully with her magnifying glass",
        20,
      ),
    ).toBe("Dr.…");
  });

  it("source ellipsis (U+2026) is NOT a terminator: falls back to word boundary", () => {
    // Only ".", "!", "?" terminate — a "…" already in the text does
    // not, so with no ASCII terminator below limit 15 the word-boundary
    // fallback applies.
    expect(
      truncateSpokenText(
        "Wait… the door creaks open slowly and a shadow moves.",
        15,
      ),
    ).toBe("Wait… the door…");
  });

  it("ASCII '...' run: cuts after its final '.'", () => {
    // Each "." of an ASCII ellipsis is a terminator; the last one below
    // the limit wins, then "…" is appended — accepted double-ellipsis.
    expect(
      truncateSpokenText(
        "Wow... that is a really wild story about the dusty old attic.",
        12,
      ),
    ).toBe("Wow...…");
  });

  it("all one sentence (no terminator below limit): word-boundary fallback", () => {
    expect(
      truncateSpokenText(
        "This whole body is one single long sentence with no early stops",
        25,
      ),
    ).toBe("This whole body is one…");
  });

  it("leading terminator (index 0) is not an eligible cut: word-boundary fallback", () => {
    // A terminator at index 0 would yield a punctuation-only utterance;
    // the > 0 guard skips it, mirroring the word-boundary degenerate-cut
    // guard.
    expect(truncateSpokenText("! wow this is exciting stuff here", 10)).toBe(
      "! wow…",
    );
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
