// Component tests for the Phase H step H5 StatsPanel.
// Covers the metrics-snapshot half that used to live in
// OperatorTab.test.tsx — initial fetch + ws subscription + REST poll
// fallback + abort-on-unmount + error-then-recover. Direct api-object
// stubbing (no vi.stubGlobal), matching ImageGenModeToggle.test.tsx.

import { cleanup, render, screen } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiClient, MetricsSnapshot } from "../api";
import type { Envelope } from "../ws";
import { StatsPanel } from "./StatsPanel";

function fakeSnapshot(overrides: Partial<MetricsSnapshot> = {}): MetricsSnapshot {
  return {
    generated_at: "2026-05-10T12:00:00Z",
    ws_subscribers: 1,
    activities: {
      proposed_current: 3,
      approved_current: 2,
      running_current: 1,
      completed_current: 0,
      ended_current: 4,
      dismissed_current: 1,
      didnt_work_current: 0,
      last_24h: { proposed: 1, approved: 0, dismissed: 0, ended: 1 },
    },
    transcripts: { total: 42, last_24h: 5 },
    audio: {
      mic_device: "USB Audio (default)",
      queue_depth: 0,
      buffer_overruns_total: 2,
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
      last_24h_mean_scores: {
        schema: 4.2,
        safety: 4.5,
      },
      judge_parent_agreement: {
        overlap_count: 7,
        agreement_rate: 0.857,
        metric_name: "sign_agreement_rate",
      },
      safety_autofails_last_24h: 0,
    },
    eval_gate: {
      last_run_at: "2026-05-10T00:00:00Z",
      mean_dimension_scores: { schema: 4, safety: 4 },
      regressions_detected: 0,
      placeholder_baseline: true,
    },
    ...overrides,
  };
}

interface StubApi {
  getMetrics: Mock;
}

function buildStubApi(snapshot: MetricsSnapshot): StubApi {
  return {
    getMetrics: vi.fn(async () => snapshot) as Mock,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.restoreAllMocks();
});

