// Phase O Step O1 regression tests for the PlaySubTab widening +
// localStorage migration.
//
// O1 widens ``PlaySubTab`` from the H2 pair
// (``"play-ideas" | "transcription"``) to the five-value union
// (``"all" | "adventures" | "elements" | "feelings-friends" |
// "transcriptions"``). The localStorage key
// ``toybox.parent.tabs.play`` must be migrated at mount so a parent
// returning from H/I/J/K/L/M/N sees their previous selection mapped
// into the new tab shell instead of falling all the way back to the
// default and losing their seat.
//
// Migration table (one-shot at mount):
//   stored "play-ideas"    → write "all"
//   stored "transcription" → write "transcriptions"
//   stored anything else   → write "all" (the new default)
//   no stored value        → no crash, default "all"
//
// The new value MUST be written back so a reload sees the migrated
// key (i.e. the migration is idempotent — a second mount finds the
// post-migration value already valid and is a no-op).
//
// The fetch + login stubs mirror ``App.bootstrap.test.tsx`` so the
// post-PIN tab shell mounts in tests; we then introspect
// localStorage + the rendered ``aria-selected`` attribute on the
// new sub-tab buttons to pin both halves of the contract.

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { useParentStore } from "./store";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const PLAY_TAB_STORAGE_KEY = "toybox.parent.tabs.play";

