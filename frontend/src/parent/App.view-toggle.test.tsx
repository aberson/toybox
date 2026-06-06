// Phase T Step T3 regression tests for the Queue / Browse catalog
// toggle in App.tsx.
//
// The toggle (data-testid="view-toggle") appears on all Play sub-tabs
// except Transcriptions. Clicking "Browse catalog" switches from
// PlayQueueList to CatalogPanel (data-testid="catalog-panel"); clicking
// "Queue" switches back.
//
// Test setup mirrors App.tab-migration.test.tsx: stubFullAuthFetch
// intercepts all bootstrap fetch calls and drives login via the PIN
// form so the tab shell mounts. GET /api/catalog is also stubbed so
// CatalogPanel can resolve without hitting the network.

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

const CATALOG_RESPONSE = {
  entries: [
    {
      id: "t1",
      title: "Test Template",
      intent: "boredom",
      themes: [],
      step_count: 2,
    },
  ],
  total: 1,
};

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
    if (url.endsWith("/api/catalog")) {
      return new Response(JSON.stringify(CATALOG_RESPONSE), {
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

describe("App Phase T Step T3 Queue/Browse catalog toggle", () => {
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

  it("toggle renders on the Play tab (All sub-tab) with Queue selected by default", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();

    // The view toggle must be present.
    await waitFor(() => {
      expect(screen.queryByTestId("view-toggle")).toBeTruthy();
    });

    // Queue button is the default active selection.
    const queueBtn = screen.getByTestId("view-tab-queue");
    expect(queueBtn.getAttribute("aria-selected")).toBe("true");

    // Browse catalog button is not selected by default.
    const browseBtn = screen.getByTestId("view-tab-browse");
    expect(browseBtn.getAttribute("aria-selected")).toBe("false");
  });

  it("clicking Browse catalog shows catalog-panel, hides play-queue-list", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();

    // Queue view is shown initially.
    await waitFor(() => {
      expect(screen.queryByTestId("play-queue-list")).toBeTruthy();
    });
    expect(screen.queryByTestId("catalog-panel")).toBeNull();

    // Click Browse catalog.
    fireEvent.click(screen.getByTestId("view-tab-browse"));

    // CatalogPanel should appear (may show loading state first, then entries).
    await waitFor(() => {
      expect(screen.queryByTestId("catalog-panel")).toBeTruthy();
    });

    // PlayQueueList must be hidden when Browse is active.
    expect(screen.queryByTestId("play-queue-list")).toBeNull();
  });

  it("clicking Queue after Browse catalog hides catalog-panel, shows play-queue-list", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();

    // Switch to Browse.
    await waitFor(() => {
      expect(screen.queryByTestId("view-toggle")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("view-tab-browse"));
    await waitFor(() => {
      expect(screen.queryByTestId("catalog-panel")).toBeTruthy();
    });

    // Switch back to Queue.
    fireEvent.click(screen.getByTestId("view-tab-queue"));
    await waitFor(() => {
      expect(screen.queryByTestId("play-queue-list")).toBeTruthy();
    });

    // CatalogPanel must not be in the document.
    expect(screen.queryByTestId("catalog-panel")).toBeNull();
  });

  it("toggle does NOT appear on the Transcriptions sub-tab", async () => {
    stubFullAuthFetch();
    render(<App />);
    await driveLoginToTabShell();

    fireEvent.click(screen.getByTestId("subtab-transcriptions"));

    await waitFor(() => {
      expect(screen.queryByTestId("transcripts-manager")).toBeTruthy();
    });

    // The toggle is hidden on the Transcriptions tab.
    expect(screen.queryByTestId("view-toggle")).toBeNull();
  });
});
