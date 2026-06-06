import { describe, expect, it, vi } from "vitest";

import {
  ApiClient,
  ApiError,
  isAbortError,
  VersionConflictError,
  withConflictHandler,
} from "./api";
import type {
  Activity,
  FetchLike,
  PlayTargetDepth,
  VersionConflictBody,
} from "./api";

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
    trigger_phrase: null,
    persona_reasoning: null,
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

  it("approve forwards rewardType into the request body when provided (L9)", async () => {
    // L9: parent's reward-type selection from the SuggestionCard
    // dropdown rides on the approve body's ``reward_type`` field.
    // L4 backend accepts the four-value union; absent (or null
    // implicitly via omit) resolves to "random" server-side. The
    // wire shape this test pins matches the L4 ApproveRequest.
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, fakeActivity()));
    const client = new ApiClient({ fetchImpl, getToken: () => "t" });
    await client.approve("act-1", 3, undefined, "picture");
    const init = fetchImpl.mock.calls[0]?.[1];
    expect(JSON.parse(init?.body as string)).toEqual({
      child_ids: null,
      reward_type: "picture",
    });
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

  it("recastActivity POSTs to /api/activities/<id>/recast with empty body and If-Match-Version", async () => {
    // Phase K K6: recast re-rolls role cast on a proposed activity.
    // Wire shape: POST, empty JSON body (server picks a fresh seed),
    // If-Match-Version header set from the version argument.
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, fakeActivity({ version: 4 })));
    const client = new ApiClient({ fetchImpl, getToken: () => "t" });
    await client.recastActivity("act-1", 3);
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/activities/act-1/recast");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({});
    const headers = new Headers(init?.headers);
    expect(headers.get("If-Match-Version")).toBe("3");
  });

  it("recastActivity turns 409 version_conflict body into VersionConflictError", async () => {
    // Phase K K6: matches the K7 call-site pattern (withConflictHandler
    // unwraps VersionConflictError into the refetch + onConflict path).
    const conflict: VersionConflictBody = {
      code: "version_conflict",
      current_version: 4,
      current_state: "proposed",
    };
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(409, { detail: conflict }));
    const client = new ApiClient({ fetchImpl, getToken: () => "t" });
    await expect(client.recastActivity("act-1", 1)).rejects.toBeInstanceOf(
      VersionConflictError,
    );
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

// =====================================================================
// Phase J6: play-queue API additions.
//
// New methods that match the J5 (proposed list) + J1 (settings) wire
// shapes shipped on the backend.
// =====================================================================

describe("ApiClient — Phase J6 play-queue additions", () => {
  describe("listProposedActivities", () => {
    it("calls GET /api/activities/proposed and returns {items, active: null}", async () => {
      // Default branch (no include_active): backend returns
      // ``{items: [...]}`` and the typed response carries
      // ``active: null`` to keep the wire shape uniform.
      const item = fakeActivity({
        id: "p1",
        state: "proposed",
        version: 1,
      });
      const fetchImpl = vi
        .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
        .mockResolvedValue(jsonResponse(200, { items: [item], active: null }));
      const client = new ApiClient({ fetchImpl, getToken: () => "t" });
      const result = await client.listProposedActivities();
      const [url, init] = fetchImpl.mock.calls[0]!;
      expect(url).toBe("/api/activities/proposed");
      expect(init?.method).toBe("GET");
      expect(result.items).toHaveLength(1);
      expect(result.items[0]?.id).toBe("p1");
      expect(result.active).toBeNull();
    });

    it("calls GET /api/activities/proposed?include_active=true and returns active", async () => {
      // include_active=true branch: backend adds the currently-
      // playing card to the same round-trip so App.tsx can paint
      // queue + active in one mount-time fetch.
      const item = fakeActivity({ id: "p1", state: "proposed", version: 1 });
      const active = fakeActivity({ id: "a1", state: "running", version: 3 });
      const fetchImpl = vi
        .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
        .mockResolvedValue(
          jsonResponse(200, { items: [item], active }),
        );
      const client = new ApiClient({ fetchImpl, getToken: () => "t" });
      const result = await client.listProposedActivities({
        include_active: true,
      });
      const [url] = fetchImpl.mock.calls[0]!;
      expect(url).toBe("/api/activities/proposed?include_active=true");
      expect(result.items).toHaveLength(1);
      expect(result.active?.id).toBe("a1");
      expect(result.active?.state).toBe("running");
    });

    it("does NOT include include_active param when omitted or false", async () => {
      // Defensive: passing ``{include_active: false}`` should not add
      // ``?include_active=false`` to the URL — the backend's bool
      // parser would coerce "false" to True (FastAPI gotcha).
      const fetchImpl = vi
        .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
        .mockResolvedValue(jsonResponse(200, { items: [], active: null }));
      const client = new ApiClient({ fetchImpl, getToken: () => "t" });
      await client.listProposedActivities({ include_active: false });
      const [url] = fetchImpl.mock.calls[0]!;
      expect(url).toBe("/api/activities/proposed");
    });
  });

  describe("getPlayTargetDepth / setPlayTargetDepth", () => {
    it("getPlayTargetDepth calls GET /api/settings/play-target-depth and returns {value}", async () => {
      const fetchImpl = vi
        .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
        .mockResolvedValue(jsonResponse(200, { value: 3 }));
      const client = new ApiClient({ fetchImpl, getToken: () => "t" });
      const result = await client.getPlayTargetDepth();
      const [url, init] = fetchImpl.mock.calls[0]!;
      expect(url).toBe("/api/settings/play-target-depth");
      expect(init?.method).toBe("GET");
      expect(result.value).toBe(3);
    });

    it("setPlayTargetDepth PUTs {value: 5} and returns {value}", async () => {
      const fetchImpl = vi
        .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
        .mockResolvedValue(jsonResponse(200, { value: 5 }));
      const client = new ApiClient({ fetchImpl, getToken: () => "t" });
      const value: PlayTargetDepth = 5;
      const result = await client.setPlayTargetDepth(value);
      const [url, init] = fetchImpl.mock.calls[0]!;
      expect(url).toBe("/api/settings/play-target-depth");
      expect(init?.method).toBe("PUT");
      expect(JSON.parse(init?.body as string)).toEqual({ value: 5 });
      expect(result.value).toBe(5);
    });

    it("setPlayTargetDepth accepts each canonical preset 1/3/5", async () => {
      // Smoke test that the type literal-union accepts each canonical
      // value without TS narrowing complaints. The runtime call shape
      // is identical; we just verify all three round-trip.
      const presets: PlayTargetDepth[] = [1, 3, 5];
      for (const v of presets) {
        const fetchImpl = vi
          .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
          .mockResolvedValue(jsonResponse(200, { value: v }));
        const client = new ApiClient({ fetchImpl, getToken: () => "t" });
        const result = await client.setPlayTargetDepth(v);
        expect(result.value).toBe(v);
      }
    });
  });

});

