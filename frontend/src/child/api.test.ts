import { describe, expect, it, vi } from "vitest";

import {
  ApiClient,
  ApiError,
  isAbortError,
  isTransientError,
  retryWithBackoff,
  VersionConflictError,
  withConflictHandler,
} from "./api";
import type { Activity, FetchLike, VersionConflictBody } from "./api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function fakeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    id: "act-1",
    state: "running",
    version: 3,
    title: "Pretend you're cats",
    summary: null,
    persona_id: null,
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-05-02T10:00:00Z",
    started_at: null,
    ended_at: null,
    steps: [],
    metadata: {},
    ...overrides,
  };
}

describe("child ApiClient", () => {
  it("attaches the parent token via X-Toybox-Token", async () => {
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, fakeActivity()));
    const client = new ApiClient({ fetchImpl, getToken: () => "tok-xyz" });
    await client.getActivity("act-1");
    const init = fetchImpl.mock.calls[0]?.[1];
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Toybox-Token")).toBe("tok-xyz");
  });

  it("omits X-Toybox-Token when getToken returns null but still issues the request", async () => {
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, fakeActivity()));
    const client = new ApiClient({ fetchImpl, getToken: () => null });
    await client.getActivity("act-1");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const headers = new Headers(fetchImpl.mock.calls[0]?.[1]?.headers);
    expect(headers.has("X-Toybox-Token")).toBe(false);
  });

  it("advance POSTs to /api/activities/<id>/advance with If-Match-Version", async () => {
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, fakeActivity({ version: 4 })));
    const client = new ApiClient({ fetchImpl, getToken: () => "t" });
    await client.advance("act-1", 3);
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/activities/act-1/advance");
    expect(init?.method).toBe("POST");
    const headers = new Headers(init?.headers);
    expect(headers.get("If-Match-Version")).toBe("3");
  });

  it("issueParentToken POSTs to /api/auth/parent with PIN body", async () => {
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(
        jsonResponse(200, {
          token: "tok-abc",
          expires_at: 9999,
          subject: { kind: "parent" },
        }),
      );
    const client = new ApiClient({ fetchImpl, getToken: () => null });
    const resp = await client.issueParentToken({ pin: "1357" });
    expect(resp.token).toBe("tok-abc");
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/auth/parent");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ pin: "1357" });
  });

  it("turns 409 with version_conflict body into VersionConflictError", async () => {
    const conflict: VersionConflictBody = {
      code: "version_conflict",
      current_version: 4,
      current_state: "running",
    };
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(409, { detail: conflict }));
    const client = new ApiClient({ fetchImpl, getToken: () => "t" });
    await expect(client.advance("act-1", 1)).rejects.toBeInstanceOf(
      VersionConflictError,
    );
  });

  it("non-conflict errors raise ApiError with status + body", async () => {
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(500, { detail: { code: "boom" } }));
    const client = new ApiClient({ fetchImpl, getToken: () => "t" });
    await expect(client.advance("act-1", 1)).rejects.toBeInstanceOf(ApiError);
  });

  it("threads AbortSignal through to fetch and rejects on abort", async () => {
    const aborter = new AbortController();
    let observedSignal: AbortSignal | undefined;
    const fetchImpl: FetchLike = (_input, init) => {
      observedSignal = init?.signal ?? undefined;
      return new Promise((_resolve, reject) => {
        const sig = init?.signal;
        if (sig) {
          sig.addEventListener("abort", () => {
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        }
      });
    };
    const client = new ApiClient({ fetchImpl, getToken: () => "t" });
    const pending = client.getActivity("act-1", { signal: aborter.signal });
    aborter.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(observedSignal).toBe(aborter.signal);
  });

  it("isAbortError recognizes AbortError shape", () => {
    const e = new Error("x");
    e.name = "AbortError";
    expect(isAbortError(e)).toBe(true);
    expect(isAbortError(new Error("nope"))).toBe(false);
    expect(isAbortError(null)).toBe(false);
    expect(isAbortError("string")).toBe(false);
  });
});

