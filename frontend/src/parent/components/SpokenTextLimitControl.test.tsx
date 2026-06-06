// Phase R Step R2 — SpokenTextLimitControl component tests.
// Mirrors the shape of TranscriptRetentionControl.test.tsx:
// - All 5 preset buttons render with correct labels + testids
// - currentValue determines which button shows aria-pressed="true"
// - Clicking a different preset fires setSpokenTextLimit PUT
// - Optimistic pendingValue: all buttons disabled while PUT is in flight
// - On PUT success, onValueChanged callback is called with the new value
//   and the pending state is cleared
// - On PUT failure, the optimistic update reverts to currentValue and
//   an inline error message appears
//
// Mocking style: direct api-object stub via vi.fn, no vi.mock module-level
// replacement (mirrors ImageGenModeToggle.test.tsx + SettingsPanel.test.tsx).

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient, SpokenTextLimit } from "../api";
import { SpokenTextLimitControl } from "./SpokenTextLimitControl";

interface StubApi {
  setSpokenTextLimit: Mock;
}

function buildStubApi(): StubApi {
  return {
    setSpokenTextLimit: vi.fn(async (value: SpokenTextLimit) => ({
      value,
    })) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SpokenTextLimitControl", () => {
  it("renders all 5 preset buttons with the correct labels + testids", () => {
    const api = buildStubApi();
    render(
      <SpokenTextLimitControl
        api={api as unknown as ApiClient}
        currentValue={150}
        onValueChanged={() => {}}
      />,
    );
    const btnOff = screen.getByTestId("spoken-text-limit-0");
    const btn50 = screen.getByTestId("spoken-text-limit-50");
    const btn100 = screen.getByTestId("spoken-text-limit-100");
    const btn150 = screen.getByTestId("spoken-text-limit-150");
    const btn250 = screen.getByTestId("spoken-text-limit-250");
    expect(btnOff.textContent).toBe("off");
    expect(btn50.textContent).toBe("50");
    expect(btn100.textContent).toBe("100");
    expect(btn150.textContent).toBe("150");
    expect(btn250.textContent).toBe("250");
  });

  it("currentValue=150 shows the 150 button as pressed and the others as not", () => {
    const api = buildStubApi();
    render(
      <SpokenTextLimitControl
        api={api as unknown as ApiClient}
        currentValue={150}
        onValueChanged={() => {}}
      />,
    );
    expect(
      screen.getByTestId("spoken-text-limit-150").getAttribute("aria-pressed"),
    ).toBe("true");
    for (const value of [0, 50, 100, 250]) {
      expect(
        screen
          .getByTestId(`spoken-text-limit-${value}`)
          .getAttribute("aria-pressed"),
      ).toBe("false");
    }
  });

  it("currentValue=0 (off) shows the 'off' button as pressed", () => {
    const api = buildStubApi();
    render(
      <SpokenTextLimitControl
        api={api as unknown as ApiClient}
        currentValue={0}
        onValueChanged={() => {}}
      />,
    );
    expect(
      screen.getByTestId("spoken-text-limit-0").getAttribute("aria-pressed"),
    ).toBe("true");
    for (const value of [50, 100, 150, 250]) {
      expect(
        screen
          .getByTestId(`spoken-text-limit-${value}`)
          .getAttribute("aria-pressed"),
      ).toBe("false");
    }
  });

  it("clicking 250 calls setSpokenTextLimit(250) and onValueChanged(250)", async () => {
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <SpokenTextLimitControl
        api={api as unknown as ApiClient}
        currentValue={150}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("spoken-text-limit-250"));
    await waitFor(() => {
      // Second arg is the AbortController options — mirror with
      // ``expect.anything()`` so this test pins the value without
      // coupling to the request-options shape.
      expect(api.setSpokenTextLimit).toHaveBeenCalledWith(
        250,
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith(250);
    });
    // After the PUT resolves, no button is disabled — the pending state
    // has lifted and the operator can pick a different preset.
    await waitFor(() => {
      expect(
        (
          screen.getByTestId("spoken-text-limit-250") as HTMLButtonElement
        ).disabled,
      ).toBe(false);
    });
  });

  it("during the in-flight PUT, all buttons are disabled and aria-pressed tracks the optimistic value", async () => {
    const api = buildStubApi();
    // Hang the PUT until the test releases the resolver.
    type Resolver = (value: { value: SpokenTextLimit }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setSpokenTextLimit.mockImplementationOnce(
      () =>
        new Promise<{ value: SpokenTextLimit }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );
    const onValueChanged = vi.fn();
    render(
      <SpokenTextLimitControl
        api={api as unknown as ApiClient}
        currentValue={150}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("spoken-text-limit-50"));
    // While the PUT is in flight, ALL buttons are disabled.
    await waitFor(() => {
      expect(
        (
          screen.getByTestId("spoken-text-limit-50") as HTMLButtonElement
        ).disabled,
      ).toBe(true);
    });
    for (const value of [0, 100, 150, 250]) {
      expect(
        (
          screen.getByTestId(
            `spoken-text-limit-${value}`,
          ) as HTMLButtonElement
        ).disabled,
      ).toBe(true);
    }
    // The optimistic selection is shown via aria-pressed on the clicked button.
    expect(
      screen.getByTestId("spoken-text-limit-50").getAttribute("aria-pressed"),
    ).toBe("true");
    // Release the PUT — the pending state lifts.
    resolverRef.current?.({ value: 50 });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith(50);
    });
    await waitFor(() => {
      expect(
        (
          screen.getByTestId("spoken-text-limit-50") as HTMLButtonElement
        ).disabled,
      ).toBe(false);
    });
  });

  it("PUT rejection surfaces an inline error and does NOT call onValueChanged, reverts to currentValue", async () => {
    const api = buildStubApi();
    api.setSpokenTextLimit.mockRejectedValueOnce(
      new Error("limit boom"),
    );
    const onValueChanged = vi.fn();
    render(
      <SpokenTextLimitControl
        api={api as unknown as ApiClient}
        currentValue={150}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("spoken-text-limit-50"));
    await waitFor(() => {
      expect(
        screen.getByTestId("spoken-text-limit-error").textContent,
      ).toContain("limit boom");
    });
    expect(onValueChanged).not.toHaveBeenCalled();
    // The pending state is cleared so the operator can retry.
    expect(
      (
        screen.getByTestId("spoken-text-limit-50") as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    // The prior selection (150) remains the pressed button — the
    // optimistic flip was reverted because the PUT failed.
    expect(
      screen
        .getByTestId("spoken-text-limit-150")
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen
        .getByTestId("spoken-text-limit-50")
        .getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("does not fire a second PUT when the operator re-clicks during an in-flight request", async () => {
    const api = buildStubApi();
    type Resolver = (value: { value: SpokenTextLimit }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setSpokenTextLimit.mockImplementationOnce(
      () =>
        new Promise<{ value: SpokenTextLimit }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );
    const onValueChanged = vi.fn();
    render(
      <SpokenTextLimitControl
        api={api as unknown as ApiClient}
        currentValue={150}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("spoken-text-limit-50"));
    // A rapid second click against a different preset is a no-op while
    // the first is in flight (the disabled attribute already blocks the
    // event in browsers, but the handler guards too).
    fireEvent.click(screen.getByTestId("spoken-text-limit-100"));
    expect(api.setSpokenTextLimit).toHaveBeenCalledTimes(1);
    // Release the first PUT to clean up.
    resolverRef.current?.({ value: 50 });
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith(50);
    });
  });
});
