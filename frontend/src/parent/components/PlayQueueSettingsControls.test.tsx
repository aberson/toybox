// Component tests for the Phase J step J10 play-queue settings
// segmented control (PlayTargetDepthControl). Mirrors
// TranscriptRetentionControl.test.tsx shape: stubs the relevant
// ApiClient method, exercises render + click + reject paths, and pins
// the snap-to-nearest defensive behavior for non-canonical seed values.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../api";
import {
  PlayTargetDepthControl,
} from "./PlayQueueSettingsControls";

interface StubTargetDepthApi {
  setPlayTargetDepth: Mock;
}

function buildTargetDepthApi(): StubTargetDepthApi {
  return {
    setPlayTargetDepth: vi.fn(async (value: number) => ({ value })) as Mock,
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
