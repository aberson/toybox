// Phase K Step K12 — SongPlayer component tests.
//
// JSdom does not implement HTMLMediaElement.play / pause natively
// (Vitest's default environment is jsdom for these tests via
// vitest.config.ts; the kiosk substrate tests run in node and stage
// their own window). We stub the three methods we exercise here:
// ``play`` (returns Promise so we can drive both success + autoplay-
// blocked rejection), ``pause`` (no-op), and ``error`` / ``ended``
// events fired via fireEvent.
//
// Coverage:
//   - Initial render: title + state label + audio src.
//   - Autoplay success: state transitions idle → playing → done; Next
//     enables on ended; onEnded callback fires.
//   - Autoplay blocked: state goes to "blocked"; play button is
//     visible; tapping it succeeds.
//   - Toggle play/pause while playing.
//   - Audio error: 2s grace, then Next enables to unblock the kiosk.
//   - No ReadMeButton mounted (song owns audio surface).

import { cleanup, fireEvent, render, screen, act } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import { SongPlayer } from "./SongPlayer";

// jsdom shim: define play / pause on HTMLMediaElement so the component
// can drive them. Each test re-assigns play to control the resolution.
let playMock: Mock;
let pauseMock: Mock;

beforeEach(() => {
  playMock = vi.fn(() => Promise.resolve());
  pauseMock = vi.fn();
  // jsdom doesn't implement these methods by default — assigning them
  // on the prototype is the canonical jsdom test pattern.
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    writable: true,
    value: function play() {
      return playMock();
    },
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true,
    writable: true,
    value: function pause() {
      return pauseMock();
    },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("SongPlayer — initial render", () => {
  it("renders the title, state label, and an <audio> element with the src", () => {
    render(
      <SongPlayer src="/api/static/songs/audio/rocket.mp3" title="Rocket Song" />,
    );
    expect(screen.getByTestId("song-player-title").textContent).toBe(
      "Rocket Song",
    );
    const audio = screen.getByTestId(
      "song-player-audio",
    ) as HTMLAudioElement;
    expect(audio.getAttribute("src")).toBe(
      "/api/static/songs/audio/rocket.mp3",
    );
    expect(audio.tagName).toBe("AUDIO");
  });

  it("starts in the idle state, showing the loading label", () => {
    render(<SongPlayer src="/x.mp3" title="X" />);
    const player = screen.getByTestId("song-player");
    // Autoplay's play() resolves synchronously here, but the onplay
    // event hasn't fired yet (jsdom doesn't auto-fire it). The
    // component is still in idle until onplay lands.
    expect(player.dataset["state"]).toBe("idle");
    expect(screen.getByTestId("song-player-state-label").textContent).toBe(
      "Loading...",
    );
  });

  it("disables the Next button on mount", () => {
    render(<SongPlayer src="/x.mp3" title="X" />);
    const next = screen.getByTestId("song-player-next") as HTMLButtonElement;
    expect(next.disabled).toBe(true);
  });
});

describe("SongPlayer — autoplay success path", () => {
  it("transitions to playing on the audio element's onplay event", async () => {
    render(<SongPlayer src="/x.mp3" title="X" />);
    // jsdom doesn't fire onplay automatically — we simulate the
    // engine's "playback started" by dispatching the event directly.
    const audio = screen.getByTestId("song-player-audio");
    await act(async () => {
      fireEvent.play(audio);
    });
    expect(screen.getByTestId("song-player").dataset["state"]).toBe("playing");
    expect(screen.getByTestId("song-player-state-label").textContent).toBe(
      "Playing",
    );
  });

  it("enables Next + fires onEnded callback when the audio finishes", async () => {
    const onEnded = vi.fn();
    render(<SongPlayer src="/x.mp3" title="X" onEnded={onEnded} />);
    const audio = screen.getByTestId("song-player-audio");
    await act(async () => {
      fireEvent.play(audio);
    });
    await act(async () => {
      fireEvent.ended(audio);
    });
    expect(screen.getByTestId("song-player").dataset["state"]).toBe("done");
    expect(onEnded).toHaveBeenCalledTimes(1);
    const next = screen.getByTestId("song-player-next") as HTMLButtonElement;
    expect(next.disabled).toBe(false);
  });

  it("invokes onEnded again when the manual Next button is tapped", async () => {
    const onEnded = vi.fn();
    render(<SongPlayer src="/x.mp3" title="X" onEnded={onEnded} />);
    const audio = screen.getByTestId("song-player-audio");
    await act(async () => {
      fireEvent.play(audio);
      fireEvent.ended(audio);
    });
    // Next is now enabled — tapping it fires onEnded again so the
    // kiosk can move forward on the kid's tap even after auto-advance
    // is mooted by a slow App layer.
    const next = screen.getByTestId("song-player-next") as HTMLButtonElement;
    fireEvent.click(next);
    expect(onEnded).toHaveBeenCalledTimes(2);
  });
});

describe("SongPlayer — autoplay blocked path", () => {
  it("transitions to blocked when the autoplay promise rejects", async () => {
    // Override play() to reject on the first call (iOS / Chrome
    // autoplay policy).
    playMock.mockImplementation(() =>
      Promise.reject(new Error("NotAllowedError")),
    );
    render(<SongPlayer src="/x.mp3" title="X" />);
    // Let the rejected promise propagate through React's microtask
    // queue; act() waits for state updates to land.
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByTestId("song-player").dataset["state"]).toBe(
      "blocked",
    );
    expect(screen.getByTestId("song-player-state-label").textContent).toBe(
      "Tap Play to start",
    );
    // The play/pause toggle is the user's escape hatch — must be
    // enabled in the blocked state so the kid's tap can unlock.
    const toggle = screen.getByTestId(
      "song-player-toggle",
    ) as HTMLButtonElement;
    expect(toggle.disabled).toBe(false);
  });

  it("succeeds on a manual play tap after the autoplay blocked", async () => {
    let firstCall = true;
    playMock.mockImplementation(() => {
      if (firstCall) {
        firstCall = false;
        return Promise.reject(new Error("NotAllowedError"));
      }
      return Promise.resolve();
    });
    render(<SongPlayer src="/x.mp3" title="X" />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByTestId("song-player").dataset["state"]).toBe(
      "blocked",
    );
    // Tap the toggle — it calls play() again, which resolves this time.
    await act(async () => {
      fireEvent.click(screen.getByTestId("song-player-toggle"));
    });
    // Now simulate the engine firing onplay.
    await act(async () => {
      fireEvent.play(screen.getByTestId("song-player-audio"));
    });
    expect(screen.getByTestId("song-player").dataset["state"]).toBe(
      "playing",
    );
  });
});

describe("SongPlayer — error + 2s grace path", () => {
  it("enters error state on audio onerror, then enables Next after 2s grace", async () => {
    vi.useFakeTimers();
    render(<SongPlayer src="/broken.mp3" title="X" />);
    // Simulate the engine firing onerror (404, decoder failure, etc.).
    await act(async () => {
      fireEvent.error(screen.getByTestId("song-player-audio"));
    });
    expect(screen.getByTestId("song-player").dataset["state"]).toBe("error");
    expect(screen.getByTestId("song-player-state-label").textContent).toBe(
      "Could not play this song",
    );
    // Before the 2s grace, Next stays disabled.
    let next = screen.getByTestId("song-player-next") as HTMLButtonElement;
    expect(next.disabled).toBe(true);
    // Advance time past the grace.
    await act(async () => {
      vi.advanceTimersByTime(2001);
    });
    next = screen.getByTestId("song-player-next") as HTMLButtonElement;
    expect(next.disabled).toBe(false);
  });

  it("Next button on the error path fires onEnded so the kiosk advances", async () => {
    vi.useFakeTimers();
    const onEnded = vi.fn();
    render(<SongPlayer src="/broken.mp3" title="X" onEnded={onEnded} />);
    await act(async () => {
      fireEvent.error(screen.getByTestId("song-player-audio"));
    });
    await act(async () => {
      vi.advanceTimersByTime(2001);
    });
    // After the grace timer the Next button is enabled — verify
    // before clicking so a regression in the grace effect surfaces
    // here rather than as a silent no-op click below.
    const next = screen.getByTestId("song-player-next") as HTMLButtonElement;
    expect(next.disabled).toBe(false);
    await act(async () => {
      fireEvent.click(next);
    });
    expect(onEnded).toHaveBeenCalledTimes(1);
  });
});

describe("SongPlayer — pause / resume", () => {
  it("transitions playing → paused on toggle, then back to playing on a second tap", async () => {
    render(<SongPlayer src="/x.mp3" title="X" />);
    const audio = screen.getByTestId("song-player-audio");
    await act(async () => {
      fireEvent.play(audio);
    });
    expect(screen.getByTestId("song-player").dataset["state"]).toBe(
      "playing",
    );
    // Tap toggle while playing → pause.
    await act(async () => {
      fireEvent.click(screen.getByTestId("song-player-toggle"));
    });
    expect(pauseMock).toHaveBeenCalled();
    expect(screen.getByTestId("song-player").dataset["state"]).toBe("paused");
    // Tap again → calls play(), which (mocked) resolves; engine onplay
    // brings it back to playing.
    await act(async () => {
      fireEvent.click(screen.getByTestId("song-player-toggle"));
      fireEvent.play(audio);
    });
    expect(screen.getByTestId("song-player").dataset["state"]).toBe(
      "playing",
    );
  });
});

describe("SongPlayer — no Read Me button", () => {
  it("does NOT render a ReadMeButton (audio surface owned by SongPlayer)", () => {
    render(<SongPlayer src="/x.mp3" title="X" />);
    // SongPlayer itself never mounts a ReadMeButton — that's enforced
    // by StepCard's READ_ME_ELIGIBLE_KINDS exclusion. Asserting the
    // negative here too is defense-in-depth.
    expect(screen.queryByTestId("read-me-button")).toBeNull();
  });
});
