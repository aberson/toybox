// Phase K Step K9 — App → StepCard flag threading integration test.
//
// The K2 bootstrap fetch test (``App.k2-feature-flags.test.tsx``)
// already pins the fetch → ``featureFlags`` state pipeline. THIS file
// pins the next leg: ``featureFlags.{clickable_words_enabled,
// read_me_button_enabled}`` → ``StepCard`` props → rendered K9
// affordances (ClickableText word spans + ReadMeButton bubble).
//
// Code-quality rule §4: a new component required to be invoked from
// existing production code must have an integration test through the
// production caller. The K9 components are reached via StepCard, which
// is reached via App. Seeding the kiosk store with an "approved"
// activity drives the production rendering path that mounts both.
//
// We mock the TTS substrate (no speechSynthesis in happy-dom).

import { cleanup, render, screen, waitFor } from "@testing-library/react";
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

// Minimal active activity that drives StepCard onto the screen — the
// kiosk's ``isActiveKioskActivity`` predicate accepts "approved" and
// "running". One step with body text is enough to exercise both K9
// affordances (ClickableText wraps the body; ReadMeButton mounts on
// the step kind defaulting to "text").
function seedActiveActivity(): Activity {
  const activity: Activity = {
    id: "act-k9",
    state: "running",
    version: 1,
    title: "K9 threading",
    summary: null,
    persona_id: null,
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-05-15T12:00:00Z",
    started_at: "2026-05-15T12:00:00Z",
    ended_at: null,
    steps: [
      {
        seq: 1,
        body: "Touch any word",
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

function stubBootstrapFetch(opts: {
  clickableWordsValue?: boolean;
  readMeButtonValue?: boolean;
}): void {
  const handler = async (input: string | URL | Request): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/auth/parent")) {
      return new Response(
        JSON.stringify({
          token: "tok-k9",
          expires_at: 4102444800,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    // Per-flag responses. Defaults match the K2 seeded backend defaults.
    const defaultByPath: Record<string, boolean> = {
      "/api/settings/jokes-enabled": true,
      "/api/settings/songs-enabled": true,
      "/api/settings/play-standalone-enabled": true,
      "/api/settings/play-embedded-enabled": true,
      "/api/settings/play-endings-enabled": true,
      "/api/settings/play-spontaneity-enabled": false,
      "/api/settings/clickable-words-enabled":
        opts.clickableWordsValue ?? true,
      "/api/settings/read-me-button-enabled":
        opts.readMeButtonValue ?? true,
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
  // the StepCard path. This bypasses the WS envelope plumbing entirely —
  // the same setState the WS path drives through ``applyEnvelope``.
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
  // Reset the singleton store so a leftover activity doesn't poison the
  // next test in the run.
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
  // Clean slate for each test — a prior boot may have left mounted
  // ChildStore state behind.
  useChildStore.setState({
    token: null,
    tokenExpiresAt: null,
    activity: null,
    wsState: "idle",
    toasts: [],
    nextToastId: 1,
  });
});

describe("Kiosk K9 — App → StepCard flag threading", () => {
  it("renders Read Me button + ClickableText word spans when both flags fetched true", async () => {
    stubBootstrapFetch({
      clickableWordsValue: true,
      readMeButtonValue: true,
    });
    await bootKioskAndSeedActivity();
    // The bootstrap fetch is async; the data-flag-* attributes flip
    // when the fetched values land. Wait for one of them so we know
    // the props are threaded through, then assert the K9 components.
    await waitFor(() => {
      const root = document.querySelector('[data-testid="child-root"]');
      expect(root).not.toBeNull();
      expect(
        root!.getAttribute("data-flag-clickable-words-enabled"),
      ).toBe("true");
      expect(
        root!.getAttribute("data-flag-read-me-button-enabled"),
      ).toBe("true");
    });
    // Read Me button is mounted.
    expect(screen.getByTestId("read-me-button")).not.toBeNull();
    // ClickableText is enabled on the step body — word spans present.
    const wrapper = screen.getByTestId("clickable-text");
    expect(wrapper.getAttribute("data-clickable")).toBe("true");
    expect(screen.getAllByTestId("clickable-word").length).toBeGreaterThan(0);
  });

  it("hides Read Me button + renders plain text when both flags fetched false", async () => {
    stubBootstrapFetch({
      clickableWordsValue: false,
      readMeButtonValue: false,
    });
    await bootKioskAndSeedActivity();
    // Same wait pattern — the K2 data-flag-* attrs are our visible
    // signal that the bootstrap fetch resolved.
    await waitFor(() => {
      const root = document.querySelector('[data-testid="child-root"]');
      expect(root).not.toBeNull();
      expect(
        root!.getAttribute("data-flag-clickable-words-enabled"),
      ).toBe("false");
      expect(
        root!.getAttribute("data-flag-read-me-button-enabled"),
      ).toBe("false");
    });
    // No Read Me bubble.
    expect(screen.queryByTestId("read-me-button")).toBeNull();
    // ClickableText still mounts as a span (it's the wrapper) but
    // ``data-clickable=false`` and no word spans inside.
    const wrapper = screen.getByTestId("clickable-text");
    expect(wrapper.getAttribute("data-clickable")).toBe("false");
    expect(screen.queryAllByTestId("clickable-word")).toHaveLength(0);
  });
});
