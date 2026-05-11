// Component tests for the Phase I step I3 TranscriptRetentionControl.
// Stubs ApiClient.setTranscriptRetention and exercises the round-trip:
// - all 5 preset buttons render with the correct labels + data-testids
// - currentSeconds=60 shows 1m as pressed (via aria-pressed) and the
//   other four as not pressed
// - clicking a button calls setTranscriptRetention with the right value
//   and bubbles the response back through onSecondsChanged + clears
//   the pending state
// - API rejection surfaces an inline error message and onSecondsChanged
//   is NOT called
//
// Mocking style mirrors ImageGenModeToggle.test.tsx + SettingsPanel.test.tsx
// (direct api-object stub via vi.fn, no vi.mock module-level replacement).

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../api";
import { TranscriptRetentionControl } from "./TranscriptRetentionControl";

interface StubApi {
  setTranscriptRetention: Mock;
}

function buildStubApi(): StubApi {
  return {
    setTranscriptRetention: vi.fn(async (seconds: number) => ({
      seconds,
    })) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("TranscriptRetentionControl", () => {
  it("renders all 5 preset buttons with the correct labels + testids", () => {
    const api = buildStubApi();
    render(
      <TranscriptRetentionControl
        api={api as unknown as ApiClient}
        currentSeconds={60}
        onSecondsChanged={() => {}}
      />,
    );
    const btn60 = screen.getByTestId("transcript-retention-60");
    const btn180 = screen.getByTestId("transcript-retention-180");
    const btn300 = screen.getByTestId("transcript-retention-300");
    const btn600 = screen.getByTestId("transcript-retention-600");
    const btn900 = screen.getByTestId("transcript-retention-900");
    expect(btn60.textContent).toBe("1m");
    expect(btn180.textContent).toBe("3m");
    expect(btn300.textContent).toBe("5m");
    expect(btn600.textContent).toBe("10m");
    expect(btn900.textContent).toBe("15m");
  });

  it("currentSeconds=60 shows the 1m button as pressed and the others as not", () => {
    const api = buildStubApi();
    render(
      <TranscriptRetentionControl
        api={api as unknown as ApiClient}
        currentSeconds={60}
        onSecondsChanged={() => {}}
      />,
    );
    expect(
      screen.getByTestId("transcript-retention-60").getAttribute("aria-pressed"),
    ).toBe("true");
    for (const seconds of [180, 300, 600, 900]) {
      expect(
        screen
          .getByTestId(`transcript-retention-${seconds}`)
          .getAttribute("aria-pressed"),
      ).toBe("false");
    }
  });

  it("currentSeconds=120 (non-canonical) snaps to the nearest preset (1m)", () => {
    // Defensive: a backend response skew, test stub, or future
    // migration could deliver a value outside the canonical preset
    // set. The component snaps to the closest preset for DISPLAY so
    // the "exactly one selected" aria-pressed contract holds. 120s
    // sits between 60s and 180s but is closer to 60 (diff 60 vs 60 —
    // ties break toward the earlier preset).
    const api = buildStubApi();
    render(
      <TranscriptRetentionControl
        api={api as unknown as ApiClient}
        currentSeconds={120}
        onSecondsChanged={() => {}}
      />,
    );
    expect(
      screen.getByTestId("transcript-retention-60").getAttribute("aria-pressed"),
    ).toBe("true");
    for (const seconds of [180, 300, 600, 900]) {
      expect(
        screen
          .getByTestId(`transcript-retention-${seconds}`)
          .getAttribute("aria-pressed"),
      ).toBe("false");
    }
  });

  it("clicking 3m calls setTranscriptRetention(180) and onSecondsChanged(180)", async () => {
    const api = buildStubApi();
    const onSecondsChanged = vi.fn();
    render(
      <TranscriptRetentionControl
        api={api as unknown as ApiClient}
        currentSeconds={60}
        onSecondsChanged={onSecondsChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("transcript-retention-180"));
    await waitFor(() => {
      // Second arg is the AbortController options object — mirror it
      // with ``expect.anything()`` so this test pins the seconds value
      // without coupling to the request-options shape.
      expect(api.setTranscriptRetention).toHaveBeenCalledWith(
        180,
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(onSecondsChanged).toHaveBeenCalledWith(180);
    });
    // After the PUT resolves, no button is disabled — the pending state
    // has lifted and the operator can pick a different preset.
    await waitFor(() => {
      expect(
        (
          screen.getByTestId("transcript-retention-180") as HTMLButtonElement
        ).disabled,
      ).toBe(false);
    });
  });

  it("during the in-flight PUT, all buttons are disabled and the clicked one is the pending choice", async () => {
    const api = buildStubApi();
    // Hang the PUT until the test releases the resolver — lets us
    // observe the pending/busy state mid-flight.
    type Resolver = (value: { seconds: number }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setTranscriptRetention.mockImplementationOnce(
      () =>
        new Promise<{ seconds: number }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );
    const onSecondsChanged = vi.fn();
    render(
      <TranscriptRetentionControl
        api={api as unknown as ApiClient}
        currentSeconds={60}
        onSecondsChanged={onSecondsChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("transcript-retention-300"));
    // While the PUT is in flight, ALL buttons are disabled.
    await waitFor(() => {
      expect(
        (
          screen.getByTestId("transcript-retention-300") as HTMLButtonElement
        ).disabled,
      ).toBe(true);
    });
    for (const seconds of [60, 180, 600, 900]) {
      expect(
        (
          screen.getByTestId(
            `transcript-retention-${seconds}`,
          ) as HTMLButtonElement
        ).disabled,
      ).toBe(true);
    }
    // Release the PUT — the pending state lifts.
    resolverRef.current?.({ seconds: 300 });
    await waitFor(() => {
      expect(onSecondsChanged).toHaveBeenCalledWith(300);
    });
    await waitFor(() => {
      expect(
        (
          screen.getByTestId("transcript-retention-300") as HTMLButtonElement
        ).disabled,
      ).toBe(false);
    });
  });

  it("PUT rejection surfaces an inline error and does NOT call onSecondsChanged", async () => {
    const api = buildStubApi();
    api.setTranscriptRetention.mockRejectedValueOnce(
      new Error("retention boom"),
    );
    const onSecondsChanged = vi.fn();
    render(
      <TranscriptRetentionControl
        api={api as unknown as ApiClient}
        currentSeconds={60}
        onSecondsChanged={onSecondsChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("transcript-retention-180"));
    await waitFor(() => {
      expect(
        screen.getByTestId("transcript-retention-error").textContent,
      ).toContain("retention boom");
    });
    expect(onSecondsChanged).not.toHaveBeenCalled();
    // The pending state is cleared so the operator can retry.
    expect(
      (
        screen.getByTestId("transcript-retention-180") as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    // The prior selection (60 / 1m) remains the pressed button — the
    // optimistic flip was reverted because the PUT failed.
    expect(
      screen
        .getByTestId("transcript-retention-60")
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen
        .getByTestId("transcript-retention-180")
        .getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("does not fire a second PUT when the operator re-clicks during an in-flight request", async () => {
    const api = buildStubApi();
    type Resolver = (value: { seconds: number }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setTranscriptRetention.mockImplementationOnce(
      () =>
        new Promise<{ seconds: number }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );
    const onSecondsChanged = vi.fn();
    render(
      <TranscriptRetentionControl
        api={api as unknown as ApiClient}
        currentSeconds={60}
        onSecondsChanged={onSecondsChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("transcript-retention-180"));
    // A rapid second click against a different preset is a no-op while
    // the first is in flight (the disabled attribute already blocks the
    // event in browsers, but the handler guards too).
    fireEvent.click(screen.getByTestId("transcript-retention-300"));
    expect(api.setTranscriptRetention).toHaveBeenCalledTimes(1);
    // Release the first PUT to clean up.
    resolverRef.current?.({ seconds: 180 });
    await waitFor(() => {
      expect(onSecondsChanged).toHaveBeenCalledWith(180);
    });
  });
});
