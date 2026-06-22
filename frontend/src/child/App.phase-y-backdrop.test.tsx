// Phase Y Step Y6 — kiosk scene-backdrop layer.
//
// Pins that an active activity carrying a ``scene_url`` renders the
// full-viewport backdrop <img> (+ readability scrim) BEHIND the step card,
// through the production App render path; and that an activity WITHOUT a
// scene_url renders no backdrop (prior flat-gradient look preserved). The
// backdrop is static (no animation) so prefers-reduced-motion has nothing to
// disable — asserted structurally by the absence of any animation style.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Activity } from "./api";
import { useChildStore } from "./store";

vi.mock("./tts", async () => {
  return { speak: vi.fn(async () => undefined), cancel: vi.fn() };
});

function seedActiveActivity(sceneUrl: string | null): Activity {
  const activity: Activity = {
    id: "act-y6",
    state: "running",
    version: 1,
    title: "Y6 backdrop",
    summary: null,
    persona_id: null,
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-06-22T12:00:00Z",
    started_at: "2026-06-22T12:00:00Z",
    ended_at: null,
    steps: [
      {
        seq: 1,
        body: "Look at the scene",
        sfx: null,
        expected_action: null,
        current: true,
        action_slot: null,
      },
    ],
    metadata: {},
  };
  if (sceneUrl !== null) {
    activity.scene_url = sceneUrl;
  }
  return activity;
}

function stubBootstrapFetch(): void {
  const handler = async (input: string | URL | Request): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/auth/parent")) {
      return new Response(
        JSON.stringify({
          token: "tok-y6",
          expires_at: 4102444800,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    // Every settings flag returns a benign default; unknown paths 404.
    if (url.includes("/api/settings/")) {
      const value = !url.endsWith("/api/settings/play-spontaneity-enabled");
      return new Response(JSON.stringify({ value }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("", { status: 404 });
  };
  vi.stubGlobal("fetch", vi.fn(handler) as unknown as Mock);
}

async function bootWithScene(sceneUrl: string | null): Promise<void> {
  (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ = "1357";
  stubBootstrapFetch();
  useChildStore.getState().setActivity(seedActiveActivity(sceneUrl));
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

beforeEach(resetStore);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  delete (window as unknown as { __TOYBOX_KIOSK_PIN__?: string }).__TOYBOX_KIOSK_PIN__;
  resetStore();
});

describe("Kiosk Y6 — scene backdrop layer", () => {
  it("renders the backdrop img + scrim when the activity carries a scene_url", async () => {
    await bootWithScene("/api/static/images/scenes/lab.png");
    await waitFor(() => expect(screen.getByTestId("step-card")).toBeTruthy());

    const backdrop = screen.getByTestId("scene-backdrop") as HTMLImageElement;
    expect(backdrop.tagName).toBe("IMG");
    expect(backdrop.getAttribute("src")).toBe("/api/static/images/scenes/lab.png");
    // Decorative: hidden from a11y tree, no animation (static layer).
    expect(backdrop.getAttribute("aria-hidden")).toBe("true");
    expect(backdrop.style.animation === "" || backdrop.style.animation === undefined).toBe(true);
    expect(screen.getByTestId("scene-scrim")).toBeTruthy();
  });

  it("renders NO backdrop when the activity has no scene_url", async () => {
    await bootWithScene(null);
    await waitFor(() => expect(screen.getByTestId("step-card")).toBeTruthy());

    expect(screen.queryByTestId("scene-backdrop")).toBeNull();
    expect(screen.queryByTestId("scene-scrim")).toBeNull();
  });
});
