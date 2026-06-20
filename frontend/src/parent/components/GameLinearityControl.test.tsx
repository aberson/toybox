// Phase W Step W2 — GameLinearityControl component tests.
// Mirrors the shape of GameComplexityControl.test.tsx but with the
// 2-option set (Linear / Non-linear), default "nonlinear":
// - Both option buttons render with correct labels + testids
// - currentValue determines which button shows aria-pressed="true"
// - Clicking a different option fires setGameLinearity PUT
// - Optimistic pendingValue: all buttons disabled while PUT is in flight
// - On PUT success, onValueChanged callback is called with the new value
//   and the pending state is cleared
// - On PUT failure, the optimistic update reverts to currentValue and
//   an inline error message appears
//
// Mocking style: direct api-object stub via vi.fn, no vi.mock module-level
// replacement (mirrors GameComplexityControl.test.tsx).

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient, GameLinearity } from "../api";
import { GameLinearityControl } from "./GameLinearityControl";

interface StubApi {
  setGameLinearity: Mock;
}

function buildStubApi(): StubApi {
  return {
    setGameLinearity: vi.fn(async (value: GameLinearity) => ({
      value,
    })) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("GameLinearityControl", () => {
  it("renders both option buttons with the correct labels + testids", () => {
    const api = buildStubApi();
    render(
      <GameLinearityControl
        api={api as unknown as ApiClient}
        currentValue="nonlinear"
        onValueChanged={() => {}}
      />,
    );
    expect(screen.getByTestId("game-linearity-linear").textContent).toBe(
      "Linear",
    );
    expect(screen.getByTestId("game-linearity-nonlinear").textContent).toBe(
      "Non-linear",
    );
  });

  it("currentValue=nonlinear shows the nonlinear button as pressed and linear as not", () => {
    const api = buildStubApi();
    render(
      <GameLinearityControl
        api={api as unknown as ApiClient}
        currentValue="nonlinear"
        onValueChanged={() => {}}
      />,
    );
    expect(
      screen.getByTestId("game-linearity-nonlinear").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("game-linearity-linear").getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("clicking linear calls setGameLinearity(linear) and onValueChanged(linear)", async () => {
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <GameLinearityControl
        api={api as unknown as ApiClient}
        currentValue="nonlinear"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("game-linearity-linear"));
    await waitFor(() => {
      expect(api.setGameLinearity).toHaveBeenCalledWith(
        "linear",
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith("linear");
    });
    await waitFor(() => {
      expect(
        (screen.getByTestId("game-linearity-linear") as HTMLButtonElement)
          .disabled,
      ).toBe(false);
    });
  });

  it("during the in-flight PUT, all buttons are disabled and aria-pressed tracks the optimistic value", async () => {
    const api = buildStubApi();
    type Resolver = (value: { value: GameLinearity }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setGameLinearity.mockImplementationOnce(
      () =>
        new Promise<{ value: GameLinearity }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );
    const onValueChanged = vi.fn();
    render(
      <GameLinearityControl
        api={api as unknown as ApiClient}
        currentValue="nonlinear"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("game-linearity-linear"));
    await waitFor(() => {
      expect(
        (screen.getByTestId("game-linearity-linear") as HTMLButtonElement)
          .disabled,
      ).toBe(true);
    });
    expect(
      (screen.getByTestId("game-linearity-nonlinear") as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      screen.getByTestId("game-linearity-linear").getAttribute("aria-pressed"),
    ).toBe("true");
    resolverRef.current?.({ value: "linear" });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith("linear");
    });
    await waitFor(() => {
      expect(
        (screen.getByTestId("game-linearity-linear") as HTMLButtonElement)
          .disabled,
      ).toBe(false);
    });
  });

  it("PUT rejection surfaces an inline error and does NOT call onValueChanged, reverts to currentValue", async () => {
    const api = buildStubApi();
    api.setGameLinearity.mockRejectedValueOnce(new Error("dial boom"));
    const onValueChanged = vi.fn();
    render(
      <GameLinearityControl
        api={api as unknown as ApiClient}
        currentValue="nonlinear"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("game-linearity-linear"));
    await waitFor(() => {
      expect(
        screen.getByTestId("game-linearity-error").textContent,
      ).toContain("dial boom");
    });
    expect(onValueChanged).not.toHaveBeenCalled();
    expect(
      (screen.getByTestId("game-linearity-linear") as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    expect(
      screen.getByTestId("game-linearity-nonlinear").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("game-linearity-linear").getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("does not fire a second PUT when the operator re-clicks during an in-flight request", async () => {
    const api = buildStubApi();
    type Resolver = (value: { value: GameLinearity }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setGameLinearity.mockImplementationOnce(
      () =>
        new Promise<{ value: GameLinearity }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );
    const onValueChanged = vi.fn();
    render(
      <GameLinearityControl
        api={api as unknown as ApiClient}
        currentValue="nonlinear"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("game-linearity-linear"));
    fireEvent.click(screen.getByTestId("game-linearity-nonlinear"));
    expect(api.setGameLinearity).toHaveBeenCalledTimes(1);
    resolverRef.current?.({ value: "linear" });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith("linear");
    });
  });
});
