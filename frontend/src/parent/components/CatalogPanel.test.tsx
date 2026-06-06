// Phase T Step T3 — CatalogPanel unit tests.
//
// Covers:
//   - renders template cards with titles on successful fetch
//   - Launch button calls api.propose with correct template_id and intent
//   - theme chip filter hides non-matching cards
//   - no crash on empty catalog response
//   - loading state shown before fetch resolves
//   - error state shown on fetch failure

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CatalogEntry, CatalogResponse } from "../../shared/types";
import type { ApiClient } from "../api";
import { ApiError } from "../api";
import { CatalogPanel } from "./CatalogPanel";

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const ENTRY_ADVENTURES: CatalogEntry = {
  id: "adv_quest_001",
  title: "The Lost Treasure",
  intent: "play",
  themes: ["adventure", "treasure"],
  step_count: 5,
};

const ENTRY_ELEMENTS: CatalogEntry = {
  id: "element_gold_001",
  title: "Gold Explorer",
  intent: "learn",
  themes: ["periodic_table", "science"],
  step_count: 4,
};

const ENTRY_FEELINGS: CatalogEntry = {
  id: "feelings_share_001",
  title: "Sharing Circle",
  intent: "social",
  themes: ["feelings", "friendship"],
  step_count: 3,
};

function makeCatalogResponse(
  entries: CatalogEntry[] = [ENTRY_ADVENTURES, ENTRY_ELEMENTS, ENTRY_FEELINGS],
): CatalogResponse {
  return { entries, total: entries.length };
}

