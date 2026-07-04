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
import { JokeStep, replayJoke, SETUP_AUDIO_WATCHDOG_MS } from "./JokeStep";

// Mock the TTS substrate so the test isolates JokeStep's render +
// timer wiring from the actual Web Speech API. Same pattern as
// ClickableText / ReadMeButton tests.
vi.mock("../tts", async () => {
  return {
    speak: vi.fn(async () => undefined),
    cancel: vi.fn(),
  };
});

// Phase Z Z5: partial-mock the clip substrate — playClip/stopClip
// mocked (DOM seams), isClipInterrupted / effectiveClipUrl kept real
// so the sequencing decisions under test use production logic.
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

const SETUP_CLIP = "/api/static/tts/af_bella/aaaa111122223333.wav";
const PUNCHLINE_CLIP = "/api/static/tts/af_bella/bbbb444455556666.wav";

// Interruption rejection built from the substrate's own exported marker
// (one source of truth; the partial mock spreads the actual module, so
// the constant rides through and the REAL isClipInterrupted matches it).
const INTERRUPTED = new Error(clipAudio.CLIP_INTERRUPTED_MESSAGE);

// Flush microtasks under fake timers (promise continuations are not
// timer-driven, so plain awaits inside act() drain them).
async function flushMicrotasks(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

// A manually-settleable playClip result, for tests that need the clip
// to still be "playing" when the reveal timer fires.
function deferred(): { promise: Promise<void>; resolve: () => void; reject: (e: Error) => void } {
  let resolve!: () => void;
  let reject!: (e: Error) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

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

// Phase Z Z5 — clip-path autoplay sequencing. The 1.5s VISUAL reveal is
// unchanged on every path; what the clip path changes is the punchline
// AUDIO start: Web Speech queues natively, the shared clip element
// cannot, so the punchline clip chains on the setup audio's END.
describe("JokeStep — Z5 clip autoplay", () => {
  function renderClipJoke(overrides: Partial<Parameters<typeof JokeStep>[0]> = {}) {
    return render(
      <JokeStep
        setup="setup line"
        punchline="punchline line"
        profile={TEST_PROFILE}
        clickableWordsEnabled={false}
        setupClipUrl={SETUP_CLIP}
        punchlineClipUrl={PUNCHLINE_CLIP}
        {...overrides}
      />,
    );
  }

  it("plays the setup clip on mount instead of speaking (order: setup → 1.5s reveal → punchline clip)", async () => {
    const setupClip = deferred();
    const punchlineClip = deferred();
    vi.mocked(clipAudio.playClip)
      .mockReturnValueOnce(setupClip.promise)
      .mockReturnValueOnce(punchlineClip.promise);
    renderClipJoke();
    expect(clipAudio.playClip).toHaveBeenCalledTimes(1);
    expect(clipAudio.playClip).toHaveBeenNthCalledWith(1, SETUP_CLIP);
    expect(tts.speak).not.toHaveBeenCalled();
    // The reveal timer is the SAME 1.5s beat as the speech path.
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(screen.getByTestId("joke-punchline").textContent).toBe(
      "punchline line",
    );
    // The setup clip is still playing — the punchline clip must WAIT
    // (starting it now would truncate the setup mid-sentence, because
    // playClip interrupts the shared element).
    await flushMicrotasks();
    expect(clipAudio.playClip).toHaveBeenCalledTimes(1);
    // Setup ends → punchline clip starts.
    setupClip.resolve();
    await flushMicrotasks();
    expect(clipAudio.playClip).toHaveBeenCalledTimes(2);
    expect(clipAudio.playClip).toHaveBeenNthCalledWith(2, PUNCHLINE_CLIP);
    expect(tts.speak).not.toHaveBeenCalled();
    punchlineClip.resolve();
    await flushMicrotasks();
  });

  it("short setup: punchline clip fires at the reveal when the setup clip already ended", async () => {
    const punchlineClip = deferred();
    vi.mocked(clipAudio.playClip)
      .mockReturnValueOnce(Promise.resolve())
      .mockReturnValueOnce(punchlineClip.promise);
    renderClipJoke();
    await flushMicrotasks();
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await flushMicrotasks();
    expect(clipAudio.playClip).toHaveBeenCalledTimes(2);
    expect(clipAudio.playClip).toHaveBeenNthCalledWith(2, PUNCHLINE_CLIP);
    punchlineClip.resolve();
    await flushMicrotasks();
  });

  it("mid-sequence fallback: punchline clip failure speaks the punchline ONLY (never restarts the setup)", async () => {
    vi.mocked(clipAudio.playClip)
      .mockReturnValueOnce(Promise.resolve())
      .mockRejectedValueOnce(new Error("clip-audio: load or decode error"));
    renderClipJoke();
    await flushMicrotasks();
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await flushMicrotasks();
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledWith("punchline line", TEST_PROFILE);
  });

  it("setup clip failure falls back to speak(setup), then the punchline clip still plays", async () => {
    vi.mocked(clipAudio.playClip)
      .mockRejectedValueOnce(new Error("clip-audio: load or decode error"))
      .mockReturnValueOnce(Promise.resolve());
    renderClipJoke();
    await flushMicrotasks();
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledWith("setup line", TEST_PROFILE);
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await flushMicrotasks();
    // The punchline still prefers ITS clip — the setup's failure only
    // dropped the setup beat to Web Speech.
    expect(clipAudio.playClip).toHaveBeenCalledTimes(2);
    expect(clipAudio.playClip).toHaveBeenNthCalledWith(2, PUNCHLINE_CLIP);
    expect(tts.speak).toHaveBeenCalledTimes(1);
  });

  it("an interrupted setup clip aborts the sequence — no fallback speak, no punchline audio", async () => {
    vi.mocked(clipAudio.playClip).mockRejectedValueOnce(INTERRUPTED);
    renderClipJoke();
    await flushMicrotasks();
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await flushMicrotasks();
    // The reveal (visual) still happened; only the AUDIO sequence
    // aborted (another surface owns audio focus now).
    expect(screen.getByTestId("joke-step").dataset["revealed"]).toBe("true");
    expect(clipAudio.playClip).toHaveBeenCalledTimes(1);
    expect(tts.speak).not.toHaveBeenCalled();
  });

  it("mixed shape (no setup clip, punchline clip present): setup speaks, punchline clip waits for the utterance's end", async () => {
    const setupUtterance = deferred();
    vi.mocked(tts.speak).mockReturnValueOnce(setupUtterance.promise);
    renderClipJoke({ setupClipUrl: null });
    expect(tts.speak).toHaveBeenCalledWith("setup line", TEST_PROFILE);
    expect(clipAudio.playClip).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await flushMicrotasks();
    // Utterance still in flight → the punchline clip must not cut it off.
    expect(clipAudio.playClip).not.toHaveBeenCalled();
    setupUtterance.resolve();
    await flushMicrotasks();
    expect(clipAudio.playClip).toHaveBeenCalledTimes(1);
    expect(clipAudio.playClip).toHaveBeenCalledWith(PUNCHLINE_CLIP);
  });

  it("neuralVoiceEnabled=false keeps the pre-Z5 Web Speech path (synchronous punchline at the reveal)", () => {
    renderClipJoke({ neuralVoiceEnabled: false });
    expect(clipAudio.playClip).not.toHaveBeenCalled();
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledWith("setup line", TEST_PROFILE);
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    // Pre-Z5 semantics: the punchline speak fires synchronously at the
    // reveal (the engine's own queue handles a still-speaking setup).
    expect(tts.speak).toHaveBeenCalledTimes(2);
    expect(tts.speak).toHaveBeenNthCalledWith(
      2,
      "punchline line",
      TEST_PROFILE,
    );
  });

  it("unmount stops the clip so a kiosk advance can't leak audio into the next step", () => {
    const setupClip = deferred();
    vi.mocked(clipAudio.playClip).mockReturnValueOnce(setupClip.promise);
    const { unmount } = renderClipJoke();
    unmount();
    expect(clipAudio.stopClip).toHaveBeenCalled();
    expect(tts.cancel).toHaveBeenCalled();
    // Settle the deferred so the test leaves no dangling promise (the
    // component's pre-attached catch swallows it).
    setupClip.reject(INTERRUPTED);
  });

  it("a replay in the reveal gap supersedes the autoplay punchline (generation token, no focus theft)", async () => {
    // The settled-setup race: the autoplay setup clip is SHORT and ends
    // before the 1.5s reveal, so when the kid taps the joke Read Me in
    // that gap there is no in-flight clip for the replay's playClip to
    // interrupt. Without the generation token, the reveal timer would
    // fire the autoplay punchline ON TOP of the replay's setup clip.
    const replaySetup = deferred();
    vi.mocked(clipAudio.playClip)
      .mockReturnValueOnce(Promise.resolve()) // autoplay setup — ends early
      .mockReturnValueOnce(replaySetup.promise); // replay setup — still playing
    renderClipJoke();
    await flushMicrotasks(); // autoplay setup beat settles pre-reveal
    replayJoke("setup line", "punchline line", TEST_PROFILE, {
      setupClipUrl: SETUP_CLIP,
      punchlineClipUrl: PUNCHLINE_CLIP,
    });
    expect(clipAudio.playClip).toHaveBeenCalledTimes(2);
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await flushMicrotasks();
    // The autoplay chain is STALE: no third playClip, no speak — the
    // replay's setup clip keeps the floor.
    expect(clipAudio.playClip).toHaveBeenCalledTimes(2);
    expect(tts.speak).not.toHaveBeenCalled();
    // The replay's own sequence continues normally: setup end →
    // punchline clip.
    replaySetup.resolve();
    await flushMicrotasks();
    expect(clipAudio.playClip).toHaveBeenCalledTimes(3);
    expect(clipAudio.playClip).toHaveBeenLastCalledWith(PUNCHLINE_CLIP);
  });

  it("setup-clip + no-punchline-clip shape: the chained speak takes clip focus (stopClip before speak)", async () => {
    vi.mocked(clipAudio.playClip).mockReturnValueOnce(Promise.resolve());
    renderClipJoke({ punchlineClipUrl: null });
    await flushMicrotasks();
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await flushMicrotasks();
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledWith("punchline line", TEST_PROFILE);
    // stopClip precedes the speak so a clip that slipped onto the
    // shared element during the beat can't play under the utterance.
    expect(clipAudio.stopClip).toHaveBeenCalled();
    const stopClipMock = clipAudio.stopClip as unknown as {
      mock: { invocationCallOrder: number[] };
    };
    const speakMock = tts.speak as unknown as {
      mock: { invocationCallOrder: number[] };
    };
    expect(stopClipMock.mock.invocationCallOrder[0]!).toBeLessThan(
      speakMock.mock.invocationCallOrder[0]!,
    );
  });

  it("a hung setup leg degrades via the watchdog — the punchline beat still fires", async () => {
    // tts.ts documents that iOS can drop a cancel-adjacent utterance
    // with NO events; the media element analogue is a clip that never
    // fires ended/error. The watchdog bounds the wait so the punchline
    // beat still happens (pre-Z5's timer-fired punchline was immune).
    const never = new Promise<void>(() => {});
    const punchlineClip = deferred();
    vi.mocked(clipAudio.playClip)
      .mockReturnValueOnce(never)
      .mockReturnValueOnce(punchlineClip.promise);
    renderClipJoke();
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await flushMicrotasks();
    // Setup leg hung → punchline still waiting.
    expect(clipAudio.playClip).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(SETUP_AUDIO_WATCHDOG_MS);
    });
    await flushMicrotasks();
    // Watchdog fired → the sequence proceeded to the punchline clip.
    expect(clipAudio.playClip).toHaveBeenCalledTimes(2);
    expect(clipAudio.playClip).toHaveBeenLastCalledWith(PUNCHLINE_CLIP);
    punchlineClip.resolve();
    await flushMicrotasks();
  });
});

