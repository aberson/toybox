// Phase S Step S1 — persona-keyed background gradient integration test.
//
// Code-quality rule §4: the new ``gradientForPersona`` is wired from
// App.tsx's render path into the root element's ``style.background``.
// This test exercises the production entry point (App) and asserts that
// an activity with a known persona_id causes the root element to carry
// the persona's gradient string — the silent-wiring guard. A regression
// where ``gradientForPersona`` is imported but never called, or called
// but its return value discarded, would leave the root element showing
// the idle fallback gradient instead.

import { cleanup, render, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChildStore } from "./store";
import type { Activity } from "./api";
import { gradientForPersona } from "./theming";

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

function stubBootstrapFetch(): Mock {
  const handler = async (input: string | URL | Request): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/auth/parent")) {
      return new Response(
        JSON.stringify({
          token: "tok-s1",
          expires_at: 4102444800,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    // Feature flag endpoints + spoken-text-limit — return benign defaults.
    const defaultPaths: Record<string, unknown> = {
      "/api/settings/jokes-enabled": { value: true },
      "/api/settings/songs-enabled": { value: true },
      "/api/settings/play-standalone-enabled": { value: true },
      "/api/settings/clickable-words-enabled": { value: false },
      "/api/settings/read-me-button-enabled": { value: false },
      "/api/settings/spoken-text-limit": { value: 150 },
    };
    for (const [path, body] of Object.entries(defaultPaths)) {
      if (url.endsWith(path)) {
        return new Response(JSON.stringify(body), {
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

function makeActivity(persona_id: string | null): Activity {
  return {
    id: "act-s1-gradient",
    state: "running",
    version: 1,
    title: "Gradient test activity",
    summary: null,
    persona_id,
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-06-05T12:00:00Z",
    started_at: "2026-06-05T12:00:00Z",
    ended_at: null,
    steps: [
      {
        seq: 1,
        body: "Test step body",
        sfx: null,
        expected_action: null,
        current: true,
        action_slot: null,
      },
    ],
    metadata: {},
  };
}

describe("App S1 — persona gradient wiring", () => {
  it("root element background uses the detective gradient when activity.persona_id = 'detective'", async () => {
    stubBootstrapFetch();
    (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ =
      "1357";
    useChildStore.getState().setActivity(makeActivity("detective"));

    const { App } = await import("./App");
    const { container } = render(<App />);

    const expectedGradient = gradientForPersona("detective");

    await waitFor(() => {
      const root = container.querySelector(
        '[data-testid="child-root"]',
      ) as HTMLElement | null;
      expect(root).not.toBeNull();
      expect(root!.style.background).toBe(expectedGradient);
    });
  });

  it("root element background uses the periodic_table gradient when persona_id = 'periodic_table'", async () => {
    stubBootstrapFetch();
    (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ =
      "1357";
    useChildStore.getState().setActivity(makeActivity("periodic_table"));

    const { App } = await import("./App");
    const { container } = render(<App />);

    const expectedGradient = gradientForPersona("periodic_table");

    await waitFor(() => {
      const root = container.querySelector(
        '[data-testid="child-root"]',
      ) as HTMLElement | null;
      expect(root).not.toBeNull();
      expect(root!.style.background).toBe(expectedGradient);
    });
  });

  it("root element background updates reactively when persona_id changes mid-session", async () => {
    stubBootstrapFetch();
    (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ =
      "1357";
    useChildStore.getState().setActivity(makeActivity("detective"));

    const { App } = await import("./App");
    const { container } = render(<App />);

    const detectiveGradient = gradientForPersona("detective");
    const periodicTableGradient = gradientForPersona("periodic_table");

    // First confirm detective gradient is applied.
    await waitFor(() => {
      const root = container.querySelector(
        '[data-testid="child-root"]',
      ) as HTMLElement | null;
      expect(root).not.toBeNull();
      expect(root!.style.background).toBe(detectiveGradient);
    });

    // Now update the activity's persona_id to periodic_table and assert
    // the background updates — guards against a useMemo with stale deps.
    useChildStore.getState().setActivity(makeActivity("periodic_table"));

    await waitFor(() => {
      const root = container.querySelector(
        '[data-testid="child-root"]',
      ) as HTMLElement | null;
      expect(root).not.toBeNull();
      expect(root!.style.background).toBe(periodicTableGradient);
    });
  });

  it("root element background uses the idle fallback when activity.persona_id is null", async () => {
    stubBootstrapFetch();
    (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ =
      "1357";
    useChildStore.getState().setActivity(makeActivity(null));

    const { App } = await import("./App");
    const { container } = render(<App />);

    const fallbackGradient = gradientForPersona(null);

    await waitFor(() => {
      const root = container.querySelector(
        '[data-testid="child-root"]',
      ) as HTMLElement | null;
      expect(root).not.toBeNull();
      expect(root!.style.background).toBe(fallbackGradient);
    });
  });
});
