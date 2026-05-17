// L follow-up Change C — "All done!" heading shine.
//
// The kiosk's terminal screen gets a CSS-keyframe shine on the
// "All done!" heading ONLY when the activity's last reward step was
// kind=joke or kind=song. Picture rewards have their own visual
// finale (the reward step's per-image animation), and "no reward" /
// reward_type=none paths stay plain.
//
// This test seeds the kiosk store with a completed activity carrying
// the relevant reward step shape and asserts the rendered heading
// either does or does not carry the ``all-done-shine`` class. Mirrors
// the seeding pattern used in ``App.k9-flag-threading.test.tsx``.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Activity, ActivityStep } from "./api";
import { useChildStore } from "./store";

vi.mock("./tts", async () => ({
  speak: vi.fn(async () => undefined),
  cancel: vi.fn(),
}));

function regularStep(seq: number, body: string): ActivityStep {
  return {
    seq,
    body,
    sfx: null,
    expected_action: null,
    current: false,
    action_slot: null,
  };
}

function rewardStep(
  seq: number,
  rewardKind: "picture" | "joke" | "song",
): ActivityStep {
  return {
    seq,
    body: `reward step body for ${rewardKind}`,
    sfx: null,
    expected_action: null,
    current: false,
    action_slot: null,
    kind: "reward",
    metadata: {
      reward_kind: rewardKind,
      reward_id: `${rewardKind}-1`,
    },
  };
}

function completedActivity(steps: ActivityStep[]): Activity {
  return {
    id: "act-all-done",
    state: "completed",
    version: 2,
    title: "Adventure",
    summary: null,
    persona_id: null,
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-05-17T00:00:00Z",
    started_at: "2026-05-17T00:00:00Z",
    ended_at: "2026-05-17T00:01:00Z",
    steps,
    metadata: {},
  };
}

function stubBootstrapFetch(): void {
  const handler = async (input: string | URL | Request): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/auth/parent")) {
      return new Response(
        JSON.stringify({
          token: "tok-shine",
          expires_at: 4102444800,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    const flagPaths: Record<string, boolean> = {
      "/api/settings/jokes-enabled": true,
      "/api/settings/songs-enabled": true,
      "/api/settings/play-standalone-enabled": true,
      "/api/settings/clickable-words-enabled": true,
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
  vi.stubGlobal("fetch", vi.fn(handler) as unknown as Mock);
}

async function mountWithActivity(activity: Activity): Promise<void> {
  (
    window as unknown as { __TOYBOX_KIOSK_PIN__: string }
  ).__TOYBOX_KIOSK_PIN__ = "1357";
  useChildStore.getState().setActivity(activity);
  const { App } = await import("./App");
  render(<App />);
  // The completed activity drives the "All done!" branch directly —
  // wait for the heading to land in the DOM rather than for a
  // bootstrap-fetch attribute (the all-done screen mounts on the
  // first render once the PIN gate clears).
  await waitFor(() => {
    expect(screen.queryByTestId("all-done-heading")).not.toBeNull();
  });
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
  stubBootstrapFetch();
});

describe('Kiosk — "All done!" heading shine (L follow-up Change C)', () => {
  it("no reward step → plain heading (no shine class)", async () => {
    const activity = completedActivity([
      regularStep(1, "Step one"),
      regularStep(2, "Step two"),
      regularStep(3, "Step three"),
    ]);
    await mountWithActivity(activity);
    const heading = screen.getByTestId("all-done-heading");
    expect(heading.getAttribute("data-shine")).toBe("false");
    expect(heading.classList.contains("all-done-shine")).toBe(false);
  });

  it("picture reward step → plain heading (no shine class)", async () => {
    // Picture rewards have their own visual finale; the heading stays
    // plain to avoid double-stacked animation.
    const activity = completedActivity([
      regularStep(1, "Step one"),
      regularStep(2, "Step two"),
      regularStep(3, "Step three"),
      rewardStep(4, "picture"),
    ]);
    await mountWithActivity(activity);
    const heading = screen.getByTestId("all-done-heading");
    expect(heading.getAttribute("data-shine")).toBe("false");
    expect(heading.classList.contains("all-done-shine")).toBe(false);
  });

  it("joke reward step → shine class applied", async () => {
    const activity = completedActivity([
      regularStep(1, "Step one"),
      regularStep(2, "Step two"),
      regularStep(3, "Step three"),
      rewardStep(4, "joke"),
    ]);
    await mountWithActivity(activity);
    const heading = screen.getByTestId("all-done-heading");
    expect(heading.getAttribute("data-shine")).toBe("true");
    expect(heading.classList.contains("all-done-shine")).toBe(true);
  });

  it("song reward step → shine class applied", async () => {
    const activity = completedActivity([
      regularStep(1, "Step one"),
      regularStep(2, "Step two"),
      regularStep(3, "Step three"),
      rewardStep(4, "song"),
    ]);
    await mountWithActivity(activity);
    const heading = screen.getByTestId("all-done-heading");
    expect(heading.getAttribute("data-shine")).toBe("true");
    expect(heading.classList.contains("all-done-shine")).toBe(true);
  });
});
