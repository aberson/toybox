// Phase W Step W4 — AdventureButton unit tests.
//
// Covers:
//   - renders the "Start an Adventure" CTA
//   - clicking fires onStart exactly once
//   - the button is disabled (and onStart not re-fired) while the
//     in-flight onStart promise is still pending (busy state)
//   - respects the explicit ``disabled`` prop
//
// Uses plain DOM assertions (this project has no jest-dom setup); mirrors
// the .test.tsx style already used across src/parent.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdventureButton } from "./TriggerButton";

afterEach(() => {
  cleanup();
});

describe("AdventureButton", () => {
  it("renders the Start an Adventure CTA", () => {
    render(<AdventureButton onStart={vi.fn().mockResolvedValue(undefined)} />);
    const btn = screen.getByTestId("adventure-button") as HTMLButtonElement;
    expect(btn.textContent).toBe("Start an Adventure");
    expect(btn.disabled).toBe(false);
  });

  it("fires onStart exactly once on click", async () => {
    const onStart = vi.fn().mockResolvedValue(undefined);
    render(<AdventureButton onStart={onStart} />);
    fireEvent.click(screen.getByTestId("adventure-button"));
    await waitFor(() => expect(onStart).toHaveBeenCalledTimes(1));
  });

  it("disables and ignores extra clicks while onStart is pending", async () => {
    // A never-resolving promise pins the button in its busy state so we can
    // assert the disabled guard blocks a second invocation.
    let resolve: (() => void) | undefined;
    const onStart = vi.fn(
      () =>
        new Promise<void>((r) => {
          resolve = r;
        }),
    );
    render(<AdventureButton onStart={onStart} />);
    const btn = screen.getByTestId("adventure-button") as HTMLButtonElement;

    fireEvent.click(btn);
    await waitFor(() => expect(btn.disabled).toBe(true));
    expect(btn.textContent).toBe("Loading…");

    // Second click while busy must be a no-op.
    fireEvent.click(btn);
    expect(onStart).toHaveBeenCalledTimes(1);

    // Resolve and confirm the button re-enables.
    resolve?.();
    await waitFor(() => expect(btn.disabled).toBe(false));
  });

  it("respects the disabled prop and never fires onStart", () => {
    const onStart = vi.fn().mockResolvedValue(undefined);
    render(<AdventureButton onStart={onStart} disabled />);
    const btn = screen.getByTestId("adventure-button") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);
    expect(onStart).not.toHaveBeenCalled();
  });
});
