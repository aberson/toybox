// Phase I step I3: App.tsx transcript retention plumbing tests.
//
// Three assertions per the plan:
//   1. ``getTranscriptRetention`` fires exactly once on mount (alongside
//      the existing initial-fetch chain).
//   2. The resolved value threads into BOTH ``SettingsPanel.
//      currentRetentionSeconds`` and ``TranscriptsManager.retentionSeconds``.
//      Verified via stub component mocks that capture the props they
//      receive — TranscriptsManager does NOT consume the prop at
//      runtime until I4, so the assertion is at the prop-pass level.
//   3. On fetch rejection, the optimistic default ``60`` is retained
//      and ``console.warn`` is called exactly once.
//
// Stubbing SettingsPanel + TranscriptsManager via ``vi.mock`` keeps the
// assertion focused on App.tsx's wiring layer; the real components have
// their own dedicated test files. ``vi.mock`` is hoisted to the top of
// the module, so this file lives separately from the larger App.test.tsx
// (which exercises the real children).

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ``capturedProps`` is updated on every render of the stubbed children
// so tests can inspect the latest prop pass. Defined inside ``vi.mock``
// factories below as well — but exporting from the factory is awkward,
// so we keep separate module-level holders here and assign into them
// from the mocked factory via closures captured at hoist time.
const settingsPanelPropsLog: Array<Record<string, unknown>> = [];
const transcriptsManagerPropsLog: Array<Record<string, unknown>> = [];

vi.mock("./components/SettingsPanel", () => ({
  SettingsPanel: (props: Record<string, unknown>) => {
    settingsPanelPropsLog.push(props);
    return <div data-testid="settings-panel" />;
  },
}));

vi.mock("./components/TranscriptsManager", () => ({
  TranscriptsManager: (props: Record<string, unknown>) => {
    transcriptsManagerPropsLog.push(props);
    return <div data-testid="transcripts-manager" />;
  },
}));

// Late import so the vi.mock factories are applied first.
import { App } from "./App";
import { useParentStore } from "./store";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  settingsPanelPropsLog.length = 0;
  transcriptsManagerPropsLog.length = 0;
});

interface FetchOpts {
  retentionSeconds?: number;
  retentionRejects?: boolean;
  retentionCallCounter?: { count: number };
}

function stubFullAuthFetch(opts: FetchOpts = {}): Mock {
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
          generated_at: "2026-05-10T12:00:00Z",
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
      if (opts.retentionCallCounter !== undefined) {
        opts.retentionCallCounter.count += 1;
      }
      if (opts.retentionRejects === true) {
        return new Response("server boom", { status: 500 });
      }
      return new Response(
        JSON.stringify({ seconds: opts.retentionSeconds ?? 60 }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      );
    }
    // Catch-all for the other initial fetches the bootstrap fires.
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

describe("App transcript retention (I3)", () => {
  it("fetches /api/settings/transcript-retention exactly once on mount", async () => {
    const counter = { count: 0 };
    stubFullAuthFetch({
      retentionSeconds: 300,
      retentionCallCounter: counter,
    });
    render(<App />);
    await driveLoginToTabShell();
    // The fetch fires inside continueBootstrap, which runs as part of
    // handleAuthSuccess. After the tab shell is up, exactly one
    // retention fetch should have landed.
    await waitFor(() => {
      expect(counter.count).toBe(1);
    });
  });

  it("threads the resolved retention seconds into SettingsPanel.currentRetentionSeconds", async () => {
    stubFullAuthFetch({ retentionSeconds: 600 });
    render(<App />);
    await driveLoginToTabShell();
    // Switch to Settings tab so SettingsPanel mounts.
    fireEvent.click(screen.getByTestId("tab-settings"));
    await waitFor(() => {
      expect(screen.queryByTestId("settings-panel")).toBeTruthy();
    });
    // Wait for the retention fetch to resolve + a re-render of the
    // stub SettingsPanel with the new prop value. ``settingsPanelPropsLog``
    // captures every render — the latest entry should reflect the
    // fetched value.
    await waitFor(() => {
      const latest =
        settingsPanelPropsLog[settingsPanelPropsLog.length - 1];
      expect(latest).toBeDefined();
      expect(latest!.currentRetentionSeconds).toBe(600);
    });
  });

  it("threads the resolved retention seconds into TranscriptsManager.retentionSeconds", async () => {
    stubFullAuthFetch({ retentionSeconds: 900 });
    render(<App />);
    await driveLoginToTabShell();
    // Switch to Play → Transcriptions so TranscriptsManager mounts.
    // Phase O Step O1 rename: ``transcription`` → ``transcriptions``.
    fireEvent.click(screen.getByTestId("subtab-transcriptions"));
    await waitFor(() => {
      expect(screen.queryByTestId("transcripts-manager")).toBeTruthy();
    });
    await waitFor(() => {
      const latest =
        transcriptsManagerPropsLog[transcriptsManagerPropsLog.length - 1];
      expect(latest).toBeDefined();
      expect(latest!.retentionSeconds).toBe(900);
    });
  });

  it("retains optimistic default 60 and warns on fetch rejection", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    stubFullAuthFetch({ retentionRejects: true });
    render(<App />);
    await driveLoginToTabShell();
    // Switch to Settings so the SettingsPanel mounts + receives the
    // fallback retention value.
    fireEvent.click(screen.getByTestId("tab-settings"));
    await waitFor(() => {
      expect(screen.queryByTestId("settings-panel")).toBeTruthy();
    });
    // console.warn fires exactly once from the App.tsx catch-arm.
    await waitFor(() => {
      const retentionWarns = warnSpy.mock.calls.filter((call) =>
        String(call[0]).includes("transcript retention initial fetch failed"),
      );
      expect(retentionWarns.length).toBe(1);
    });
    // The optimistic default ``60`` survives — the props log's latest
    // entry should still carry it.
    const latest = settingsPanelPropsLog[settingsPanelPropsLog.length - 1];
    expect(latest).toBeDefined();
    expect(latest!.currentRetentionSeconds).toBe(60);
  });
});