describe("child withConflictHandler", () => {
  it("returns the mutation result on success", async () => {
    const onConflict = vi.fn();
    const refetch = vi.fn().mockResolvedValue(null);
    const result = await withConflictHandler({
      mutation: async () => "ok" as const,
      refetch,
      onConflict,
    });
    expect(result).toBe("ok");
    expect(onConflict).not.toHaveBeenCalled();
    expect(refetch).not.toHaveBeenCalled();
  });

  it("on 409 refetches, invokes onConflict, returns null without retry", async () => {
    const conflict: VersionConflictBody = {
      code: "version_conflict",
      current_version: 9,
      current_state: "ended",
    };
    const fresh = fakeActivity({ version: 9, state: "ended" });
    const mutation = vi
      .fn()
      .mockRejectedValue(new VersionConflictError(conflict));
    const refetch = vi.fn().mockResolvedValue(fresh);
    const onConflict = vi.fn();
    const result = await withConflictHandler({
      mutation,
      refetch,
      onConflict,
    });
    expect(result).toBeNull();
    expect(mutation).toHaveBeenCalledTimes(1);
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(onConflict).toHaveBeenCalledTimes(1);
    expect(onConflict).toHaveBeenCalledWith(conflict, fresh);
  });

  it("on refetch failure still invokes onConflict with null fresh", async () => {
    const conflict: VersionConflictBody = {
      code: "version_conflict",
      current_version: 1,
      current_state: "dismissed",
    };
    const mutation = vi
      .fn()
      .mockRejectedValue(new VersionConflictError(conflict));
    const refetch = vi.fn().mockRejectedValue(new Error("network down"));
    const onConflict = vi.fn();
    const result = await withConflictHandler({ mutation, refetch, onConflict });
    expect(result).toBeNull();
    expect(onConflict).toHaveBeenCalledWith(conflict, null);
  });

  it("non-conflict mutation errors propagate", async () => {
    const mutation = vi.fn().mockRejectedValue(new Error("boom"));
    const refetch = vi.fn();
    const onConflict = vi.fn();
    await expect(
      withConflictHandler({ mutation, refetch, onConflict }),
    ).rejects.toThrow("boom");
    expect(onConflict).not.toHaveBeenCalled();
    expect(refetch).not.toHaveBeenCalled();
  });
});

describe("child retryWithBackoff", () => {
  it("isTransientError treats 5xx + network errors as retryable, 4xx + abort as not", () => {
    expect(isTransientError(new ApiError(500, null))).toBe(true);
    expect(isTransientError(new ApiError(503, null))).toBe(true);
    expect(isTransientError(new ApiError(599, null))).toBe(true);
    expect(isTransientError(new ApiError(400, null))).toBe(false);
    expect(isTransientError(new ApiError(401, null))).toBe(false);
    expect(isTransientError(new ApiError(404, null))).toBe(false);
    // Native fetch network failures throw TypeError. We treat any
    // non-Api Error as a network blip during bootstrap.
    expect(isTransientError(new TypeError("failed to fetch"))).toBe(true);
    // AbortError must NOT be retried — the caller asked us to stop.
    const abort = new Error("aborted");
    abort.name = "AbortError";
    expect(isTransientError(abort)).toBe(false);
    expect(isTransientError(null)).toBe(false);
    expect(isTransientError(undefined)).toBe(false);
  });

  it("retries 5xx with backoff, eventually succeeds", async () => {
    // Regression: iter-1 bootstrap fired ``api.issueParentToken`` once
    // and toasted the kiosk into a stuck idle state on the SQLite
    // cross-thread 500. Retry must catch that.
    let attempts = 0;
    const op = vi.fn().mockImplementation(() => {
      attempts += 1;
      if (attempts < 3) {
        return Promise.reject(new ApiError(500, null));
      }
      return Promise.resolve("ok");
    });
    const sleep = vi.fn().mockResolvedValue(undefined);
    const result = await retryWithBackoff(op, {
      attempts: 3,
      sleep,
      jitter: () => 0,
    });
    expect(result).toBe("ok");
    expect(op).toHaveBeenCalledTimes(3);
    // Two backoff naps between three attempts.
    expect(sleep).toHaveBeenCalledTimes(2);
  });

  it("does NOT retry 4xx; throws immediately", async () => {
    const err = new ApiError(401, { code: "auth_required" });
    const op = vi.fn().mockRejectedValue(err);
    const sleep = vi.fn();
    await expect(
      retryWithBackoff(op, { attempts: 3, sleep, jitter: () => 0 }),
    ).rejects.toBe(err);
    expect(op).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("does NOT retry AbortError; threads it through", async () => {
    const abort = new Error("aborted");
    abort.name = "AbortError";
    const op = vi.fn().mockRejectedValue(abort);
    const sleep = vi.fn();
    await expect(
      retryWithBackoff(op, { attempts: 5, sleep, jitter: () => 0 }),
    ).rejects.toBe(abort);
    expect(op).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("after max attempts throws the last 5xx", async () => {
    const err = new ApiError(503, null);
    const op = vi.fn().mockRejectedValue(err);
    await expect(
      retryWithBackoff(op, {
        attempts: 3,
        sleep: () => Promise.resolve(),
        jitter: () => 0,
      }),
    ).rejects.toBe(err);
    expect(op).toHaveBeenCalledTimes(3);
  });

  it("uses exponential backoff capped at maxDelayMs (jittered range)", async () => {
    const sleep = vi.fn().mockResolvedValue(undefined);
    const op = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(500, null))
      .mockRejectedValueOnce(new ApiError(500, null))
      .mockResolvedValue("ok");
    await retryWithBackoff(op, {
      attempts: 3,
      baseDelayMs: 1_000,
      maxDelayMs: 8_000,
      sleep,
      jitter: () => 0, // lower bound
    });
    // Two sleeps. With jitter=0, multiplier is 0.5. So:
    //   first  = min(1000*1, 8000) * 0.5 = 500
    //   second = min(1000*2, 8000) * 0.5 = 1000
    expect(sleep).toHaveBeenNthCalledWith(1, 500);
    expect(sleep).toHaveBeenNthCalledWith(2, 1_000);
  });
});
