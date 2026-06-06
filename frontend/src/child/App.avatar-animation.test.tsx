// Phase S Step S2 — avatar animation wiring integration test.
//
// Code-quality rule §4: the new avatarAnimName derivation from
// currentStep.metadata.avatar_animation must be wired end-to-end into
// the PersonaAvatar's animationName prop. This test exercises the
// production entry point (App) and asserts that a running activity with
// a known avatar_animation in the current step causes PersonaAvatar to
// carry the matching class — the silent-wiring guard.

import { cleanup, render, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChildStore } from "./store";
import type { Activity } from "./api";

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
          token: "tok-s2",
          expires_at: 4102444800,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
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

function makeRunningActivity(avatarAnimation: string | null): Activity {
  return {
    id: "act-s2-anim",
    state: "running",
    version: 2,
    title: "Animation test activity",
    summary: null,
    persona_id: "wizard",
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-06-05T12:00:00Z",
    started_at: "2026-06-05T12:01:00Z",
    ended_at: null,
    steps: [
      {
        seq: 1,
        body: "Do the thing!",
        sfx: null,
        expected_action: null,
        current: true,
        action_slot: null,
        metadata: avatarAnimation !== null
          ? { avatar_animation: avatarAnimation }
          : null,
      },
    ],
    metadata: {},
  };
}

describe("App S2 — avatar animation wiring", () => {
  it("PersonaAvatar gets animationName='wobble' when current step has avatar_animation='wobble'", async () => {
    stubBootstrapFetch();
    (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ =
      "1357";
    useChildStore.getState().setActivity(makeRunningActivity("wobble"));

    const { App } = await import("./App");
    const { container } = render(<App />);

    await waitFor(() => {
      const avatar = container.querySelector(
        '[data-testid="persona-avatar"]',
      ) as HTMLElement | null;
      expect(avatar).not.toBeNull();
      expect(avatar!.className).toBe("avatar-animate-wobble");
    });
  });

  it("PersonaAvatar gets animationName='jump' when current step has avatar_animation='jump'", async () => {
    stubBootstrapFetch();
    (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ =
      "1357";
    useChildStore.getState().setActivity(makeRunningActivity("jump"));

    const { App } = await import("./App");
    const { container } = render(<App />);

    await waitFor(() => {
      const avatar = container.querySelector(
        '[data-testid="persona-avatar"]',
      ) as HTMLElement | null;
      expect(avatar).not.toBeNull();
      expect(avatar!.className).toBe("avatar-animate-jump");
    });
  });

  it("PersonaAvatar falls back to 'float' when current step has no avatar_animation in metadata", async () => {
    stubBootstrapFetch();
    (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ =
      "1357";
    useChildStore.getState().setActivity(makeRunningActivity(null));

    const { App } = await import("./App");
    const { container } = render(<App />);

    await waitFor(() => {
      const avatar = container.querySelector(
        '[data-testid="persona-avatar"]',
      ) as HTMLElement | null;
      expect(avatar).not.toBeNull();
      expect(avatar!.className).toBe("avatar-animate-float");
    });
  });
});
