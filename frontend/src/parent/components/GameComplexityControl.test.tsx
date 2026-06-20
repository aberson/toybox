// Phase W Step W1 — GameComplexityControl component tests.
// Mirrors the shape of SpokenTextLimitControl.test.tsx:
// - All 3 option buttons render with correct labels + testids
// - currentValue determines which button shows aria-pressed="true"
// - Clicking a different option fires setGameComplexity PUT
// - Optimistic pendingValue: all buttons disabled while PUT is in flight
// - On PUT success, onValueChanged callback is called with the new value
//   and the pending state is cleared
// - On PUT failure, the optimistic update reverts to currentValue and
//   an inline error message appears
//
// Mocking style: direct api-object stub via vi.fn, no vi.mock module-level
// replacement (mirrors SpokenTextLimitControl.test.tsx).

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient, GameComplexity } from "../api";
import { GameComplexityControl } from "./GameComplexityControl";

interface StubApi {
  setGameComplexity: Mock;
}

function buildStubApi(): StubApi {
  return {
    setGameComplexity: vi.fn(async (value: GameComplexity) => ({
      value,
    })) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("GameComplexityControl", () => {
  it("renders all 3 option buttons with the correct labels + testids", () => {
    const api = buildStubApi();
    render(
      <GameComplexityControl
        api={api as unknown as ApiClient}
        currentValue="medium"
        onValueChanged={() => {}}
      />,
    );
    expect(screen.getByTestId("game-complexity-low").textContent).toBe("Low");
    expect(screen.getByTestId("game-complexity-medium").textContent).toBe(
      "Medium",
    );
    expect(screen.getByTestId("game-complexity-high").textContent).toBe(
      "High",
    );
  });

  it("currentValue=medium shows the medium button as pressed and the others as not", () => {
    const api = buildStubApi();
    render(
      <GameComplexityControl
        api={api as unknown as ApiClient}
        currentValue="medium"
        onValueChanged={() => {}}
      />,
    );
    expect(
      screen.getByTestId("game-complexity-medium").getAttribute("aria-pressed"),
    ).toBe("true");
    for (const value of ["low", "high"]) {
      expect(
        screen
          .getByTestId(`game-complexity-${value}`)
          .getAttribute("aria-pressed"),
      ).toBe("false");
    }
  });

  it("clicking high calls setGameComplexity(high) and onValueChanged(high)", async () => {
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <GameComplexityControl
        api={api as unknown as ApiClient}
        currentValue="medium"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("game-complexity-high"));
    await waitFor(() => {
      expect(api.setGameComplexity).toHaveBeenCalledWith(
        "high",
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith("high");
    });
    await waitFor(() => {
      expect(
        (screen.getByTestId("game-complexity-high") as HTMLButtonElement)
          .disabled,
      ).toBe(false);
    });
  });

  it("during the in-flight PUT, all buttons are disabled and aria-pressed tracks the optimistic value", async () => {
    const api = buildStubApi();
    type Resolver = (value: { value: GameComplexity }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setGameComplexity.mockImplementationOnce(
      () =>
        new Promise<{ value: GameComplexity }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );
    const onValueChanged = vi.fn();
    render(
      <GameComplexityControl
        api={api as unknown as ApiClient}
        currentValue="medium"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("game-complexity-low"));
    await waitFor(() => {
      expect(
        (screen.getByTestId("game-complexity-low") as HTMLButtonElement)
          .disabled,
      ).toBe(true);
    });
    for (const value of ["medium", "high"]) {
      expect(
        (
          screen.getByTestId(`game-complexity-${value}`) as HTMLButtonElement
        ).disabled,
      ).toBe(true);
    }
    expect(
      screen.getByTestId("game-complexity-low").getAttribute("aria-pressed"),
    ).toBe("true");
    resolverRef.current?.({ value: "low" });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith("low");
    });
    await waitFor(() => {
      expect(
        (screen.getByTestId("game-complexity-low") as HTMLButtonElement)
          .disabled,
      ).toBe(false);
    });
  });

  it("PUT rejection surfaces an inline error and does NOT call onValueChanged, reverts to currentValue", async () => {
    const api = buildStubApi();
    api.setGameComplexity.mockRejectedValueOnce(new Error("dial boom"));
    const onValueChanged = vi.fn();
    render(
      <GameComplexityControl
        api={api as unknown as ApiClient}
        currentValue="medium"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("game-complexity-low"));
    await waitFor(() => {
      expect(
        screen.getByTestId("game-complexity-error").textContent,
      ).toContain("dial boom");
    });
    expect(onValueChanged).not.toHaveBeenCalled();
    expect(
      (screen.getByTestId("game-complexity-low") as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(
      screen.getByTestId("game-complexity-medium").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("game-complexity-low").getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("does not fire a second PUT when the operator re-clicks during an in-flight request", async () => {
    const api = buildStubApi();
    type Resolver = (value: { value: GameComplexity }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setGameComplexity.mockImplementationOnce(
      () =>
        new Promise<{ value: GameComplexity }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );
    const onValueChanged = vi.fn();
    render(
      <GameComplexityControl
        api={api as unknown as ApiClient}
        currentValue="medium"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("game-complexity-low"));
    fireEvent.click(screen.getByTestId("game-complexity-high"));
    expect(api.setGameComplexity).toHaveBeenCalledTimes(1);
    resolverRef.current?.({ value: "low" });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith("low");
    });
  });
});
