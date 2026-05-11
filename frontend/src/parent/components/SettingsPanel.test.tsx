// Component tests for the Phase H step H5 SettingsPanel.
// Covers the seed-on-mount + toggle wiring that used to live in
// OperatorTab.test.tsx (the half that moved to SettingsPanel.tsx).
// ImageGenModeToggle has its own test file (ImageGenModeToggle.test.tsx).
// Stubs ApiClient via a direct api-object stub (matching the pattern in
// ImageGenModeToggle.test.tsx + BannedThemesSettings.test.tsx — no
// vi.stubGlobal).

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ApiClient,
  BannedThemesResponse,
  ImageGenMode,
  ListeningMode,
  MetricsSnapshot,
} from "../api";
import { SettingsPanel } from "./SettingsPanel";

function fakeSnapshot(overrides: Partial<MetricsSnapshot> = {}): MetricsSnapshot {
  return {
    generated_at: "2026-05-10T12:00:00Z",
    ws_subscribers: 1,
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
    transcripts: { total: 0, last_24h: 0 },
    audio: {
      mic_device: "default",
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
    ...overrides,
  };
}

interface StubApi {
  getMetrics: Mock;
  setListeningMode: Mock;
  setMicEnabled: Mock;
  getImageGenMode: Mock;
  setImageGenMode: Mock;
  getBannedThemesGlobal: Mock;
  setBannedThemesGlobal: Mock;
}

function buildStubApi(snapshot: MetricsSnapshot): StubApi {
  return {
    getMetrics: vi.fn(async () => snapshot) as Mock,
    setListeningMode: vi.fn(async (mode: ListeningMode) => ({ mode })) as Mock,
    setMicEnabled: vi.fn(async (enabled: boolean) => ({ enabled })) as Mock,
    getImageGenMode: vi.fn(
      async () => ({ mode: "cartoon" as ImageGenMode }),
    ) as Mock,
    setImageGenMode: vi.fn(async (mode: ImageGenMode) => ({ mode })) as Mock,
    getBannedThemesGlobal: vi.fn(
      async (): Promise<BannedThemesResponse> => ({ themes: null }),
    ) as Mock,
    setBannedThemesGlobal: vi.fn(
      async (themes: string | null): Promise<BannedThemesResponse> => ({
        themes:
          themes === null || themes.trim() === "" ? null : themes,
      }),
    ) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SettingsPanel", () => {
  it("seeds listening-mode + mic-enabled display values from getMetrics on mount", async () => {
    const snapshot = fakeSnapshot({
      ai: {
        breaker_state: "closed",
        breaker_retry_after_iso: null,
        claude_capable: false,
        claude_capability_reason: "token_missing",
        listening_mode: 4,
        min_interval_throttle_seconds: 15.0,
      },
      audio: {
        mic_device: "default",
        queue_depth: 0,
        buffer_overruns_total: 0,
        mic_enabled: false,
      },
    });
    const api = buildStubApi(snapshot);
    render(<SettingsPanel api={api as unknown as ApiClient} />);
    await waitFor(() => {
      expect(api.getMetrics).toHaveBeenCalled();
    });
    // listening_mode=4 → btn-4 is active.
    await waitFor(() => {
      expect(
        screen.getByTestId("listening-mode-btn-4").getAttribute("data-active"),
      ).toBe("true");
    });
    // mic_enabled=false → mute toggle reads "muted".
    expect(
      screen
        .getByTestId("operator-mic-mute-toggle")
        .getAttribute("data-mic-enabled"),
    ).toBe("false");
  });

  it("aborts the in-flight getMetrics on unmount", () => {
    const aborted: AbortSignal[] = [];
    const snapshot = fakeSnapshot();
    const api = {
      ...buildStubApi(snapshot),
      getMetrics: vi.fn(async (opts?: { signal?: AbortSignal }) => {
        if (opts?.signal !== undefined) {
          aborted.push(opts.signal);
        }
        // Return a never-resolving promise so the test can unmount
        // mid-flight without the .then firing setState on a dead tree.
        return new Promise<MetricsSnapshot>(() => {});
      }) as Mock,
    };
    const { unmount } = render(
      <SettingsPanel api={api as unknown as ApiClient} />,
    );
    unmount();
    expect(aborted.length).toBeGreaterThanOrEqual(1);
    expect(aborted[0]!.aborted).toBe(true);
  });

  it("surfaces a seed error when getMetrics rejects (toggles still render)", async () => {
    const snapshot = fakeSnapshot();
    const api = {
      ...buildStubApi(snapshot),
      getMetrics: vi.fn(async () => {
        throw new Error("seed boom");
      }) as Mock,
    };
    render(<SettingsPanel api={api as unknown as ApiClient} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("settings-panel-seed-error").textContent,
      ).toContain("seed boom");
    });
    // The toggles still render (fall back to hardcoded defaults: mode=3
    // active, mic enabled).
    expect(
      screen.getByTestId("listening-mode-btn-3").getAttribute("data-active"),
    ).toBe("true");
  });

  it("PUTs the requested listening mode and reflects it in the active button", async () => {
    const snapshot = fakeSnapshot();
    const api = buildStubApi(snapshot);
    render(<SettingsPanel api={api as unknown as ApiClient} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("listening-mode-btn-3").getAttribute("data-active"),
      ).toBe("true");
    });

    fireEvent.click(screen.getByTestId("listening-mode-btn-1"));

    await waitFor(() => {
      expect(api.setListeningMode).toHaveBeenCalledWith(1);
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("listening-mode-btn-1").getAttribute("data-active"),
      ).toBe("true");
    });
    expect(
      screen.getByTestId("listening-mode-btn-3").getAttribute("data-active"),
    ).toBe("false");
  });

  it("surfaces an inline error when the listening-mode PUT fails and keeps the prior selection", async () => {
    const snapshot = fakeSnapshot();
    const api = buildStubApi(snapshot);
    api.setListeningMode.mockRejectedValueOnce(new Error("backend down"));
    render(<SettingsPanel api={api as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByTestId("listening-mode-btn-5")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("listening-mode-btn-5"));
    await waitFor(() => {
      expect(
        screen.getByTestId("listening-mode-error").textContent,
      ).toContain("backend down");
    });
    // Prior selection stays active when the PUT fails.
    expect(
      screen.getByTestId("listening-mode-btn-3").getAttribute("data-active"),
    ).toBe("true");
  });

  it("mic-mute PUT success flips the display; PUT failure surfaces an inline error without flipping", async () => {
    const snapshot = fakeSnapshot();
    const api = buildStubApi(snapshot);
    render(<SettingsPanel api={api as unknown as ApiClient} />);
    // Seed: mic_enabled=true → toggle reads "true".
    await waitFor(() => {
      expect(
        screen
          .getByTestId("operator-mic-mute-toggle")
          .getAttribute("data-mic-enabled"),
      ).toBe("true");
    });

    // Success path: click flips to muted.
    fireEvent.click(screen.getByTestId("operator-mic-mute-toggle"));
    await waitFor(() => {
      expect(api.setMicEnabled).toHaveBeenCalledWith(false);
    });
    await waitFor(() => {
      expect(
        screen
          .getByTestId("operator-mic-mute-toggle")
          .getAttribute("data-mic-enabled"),
      ).toBe("false");
    });

    // Failure path: next click rejects → error visible, display NOT
    // flipped back.
    api.setMicEnabled.mockRejectedValueOnce(new Error("mic boom"));
    fireEvent.click(screen.getByTestId("operator-mic-mute-toggle"));
    await waitFor(() => {
      expect(
        screen.getByTestId("mic-mute-error").textContent,
      ).toContain("mic boom");
    });
    // Display still says muted (false) — failed PUT didn't toggle it.
    expect(
      screen
        .getByTestId("operator-mic-mute-toggle")
        .getAttribute("data-mic-enabled"),
    ).toBe("false");
  });
});
