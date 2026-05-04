// Component tests for the Step 22 transcript management UI. Spins up a
// stubbed ApiClient (only the methods the manager calls are wired) and
// asserts list / search / delete / wipe-all behaviour.

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import type {
  ApiClient,
  TranscriptListResponse,
  TranscriptRow,
  TranscriptWipeRequest,
  TranscriptWipeResponse,
} from "../api";
import { TranscriptsManager } from "./TranscriptsManager";

function fakeRow(overrides: Partial<TranscriptRow> = {}): TranscriptRow {
  return {
    id: "t-1",
    session_id: "s-1",
    mic_id: null,
    started_at: "2026-01-01T00:00:01Z",
    ended_at: "2026-01-01T00:00:02Z",
    text: "hello world",
    confidence: 0.8,
    language: "en",
    triggered_intent: null,
    ...overrides,
  };
}

interface StubApi {
  listTranscripts: Mock;
  searchTranscripts: Mock;
  deleteTranscript: Mock;
  wipeTranscripts: Mock;
}

function buildStubApi(initial: TranscriptRow[]): StubApi {
  return {
    listTranscripts: vi.fn(
      async (
        _params: { limit?: number; before?: string | null } = {},
      ): Promise<TranscriptListResponse> => ({ items: initial }),
    ) as Mock,
    searchTranscripts: vi.fn(
      async (q: string): Promise<TranscriptListResponse> => ({
        items: initial.filter((r) =>
          (r.text ?? "").toLowerCase().includes(q.toLowerCase()),
        ),
      }),
    ) as Mock,
    deleteTranscript: vi.fn(
      async (_id: string): Promise<{ ok: boolean }> => ({ ok: true }),
    ) as Mock,
    wipeTranscripts: vi.fn(
      async (_body: TranscriptWipeRequest): Promise<TranscriptWipeResponse> =>
        ({ deleted: 0 }),
    ) as Mock,
  };
}

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  // Per-row delete uses ``window.confirm`` (mirrors ChildProfileEditor).
  // Default to "yes" for the delete tests; the cancel case overrides
  // this in-test.
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("TranscriptsManager", () => {
  it("renders the list from the api on mount", async () => {
    const stub = buildStubApi([
      fakeRow({ id: "t-1", text: "first" }),
      fakeRow({ id: "t-2", text: "second" }),
    ]);
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listTranscripts).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getAllByTestId("transcript-row")).toHaveLength(2);
    });
    expect(screen.getByText("first")).toBeTruthy();
    expect(screen.getByText("second")).toBeTruthy();
  });

  it("debounces the search field and calls searchTranscripts", async () => {
    vi.useFakeTimers();
    const stub = buildStubApi([
      fakeRow({ id: "t-1", text: "hello world" }),
      fakeRow({ id: "t-2", text: "goodbye" }),
    ]);
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    // Initial mount fires listTranscripts.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(stub.listTranscripts).toHaveBeenCalledTimes(1);

    const input = screen.getByTestId(
      "transcripts-search-input",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "hel" } });
    fireEvent.change(input, { target: { value: "hell" } });
    fireEvent.change(input, { target: { value: "hello" } });
    // No call until debounce elapses.
    expect(stub.searchTranscripts).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(260);
    });
    expect(stub.searchTranscripts).toHaveBeenCalledTimes(1);
    expect(stub.searchTranscripts.mock.calls[0]?.[0]).toBe("hello");
  });

  it("clicks delete and calls api.deleteTranscript with the row id", async () => {
    const stub = buildStubApi([fakeRow({ id: "t-1", text: "first" })]);
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByText("first")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("delete-transcript-button"));
    await waitFor(() => {
      expect(stub.deleteTranscript).toHaveBeenCalledWith(
        "t-1",
        expect.anything(),
      );
    });
    // Optimistic remove — row gone before refetch.
    await waitFor(() => {
      expect(screen.queryByText("first")).toBeNull();
    });
  });

  it("surfaces an 'already deleted' notice when delete returns 404", async () => {
    const stub = buildStubApi([fakeRow({ id: "t-1", text: "first" })]);
    stub.deleteTranscript.mockRejectedValueOnce(
      new ApiError(404, {
        detail: { code: "transcript_not_found", id: "t-1" },
      }),
    );
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByText("first")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("delete-transcript-button"));
    await waitFor(() => {
      expect(screen.getByTestId("transcripts-row-notice")).toBeTruthy();
    });
    expect(
      screen.getByTestId("transcripts-row-notice").textContent,
    ).toContain("already deleted");
    // Row stays removed (optimistic).
    expect(screen.queryByText("first")).toBeNull();
  });

  it("opens the wipe-all modal with a PIN field", async () => {
    const stub = buildStubApi([fakeRow({ id: "t-1" })]);
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByTestId("transcripts-list")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("transcripts-wipe-button"));
    expect(screen.getByTestId("transcripts-wipe-modal")).toBeTruthy();
    expect(screen.getByTestId("transcripts-wipe-pin-input")).toBeTruthy();
  });

  it("submits the wipe modal with a wrong PIN and shows attempts remaining", async () => {
    const stub = buildStubApi([fakeRow({ id: "t-1" })]);
    stub.wipeTranscripts.mockRejectedValueOnce(
      new ApiError(401, {
        detail: { code: "pin_invalid", attempts_remaining: 3 },
      }),
    );
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByTestId("transcripts-list")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("transcripts-wipe-button"));
    const pinInput = screen.getByTestId(
      "transcripts-wipe-pin-input",
    ) as HTMLInputElement;
    fireEvent.change(pinInput, { target: { value: "9999" } });
    fireEvent.click(screen.getByTestId("transcripts-wipe-confirm"));
    await waitFor(() => {
      expect(screen.getByTestId("transcripts-wipe-error")).toBeTruthy();
    });
    expect(
      screen.getByTestId("transcripts-wipe-error").textContent,
    ).toContain("3 attempts remaining");
  });

  it("shows the locked countdown when wipe returns 423", async () => {
    const stub = buildStubApi([fakeRow({ id: "t-1" })]);
    stub.wipeTranscripts.mockRejectedValueOnce(
      new ApiError(423, {
        detail: { code: "pin_locked", seconds_until_unlock: 65 },
      }),
    );
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByTestId("transcripts-list")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("transcripts-wipe-button"));
    fireEvent.change(
      screen.getByTestId("transcripts-wipe-pin-input") as HTMLInputElement,
      { target: { value: "1234" } },
    );
    fireEvent.click(screen.getByTestId("transcripts-wipe-confirm"));
    await waitFor(() => {
      expect(screen.getByTestId("transcripts-wipe-countdown")).toBeTruthy();
    });
    expect(
      screen.getByTestId("transcripts-wipe-countdown").textContent,
    ).toContain("1:05");
    // Submit is disabled while locked.
    const confirm = screen.getByTestId(
      "transcripts-wipe-confirm",
    ) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
  });

  it("on successful wipe shows count and clears the list", async () => {
    const stub = buildStubApi([fakeRow({ id: "t-1" }), fakeRow({ id: "t-2" })]);
    stub.wipeTranscripts.mockResolvedValueOnce({ deleted: 2 });
    // After wipe the next list call returns empty.
    stub.listTranscripts.mockImplementation(
      async (): Promise<TranscriptListResponse> => ({ items: [] }),
    );
    // Re-seed initial response so the first mount load still returns
    // both rows. ``listTranscripts`` is shared, so mockImplementationOnce
    // covers the initial mount; the .mockImplementation above takes over
    // for the post-wipe refetch.
    stub.listTranscripts.mockImplementationOnce(
      async (): Promise<TranscriptListResponse> => ({
        items: [fakeRow({ id: "t-1" }), fakeRow({ id: "t-2" })],
      }),
    );
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("transcript-row")).toHaveLength(2);
    });
    fireEvent.click(screen.getByTestId("transcripts-wipe-button"));
    fireEvent.change(
      screen.getByTestId("transcripts-wipe-pin-input") as HTMLInputElement,
      { target: { value: "1357" } },
    );
    fireEvent.click(screen.getByTestId("transcripts-wipe-confirm"));
    await waitFor(() => {
      expect(stub.wipeTranscripts).toHaveBeenCalledWith(
        { pin: "1357" },
        expect.anything(),
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("transcripts-wipe-success")).toBeTruthy();
    });
    expect(
      screen.getByTestId("transcripts-wipe-success").textContent,
    ).toContain("Deleted 2 transcripts");
    await waitFor(() => {
      expect(screen.queryByTestId("transcript-row")).toBeNull();
    });
  });

  it("aborts the in-flight delete signal when the manager unmounts mid-flight", async () => {
    const stub = buildStubApi([fakeRow({ id: "t-1" })]);
    let capturedSignal: AbortSignal | undefined;
    stub.deleteTranscript.mockImplementationOnce(
      async (
        _id: string,
        opts: { signal?: AbortSignal } = {},
      ): Promise<{ ok: boolean }> => {
        capturedSignal = opts.signal;
        return new Promise<{ ok: boolean }>((_resolve, reject) => {
          opts.signal?.addEventListener("abort", () => {
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        });
      },
    );
    const consoleErrorSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    const { unmount } = render(
      <TranscriptsManager api={stub as unknown as ApiClient} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("transcripts-list")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("delete-transcript-button"));
    await waitFor(() => {
      expect(stub.deleteTranscript).toHaveBeenCalled();
    });
    expect(capturedSignal).toBeDefined();
    expect(capturedSignal?.aborted).toBe(false);
    await act(async () => {
      unmount();
    });
    expect(capturedSignal?.aborted).toBe(true);
    const setStateWarnings = consoleErrorSpy.mock.calls.filter((args) =>
      args.some(
        (arg) => typeof arg === "string" && arg.includes("unmounted"),
      ),
    );
    expect(setStateWarnings).toHaveLength(0);
  });

  it("skips delete when the confirm dialog is dismissed", async () => {
    // Override the default "yes" for this case — operator hits Cancel.
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const stub = buildStubApi([fakeRow({ id: "t-1", text: "first" })]);
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByText("first")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("delete-transcript-button"));
    // Confirm prompt fired but rejected — no API call, row still there.
    expect(stub.deleteTranscript).not.toHaveBeenCalled();
    expect(screen.getByText("first")).toBeTruthy();
  });

  it("clears the PIN field when the wipe modal Cancel is clicked", async () => {
    const stub = buildStubApi([fakeRow({ id: "t-1" })]);
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByTestId("transcripts-list")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("transcripts-wipe-button"));
    const pinInput = screen.getByTestId(
      "transcripts-wipe-pin-input",
    ) as HTMLInputElement;
    fireEvent.change(pinInput, { target: { value: "1357" } });
    expect(pinInput.value).toBe("1357");
    fireEvent.click(screen.getByTestId("transcripts-wipe-cancel"));
    // Modal closes; wipe API NEVER called.
    expect(screen.queryByTestId("transcripts-wipe-modal")).toBeNull();
    expect(stub.wipeTranscripts).not.toHaveBeenCalled();
    // Re-open and assert the PIN field is empty (not stuck on 1357).
    fireEvent.click(screen.getByTestId("transcripts-wipe-button"));
    const pinAgain = screen.getByTestId(
      "transcripts-wipe-pin-input",
    ) as HTMLInputElement;
    expect(pinAgain.value).toBe("");
  });

  it("falls back to listTranscripts when the search field is cleared", async () => {
    vi.useFakeTimers();
    const stub = buildStubApi([
      fakeRow({ id: "t-1", text: "hello world" }),
      fakeRow({ id: "t-2", text: "goodbye" }),
    ]);
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    // Initial mount fires listTranscripts.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(stub.listTranscripts).toHaveBeenCalledTimes(1);

    const input = screen.getByTestId(
      "transcripts-search-input",
    ) as HTMLInputElement;
    // Type "hello" → debounce → searchTranscripts.
    fireEvent.change(input, { target: { value: "hello" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(260);
    });
    expect(stub.searchTranscripts).toHaveBeenCalledTimes(1);
    expect(stub.searchTranscripts.mock.calls[0]?.[0]).toBe("hello");
    const listCallsAfterSearch = stub.listTranscripts.mock.calls.length;

    // Clear the search → debounce → listTranscripts (NOT searchTranscripts("")).
    fireEvent.change(input, { target: { value: "" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(260);
    });
    expect(stub.searchTranscripts).toHaveBeenCalledTimes(1);
    expect(stub.listTranscripts.mock.calls.length).toBeGreaterThan(
      listCallsAfterSearch,
    );
  });

  it("'Load more' calls listTranscripts with the oldest ended_at as cursor", async () => {
    const fullPage: TranscriptRow[] = Array.from({ length: 50 }, (_, i) =>
      fakeRow({
        id: `t-${i}`,
        text: `row ${i}`,
        ended_at: `2026-01-01T00:${String(50 - i).padStart(2, "0")}:00Z`,
        started_at: `2026-01-01T00:${String(50 - i).padStart(2, "0")}:00Z`,
      }),
    );
    const stub = buildStubApi(fullPage);
    // Second call (for "Load more") returns one extra row.
    stub.listTranscripts.mockImplementationOnce(
      async (): Promise<TranscriptListResponse> => ({ items: fullPage }),
    );
    stub.listTranscripts.mockImplementationOnce(
      async (
        params: { limit?: number; before?: string | null } = {},
      ): Promise<TranscriptListResponse> => ({
        items:
          params.before !== undefined && params.before !== null
            ? [fakeRow({ id: "t-50", text: "older" })]
            : [],
      }),
    );
    render(<TranscriptsManager api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("transcript-row")).toHaveLength(50);
    });
    const loadMore = screen.getByTestId("transcripts-load-more");
    fireEvent.click(loadMore);
    await waitFor(() => {
      expect(stub.listTranscripts).toHaveBeenCalledTimes(2);
    });
    const secondCall = stub.listTranscripts.mock.calls[1]?.[0] as {
      before?: string | null;
    };
    // Cursor must be the LAST (oldest) row's ended_at — the page is
    // ordered most-recent first, so item[49] is the oldest.
    expect(secondCall.before).toBe(fullPage[49]?.ended_at);
    await waitFor(() => {
      expect(screen.getAllByTestId("transcript-row")).toHaveLength(51);
    });
  });
});
