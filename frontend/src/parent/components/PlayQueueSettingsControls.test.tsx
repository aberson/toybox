// Component tests for the Phase J step J10 play-queue settings
// segmented controls. Mirrors TranscriptRetentionControl.test.tsx
// shape: stubs the relevant ApiClient method, exercises render +
// click + reject paths, and pins the snap-to-nearest defensive
// behavior for non-canonical seed values. The cadence-off
// round-trip is explicit — ``0`` must NOT be coerced anywhere on
// this path.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../api";
import {
  PlayCadenceSecondsControl,
  PlayTargetDepthControl,
} from "./PlayQueueSettingsControls";

interface StubTargetDepthApi {
  setPlayTargetDepth: Mock;
}

interface StubCadenceApi {
  setPlayCadenceSeconds: Mock;
}

function buildTargetDepthApi(): StubTargetDepthApi {
  return {
    setPlayTargetDepth: vi.fn(async (value: number) => ({ value })) as Mock,
  };
}

function buildCadenceApi(): StubCadenceApi {
  return {
    setPlayCadenceSeconds: vi.fn(async (value: number) => ({ value })) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PlayTargetDepthControl", () => {
  it("renders all 3 preset buttons with the right labels + testids", () => {
    const api = buildTargetDepthApi();
    render(
      <PlayTargetDepthControl
        api={api as unknown as ApiClient}
        currentValue={3}
        onValueChanged={() => {}}
      />,
    );
    expect(screen.getByTestId("play-target-depth-1").textContent).toBe("1");
    expect(screen.getByTestId("play-target-depth-3").textContent).toBe("3");
    expect(screen.getByTestId("play-target-depth-5").textContent).toBe("5");
  });

  it("currentValue=3 shows the 3 button as pressed and the others as not", () => {
    const api = buildTargetDepthApi();
    render(
      <PlayTargetDepthControl
        api={api as unknown as ApiClient}
        currentValue={3}
        onValueChanged={() => {}}
      />,
    );
    expect(
      screen.getByTestId("play-target-depth-3").getAttribute("aria-pressed"),
    ).toBe("true");
    for (const value of [1, 5]) {
      expect(
        screen
          .getByTestId(`play-target-depth-${value}`)
          .getAttribute("aria-pressed"),
      ).toBe("false");
    }
  });

  it("currentValue=7 (non-canonical) snaps to the nearest preset (5)", () => {
    // Defensive: a backend skew or future preset migration could
    // deliver a value outside {1, 3, 5}. The component snaps to the
    // closest preset for DISPLAY so exactly one button stays
    // aria-pressed. 7 is closer to 5 (diff 2) than to 3 (diff 4).
    const api = buildTargetDepthApi();
    render(
      <PlayTargetDepthControl
        api={api as unknown as ApiClient}
        currentValue={7}
        onValueChanged={() => {}}
      />,
    );
    expect(
      screen.getByTestId("play-target-depth-5").getAttribute("aria-pressed"),
    ).toBe("true");
    for (const value of [1, 3]) {
      expect(
        screen
          .getByTestId(`play-target-depth-${value}`)
          .getAttribute("aria-pressed"),
      ).toBe("false");
    }
  });

  it("clicking 5 calls setPlayTargetDepth(5) and onValueChanged(5)", async () => {
    const api = buildTargetDepthApi();
    const onValueChanged = vi.fn();
    render(
      <PlayTargetDepthControl
        api={api as unknown as ApiClient}
        currentValue={1}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("play-target-depth-5"));
    await waitFor(() => {
      expect(api.setPlayTargetDepth).toHaveBeenCalledWith(
        5,
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith(5);
    });
  });

  it("PUT rejection surfaces an inline error and reverts the optimistic flip", async () => {
    const api = buildTargetDepthApi();
    api.setPlayTargetDepth.mockRejectedValueOnce(new Error("depth boom"));
    const onValueChanged = vi.fn();
    render(
      <PlayTargetDepthControl
        api={api as unknown as ApiClient}
        currentValue={1}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("play-target-depth-5"));
    await waitFor(() => {
      expect(
        screen.getByTestId("play-target-depth-error").textContent,
      ).toContain("depth boom");
    });
    expect(onValueChanged).not.toHaveBeenCalled();
    // The prior selection (1) remains the pressed button — the
    // optimistic flip was reverted because the PUT failed.
    expect(
      screen.getByTestId("play-target-depth-1").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("play-target-depth-5").getAttribute("aria-pressed"),
    ).toBe("false");
  });
});

describe("PlayCadenceSecondsControl", () => {
  it("renders all 4 preset buttons with the right labels + testids", () => {
    const api = buildCadenceApi();
    render(
      <PlayCadenceSecondsControl
        api={api as unknown as ApiClient}
        currentValue={30}
        onValueChanged={() => {}}
      />,
    );
    expect(screen.getByTestId("play-cadence-seconds-0").textContent).toBe(
      "off",
    );
    expect(screen.getByTestId("play-cadence-seconds-10").textContent).toBe(
      "10s",
    );
    expect(screen.getByTestId("play-cadence-seconds-30").textContent).toBe(
      "30s",
    );
    expect(screen.getByTestId("play-cadence-seconds-60").textContent).toBe(
      "1m",
    );
  });

  it("currentValue=0 shows the 'off' button as pressed and the others as not", () => {
    // The off state is a real in-set value, not a sentinel — the
    // ``aria-pressed`` contract must hold for it too. A bug that
    // treats 0 as falsy would show NO pressed button (or the wrong
    // one snapped from the default-display fallback). This test
    // pins the round-trip.
    const api = buildCadenceApi();
    render(
      <PlayCadenceSecondsControl
        api={api as unknown as ApiClient}
        currentValue={0}
        onValueChanged={() => {}}
      />,
    );
    expect(
      screen
        .getByTestId("play-cadence-seconds-0")
        .getAttribute("aria-pressed"),
    ).toBe("true");
    for (const value of [10, 30, 60]) {
      expect(
        screen
          .getByTestId(`play-cadence-seconds-${value}`)
          .getAttribute("aria-pressed"),
      ).toBe("false");
    }
  });

  it("renders the cadence-off hint copy describing the manual fallbacks", () => {
    const api = buildCadenceApi();
    render(
      <PlayCadenceSecondsControl
        api={api as unknown as ApiClient}
        currentValue={0}
        onValueChanged={() => {}}
      />,
    );
    // The hint copy explicitly mentions transcripts + the manual
    // Trigger so the operator can predict the off-state behavior.
    expect(
      screen.getByTestId("play-cadence-seconds-control").textContent,
    ).toContain("transcripts");
    expect(
      screen.getByTestId("play-cadence-seconds-control").textContent,
    ).toContain("Trigger");
  });

  it("currentValue=20 (non-canonical) snaps to the nearest preset", () => {
    // 20 sits between 10 and 30 — ties break toward the earlier
    // preset (the snap function iterates in order and only swaps on
    // strictly-smaller diffs). diff(20,10)=10 == diff(20,30)=10, so
    // 10s wins by tie-break.
    const api = buildCadenceApi();
    render(
      <PlayCadenceSecondsControl
        api={api as unknown as ApiClient}
        currentValue={20}
        onValueChanged={() => {}}
      />,
    );
    expect(
      screen
        .getByTestId("play-cadence-seconds-10")
        .getAttribute("aria-pressed"),
    ).toBe("true");
    for (const value of [0, 30, 60]) {
      expect(
        screen
          .getByTestId(`play-cadence-seconds-${value}`)
          .getAttribute("aria-pressed"),
      ).toBe("false");
    }
  });

  it("clicking 'off' calls setPlayCadenceSeconds(0) — NOT undefined, NOT a falsy short-circuit", async () => {
    // Explicit pin against the ``pendingValue || currentValue``
    // anti-pattern: ``0`` must round-trip as 0 through both the
    // callback and the in-flight pending state. A regression here
    // would silently coerce off to the default cadence.
    const api = buildCadenceApi();
    const onValueChanged = vi.fn();
    render(
      <PlayCadenceSecondsControl
        api={api as unknown as ApiClient}
        currentValue={30}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("play-cadence-seconds-0"));
    await waitFor(() => {
      expect(api.setPlayCadenceSeconds).toHaveBeenCalledWith(
        0,
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith(0);
    });
    // After the PUT resolves the off button is pressed (the parent
    // would normally rerender us with currentValue=0; here the
    // pending state has cleared and the parent prop is still 30 —
    // but the test above already pinned the callback. To pin the
    // display path we need a fresh render with currentValue=0,
    // which the dedicated "currentValue=0 shows ..." test covers.)
  });

  it("clicking 10 calls setPlayCadenceSeconds(10) and onValueChanged(10)", async () => {
    const api = buildCadenceApi();
    const onValueChanged = vi.fn();
    render(
      <PlayCadenceSecondsControl
        api={api as unknown as ApiClient}
        currentValue={60}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("play-cadence-seconds-10"));
    await waitFor(() => {
      expect(api.setPlayCadenceSeconds).toHaveBeenCalledWith(
        10,
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith(10);
    });
  });

  it("PUT rejection surfaces an inline error and reverts the optimistic flip", async () => {
    const api = buildCadenceApi();
    api.setPlayCadenceSeconds.mockRejectedValueOnce(new Error("cadence boom"));
    const onValueChanged = vi.fn();
    render(
      <PlayCadenceSecondsControl
        api={api as unknown as ApiClient}
        currentValue={30}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("play-cadence-seconds-10"));
    await waitFor(() => {
      expect(
        screen.getByTestId("play-cadence-seconds-error").textContent,
      ).toContain("cadence boom");
    });
    expect(onValueChanged).not.toHaveBeenCalled();
    // The prior selection (30s) remains the pressed button — the
    // optimistic flip reverted because the PUT failed.
    expect(
      screen
        .getByTestId("play-cadence-seconds-30")
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen
        .getByTestId("play-cadence-seconds-10")
        .getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("disables all buttons while the PUT is in flight", async () => {
    const api = buildCadenceApi();
    type Resolver = (value: { value: number }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setPlayCadenceSeconds.mockImplementationOnce(
      () =>
        new Promise<{ value: number }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );
    render(
      <PlayCadenceSecondsControl
        api={api as unknown as ApiClient}
        currentValue={30}
        onValueChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("play-cadence-seconds-60"));
    await waitFor(() => {
      expect(
        (screen.getByTestId("play-cadence-seconds-60") as HTMLButtonElement)
          .disabled,
      ).toBe(true);
    });
    for (const value of [0, 10, 30]) {
      expect(
        (
          screen.getByTestId(
            `play-cadence-seconds-${value}`,
          ) as HTMLButtonElement
        ).disabled,
      ).toBe(true);
    }
    // Release the PUT so the test cleans up.
    resolverRef.current?.({ value: 60 });
    await waitFor(() => {
      expect(
        (screen.getByTestId("play-cadence-seconds-60") as HTMLButtonElement)
          .disabled,
      ).toBe(false);
    });
  });
});
