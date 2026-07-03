// Phase R Step R2 — App → StepCard spoken text limit bootstrap integration test.
//
// Pins that:
//   1. GET /api/settings/spoken-text-limit is fetched during child bootstrap
//      (alongside the feature flags in the same Promise.allSettled batch).
//   2. The fetched value reaches StepCard via the ``spokenTextLimit`` prop and
//      ultimately controls what ReadMeButton passes to ``speak()``.
//
// Code-quality rule §4: a new component required to be invoked from existing
// production code must have an integration test through the production caller.
// The spoken text limit is fetched in App.tsx and drilled through
// StepCard → ReadMeButton. This file is the integration seam.
//
// Pattern mirrors App.k9-flag-threading.test.tsx: mock TTS substrate, seed an
// active activity in the store pre-mount, stub global fetch, assert the fetch
// happened and the value threads through to the component.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChildStore } from "./store";
import type { Activity } from "./api";

vi.mock("./tts", async () => {
  return {
    speak: vi.fn(async () => undefined),
    cancel: vi.fn(),
  };
});

import * as tts from "./tts";

// Minimal active activity with a text step long enough to exercise truncation.
// "One two three four five" is 23 chars — well above limit=10 and well below
// limit=250 so we can test both truncated and untouched paths.
const LONG_STEP_TEXT = "One two three four five six seven eight nine ten.";

function seedActiveActivity(): Activity {
  const activity: Activity = {
    id: "act-r2",
    state: "running",
    version: 1,
    title: "R2 limit threading",
    summary: null,
    persona_id: null,
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-06-05T12:00:00Z",
    started_at: "2026-06-05T12:00:00Z",
    ended_at: null,
    steps: [
      {
        seq: 1,
        body: LONG_STEP_TEXT,
        sfx: null,
        expected_action: null,
        current: true,
        action_slot: null,
      },
    ],
    metadata: {},
  };
  return activity;
}

function stubBootstrapFetch(spokenTextLimitValue: number): Mock {
  const handler = async (input: string | URL | Request): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/auth/parent")) {
      return new Response(
        JSON.stringify({
          token: "tok-r2",
          expires_at: 4102444800,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/settings/spoken-text-limit")) {
      return new Response(
        JSON.stringify({ value: spokenTextLimitValue }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    // All feature flag endpoints — return defaults so the flags don't block.
    const flagPaths: Record<string, boolean> = {
      "/api/settings/jokes-enabled": true,
      "/api/settings/songs-enabled": true,
      "/api/settings/play-standalone-enabled": true,
      "/api/settings/play-embedded-enabled": true,
      "/api/settings/play-endings-enabled": true,
      "/api/settings/play-spontaneity-enabled": false,
      "/api/settings/clickable-words-enabled": false,
      "/api/settings/read-me-button-enabled": true,
    };
    for (const [path, value] of Object.entries(flagPaths)) {
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
  return mock;
}

async function bootKioskAndSeedActivity(): Promise<void> {
  (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ =
    "1357";
  useChildStore.getState().setActivity(seedActiveActivity());
  const { App } = await import("./App");
  render(<App />);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  delete (window as unknown as { __TOYBOX_KIOSK_PIN__?: string })
    .__TOYBOX_KIOSK_PIN__;
  useChildStore.setState({
    token: null,
    tokenExpiresAt: null,
    activity: null,
    wsState: "idle",
    toasts: [],
    nextToastId: 1,
  });
});

beforeEach(() => {
  useChildStore.setState({
    token: null,
    tokenExpiresAt: null,
    activity: null,
    wsState: "idle",
    toasts: [],
    nextToastId: 1,
  });
});

describe("Kiosk R2 — spoken text limit bootstrap threading", () => {
  it("GET /api/settings/spoken-text-limit is called during bootstrap", async () => {
    const fetchMock = stubBootstrapFetch(150);
    await bootKioskAndSeedActivity();
    // Wait for the bootstrap to complete — the read-me-button appearing is
    // a reliable signal that the feature flags + limit fetch both resolved.
    await waitFor(() => {
      expect(screen.queryByTestId("read-me-button")).not.toBeNull();
    });
    const calls = (fetchMock as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    const limitCalls = calls.filter((args) => {
      const url = typeof args[0] === "string" ? args[0] : String(args[0]);
      return url.includes("/api/settings/spoken-text-limit");
    });
    expect(limitCalls.length).toBeGreaterThanOrEqual(1);
  });

  it("fetched limit=0 (off): clicking Read Me speaks the full text", async () => {
    stubBootstrapFetch(0);
    await bootKioskAndSeedActivity();
    // Wait for the Read Me button to appear (bootstrap resolved + flags landed).
    await waitFor(() => {
      expect(screen.getByTestId("read-me-button")).not.toBeNull();
    });
    fireEvent.click(screen.getByTestId("read-me-button"));
    await waitFor(() => {
      expect(tts.speak).toHaveBeenCalledWith(LONG_STEP_TEXT, expect.anything());
    });
  });

  it("fetched limit=10: clicking Read Me speaks truncated text (word-boundary fallback)", async () => {
    stubBootstrapFetch(10);
    await bootKioskAndSeedActivity();
    await waitFor(() => {
      expect(screen.getByTestId("read-me-button")).not.toBeNull();
    });
    fireEvent.click(screen.getByTestId("read-me-button"));
    // "One two three..." — slice to 10 = "One two th", last space at 7
    // → "One two" + "…"
    await waitFor(() => {
      const call = (tts.speak as ReturnType<typeof vi.fn>).mock.calls[0];
      expect(call).toBeDefined();
      const spokenText = call![0] as string;
      expect(spokenText.endsWith("…")).toBe(true);
      expect(spokenText.length).toBeLessThan(LONG_STEP_TEXT.length);
    });
  });
});