describe("StatsPanel", () => {
  it("fetches metrics on mount, renders fields, and labels the source 'via rest'", async () => {
    const snapshot = fakeSnapshot();
    const api = buildStubApi(snapshot);
    render(<StatsPanel api={api as unknown as ApiClient} />);
    await vi.waitFor(() => {
      expect(api.getMetrics).toHaveBeenCalled();
    });
    await vi.waitFor(() => {
      expect(screen.getByTestId("m-activities-proposed").textContent).toBe("3");
    });
    expect(screen.getByTestId("m-transcripts-total").textContent).toBe("42");
    expect(screen.getByTestId("m-audio-device").textContent).toBe(
      "USB Audio (default)",
    );
    // Source label flips to "via rest" on initial REST fetch.
    expect(screen.getByTestId("stats-last-update").textContent).toContain(
      "via rest",
    );
  });

  it("replaces the snapshot from a ws envelope and flips the source label to 'via ws'", async () => {
    const initial = fakeSnapshot();
    const api = buildStubApi(initial);
    let registered: ((env: Envelope) => void) | null = null;
    const subscribeToMetrics = (
      handler: (env: Envelope) => void,
    ): (() => void) => {
      registered = handler;
      return () => {
        registered = null;
      };
    };
    render(
      <StatsPanel
        api={api as unknown as ApiClient}
        subscribeToMetrics={subscribeToMetrics}
      />,
    );
    await vi.waitFor(() => {
      expect(screen.getByTestId("m-activities-proposed").textContent).toBe("3");
    });

    // Push a ws envelope with new values.
    const updated = fakeSnapshot({
      activities: {
        proposed_current: 99,
        approved_current: 5,
        running_current: 0,
        completed_current: 0,
        ended_current: 1,
        dismissed_current: 2,
        didnt_work_current: 0,
        last_24h: { proposed: 0, approved: 0, dismissed: 0, ended: 0 },
      },
    });
    expect(registered).not.toBeNull();
    registered!({
      topic: "metrics",
      ts: "2026-05-10T12:01:00Z",
      payload: updated as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    await vi.waitFor(() => {
      expect(screen.getByTestId("m-activities-proposed").textContent).toBe("99");
    });
    expect(screen.getByTestId("stats-last-update").textContent).toContain(
      "via ws",
    );
  });

  it("resumes the REST poll once the ws snapshot ages past the staleness threshold", async () => {
    const snapshot = fakeSnapshot();
    const api = buildStubApi(snapshot);
    let registered: ((env: Envelope) => void) | null = null;
    const subscribeToMetrics = (
      handler: (env: Envelope) => void,
    ): (() => void) => {
      registered = handler;
      return () => {
        registered = null;
      };
    };
    render(
      <StatsPanel
        api={api as unknown as ApiClient}
        subscribeToMetrics={subscribeToMetrics}
        pollIntervalMs={100}
        stalenessThresholdMs={300}
      />,
    );
    // Wait for the initial REST fetch to fully land so its setState
    // doesn't race the ws envelope below. ``via rest`` only appears
    // after the fetch promise resolves and the snapshot is set.
    await vi.waitFor(() => {
      expect(screen.getByTestId("stats-last-update").textContent).toContain(
        "via rest",
      );
    });
    // Fire a ws envelope with a distinctive ``proposed_current`` so we
    // can synchronise on the state update before asserting source.
    expect(registered).not.toBeNull();
    registered!({
      topic: "metrics",
      ts: "2026-05-10T12:01:00Z",
      payload: fakeSnapshot({
        activities: {
          proposed_current: 77,
          approved_current: 0,
          running_current: 0,
          completed_current: 0,
          ended_current: 0,
          dismissed_current: 0,
          didnt_work_current: 0,
          last_24h: { proposed: 0, approved: 0, dismissed: 0, ended: 0 },
        },
      }) as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    await vi.waitFor(() => {
      expect(screen.getByTestId("m-activities-proposed").textContent).toBe("77");
    });
    expect(screen.getByTestId("stats-last-update").textContent).toContain(
      "via ws",
    );
    const callsAfterWs = api.getMetrics.mock.calls.length;
    // Advance past the staleness threshold + poll interval — the next
    // tick MUST refire the REST fetch because the ws is now stale.
    await vi.advanceTimersByTimeAsync(500);
    expect(api.getMetrics.mock.calls.length).toBeGreaterThan(callsAfterWs);
    // Source flips back to rest after the REST fetch lands.
    await vi.waitFor(() => {
      expect(screen.getByTestId("stats-last-update").textContent).toContain(
        "via rest",
      );
    });
  });

  it("aborts the in-flight fetch on unmount", () => {
    const aborted: AbortSignal[] = [];
    const snapshot = fakeSnapshot();
    const api = {
      ...buildStubApi(snapshot),
      getMetrics: vi.fn(async (opts?: { signal?: AbortSignal }) => {
        if (opts?.signal !== undefined) {
          aborted.push(opts.signal);
        }
        // Never resolves — test unmounts before the promise lands.
        return new Promise<MetricsSnapshot>(() => {});
      }) as Mock,
    };
    const { unmount } = render(
      <StatsPanel api={api as unknown as ApiClient} />,
    );
    unmount();
    expect(aborted.length).toBeGreaterThanOrEqual(1);
    expect(aborted[0]!.aborted).toBe(true);
  });

  it("clears the error after a failed fetch is followed by a success", async () => {
    const snapshot = fakeSnapshot();
    let calls = 0;
    // Resolve first promise as a rejection, second+ as success — but
    // make the rejection happen via a delayed reject so the error
    // surfaces in the rendered tree BEFORE the success fetch lands.
    const api = {
      ...buildStubApi(snapshot),
      getMetrics: vi.fn(async () => {
        calls += 1;
        if (calls === 1) {
          throw new Error("first call boom");
        }
        return snapshot;
      }) as Mock,
    };
    render(
      <StatsPanel
        api={api as unknown as ApiClient}
        pollIntervalMs={100}
        stalenessThresholdMs={50}
      />,
    );
    // Wait for the first fetch to complete (call count ≥ 1) and the
    // error to surface. ``vi.waitFor`` polls on real time so the
    // microtask queue drains between attempts.
    await vi.waitFor(() => {
      expect(api.getMetrics).toHaveBeenCalledTimes(1);
    });
    // Advance past the staleness threshold + poll interval; the second
    // fetch resolves successfully and the error must clear.
    await vi.advanceTimersByTimeAsync(200);
    await vi.waitFor(() => {
      expect(api.getMetrics.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
    await vi.waitFor(() => {
      expect(screen.queryByTestId("stats-error")).toBeNull();
    });
    expect(screen.getByTestId("m-activities-proposed").textContent).toBe("3");
  });

  it("clears the poll interval on unmount (no leaked timers)", async () => {
    const snapshot = fakeSnapshot();
    const api = buildStubApi(snapshot);
    const { unmount } = render(
      <StatsPanel
        api={api as unknown as ApiClient}
        pollIntervalMs={100}
        stalenessThresholdMs={50}
      />,
    );
    // Let the initial fetch + interval setup run.
    await vi.waitFor(() => {
      expect(api.getMetrics).toHaveBeenCalled();
    });
    const before = vi.getTimerCount();
    expect(before).toBeGreaterThanOrEqual(1);
    unmount();
    // After unmount the StatsPanel's setInterval is cleared.
    expect(vi.getTimerCount()).toBe(0);
  });
});
