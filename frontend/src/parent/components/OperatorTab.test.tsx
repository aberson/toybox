// Component tests for the Step 24 OperatorTab dashboard.
// Stubs the ApiClient to control the metrics fetch and exercises:
// - initial fetch + render
// - ws envelope subscription updates state
// - REST poll fallback when ws is unavailable
// - last-update timestamp surfaces
// - cancel-on-unmount aborts the in-flight fetch
// - error retry: fetch fails, next poll succeeds, error clears
// - ws-then-stale fallback timing: ws envelope arrives, threshold trips,
//   poll resumes

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiClient, ListeningMode, MetricsSnapshot } from "../api";
import type { Envelope } from "../ws";
import { OperatorTab } from "./OperatorTab";

function fakeSnapshot(overrides: Partial<MetricsSnapshot> = {}): MetricsSnapshot {
  return {
    generated_at: "2026-05-03T12:00:00Z",
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
        age_appropriateness: 4.0,
        doability: 3.8,
        persona_fidelity: 4.1,
        coherence: 3.9,
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
      last_run_at: "2026-05-03T00:00:00Z",
      mean_dimension_scores: { schema: 4, safety: 4 },
      regressions_detected: 0,
      placeholder_baseline: true,
    },
    ...overrides,
  };
}

interface StubApi {
  getMetrics: Mock;
  setListeningMode: Mock;
}

