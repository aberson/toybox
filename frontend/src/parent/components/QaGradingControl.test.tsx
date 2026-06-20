// Phase W Step W3 — QaGradingControl component tests.
// Mirrors ParentInvolvementControl.test.tsx (3-option segmented dial):
// - All 3 option buttons render with correct labels + testids
// - currentValue determines which button shows aria-pressed="true"
// - Clicking a different option fires setQaGrading PUT + onValueChanged
// - Optimistic pendingValue: all buttons disabled while PUT is in flight
// - On PUT failure, the optimistic update reverts + an inline error shows
// - A re-click during an in-flight request fires no second PUT

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient, QaGrading } from "../api";
import { QaGradingControl } from "./QaGradingControl";

interface StubApi {
  setQaGrading: Mock;
}

function buildStubApi(): StubApi {
  return {
    setQaGrading: vi.fn(async (value: QaGrading) => ({ value })) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("QaGradingControl", () => {
  it("renders all 3 option buttons with the correct labels + testids", () => {
    const api = buildStubApi();
    render(
      <QaGradingControl
        api={api as unknown as ApiClient}
        currentValue="off"
        onValueChanged={() => {}}
      />,
    );
    expect(screen.getByTestId("qa-grading-off").textContent).toBe("Off");
    expect(screen.getByTestId("qa-grading-lenient").textContent).toBe(
      "Lenient",
    );
    expect(screen.getByTestId("qa-grading-strict").textContent).toBe("Strict");
  });

  it("currentValue=off shows the off button pressed and the others not", () => {
    const api = buildStubApi();
    render(
      <QaGradingControl
        api={api as unknown as ApiClient}
        currentValue="off"
        onValueChanged={() => {}}
      />,
    );
    expect(
      screen.getByTestId("qa-grading-off").getAttribute("aria-pressed"),
    ).toBe("true");
    for (const value of ["lenient", "strict"]) {
      expect(
        screen.getByTestId(`qa-grading-${value}`).getAttribute("aria-pressed"),
      ).toBe("false");
    }
  });

  it("clicking strict calls setQaGrading(strict) and onValueChanged(strict)", async () => {
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <QaGradingControl
        api={api as unknown as ApiClient}
        currentValue="off"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("qa-grading-strict"));
    await waitFor(() => {
      expect(api.setQaGrading).toHaveBeenCalledWith(
        "strict",
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith("strict");
    });
    await waitFor(() => {
      expect(
        (screen.getByTestId("qa-grading-strict") as HTMLButtonElement).disabled,
      ).toBe(false);
    });
  });

  it("during the in-flight PUT, all buttons are disabled and aria-pressed tracks the optimistic value", async () => {
    const api = buildStubApi();
    type Resolver = (value: { value: QaGrading }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setQaGrading.mockImplementationOnce(
      () =>
        new Promise<{ value: QaGrading }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );
    const onValueChanged = vi.fn();
    render(
      <QaGradingControl
        api={api as unknown as ApiClient}
        currentValue="off"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("qa-grading-lenient"));
    await waitFor(() => {
      expect(
        (screen.getByTestId("qa-grading-lenient") as HTMLButtonElement)
          .disabled,
      ).toBe(true);
    });
    for (const value of ["off", "strict"]) {
      expect(
        (screen.getByTestId(`qa-grading-${value}`) as HTMLButtonElement)
          .disabled,
      ).toBe(true);
    }
    expect(
      screen.getByTestId("qa-grading-lenient").getAttribute("aria-pressed"),
    ).toBe("true");
    resolverRef.current?.({ value: "lenient" });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith("lenient");
    });
  });

  it("PUT rejection surfaces an inline error and reverts to currentValue", async () => {
    const api = buildStubApi();
    api.setQaGrading.mockRejectedValueOnce(new Error("dial boom"));
    const onValueChanged = vi.fn();
    render(
      <QaGradingControl
        api={api as unknown as ApiClient}
        currentValue="off"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("qa-grading-lenient"));
    await waitFor(() => {
      expect(screen.getByTestId("qa-grading-error").textContent).toContain(
        "dial boom",
      );
    });
    expect(onValueChanged).not.toHaveBeenCalled();
    expect(
      screen.getByTestId("qa-grading-off").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("qa-grading-lenient").getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("does not fire a second PUT when re-clicked during an in-flight request", async () => {
    const api = buildStubApi();
    type Resolver = (value: { value: QaGrading }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setQaGrading.mockImplementationOnce(
      () =>
        new Promise<{ value: QaGrading }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );
    const onValueChanged = vi.fn();
    render(
      <QaGradingControl
        api={api as unknown as ApiClient}
        currentValue="off"
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("qa-grading-lenient"));
    fireEvent.click(screen.getByTestId("qa-grading-strict"));
    expect(api.setQaGrading).toHaveBeenCalledTimes(1);
    resolverRef.current?.({ value: "lenient" });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith("lenient");
    });
  });
});
