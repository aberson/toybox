// Component tests for the Phase H step H5 BannedThemesSettings editor.
// Stubs ApiClient.getBannedThemesGlobal + setBannedThemesGlobal and
// exercises:
// - initial GET populates the textarea + Save is disabled
// - typing into the textarea enables Save + shows the dirty indicator
// - click Save → PUT called with the typed value; Save disables again
// - click preset → textarea gets the merged themes; Save enables
// - server error on Save → inline error visible + value retained for retry

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient, BannedThemesResponse } from "../api";
import { BannedThemesSettings } from "./BannedThemesSettings";

interface StubApi {
  getBannedThemesGlobal: Mock;
  setBannedThemesGlobal: Mock;
}

function buildStubApi(initial: string | null = null): StubApi {
  return {
    getBannedThemesGlobal: vi.fn(
      async (): Promise<BannedThemesResponse> => ({ themes: initial }),
    ) as Mock,
    setBannedThemesGlobal: vi.fn(
      async (themes: string | null): Promise<BannedThemesResponse> => ({
        // Mirror the backend contract: empty/whitespace round-trips to
        // null. Tests that need a different contract override via
        // ``mockResolvedValueOnce``.
        themes:
          themes === null || themes.trim() === "" ? null : themes,
      }),
    ) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BannedThemesSettings", () => {
  it("loads the initial value (empty) and Save is disabled", async () => {
    const api = buildStubApi(null);
    render(<BannedThemesSettings api={api as unknown as ApiClient} />);
    await waitFor(() => {
      expect(api.getBannedThemesGlobal).toHaveBeenCalled();
    });
    const textarea = (await screen.findByTestId(
      "banned-themes-textarea",
    )) as HTMLTextAreaElement;
    expect(textarea.value).toBe("");
    const saveBtn = screen.getByTestId(
      "banned-themes-save-button",
    ) as HTMLButtonElement;
    expect(saveBtn.disabled).toBe(true);
    // No dirty indicator on a clean load.
    expect(screen.queryByTestId("banned-themes-dirty-indicator")).toBeNull();
  });

  it("loads a non-empty initial value into the textarea", async () => {
    const api = buildStubApi("monsters, spiders");
    render(<BannedThemesSettings api={api as unknown as ApiClient} />);
    const textarea = (await screen.findByTestId(
      "banned-themes-textarea",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(textarea.value).toBe("monsters, spiders");
    });
    expect(
      (screen.getByTestId("banned-themes-save-button") as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("typing into the textarea enables Save and shows dirty indicator", async () => {
    const api = buildStubApi(null);
    render(<BannedThemesSettings api={api as unknown as ApiClient} />);
    const textarea = (await screen.findByTestId(
      "banned-themes-textarea",
    )) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "monsters" } });
    const saveBtn = screen.getByTestId(
      "banned-themes-save-button",
    ) as HTMLButtonElement;
    expect(saveBtn.disabled).toBe(false);
    expect(screen.getByTestId("banned-themes-dirty-indicator")).toBeTruthy();
  });

  it("clicking Save PUTs the value, then Save returns to disabled and dirty hint clears", async () => {
    const api = buildStubApi(null);
    render(<BannedThemesSettings api={api as unknown as ApiClient} />);
    const textarea = (await screen.findByTestId(
      "banned-themes-textarea",
    )) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "monsters, spiders" } });
    fireEvent.click(screen.getByTestId("banned-themes-save-button"));
    await waitFor(() => {
      expect(api.setBannedThemesGlobal).toHaveBeenCalledWith(
        "monsters, spiders",
      );
    });
    await waitFor(() => {
      expect(
        (screen.getByTestId("banned-themes-save-button") as HTMLButtonElement)
          .disabled,
      ).toBe(true);
    });
    expect(screen.queryByTestId("banned-themes-dirty-indicator")).toBeNull();
    // The textarea retains the saved value.
    expect(textarea.value).toBe("monsters, spiders");
  });

  it("appending a preset bundle merges its themes into the textarea and enables Save", async () => {
    const api = buildStubApi(null);
    render(<BannedThemesSettings api={api as unknown as ApiClient} />);
    await screen.findByTestId("banned-themes-textarea");
    const select = screen.getByTestId(
      "banned-themes-preset-select",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "horror-and-gore" } });
    // Preview shows for the picked bundle.
    expect(screen.getByTestId("banned-themes-preset-preview")).toBeTruthy();
    fireEvent.click(screen.getByTestId("banned-themes-preset-append"));
    const textarea = screen.getByTestId(
      "banned-themes-textarea",
    ) as HTMLTextAreaElement;
    expect(textarea.value).toContain("horror");
    expect(textarea.value).toContain("gore");
    expect(textarea.value).toContain("zombies");
    // Save enables on dirty.
    expect(
      (screen.getByTestId("banned-themes-save-button") as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    expect(screen.getByTestId("banned-themes-dirty-indicator")).toBeTruthy();
    // The picker resets after append so a second bundle can be appended.
    expect(select.value).toBe("");
    expect(screen.queryByTestId("banned-themes-preset-preview")).toBeNull();
  });

  it("surfaces a server error on Save and retains the textarea value for retry", async () => {
    const api = buildStubApi(null);
    api.setBannedThemesGlobal.mockRejectedValueOnce(new Error("backend down"));
    render(<BannedThemesSettings api={api as unknown as ApiClient} />);
    const textarea = (await screen.findByTestId(
      "banned-themes-textarea",
    )) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "ghosts" } });
    fireEvent.click(screen.getByTestId("banned-themes-save-button"));
    await waitFor(() => {
      expect(screen.getByTestId("banned-themes-save-error").textContent).toContain(
        "backend down",
      );
    });
    // The typed value survives so the operator can retry.
    expect(textarea.value).toBe("ghosts");
    // Save is back to enabled (we're still dirty + not saving).
    expect(
      (screen.getByTestId("banned-themes-save-button") as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("clearing the textarea and saving sends null and clears the persisted value", async () => {
    const api = buildStubApi("ghosts");
    render(<BannedThemesSettings api={api as unknown as ApiClient} />);
    const textarea = (await screen.findByTestId(
      "banned-themes-textarea",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(textarea.value).toBe("ghosts");
    });
    fireEvent.change(textarea, { target: { value: "" } });
    fireEvent.click(screen.getByTestId("banned-themes-save-button"));
    await waitFor(() => {
      expect(api.setBannedThemesGlobal).toHaveBeenCalledWith(null);
    });
    await waitFor(() => {
      expect(textarea.value).toBe("");
    });
    expect(
      (screen.getByTestId("banned-themes-save-button") as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });
});
