// Kiosk auto-advance: when an activity first appears in ``approved``
// state, the kiosk auto-fires one /advance so the kid's first "Next"
// tap visibly moves seq=1 → seq=2.
//
// Without this, the backend's approved → running branch keeps current
// on seq=1 (it only flips state), so the kid sees step 1, taps Next,
// and is still on step 1 — exactly the UX bug this guards against.

import { cleanup, render, waitFor } from "@testing-library/react";
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

function approvedActivity(id = "act-auto"): Activity {
  return {
    id,
    state: "approved",
    version: 1,
    title: "Auto-advance test",
    summary: null,
    persona_id: null,
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-05-16T12:00:00Z",
    started_at: null,
    ended_at: null,
    steps: [
      {
        seq: 1,
        body: "Step one",
        sfx: null,
        expected_action: null,
        current: true,
        action_slot: null,
      },
    ],
    metadata: {},
  };
}

interface FetchCall {
  url: string;
  init?: RequestInit;
}

function stubFetchAndCaptureAdvance(): FetchCall[] {
  const calls: FetchCall[] = [];
  const handler = async (
    input: string | URL | Request,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ url, init });
    if (url.endsWith("/api/auth/parent")) {
      return new Response(
        JSON.stringify({
          token: "tok-auto",
          expires_at: 4102444800,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    const flagDefaults: Record<string, boolean> = {
      "/api/settings/jokes-enabled": true,
      "/api/settings/songs-enabled": true,
      "/api/settings/play-standalone-enabled": true,
      "/api/settings/play-embedded-enabled": true,
      "/api/settings/play-endings-enabled": true,
      "/api/settings/play-spontaneity-enabled": false,
      "/api/settings/clickable-words-enabled": true,
      "/api/settings/read-me-button-enabled": true,
      "/api/settings/neural-voice-enabled": true,
    };
    for (const [path, value] of Object.entries(flagDefaults)) {
      if (url.endsWith(path)) {
        return new Response(JSON.stringify({ value }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    if (url.includes("/advance")) {
      const advanced: Activity = {
        ...approvedActivity(),
        state: "running",
        version: 2,
        started_at: "2026-05-16T12:00:01Z",
        steps: [
          {
            seq: 1,
            body: "Step one",
            sfx: null,
            expected_action: null,
            current: false,
            action_slot: null,
          },
          {
            seq: 2,
            body: "Step two",
            sfx: null,
            expected_action: null,
            current: true,
            action_slot: null,
          },
        ],
      };
      return new Response(JSON.stringify(advanced), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("", { status: 404 });
  };
  const mock = vi.fn(handler) as unknown as Mock;
  vi.stubGlobal("fetch", mock);
  return calls;
}

async function bootKioskWith(activity: Activity): Promise<void> {
  (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ =
    "1357";
  useChildStore.getState().setActivity(activity);
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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  delete (window as unknown as { __TOYBOX_KIOSK_PIN__?: string })
    .__TOYBOX_KIOSK_PIN__;
  resetStore();
});

beforeEach(() => {
  resetStore();
});

describe("Kiosk auto-advance on approved", () => {
  it("fires /advance exactly once when activity is seen in approved state", async () => {
    const calls = stubFetchAndCaptureAdvance();
    await bootKioskWith(approvedActivity());

    await waitFor(() => {
      const advanceCalls = calls.filter((c) =>
        c.url.endsWith("/api/activities/act-auto/advance"),
      );
      expect(advanceCalls.length).toBe(1);
      expect(advanceCalls[0]?.init?.method).toBe("POST");
    });

    // Store reflects the advanced activity (running, seq=2 current).
    await waitFor(() => {
      const a = useChildStore.getState().activity;
      expect(a?.state).toBe("running");
      const current = a?.steps.find((s) => s.current);
      expect(current?.seq).toBe(2);
    });

    // No second /advance is fired on subsequent renders.
    const advanceCount = calls.filter((c) =>
      c.url.endsWith("/api/activities/act-auto/advance"),
    ).length;
    expect(advanceCount).toBe(1);
  });

  it("does NOT fire /advance when activity is already running", async () => {
    const calls = stubFetchAndCaptureAdvance();
    const running: Activity = {
      ...approvedActivity("act-running"),
      state: "running",
      started_at: "2026-05-16T12:00:01Z",
    };
    await bootKioskWith(running);

    // Wait for the bootstrap to complete (flag fetches resolve).
    await waitFor(() => {
      const root = document.querySelector('[data-testid="child-root"]');
      expect(root).not.toBeNull();
    });

    const advanceCalls = calls.filter((c) =>
      c.url.endsWith("/api/activities/act-running/advance"),
    );
    expect(advanceCalls.length).toBe(0);
  });
});
