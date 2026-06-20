// Phase W Step W5 — BossFightsControl component tests.
// Mirrors GameLinearityControl.test.tsx but with the boolean On/Off set
// (default On):
// - Both option buttons render with correct labels + testids
// - currentValue determines which button shows aria-pressed="true"
// - Clicking a different option fires setBossFightsEnabled PUT
// - Optimistic pendingValue: all buttons disabled while PUT is in flight
// - On PUT success, onValueChanged is called with the new value
// - On PUT failure, the optimistic update reverts + an inline error shows

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../api";
import { BossFightsControl } from "./BossFightsControl";

interface StubApi {
  setBossFightsEnabled: Mock;
}

function buildStubApi(): StubApi {
  return {
    setBossFightsEnabled: vi.fn(async (value: boolean) => ({ value })) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BossFightsControl", () => {
  it("renders both option buttons with the correct labels + testids", () => {
    const api = buildStubApi();
    render(
      <BossFightsControl
        api={api as unknown as ApiClient}
        currentValue={true}
        onValueChanged={() => {}}
      />,
    );
    expect(screen.getByTestId("boss-fights-on").textContent).toBe("On");
    expect(screen.getByTestId("boss-fights-off").textContent).toBe("Off");
  });

  it("currentValue=true shows On pressed and Off not", () => {
    const api = buildStubApi();
    render(
      <BossFightsControl
        api={api as unknown as ApiClient}
        currentValue={true}
        onValueChanged={() => {}}
      />,
    );
    expect(
      screen.getByTestId("boss-fights-on").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("boss-fights-off").getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("clicking Off fires setBossFightsEnabled(false) + onValueChanged", async () => {
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <BossFightsControl
        api={api as unknown as ApiClient}
        currentValue={true}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("boss-fights-off"));
    await waitFor(() => {
      expect(api.setBossFightsEnabled).toHaveBeenCalledWith(
        false,
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith(false);
    });
  });

  it("disables all buttons while a PUT is in flight", async () => {
    // Defer the PUT so it stays "in flight" while we assert.
    let resolvePut: (resp: { value: boolean }) => void = () => {};
    const api: StubApi = {
      setBossFightsEnabled: vi.fn(
        () =>
          new Promise<{ value: boolean }>((resolve) => {
            resolvePut = resolve;
          }),
      ) as Mock,
    };
    render(
      <BossFightsControl
        api={api as unknown as ApiClient}
        currentValue={true}
        onValueChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("boss-fights-off"));
    // PUT is pending → both option buttons are disabled.
    await waitFor(() => {
      expect(
        (screen.getByTestId("boss-fights-on") as HTMLButtonElement).disabled,
      ).toBe(true);
    });
    expect(
      (screen.getByTestId("boss-fights-off") as HTMLButtonElement).disabled,
    ).toBe(true);
    // Resolving the PUT re-enables the buttons.
    resolvePut({ value: false });
    await waitFor(() => {
      expect(
        (screen.getByTestId("boss-fights-on") as HTMLButtonElement).disabled,
      ).toBe(false);
    });
  });

  it("reverts + shows an inline error on PUT failure", async () => {
    const api: StubApi = {
      setBossFightsEnabled: vi.fn(async () => {
        throw new Error("boom");
      }) as Mock,
    };
    const onValueChanged = vi.fn();
    render(
      <BossFightsControl
        api={api as unknown as ApiClient}
        currentValue={true}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("boss-fights-off"));
    await waitFor(() => {
      expect(screen.getByTestId("boss-fights-error")).toBeTruthy();
    });
    expect(onValueChanged).not.toHaveBeenCalled();
    // Reverts to On (currentValue) — On stays pressed.
    expect(
      screen.getByTestId("boss-fights-on").getAttribute("aria-pressed"),
    ).toBe("true");
  });
});