function stubFullAuthFetch(): Mock {
  const handler = async (
    input: string | URL | Request,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.endsWith("/api/auth/parent/status")) {
      return new Response(
        JSON.stringify({
          pin_set: true,
          locked: false,
          seconds_until_unlock: 0,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/auth/parent") && method === "POST") {
      return new Response(
        JSON.stringify({
          token: "test-token",
          expires_at: Math.floor(Date.now() / 1000) + 3600,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/metrics")) {
      return new Response(
        JSON.stringify({
          generated_at: "2026-05-18T12:00:00Z",
          ws_subscribers: 0,
          activities: {
            proposed_current: 0,
            approved_current: 0,
            running_current: 0,
            completed_current: 0,
            ended_current: 0,
            dismissed_current: 0,
            didnt_work_current: 0,
            last_24h: {},
          },
          transcripts: { total: 0, last_24h: 0 },
          audio: {
            mic_device: null,
            queue_depth: 0,
            buffer_overruns_total: 0,
            mic_enabled: true,
          },
          ai: {
            breaker_state: "closed",
            breaker_retry_after_iso: null,
            claude_capable: false,
            claude_capability_reason: "token_missing",
            listening_mode: 3,
            min_interval_throttle_seconds: 15.0,
          },
          activity_quality: {
            last_24h_mean_scores: {},
            judge_parent_agreement: {
              overlap_count: 0,
              agreement_rate: null,
              metric_name: "sign_agreement_rate",
            },
            safety_autofails_last_24h: 0,
          },
          eval_gate: {
            last_run_at: null,
            mean_dimension_scores: null,
            regressions_detected: 0,
            placeholder_baseline: true,
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/settings/transcript-retention")) {
      return new Response(JSON.stringify({ seconds: 60 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/settings/play-cadence-seconds")) {
      return new Response(JSON.stringify({ value: 30 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/settings/play-target-depth")) {
      return new Response(JSON.stringify({ value: 3 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/activities/proposed?include_active=true")) {
      return new Response(JSON.stringify({ items: [], active: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    const featureFlagDefaults: Record<string, boolean> = {
      "/api/settings/jokes-enabled": true,
      "/api/settings/songs-enabled": true,
      "/api/settings/play-standalone-enabled": true,
      "/api/settings/clickable-words-enabled": true,
      "/api/settings/read-me-button-enabled": true,
    };
    for (const [path, value] of Object.entries(featureFlagDefaults)) {
      if (url.endsWith(path)) {
        return new Response(JSON.stringify({ value }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    return new Response(JSON.stringify({ items: [], next: null }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  const fetchMock = vi.fn(handler) as unknown as Mock;
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function driveLoginToTabShell(): Promise<void> {
  await waitFor(() => {
    expect(screen.queryByTestId("pin-login")).toBeTruthy();
  });
  const input = screen.getByTestId("pin-login-pin-input") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "1234" } });
  fireEvent.click(screen.getByTestId("pin-login-submit"));
  await waitFor(() => {
    expect(screen.queryByTestId("tabs")).toBeTruthy();
  });
}

describe("App Phase O Step O1 PlaySubTab localStorage migration", () => {
  beforeEach(() => {
    useParentStore.setState({
      token: null,
      active: null,
      proposedList: [],
      wsState: "idle",
      toasts: [],
      capabilityReason: null,
    } as Partial<ReturnType<typeof useParentStore.getState>>);
    window.localStorage.clear();
  });

  it("migrates legacy 'play-ideas' → 'all' and selects the All sub-tab", async () => {
    window.localStorage.setItem(PLAY_TAB_STORAGE_KEY, "play-ideas");
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    // After mount the migration MUST have rewritten the storage value
    // so a hard reload finds the new key directly.
    await waitFor(() => {
      expect(window.localStorage.getItem(PLAY_TAB_STORAGE_KEY)).toBe("all");
    });
    // The new "all" sub-tab is the selected one.
    const allTab = screen.getByTestId("subtab-all");
    expect(allTab.getAttribute("aria-selected")).toBe("true");
    // The legacy "play-ideas" sub-tab MUST NOT render.
    expect(screen.queryByTestId("subtab-play-ideas")).toBeNull();
  });

  it("migrates legacy 'transcription' → 'transcriptions' and selects Transcriptions", async () => {
    window.localStorage.setItem(PLAY_TAB_STORAGE_KEY, "transcription");
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    await waitFor(() => {
      expect(window.localStorage.getItem(PLAY_TAB_STORAGE_KEY)).toBe(
        "transcriptions",
      );
    });
    const transcriptionsTab = screen.getByTestId("subtab-transcriptions");
    expect(transcriptionsTab.getAttribute("aria-selected")).toBe("true");
    // The legacy "transcription" sub-tab MUST NOT render.
    expect(screen.queryByTestId("subtab-transcription")).toBeNull();
  });

  it("rewrites an unknown stored value to the new default 'all'", async () => {
    window.localStorage.setItem(PLAY_TAB_STORAGE_KEY, "garbage");
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    await waitFor(() => {
      expect(window.localStorage.getItem(PLAY_TAB_STORAGE_KEY)).toBe("all");
    });
    const allTab = screen.getByTestId("subtab-all");
    expect(allTab.getAttribute("aria-selected")).toBe("true");
  });

  it("defaults to 'all' with no stored value and no crash", async () => {
    // localStorage already cleared in beforeEach.
    expect(window.localStorage.getItem(PLAY_TAB_STORAGE_KEY)).toBeNull();
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    const allTab = screen.getByTestId("subtab-all");
    expect(allTab.getAttribute("aria-selected")).toBe("true");
    // The migration writes the new default back so a subsequent reload
    // doesn't have to re-run the missing-key branch.
    await waitFor(() => {
      expect(window.localStorage.getItem(PLAY_TAB_STORAGE_KEY)).toBe("all");
    });
  });

  it("treats an already-valid 'all' as a no-op (idempotent)", async () => {
    window.localStorage.setItem(PLAY_TAB_STORAGE_KEY, "all");
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    // Value unchanged.
    expect(window.localStorage.getItem(PLAY_TAB_STORAGE_KEY)).toBe("all");
    const allTab = screen.getByTestId("subtab-all");
    expect(allTab.getAttribute("aria-selected")).toBe("true");
  });

  it("preserves an already-valid 'adventures' selection", async () => {
    window.localStorage.setItem(PLAY_TAB_STORAGE_KEY, "adventures");
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    expect(window.localStorage.getItem(PLAY_TAB_STORAGE_KEY)).toBe(
      "adventures",
    );
    const advTab = screen.getByTestId("subtab-adventures");
    expect(advTab.getAttribute("aria-selected")).toBe("true");
  });
});

describe("App Phase O Step O1 PlaySubTab 5-tab rendering + click-through", () => {
  beforeEach(() => {
    useParentStore.setState({
      token: null,
      active: null,
      proposedList: [],
      wsState: "idle",
      toasts: [],
      capabilityReason: null,
    } as Partial<ReturnType<typeof useParentStore.getState>>);
    window.localStorage.clear();
  });

  it("renders all five sub-tabs with the exact label strings in order", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    // The five sub-tabs MUST exist with the new testids.
    expect(screen.queryByTestId("subtab-all")).toBeTruthy();
    expect(screen.queryByTestId("subtab-adventures")).toBeTruthy();
    expect(screen.queryByTestId("subtab-elements")).toBeTruthy();
    expect(screen.queryByTestId("subtab-feelings-friends")).toBeTruthy();
    expect(screen.queryByTestId("subtab-transcriptions")).toBeTruthy();
    // Label strings — case + spacing pinned literally.
    expect(screen.getByTestId("subtab-all").textContent).toBe("All");
    expect(screen.getByTestId("subtab-adventures").textContent).toBe(
      "Adventures",
    );
    expect(screen.getByTestId("subtab-elements").textContent).toBe(
      "Elements",
    );
    expect(screen.getByTestId("subtab-feelings-friends").textContent).toBe(
      "Feelings & Friends",
    );
    expect(screen.getByTestId("subtab-transcriptions").textContent).toBe(
      "Transcriptions",
    );
    // Order: read every direct tab descendant inside subtabs and pin
    // the sequence so a future shuffle (or accidental swap with the
    // top-tabs row) fails fast.
    const subtabsContainer = screen.getByTestId("subtabs");
    const tabKeys = Array.from(
      subtabsContainer.querySelectorAll("[data-testid^='subtab-']"),
    ).map((el) => el.getAttribute("data-testid"));
    expect(tabKeys).toEqual([
      "subtab-all",
      "subtab-adventures",
      "subtab-elements",
      "subtab-feelings-friends",
      "subtab-transcriptions",
    ]);
  });

  it("clicking each sub-tab updates selection and persists to localStorage", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    const targets: ReadonlyArray<{ testid: string; key: string }> = [
      { testid: "subtab-adventures", key: "adventures" },
      { testid: "subtab-elements", key: "elements" },
      { testid: "subtab-feelings-friends", key: "feelings-friends" },
      { testid: "subtab-transcriptions", key: "transcriptions" },
      { testid: "subtab-all", key: "all" },
    ];
    for (const { testid, key } of targets) {
      fireEvent.click(screen.getByTestId(testid));
      // aria-selected flips on the clicked tab.
      await waitFor(() => {
        expect(screen.getByTestId(testid).getAttribute("aria-selected")).toBe(
          "true",
        );
      });
      // localStorage matches the new key.
      expect(window.localStorage.getItem(PLAY_TAB_STORAGE_KEY)).toBe(key);
    }
  });
});

describe("App Phase O Step O1 PlaySubTab empty-state copy", () => {
  beforeEach(() => {
    useParentStore.setState({
      token: null,
      active: null,
      proposedList: [],
      wsState: "idle",
      toasts: [],
      capabilityReason: null,
    } as Partial<ReturnType<typeof useParentStore.getState>>);
    window.localStorage.clear();
  });

  it("All tab: renders 'No play ideas yet. Approve one when a suggestion appears.'", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    // Default tab is 'all'; the empty queue should show the All copy.
    await waitFor(() => {
      expect(
        screen.getByText(
          "No play ideas yet. Approve one when a suggestion appears.",
        ),
      ).toBeTruthy();
    });
  });

  it("Adventures tab: renders 'No adventures suggested yet.'", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    fireEvent.click(screen.getByTestId("subtab-adventures"));
    await waitFor(() => {
      expect(screen.getByText("No adventures suggested yet.")).toBeTruthy();
    });
  });

  it("Elements tab: renders 'No element activities suggested yet.'", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    fireEvent.click(screen.getByTestId("subtab-elements"));
    await waitFor(() => {
      expect(
        screen.getByText("No element activities suggested yet."),
      ).toBeTruthy();
    });
  });

  it("Feelings & Friends tab: renders 'No feelings & friends activities suggested yet.'", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    fireEvent.click(screen.getByTestId("subtab-feelings-friends"));
    await waitFor(() => {
      expect(
        screen.getByText(
          "No feelings & friends activities suggested yet.",
        ),
      ).toBeTruthy();
    });
  });
});

describe("App Phase O Step O1 PlayQueueList routing per sub-tab", () => {
  beforeEach(() => {
    useParentStore.setState({
      token: null,
      active: null,
      proposedList: [],
      wsState: "idle",
      toasts: [],
      capabilityReason: null,
    } as Partial<ReturnType<typeof useParentStore.getState>>);
    window.localStorage.clear();
  });

  it("Transcriptions sub-tab continues to mount TranscriptsManager (not PlayQueueList)", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    fireEvent.click(screen.getByTestId("subtab-transcriptions"));
    // TranscriptsManager is the only mounted surface on this tab;
    // PlayQueueList must NOT render here.
    await waitFor(() => {
      expect(screen.queryByTestId("transcripts-manager")).toBeTruthy();
    });
    expect(screen.queryByTestId("play-queue-list")).toBeNull();
  });

  it("All sub-tab mounts PlayQueueList (not TranscriptsManager)", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    // 'all' is the default tab — it MUST exist (legacy "play-ideas"
    // sub-tab is now retired) and the PlayQueueList MUST be mounted
    // beneath it.
    expect(screen.queryByTestId("subtab-all")).toBeTruthy();
    expect(screen.getByTestId("subtab-all").getAttribute("aria-selected")).toBe(
      "true",
    );
    await waitFor(() => {
      expect(screen.queryByTestId("play-queue-list")).toBeTruthy();
    });
    expect(screen.queryByTestId("transcripts-manager")).toBeNull();
  });

  it("Adventures / Elements / Feelings sub-tabs each mount PlayQueueList", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();
    for (const testid of [
      "subtab-adventures",
      "subtab-elements",
      "subtab-feelings-friends",
    ]) {
      fireEvent.click(screen.getByTestId(testid));
      await waitFor(() => {
        expect(screen.queryByTestId("play-queue-list")).toBeTruthy();
      });
      expect(screen.queryByTestId("transcripts-manager")).toBeNull();
    }
  });
});
