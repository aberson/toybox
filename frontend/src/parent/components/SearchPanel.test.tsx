// Phase R Step R4: vitest unit tests for SearchPanel.
//
// Avoids fake timers entirely — instead we expose a ``debounceMs``
// prop (default 300) and set it to 0 in tests so the effect fires on
// the next microtask tick.  ``waitFor`` then handles the async state
// update without fighting the timer infrastructure.
//
// Tests:
//  1. renders the search input
//  2. shows "Searching..." while the fetch is in flight
//  3. shows past activity results when returned
//  4. shows template results when returned
//  5. "Play again" button calls onPropose with the correct template_id
//  6. "Try this" button calls onPropose with the correct template_id
//  7. shows "No results" when both lists are empty
//  8. clears results when input is cleared

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SearchResponse } from "../api";
import { SearchPanel } from "./SearchPanel";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeResponse(
  override: Partial<SearchResponse> = {},
): SearchResponse {
  return {
    past_activities: [],
    templates: [],
    ...override,
  };
}

interface StubApi {
  searchActivities: ReturnType<typeof vi.fn>;
}

function buildApi(resp: SearchResponse | "pending" = makeResponse()): StubApi {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fn = vi.fn<any, any>();
  if (resp === "pending") {
    fn.mockReturnValue(new Promise(() => undefined));
  } else {
    fn.mockResolvedValue(resp);
  }
  return { searchActivities: fn };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests — use debounceMs={0} to bypass the 300ms wait
// ---------------------------------------------------------------------------

describe("SearchPanel", () => {
  it("renders the search input", () => {
    render(
      <SearchPanel
        searchQuery=""
        onSearchQueryChange={vi.fn()}
        api={buildApi() as never}
        onPropose={vi.fn()}
        debounceMs={0}
      />,
    );
    expect(screen.getByTestId("search-input")).toBeTruthy();
  });

  it("shows 'Searching...' while the fetch is in flight", async () => {
    const api = buildApi("pending");
    render(
      <SearchPanel
        searchQuery="pirates"
        onSearchQueryChange={vi.fn()}
        api={api as never}
        onPropose={vi.fn()}
        debounceMs={0}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("search-loading")).toBeTruthy();
    });
  });

  it("shows past activity results when returned", async () => {
    const resp = makeResponse({
      past_activities: [
        {
          id: "act-1",
          title: "Dragon Quest",
          template_id: "tmpl-dragon",
          state: "completed",
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    render(
      <SearchPanel
        searchQuery="dragon"
        onSearchQueryChange={vi.fn()}
        api={buildApi(resp) as never}
        onPropose={vi.fn()}
        debounceMs={0}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("past-activities-section")).toBeTruthy();
    });
    expect(screen.getByText("Dragon Quest")).toBeTruthy();
  });

  it("shows template results when returned", async () => {
    const resp = makeResponse({
      templates: [
        { id: "tmpl-pirate", title: "Pirate Adventure", intent: "request_play" },
      ],
    });
    render(
      <SearchPanel
        searchQuery="pirate"
        onSearchQueryChange={vi.fn()}
        api={buildApi(resp) as never}
        onPropose={vi.fn()}
        debounceMs={0}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("templates-section")).toBeTruthy();
    });
    expect(screen.getByText("Pirate Adventure")).toBeTruthy();
  });

  it("'Play again' button calls onPropose with the correct template_id", async () => {
    const resp = makeResponse({
      past_activities: [
        {
          id: "act-2",
          title: "Wizard School",
          template_id: "tmpl-wizard",
          state: "completed",
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    const onPropose = vi.fn();
    render(
      <SearchPanel
        searchQuery="wizard"
        onSearchQueryChange={vi.fn()}
        api={buildApi(resp) as never}
        onPropose={onPropose}
        debounceMs={0}
      />,
    );
    await waitFor(() => screen.getByTestId("play-again-btn"));
    fireEvent.click(screen.getByTestId("play-again-btn"));
    expect(onPropose).toHaveBeenCalledWith({ template_id: "tmpl-wizard" });
  });

  it("'Try this' button calls onPropose with the correct template_id", async () => {
    const resp = makeResponse({
      templates: [
        { id: "tmpl-jungle", title: "Jungle Explorer", intent: "request_play" },
      ],
    });
    const onPropose = vi.fn();
    render(
      <SearchPanel
        searchQuery="jungle"
        onSearchQueryChange={vi.fn()}
        api={buildApi(resp) as never}
        onPropose={onPropose}
        debounceMs={0}
      />,
    );
    await waitFor(() => screen.getByTestId("try-this-btn"));
    fireEvent.click(screen.getByTestId("try-this-btn"));
    expect(onPropose).toHaveBeenCalledWith({ template_id: "tmpl-jungle" });
  });

  it("shows 'No results' when both lists are empty", async () => {
    render(
      <SearchPanel
        searchQuery="xyzzy-no-match"
        onSearchQueryChange={vi.fn()}
        api={buildApi(makeResponse()) as never}
        onPropose={vi.fn()}
        debounceMs={0}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("search-empty")).toBeTruthy();
    });
  });

  it("clears results when input is cleared", async () => {
    const resp = makeResponse({
      templates: [
        { id: "tmpl-abc", title: "Something", intent: "request_play" },
      ],
    });
    const api = buildApi(resp);
    const { rerender } = render(
      <SearchPanel
        searchQuery="something"
        onSearchQueryChange={vi.fn()}
        api={api as never}
        onPropose={vi.fn()}
        debounceMs={0}
      />,
    );
    await waitFor(() => screen.getByTestId("templates-section"));

    rerender(
      <SearchPanel
        searchQuery=""
        onSearchQueryChange={vi.fn()}
        api={api as never}
        onPropose={vi.fn()}
        debounceMs={0}
      />,
    );
    await waitFor(() => {
      expect(screen.queryByTestId("templates-section")).toBeNull();
    });
    expect(screen.queryByTestId("search-empty")).toBeNull();
    expect(screen.queryByTestId("search-loading")).toBeNull();
  });
});
