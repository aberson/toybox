import { describe, expect, it, vi } from "vitest";

import {
  ApiClient,
  ApiError,
  isAbortError,
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
    state: "proposed",
    version: 3,
    title: "Title",
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

describe("ApiClient", () => {
  it("attaches the parent token via X-Toybox-Token header", async () => {
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, fakeActivity()));
    const client = new ApiClient({
      fetchImpl,
      getToken: () => "tok-xyz",
    });
    await client.getActivity("act-1");
    const init = fetchImpl.mock.calls[0]?.[1];
    expect(init).toBeDefined();
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Toybox-Token")).toBe("tok-xyz");
  });

  it("when getToken returns null, omits X-Toybox-Token (mutating call still attempts)", async () => {
    // C5: documented behavior is silent omission at the client. The
    // backend will then 401 the request; that surfaces as ApiError 401
    // and the store gets a toast. We assert (a) header is absent and
    // (b) the call is still issued (no client-side noop), so the test
    // suite would catch a refactor that swallowed unauthed calls.
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, fakeActivity()));
    const client = new ApiClient({ fetchImpl, getToken: () => null });
    await client.getActivity("act-1");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const headers = new Headers(fetchImpl.mock.calls[0]?.[1]?.headers);
    expect(headers.has("X-Toybox-Token")).toBe(false);
  });

  it("approve POSTs to /api/activities/<id>/approve with child_ids body and If-Match-Version", async () => {
    // C4: strengthen approve coverage — URL ends with /approve, method
    // is POST, body is {child_ids:...}, If-Match-Version matches.
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, fakeActivity({ version: 4 })));
    const client = new ApiClient({ fetchImpl, getToken: () => "t" });
    await client.approve("act-1", 3, ["child-7", "child-8"]);
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/activities/act-1/approve");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({
      child_ids: ["child-7", "child-8"],
    });
    const headers = new Headers(init?.headers);
    expect(headers.get("If-Match-Version")).toBe("3");
  });

  it("approve with no child_ids serializes child_ids: null", async () => {
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, fakeActivity()));
    const client = new ApiClient({ fetchImpl, getToken: () => "t" });
    await client.approve("act-1", 3);
    const init = fetchImpl.mock.calls[0]?.[1];
    expect(JSON.parse(init?.body as string)).toEqual({ child_ids: null });
  });

  it("thumbsUp POSTs to /api/activities/<id>/thumbs-up with no body or version", async () => {
    // Step 15: thumbs-up writes parent_signal=+1 to labeled_events.
    // No If-Match-Version (no state transition); no JSON body required.
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, fakeActivity()));
    const client = new ApiClient({ fetchImpl, getToken: () => "t" });
    await client.thumbsUp("act-1");
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/activities/act-1/thumbs-up");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
    const headers = new Headers(init?.headers);
    expect(headers.get("If-Match-Version")).toBeNull();
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
    await expect(client.approve("act-1", 1)).rejects.toBeInstanceOf(
      VersionConflictError,
    );
  });

  it("non-conflict errors raise ApiError with status + body", async () => {
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(500, { detail: { code: "boom" } }));
    const client = new ApiClient({ fetchImpl, getToken: () => "t" });
    await expect(client.getActivity("act-1")).rejects.toBeInstanceOf(ApiError);
  });

  it("threads the AbortSignal through to fetch and rejects with AbortError on abort", async () => {
    // A4: confirms callers can bind a request to a component lifecycle.
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

describe("withConflictHandler", () => {
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