function buildStubApi(snapshot: MetricsSnapshot): StubApi {
  return {
    getMetrics: vi.fn(async () => snapshot) as Mock,
    // Default: PUT echoes the requested mode. Tests that need failure
    // mocks can override via ``mockRejectedValueOnce`` etc.
    setListeningMode: vi.fn(async (mode: ListeningMode) => ({ mode })) as Mock,
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

describe("OperatorTab", () => {
  it("fetches metrics on mount and renders fields", async () => {
    const snapshot = fakeSnapshot();
    const api = buildStubApi(snapshot);
    render(<OperatorTab api={api as unknown as ApiClient} />);
    await vi.waitFor(() => {
      expect(api.getMetrics).toHaveBeenCalled();
    });
    await vi.waitFor(() => {
      expect(screen.getByTestId("m-activities-proposed").textContent).toBe("3");
    });
    expect(screen.getByTestId("m-activities-approved").textContent).toBe("2");
    expect(screen.getByTestId("m-activities-running").textContent).toBe("1");
    expect(screen.getByTestId("m-activities-completed").textContent).toBe("0");
    expect(screen.getByTestId("m-activities-didnt-work").textContent).toBe("0");
    expect(screen.getByTestId("m-transcripts-total").textContent).toBe("42");
    expect(screen.getByTestId("m-audio-device").textContent).toBe(
      "USB Audio (default)",
    );
    expect(screen.getByTestId("m-audio-overruns").textContent).toBe("2");
    expect(screen.getByTestId("m-ai-breaker").textContent).toBe("closed");
    expect(screen.getByTestId("m-quality-overlap").textContent).toBe("7");
    expect(screen.getByTestId("m-quality-agreement").textContent).toBe("85.7%");
    // Underscores in dimension keys are normalised to kebab-case for
    // data-testid (M8). The on-screen label keeps the underscore form.
    expect(
      screen.getByTestId("m-quality-age-appropriateness").textContent,
    ).toBe("4.00");
    expect(
      screen.getByTestId("m-quality-persona-fidelity").textContent,
    ).toBe("4.10");
  });

  it("updates state from a metrics ws envelope", async () => {
    const initial = fakeSnapshot({
      activities: {
        proposed_current: 0,
        approved_current: 0,
        running_current: 0,
        completed_current: 0,
        ended_current: 0,
        dismissed_current: 0,
        didnt_work_current: 0,
        last_24h: { proposed: 0, approved: 0, dismissed: 0, ended: 0 },
      },
    });
    const api = buildStubApi(initial);
    let registeredHandler: ((env: Envelope) => void) | null = null;
    const subscribeToMetrics = (handler: (env: Envelope) => void): (() => void) => {
      registeredHandler = handler;
      return () => {
        registeredHandler = null;
      };
    };
    render(
      <OperatorTab
        api={api as unknown as ApiClient}
        subscribeToMetrics={subscribeToMetrics}
      />,
    );
    await vi.waitFor(() => {
      expect(screen.getByTestId("m-activities-proposed").textContent).toBe("0");
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
    expect(registeredHandler).not.toBeNull();
    registeredHandler!({
      topic: "metrics",
      ts: "2026-05-03T12:01:00Z",
      payload: updated as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    await vi.waitFor(() => {
      expect(screen.getByTestId("m-activities-proposed").textContent).toBe("99");
    });
    // last-update line shows ws as the source.
    expect(screen.getByTestId("operator-last-update").textContent).toContain(
      "via ws",
    );
  });

  it("falls back to REST poll on stale ws", async () => {
    const snapshot = fakeSnapshot();
    const api = buildStubApi(snapshot);
    render(
      <OperatorTab
        api={api as unknown as ApiClient}
        pollIntervalMs={100}
        stalenessThresholdMs={50}
      />,
    );
    await vi.waitFor(() => {
      expect(api.getMetrics).toHaveBeenCalledTimes(1);
    });
    // Advance time past the staleness threshold AND the poll interval.
    await vi.advanceTimersByTimeAsync(200);
    expect(api.getMetrics.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("resumes polling after a ws envelope goes stale", async () => {
    // M9 spec: ws envelope arrives, then no further envelope; once the
    // staleness threshold trips, the REST poll resumes. Use fake timers
    // to advance through both windows and a subscriber that the test
    // can fire by hand to land the ws envelope.
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
      <OperatorTab
        api={api as unknown as ApiClient}
        subscribeToMetrics={subscribeToMetrics}
        pollIntervalMs={100}
        stalenessThresholdMs={300}
      />,
    );
    // Initial fetch lands.
    await vi.waitFor(() => {
      expect(api.getMetrics).toHaveBeenCalledTimes(1);
    });
    // ws envelope lands — this resets the lastUpdate ref to "now".
    expect(registered).not.toBeNull();
    registered!({
      topic: "metrics",
      ts: "2026-05-03T12:01:00Z",
      payload: fakeSnapshot() as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    // Advance just past one poll interval but inside the staleness
    // window: the poll tick must SKIP the fetch (ws is fresh).
    await vi.advanceTimersByTimeAsync(120);
    expect(api.getMetrics).toHaveBeenCalledTimes(1);
    // Advance past the staleness threshold; the next poll tick MUST
    // fire the fetch because the ws is now considered stale.
    await vi.advanceTimersByTimeAsync(400);
    expect(api.getMetrics.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("clears the error after a failed fetch is followed by a success", async () => {
    // M9 spec: fetch fails, next poll succeeds, error clears. Stub
    // returns a rejection on the first call and the snapshot on the
    // second; the operator UI must surface the error then clear it.
    const snapshot = fakeSnapshot();
    let calls = 0;
    const api = {
      getMetrics: vi.fn(async () => {
        calls += 1;
        if (calls === 1) {
          throw new Error("first call boom");
        }
        return snapshot;
      }) as Mock,
    };
    render(
      <OperatorTab
        api={api as unknown as ApiClient}
        pollIntervalMs={100}
        stalenessThresholdMs={50}
      />,
    );
    // Initial fetch fails — error message rendered.
    await vi.waitFor(() => {
      expect(screen.getByTestId("operator-error").textContent).toContain(
        "first call boom",
      );
    });
    // Advance past the staleness threshold + poll interval; the second
    // fetch resolves successfully and the error must clear.
    await vi.advanceTimersByTimeAsync(200);
    await vi.waitFor(() => {
      expect(api.getMetrics.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
    await vi.waitFor(() => {
      expect(screen.queryByTestId("operator-error")).toBeNull();
    });
    expect(screen.getByTestId("m-activities-proposed").textContent).toBe("3");
  });

  it("surfaces last-update timestamp", async () => {
    const snapshot = fakeSnapshot();
    const api = buildStubApi(snapshot);
    render(<OperatorTab api={api as unknown as ApiClient} />);
    await vi.waitFor(() => {
      expect(screen.getByTestId("operator-last-update")).toBeTruthy();
    });
    // The text comes from formatTimestamp; it's locale-formatted, so we
    // just assert the element exists and contains the source label.
    expect(screen.getByTestId("operator-last-update").textContent).toContain(
      "last update",
    );
    expect(screen.getByTestId("operator-last-update").textContent).toContain(
      "via rest",
    );
  });

  it("aborts the in-flight fetch on unmount", () => {
    const aborted: AbortSignal[] = [];
    const api = {
      getMetrics: vi.fn(
        async (opts?: { signal?: AbortSignal }) => {
          if (opts?.signal !== undefined) {
            aborted.push(opts.signal);
          }
          return fakeSnapshot();
        },
      ) as Mock,
    };
    const { unmount } = render(
      <OperatorTab api={api as unknown as ApiClient} />,
    );
    unmount();
    // The signal handed to the in-flight request must report aborted.
    expect(aborted.length).toBeGreaterThanOrEqual(1);
    expect(aborted[0]!.aborted).toBe(true);
  });

  it("renders an error when the fetch fails", async () => {
    const api = {
      getMetrics: vi.fn(async () => {
        throw new Error("boom");
      }) as Mock,
    };
    render(<OperatorTab api={api as unknown as ApiClient} />);
    await vi.waitFor(() => {
      expect(screen.getByTestId("operator-error").textContent).toContain("boom");
    });
  });

  it("PUTs the requested listening mode and reflects it in the active button", async () => {
    const snapshot = fakeSnapshot();
    const api = buildStubApi(snapshot);
    render(<OperatorTab api={api as unknown as ApiClient} />);

    await vi.waitFor(() => {
      expect(screen.getByTestId("listening-mode-buttons")).toBeTruthy();
    });
    // Snapshot says mode 3 is active.
    expect(
      screen.getByTestId("listening-mode-btn-3").getAttribute("data-active"),
    ).toBe("true");
    expect(
      screen.getByTestId("listening-mode-btn-1").getAttribute("data-active"),
    ).toBe("false");

    fireEvent.click(screen.getByTestId("listening-mode-btn-1"));

    await vi.waitFor(() => {
      expect(api.setListeningMode).toHaveBeenCalledWith(1);
    });
    // The active button flips immediately via the optimistic
    // ``pendingMode`` state. The AI-table cell only updates once the
    // PUT resolves and the local snapshot is patched, so wait on that.
    await vi.waitFor(() => {
      expect(screen.getByTestId("m-ai-listening-mode").textContent).toBe("1");
    });
    expect(
      screen.getByTestId("listening-mode-btn-1").getAttribute("data-active"),
    ).toBe("true");
    expect(
      screen.getByTestId("listening-mode-btn-3").getAttribute("data-active"),
    ).toBe("false");
  });

  it("surfaces an inline error when the listening-mode PUT fails", async () => {
    const snapshot = fakeSnapshot();
    const api = buildStubApi(snapshot);
    api.setListeningMode.mockRejectedValueOnce(new Error("backend down"));
    render(<OperatorTab api={api as unknown as ApiClient} />);
    await vi.waitFor(() => {
      expect(screen.getByTestId("listening-mode-btn-5")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("listening-mode-btn-5"));
    await vi.waitFor(() => {
      expect(screen.getByTestId("listening-mode-error").textContent).toContain(
        "backend down",
      );
    });
    // The active button does not change when the PUT fails.
    expect(
      screen.getByTestId("listening-mode-btn-3").getAttribute("data-active"),
    ).toBe("true");
  });
});