// Build a minimal ApiClient mock with getCatalog + propose.
function makeApi(
  catalogResponse: CatalogResponse | Error = makeCatalogResponse(),
): { api: Partial<ApiClient>; getCatalog: Mock; propose: Mock } {
  const getCatalog = vi.fn<[], Promise<CatalogResponse>>();
  if (catalogResponse instanceof Error) {
    getCatalog.mockRejectedValue(catalogResponse);
  } else {
    getCatalog.mockResolvedValue(catalogResponse);
  }
  const propose = vi.fn<[], Promise<unknown>>().mockResolvedValue({
    id: "act_001",
    state: "proposed",
    version: 1,
    title: "Test",
    steps: [],
    template_id: null,
    recommended_themes: [],
  });
  return { api: { getCatalog, propose } as unknown as Partial<ApiClient>, getCatalog, propose };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CatalogPanel", () => {
  it("renders template card titles after fetch resolves", async () => {
    const { api } = makeApi();
    render(<CatalogPanel filterCategory={undefined} api={api as ApiClient} />);

    // Loading state visible first.
    expect(screen.getByText("Loading catalog...")).toBeDefined();

    // After fetch, cards appear.
    await waitFor(() => {
      expect(screen.getAllByTestId("catalog-card-title").length).toBeGreaterThan(0);
    });

    const titles = screen
      .getAllByTestId("catalog-card-title")
      .map((el) => el.textContent);
    expect(titles).toContain("The Lost Treasure");
    expect(titles).toContain("Gold Explorer");
    expect(titles).toContain("Sharing Circle");
  });

  it("Launch button calls api.propose with the correct template_id and intent", async () => {
    const { api, propose } = makeApi(
      makeCatalogResponse([ENTRY_ADVENTURES]),
    );
    render(<CatalogPanel filterCategory={undefined} api={api as ApiClient} />);

    await waitFor(() => {
      expect(screen.getAllByTestId("catalog-card-launch").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByTestId("catalog-card-launch")[0]);

    expect(propose).toHaveBeenCalledOnce();
    const callArg = propose.mock.calls[0][0] as {
      template_id: string;
      intent: string;
      use_recent_transcripts: boolean;
    };
    expect(callArg.template_id).toBe("adv_quest_001");
    expect(callArg.intent).toBe("play");
    expect(callArg.use_recent_transcripts).toBe(false);
  });

  it("shows Proposed! toast after successful launch", async () => {
    const { api } = makeApi(makeCatalogResponse([ENTRY_ADVENTURES]));
    render(<CatalogPanel filterCategory={undefined} api={api as ApiClient} />);

    await waitFor(() => {
      expect(screen.getAllByTestId("catalog-card-launch").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByTestId("catalog-card-launch")[0]);

    await waitFor(() => {
      expect(screen.getByTestId("catalog-card-toast-ok")).toBeDefined();
    });
    expect(screen.getByTestId("catalog-card-toast-ok").textContent).toBe("Proposed!");
  });

  it("theme chip filter hides non-matching cards", async () => {
    const { api } = makeApi();
    render(<CatalogPanel filterCategory={undefined} api={api as ApiClient} />);

    await waitFor(() => {
      expect(screen.getAllByTestId("catalog-card-title").length).toBe(3);
    });

    // "treasure" theme only exists on ENTRY_ADVENTURES.
    const treasureChip = screen.getByTestId("catalog-chip-treasure");
    fireEvent.click(treasureChip);

    // Only the adventures card should remain visible.
    await waitFor(() => {
      expect(screen.getAllByTestId("catalog-card-title").length).toBe(1);
    });
    expect(screen.getAllByTestId("catalog-card-title")[0].textContent).toBe(
      "The Lost Treasure",
    );
  });

  it("deselects theme chip on second click, restoring all cards", async () => {
    const { api } = makeApi();
    render(<CatalogPanel filterCategory={undefined} api={api as ApiClient} />);

    await waitFor(() => {
      expect(screen.getAllByTestId("catalog-card-title").length).toBe(3);
    });

    const treasureChip = screen.getByTestId("catalog-chip-treasure");
    // Select.
    fireEvent.click(treasureChip);
    await waitFor(() => {
      expect(screen.getAllByTestId("catalog-card-title").length).toBe(1);
    });
    // Deselect.
    fireEvent.click(treasureChip);
    await waitFor(() => {
      expect(screen.getAllByTestId("catalog-card-title").length).toBe(3);
    });
  });

  it("shows No templates found when catalog is empty", async () => {
    const { api } = makeApi(makeCatalogResponse([]));
    render(<CatalogPanel filterCategory={undefined} api={api as ApiClient} />);

    await waitFor(() => {
      expect(screen.getByTestId("catalog-empty")).toBeDefined();
    });
    expect(screen.getByTestId("catalog-empty").textContent).toBe(
      "No templates found",
    );
  });

  it("filters by category: elements shows only periodic_table-themed entries", async () => {
    const { api } = makeApi();
    render(
      <CatalogPanel filterCategory="elements" api={api as ApiClient} />,
    );

    await waitFor(() => {
      expect(screen.getAllByTestId("catalog-card-title").length).toBeGreaterThan(0);
    });

    const titles = screen
      .getAllByTestId("catalog-card-title")
      .map((el) => el.textContent);
    // Only the elements entry carries periodic_table theme.
    expect(titles).toContain("Gold Explorer");
    expect(titles).not.toContain("The Lost Treasure");
    expect(titles).not.toContain("Sharing Circle");
  });

  it("shows error state when getCatalog rejects", async () => {
    const { api } = makeApi(new Error("Network failure"));
    render(<CatalogPanel filterCategory={undefined} api={api as ApiClient} />);

    await waitFor(() => {
      expect(screen.getByTestId("catalog-error")).toBeDefined();
    });
    expect(screen.getByTestId("catalog-error").textContent).toContain(
      "Network failure",
    );
  });

  it("does not crash on empty catalog response", async () => {
    const { api } = makeApi({ entries: [], total: 0 });
    // Should render without throwing.
    expect(() => {
      render(<CatalogPanel filterCategory={undefined} api={api as ApiClient} />);
    }).not.toThrow();

    await waitFor(() => {
      expect(screen.getByTestId("catalog-empty")).toBeDefined();
    });
  });

  it("does NOT show error toast when api.propose rejects with ApiError status 0 (abort)", async () => {
    // The abort path in TemplateCard.handleLaunch: when propose rejects
    // with status === 0 the catch returns early without setting the error
    // toast. This pins that the "Failed — retry?" toast never appears for
    // aborted requests.
    const { api, propose } = makeApi(makeCatalogResponse([ENTRY_ADVENTURES]));
    propose.mockRejectedValue(new ApiError(0, "aborted"));
    render(<CatalogPanel filterCategory={undefined} api={api as ApiClient} />);

    await waitFor(() => {
      expect(screen.getAllByTestId("catalog-card-launch").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByTestId("catalog-card-launch")[0]);

    // Give the promise rejection time to settle.
    await waitFor(() => {
      expect(propose).toHaveBeenCalledOnce();
    });

    // Error toast must NOT appear.
    expect(screen.queryByTestId("catalog-card-toast-err")).toBeNull();
    // Success toast must NOT appear either (propose rejected).
    expect(screen.queryByTestId("catalog-card-toast-ok")).toBeNull();
  });
});
