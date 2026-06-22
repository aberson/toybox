// Component tests for the F.5-toggle ImageGenModeToggle card.
// Stubs ApiClient.getImageGenMode + setImageGenMode and exercises:
// - initial GET populates the active button
// - clicking a button triggers setImageGenMode with that mode
// - busy state disables all buttons + shows "Saving..." on the active
// - the persisted value is reflected after the GET resolves

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient, ImageGenMode } from "../api";
import { ImageGenModeToggle } from "./SettingsPanel";

interface StubApi {
  getImageGenMode: Mock;
  setImageGenMode: Mock;
}

function buildStubApi(initial: ImageGenMode): StubApi {
  return {
    getImageGenMode: vi.fn(async () => ({ mode: initial })) as Mock,
    setImageGenMode: vi.fn(async (mode: ImageGenMode) => ({ mode })) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ImageGenModeToggle", () => {
  it("renders all three options with their descriptors", async () => {
    const api = buildStubApi("cartoon");
    render(<ImageGenModeToggle api={api as unknown as ApiClient} />);

    // ``findByTestId`` already throws if the element isn't there, so a
    // ``.toBeTruthy()`` check on the returned node would be a tautology.
    // The actual contract is the descriptor text — assert that.
    const cartoonBtn = await screen.findByTestId("image-gen-mode-btn-cartoon");
    const compositeBtn = await screen.findByTestId(
      "image-gen-mode-btn-composite",
    );
    const claudeBtn = await screen.findByTestId(
      "image-gen-mode-btn-claude_svg",
    );
    expect(api.getImageGenMode).toHaveBeenCalled();
    expect(cartoonBtn.textContent).toContain("SD 1.5 stylized");
    expect(compositeBtn.textContent).toContain("Pillow templates");
    expect(claudeBtn.textContent).toContain("Claude draws");
  });

  it("calls setImageGenMode('claude_svg') when operator clicks Claude Images", async () => {
    const api = buildStubApi("cartoon");
    render(<ImageGenModeToggle api={api as unknown as ApiClient} />);
    await vi.waitFor(() =>
      expect(
        screen.getByTestId("image-gen-mode-btn-cartoon").getAttribute("data-active"),
      ).toBe("true"),
    );

    fireEvent.click(screen.getByTestId("image-gen-mode-btn-claude_svg"));

    await vi.waitFor(() =>
      expect(api.setImageGenMode).toHaveBeenCalledWith("claude_svg"),
    );
    await vi.waitFor(() =>
      expect(
        screen
          .getByTestId("image-gen-mode-btn-claude_svg")
          .getAttribute("data-active"),
      ).toBe("true"),
    );
  });

  it("reflects the value loaded from getImageGenMode on initial render", async () => {
    const api = buildStubApi("composite");
    render(<ImageGenModeToggle api={api as unknown as ApiClient} />);
    await vi.waitFor(() =>
      expect(
        screen
          .getByTestId("image-gen-mode-btn-composite")
          .getAttribute("data-active"),
      ).toBe("true"),
    );
    expect(
      screen.getByTestId("image-gen-mode-btn-cartoon").getAttribute("data-active"),
    ).toBe("false");
  });

  it("calls setImageGenMode('composite') when operator clicks Composite", async () => {
    const api = buildStubApi("cartoon");
    render(<ImageGenModeToggle api={api as unknown as ApiClient} />);
    // Wait for the initial GET to populate state so the click is not a
    // no-op against an already-active mode.
    await vi.waitFor(() =>
      expect(
        screen.getByTestId("image-gen-mode-btn-cartoon").getAttribute("data-active"),
      ).toBe("true"),
    );

    fireEvent.click(screen.getByTestId("image-gen-mode-btn-composite"));

    await vi.waitFor(() =>
      expect(api.setImageGenMode).toHaveBeenCalledWith("composite"),
    );
    await vi.waitFor(() =>
      expect(
        screen
          .getByTestId("image-gen-mode-btn-composite")
          .getAttribute("data-active"),
      ).toBe("true"),
    );
  });

  it("shows busy state (disabled + 'Saving...' text) while the PUT is in flight", async () => {
    const api = buildStubApi("cartoon");

    // Make setImageGenMode hang until the test releases the promise.
    type Resolver = (value: { mode: ImageGenMode }) => void;
    const resolverRef: { current: Resolver | null } = { current: null };
    api.setImageGenMode.mockImplementationOnce(
      () =>
        new Promise<{ mode: ImageGenMode }>((resolve) => {
          resolverRef.current = resolve;
        }),
    );

    render(<ImageGenModeToggle api={api as unknown as ApiClient} />);
    await vi.waitFor(() =>
      expect(
        screen.getByTestId("image-gen-mode-btn-cartoon").getAttribute("data-active"),
      ).toBe("true"),
    );

    fireEvent.click(screen.getByTestId("image-gen-mode-btn-composite"));

    // While busy, both buttons are disabled and the active-side label
    // becomes "Saving...".
    await vi.waitFor(() =>
      expect(
        (
          screen.getByTestId("image-gen-mode-btn-composite") as HTMLButtonElement
        ).disabled,
      ).toBe(true),
    );
    expect(
      (screen.getByTestId("image-gen-mode-btn-cartoon") as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      screen.getByTestId("image-gen-mode-btn-composite").textContent,
    ).toContain("Saving...");

    // Release the put.
    resolverRef.current?.({ mode: "composite" });
    await vi.waitFor(() =>
      expect(
        (
          screen.getByTestId("image-gen-mode-btn-composite") as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    );
    expect(
      screen.getByTestId("image-gen-mode-btn-composite").textContent,
    ).not.toContain("Saving...");
  });

  it("does not fire a PUT when the user re-clicks the already-active mode", async () => {
    const api = buildStubApi("cartoon");
    render(<ImageGenModeToggle api={api as unknown as ApiClient} />);
    await vi.waitFor(() => {
      expect(
        screen.getByTestId("image-gen-mode-btn-cartoon").getAttribute("data-active"),
      ).toBe("true");
    });

    fireEvent.click(screen.getByTestId("image-gen-mode-btn-cartoon"));
    // Yield once so any pending microtasks run; the click is a no-op
    // because the clicked mode equals the current mode.
    await Promise.resolve();
    expect(api.setImageGenMode).not.toHaveBeenCalled();
  });
});