// Phase Z Z5 — clip-path replay (the joke ReadMe watermark).
describe("replayJoke — Z5 clip path", () => {
  it("plays setup then punchline clips SEQUENTIALLY (the shared element cannot queue)", async () => {
    const setupClip = deferred();
    const punchlineClip = deferred();
    vi.mocked(clipAudio.playClip)
      .mockReturnValueOnce(setupClip.promise)
      .mockReturnValueOnce(punchlineClip.promise);
    replayJoke("setup line", "punchline line", TEST_PROFILE, {
      setupClipUrl: SETUP_CLIP,
      punchlineClipUrl: PUNCHLINE_CLIP,
    });
    expect(clipAudio.playClip).toHaveBeenCalledTimes(1);
    expect(clipAudio.playClip).toHaveBeenNthCalledWith(1, SETUP_CLIP);
    expect(tts.speak).not.toHaveBeenCalled();
    setupClip.resolve();
    await flushMicrotasks();
    expect(clipAudio.playClip).toHaveBeenCalledTimes(2);
    expect(clipAudio.playClip).toHaveBeenNthCalledWith(2, PUNCHLINE_CLIP);
    punchlineClip.resolve();
    await flushMicrotasks();
  });

  it("mid-sequence fallback: a failed punchline clip drops to speak(punchline) only", async () => {
    vi.mocked(clipAudio.playClip)
      .mockReturnValueOnce(Promise.resolve())
      .mockRejectedValueOnce(new Error("clip-audio: load or decode error"));
    replayJoke("setup line", "punchline line", TEST_PROFILE, {
      setupClipUrl: SETUP_CLIP,
      punchlineClipUrl: PUNCHLINE_CLIP,
    });
    await flushMicrotasks();
    expect(tts.speak).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenCalledWith("punchline line", TEST_PROFILE);
  });

  it("an interrupted setup clip (double-tap) aborts the remainder silently", async () => {
    vi.mocked(clipAudio.playClip).mockRejectedValueOnce(INTERRUPTED);
    replayJoke("setup line", "punchline line", TEST_PROFILE, {
      setupClipUrl: SETUP_CLIP,
      punchlineClipUrl: PUNCHLINE_CLIP,
    });
    await flushMicrotasks();
    expect(clipAudio.playClip).toHaveBeenCalledTimes(1);
    expect(tts.speak).not.toHaveBeenCalled();
  });

  it("neuralVoiceEnabled=false replays via Web Speech exactly as pre-Z5 (no clip attempts)", () => {
    replayJoke("setup line", "punchline line", TEST_PROFILE, {
      setupClipUrl: SETUP_CLIP,
      punchlineClipUrl: PUNCHLINE_CLIP,
      neuralVoiceEnabled: false,
    });
    expect(clipAudio.playClip).not.toHaveBeenCalled();
    expect(tts.cancel).toHaveBeenCalledTimes(1);
    expect(tts.speak).toHaveBeenNthCalledWith(1, "setup line", TEST_PROFILE);
    expect(tts.speak).toHaveBeenNthCalledWith(
      2,
      "punchline line",
      TEST_PROFILE,
    );
  });

  it("a hung replay setup leg degrades via the watchdog — the punchline clip still fires", async () => {
    const never = new Promise<void>(() => {});
    const punchlineClip = deferred();
    vi.mocked(clipAudio.playClip)
      .mockReturnValueOnce(never)
      .mockReturnValueOnce(punchlineClip.promise);
    replayJoke("setup line", "punchline line", TEST_PROFILE, {
      setupClipUrl: SETUP_CLIP,
      punchlineClipUrl: PUNCHLINE_CLIP,
    });
    await flushMicrotasks();
    expect(clipAudio.playClip).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(SETUP_AUDIO_WATCHDOG_MS);
    await flushMicrotasks();
    expect(clipAudio.playClip).toHaveBeenCalledTimes(2);
    expect(clipAudio.playClip).toHaveBeenLastCalledWith(PUNCHLINE_CLIP);
    punchlineClip.resolve();
    await flushMicrotasks();
  });
});
