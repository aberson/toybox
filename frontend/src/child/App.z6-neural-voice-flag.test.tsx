// Phase Z Z6 — App → StepCard ``neural_voice_enabled`` flag threading.
//
// The K2 bootstrap fetch test (``App.k2-feature-flags.test.tsx``)
// pins that the ``neural_voice_enabled`` GET fires and its value lands
// in the kiosk's ``featureFlags`` state (the data-flag-* attrs). THIS
// file pins the next leg — the leg Z6 exists for: the fetched value
// threads App → StepCard → ReadMeButton and actually ROUTES the
// speech surface. Flag OFF must send the tap straight to Web Speech
// (zero clip attempts, even when the wire carries a clip URL); flag
// ON must prefer the Z5 clip.
//
// Code-quality rule §4: a wiring change must be integration-tested
// through the production caller. The Z5 component tests already cover
// ``neuralVoiceEnabled={false}`` at the component level; a regression
// that left App's prop hardcoded to ``true`` (the pre-Z6 TODO state)
// would pass every one of those — only a boot-fetched-value-to-surface
// test catches it, so both tests here fetch the flag off the (stubbed)
// wire rather than passing a prop.
//
// We mock ./tts (no speechSynthesis in happy-dom) and partial-mock
// ./clip-audio (spy on playClip/stopClip, keep the REAL
// ``effectiveClipUrl`` gate — that gate is the code under test).

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { playClip } from "./clip-audio";
import { speak } from "./tts";
import { useChildStore } from "./store";
import type { Activity } from "./api";

vi.mock("./tts", () => {
  return {
    speak: vi.fn(async () => undefined),
    cancel: vi.fn(),
  };
});

vi.mock("./clip-audio", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./clip-audio")>();
  return {
    ...actual,
    playClip: vi.fn(async () => undefined),
    stopClip: vi.fn(),
  };
});

// The clip URL the step's wire metadata carries (Z4 shape). Both tests
// use the same URL so the ONLY variable between them is the flag.
const STEP_CLIP_URL = "/api/static/tts/af_bella/0123456789abcdef.wav";

// Minimal active activity with a step-body clip URL in metadata — the
// Z5 wire shape (``spoken_audio_url``) StepCard threads to
// ReadMeButton. Mirrors App.k9-flag-threading.test.tsx's seed.
function seedActiveActivity(): Activity {
  const activity: Activity = {
    id: "act-z6",
    state: "running",
    version: 1,
    title: "Z6 threading",
    summary: null,
    persona_id: null,
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-07-03T12:00:00Z",
    started_at: "2026-07-03T12:00:00Z",
    ended_at: null,
    steps: [
      {
        seq: 1,
        body: "Touch any word",
        sfx: null,
        expected_action: null,
        current: true,
        action_slot: null,
        metadata: { spoken_audio_url: STEP_CLIP_URL },
      },
    ],
    metadata: {},
  };
  return activity;
}

function stubBootstrapFetch(opts: { neuralVoiceValue: boolean }): void {
  const handler = async (input: string | URL | Request): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/auth/parent")) {
      return new Response(
        JSON.stringify({
          token: "tok-z6",
          expires_at: 4102444800,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    // Per-flag responses. Defaults match the seeded backend defaults;
    // only the neural-voice flag varies per test.
    const defaultByPath: Record<string, boolean> = {
      "/api/settings/jokes-enabled": true,
      "/api/settings/songs-enabled": true,
      "/api/settings/play-standalone-enabled": true,
      "/api/settings/clickable-words-enabled": true,
      "/api/settings/read-me-button-enabled": true,
      "/api/settings/neural-voice-enabled": opts.neuralVoiceValue,
    };
    for (const [path, value] of Object.entries(defaultByPath)) {
      if (url.endsWith(path)) {
        return new Response(JSON.stringify({ value }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    return new Response("", { status: 404 });
  };
  const mock = vi.fn(handler) as unknown as Mock;
  vi.stubGlobal("fetch", mock);
}

async function bootKioskAndSeedActivity(): Promise<void> {
  (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ =
    "1357";
  // Seed an active activity BEFORE mount so the first render lands on
  // the StepCard path — same store shortcut the K9 threading test uses.
  useChildStore.getState().setActivity(seedActiveActivity());
  const { App } = await import("./App");
  render(<App />);
}

function resetStore(): void {
  useChildStore.setState({
    token: null,
    tokenExpiresAt: null,
    activity: null,
    wsState: "idle",
    toasts: [],
    nextToastId: 1,
  });
}

beforeEach(() => {
  resetStore();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  delete (window as unknown as { __TOYBOX_KIOSK_PIN__?: string })
    .__TOYBOX_KIOSK_PIN__;
  resetStore();
});

// Wait until the bootstrap-fetched flag value visibly lands in the
// kiosk root's data-flag-* attr — the signal that the fetched value
// (not the optimistic default) is what threads into StepCard below.
async function waitForNeuralVoiceAttr(expected: "true" | "false"): Promise<void> {
  await waitFor(() => {
    const root = document.querySelector('[data-testid="child-root"]');
    expect(root).not.toBeNull();
    expect(root!.getAttribute("data-flag-neural-voice-enabled")).toBe(
      expected,
    );
  });
}

describe("Kiosk Z6 — neural_voice_enabled flag threading (App → StepCard → speech surface)", () => {
  it("flag fetched OFF routes a Read Me tap straight to Web Speech — no clip attempt despite a wire clip URL", async () => {
    stubBootstrapFetch({ neuralVoiceValue: false });
    await bootKioskAndSeedActivity();
    await waitForNeuralVoiceAttr("false");

    fireEvent.click(screen.getByTestId("read-me-button"));

    await waitFor(() => {
      // Web Speech path fired with the step body...
      expect(speak).toHaveBeenCalledWith("Touch any word", expect.anything());
    });
    // ...and the clip path was NEVER attempted (the whole point of the
    // parent flag: OFF means zero neural-clip playback on the kiosk).
    expect(playClip).not.toHaveBeenCalled();
  });

  it("flag fetched ON (the default) prefers the neural clip — playClip gets the wire URL, no Web Speech", async () => {
    stubBootstrapFetch({ neuralVoiceValue: true });
    await bootKioskAndSeedActivity();
    await waitForNeuralVoiceAttr("true");

    fireEvent.click(screen.getByTestId("read-me-button"));

    await waitFor(() => {
      expect(playClip).toHaveBeenCalledWith(STEP_CLIP_URL);
    });
    // The clip resolved (mock resolves), so no Web Speech fallback.
    expect(speak).not.toHaveBeenCalled();
  });
});
