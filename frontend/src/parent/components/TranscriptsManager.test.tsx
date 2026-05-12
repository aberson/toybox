// Component tests for the Phase I step I4 transcript management UI.
// Spins up a stubbed ApiClient (only the methods the manager calls are
// wired) and asserts list / fade / wipe-all behaviour.
//
// Phase I step I4 replaced the per-row delete affordance with a local
// 1s fade-out tick driven by ``retentionSeconds``. The delete-path
// tests have been removed; fade-machinery tests cover (a) row fades
// out after retention, (b) in-flight rows don't fade, (c) shortening
// retention fades older rows, (d) malformed ``ended_at`` is skipped
// with a once-per-id console.warn, (e) wipe-all still works.

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

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
  wipeTranscripts: Mock;
}

function buildStubApi(initial: TranscriptRow[]): StubApi {
  return {
    listTranscripts: vi.fn(
      async (
        _params: { limit?: number; before?: string | null } = {},
      ): Promise<TranscriptListResponse> => ({ items: initial }),
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

describe("TranscriptsManager", () => {
  it("renders the list from the api on mount", async () => {
    const stub = buildStubApi([
      fakeRow({ id: "t-1", text: "first" }),
      fakeRow({ id: "t-2", text: "second" }),
    ]);
    render(<TranscriptsManager api={stub as unknown as ApiClient} retentionSeconds={60} />);
    await waitFor(() => {
      expect(stub.listTranscripts).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getAllByTestId("transcript-row")).toHaveLength(2);
    });
    expect(screen.getByText("first")).toBeTruthy();
    expect(screen.getByText("second")).toBeTruthy();
  });

  it("opens the wipe-all modal with a PIN field", async () => {
    const stub = buildStubApi([fakeRow({ id: "t-1" })]);
    render(<TranscriptsManager api={stub as unknown as ApiClient} retentionSeconds={60} />);
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
    render(<TranscriptsManager api={stub as unknown as ApiClient} retentionSeconds={60} />);
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
    render(<TranscriptsManager api={stub as unknown as ApiClient} retentionSeconds={60} />);
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
    render(<TranscriptsManager api={stub as unknown as ApiClient} retentionSeconds={60} />);
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

  it("clears the PIN field when the wipe modal Cancel is clicked", async () => {
    const stub = buildStubApi([fakeRow({ id: "t-1" })]);
    render(<TranscriptsManager api={stub as unknown as ApiClient} retentionSeconds={60} />);
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
    render(<TranscriptsManager api={stub as unknown as ApiClient} retentionSeconds={60} />);
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

  // --- Phase I step I4 fade machinery ---

  it("fade: row fades out and is removed after retention elapses", async () => {
    // Pin wall-clock so the row's ``ended_at`` lines up with the fake
    // timer's notion of "now". The row's ``ended_at`` is 61 seconds
    // before ``baseNow``; with retentionSeconds=60, the 1s tick should
    // flag it as expired immediately.
    const baseNow = new Date("2026-05-11T12:00:00Z").getTime();
    vi.useFakeTimers();
    vi.setSystemTime(baseNow);
    const expiredEndedAt = new Date(baseNow - 61_000).toISOString();
    const stub = buildStubApi([
      fakeRow({
        id: "t-old",
        text: "stale row",
        started_at: new Date(baseNow - 65_000).toISOString(),
        ended_at: expiredEndedAt,
      }),
    ]);
    render(
      <TranscriptsManager
        api={stub as unknown as ApiClient}
        retentionSeconds={60}
      />,
    );
    // Flush the initial mount fetch (microtask + 0ms timers).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getAllByTestId("transcript-row")).toHaveLength(1);

    // Advance 1s — the tick should fire, flag the row as fading, and
    // queue the 600ms removal. The row should still be in the DOM with
    // ``opacity: 0`` and ``data-fading="true"``.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    const fadingRow = screen.getByTestId("transcript-row");
    expect(fadingRow.getAttribute("data-fading")).toBe("true");
    expect((fadingRow as HTMLElement).style.opacity).toBe("0");

    // Advance the 600ms transition window — the row should be removed
    // from the DOM entirely.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(screen.queryByTestId("transcript-row")).toBeNull();
  });

  it("fade: in-flight row (ended_at=null) does not fade", async () => {
    const baseNow = new Date("2026-05-11T12:00:00Z").getTime();
    vi.useFakeTimers();
    vi.setSystemTime(baseNow);
    const stub = buildStubApi([
      fakeRow({
        id: "t-live",
        text: "still talking",
        started_at: new Date(baseNow - 300_000).toISOString(),
        ended_at: null,
      }),
    ]);
    render(
      <TranscriptsManager
        api={stub as unknown as ApiClient}
        retentionSeconds={60}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getAllByTestId("transcript-row")).toHaveLength(1);

    // Advance well past any reasonable expiry — the in-flight row
    // should remain because the tick skips ``ended_at === null``.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    const row = screen.getByTestId("transcript-row");
    expect(row.getAttribute("data-fading")).toBe("false");
    expect((row as HTMLElement).style.opacity).not.toBe("0");
  });

  it("fade: shortening retentionSeconds fades older rows on next tick", async () => {
    const baseNow = new Date("2026-05-11T12:00:00Z").getTime();
    vi.useFakeTimers();
    vi.setSystemTime(baseNow);
    // 5-minute-old row — safe under 15m retention, expired under 1m.
    const fiveMinAgo = new Date(baseNow - 5 * 60_000).toISOString();
    const stub = buildStubApi([
      fakeRow({
        id: "t-medium",
        text: "five minutes old",
        started_at: fiveMinAgo,
        ended_at: fiveMinAgo,
      }),
    ]);
    const { rerender } = render(
      <TranscriptsManager
        api={stub as unknown as ApiClient}
        retentionSeconds={900}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // Tick a couple of times under 15m retention — row should stay
    // non-fading.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByTestId("transcript-row").getAttribute("data-fading")).toBe(
      "false",
    );

    // Drop retention to 60s; the next tick should flag the 5-min row.
    rerender(
      <TranscriptsManager
        api={stub as unknown as ApiClient}
        retentionSeconds={60}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    const row = screen.getByTestId("transcript-row");
    expect(row.getAttribute("data-fading")).toBe("true");
    expect((row as HTMLElement).style.opacity).toBe("0");
  });

  it("fade: malformed ended_at is skipped with a once-per-id console.warn", async () => {
    const baseNow = new Date("2026-05-11T12:00:00Z").getTime();
    vi.useFakeTimers();
    vi.setSystemTime(baseNow);
    const warnSpy = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);
    const stub = buildStubApi([
      fakeRow({
        id: "t-bogus",
        text: "bogus row",
        started_at: new Date(baseNow - 10_000).toISOString(),
        ended_at: "not a date",
      }),
    ]);
    render(
      <TranscriptsManager
        api={stub as unknown as ApiClient}
        retentionSeconds={60}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // Advance enough to fire two ticks. The row should remain in the
    // DOM, console.warn called exactly once for ``t-bogus`` (the second
    // tick is gated by ``warnedIdsRef``).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByTestId("transcript-row")).toBeTruthy();
    // The "once-per-id" contract is what matters — assert on the call
    // count directly rather than coupling to the exact message wording
    // or argument layout. After two ticks the warn must have fired
    // exactly once even though the row stays in the DOM.
    expect(warnSpy.mock.calls).toHaveLength(1);

    // Two more ticks — still no additional warn.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(warnSpy.mock.calls).toHaveLength(1);
    expect(screen.getByTestId("transcript-row")).toBeTruthy();
  });
});
