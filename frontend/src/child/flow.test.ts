// Integration-style test for the "next step button click → REST call
// → store update" flow. We test the same logic the App.tsx callback
// runs, but without React rendering (the project doesn't ship jsdom +
// react-testing-library yet — the parent UI also relies on Playwright
// for interactive UI evidence). Once those deps land, this file can
// be replaced with a true component test.

import { describe, expect, it, vi } from "vitest";

import { ApiClient, withConflictHandler } from "./api";
import type { Activity, FetchLike, VersionConflictBody } from "./api";
import { createChildStore } from "./store";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function fakeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    id: "act-1",
    state: "approved",
    version: 1,
    title: "Pretend you're cats",
    summary: null,
    persona_id: null,
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-05-02T10:00:00Z",
    started_at: null,
    ended_at: null,
    steps: [
      { seq: 1, body: "Sit", sfx: null, expected_action: null, current: false },
      { seq: 2, body: "Meow", sfx: null, expected_action: null, current: false },
    ],
    metadata: {},
    ...overrides,
  };
}

describe("child kiosk advance flow", () => {
  it("happy path: button click POSTs /advance and updates the store", async () => {
    const store = createChildStore();
    store.setState({
      ...store.getState(),
      activity: fakeActivity({ state: "approved", version: 1 }),
      token: "tok",
    });
    const advanced = fakeActivity({
      state: "running",
      version: 2,
      steps: [
        { seq: 1, body: "Sit", sfx: null, expected_action: null, current: true },
        { seq: 2, body: "Meow", sfx: null, expected_action: null, current: false },
      ],
    });
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, advanced));
    const api = new ApiClient({ fetchImpl, getToken: () => store.getState().token });

    const cur = store.getState().activity!;
    const result = await withConflictHandler({
      mutation: () => api.advance(cur.id, cur.version),
      refetch: () => api.getActivity(cur.id).catch(() => null),
      onConflict: (conflict, fresh) => {
        store.getState().applyVersionConflict(conflict, fresh);
      },
    });
    expect(result).not.toBeNull();
    if (result !== null) store.getState().setActivity(result);

    // Store now reflects the running activity with the first step
    // flagged current — what the kiosk would render after one click.
    expect(store.getState().activity?.state).toBe("running");
    expect(store.getState().activity?.version).toBe(2);
    const cs = store.getState().activity?.steps.find((s) => s.current);
    expect(cs?.seq).toBe(1);

    // Wire-level assertions: the request hit the right URL, carried
    // the If-Match-Version, and used the token from the store.
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/activities/act-1/advance");
    expect(init?.method).toBe("POST");
    const headers = new Headers(init?.headers);
    expect(headers.get("If-Match-Version")).toBe("1");
    expect(headers.get("X-Toybox-Token")).toBe("tok");
  });

  it("409 path: button click refetches, surfaces toast, leaves activity refreshed", async () => {
    const store = createChildStore();
    store.setState({
      ...store.getState(),
      activity: fakeActivity({ state: "approved", version: 1 }),
      token: "tok",
    });
    const conflict: VersionConflictBody = {
      code: "version_conflict",
      current_version: 5,
      current_state: "running",
    };
    const fresh = fakeActivity({ state: "running", version: 5 });
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      // First call: the advance POST (returns 409)
      .mockResolvedValueOnce(jsonResponse(409, { detail: conflict }))
      // Second call: the refetch GET inside withConflictHandler
      .mockResolvedValueOnce(jsonResponse(200, fresh));
    const api = new ApiClient({ fetchImpl, getToken: () => store.getState().token });
    const cur = store.getState().activity!;
    const result = await withConflictHandler({
      mutation: () => api.advance(cur.id, cur.version),
      refetch: () => api.getActivity(cur.id).catch(() => null),
      onConflict: (c, freshAct) => {
        store.getState().applyVersionConflict(c, freshAct);
      },
    });
    // Result is null on conflict (no blind retry).
    expect(result).toBeNull();
    // Two REST calls: advance + refetch. The second was the GET.
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(fetchImpl.mock.calls[1]?.[0]).toBe("/api/activities/act-1");
    // Activity is now the fresh refetched one, and a warning toast was queued.
    expect(store.getState().activity?.version).toBe(5);
    expect(store.getState().activity?.state).toBe("running");
    expect(store.getState().toasts.length).toBe(1);
    expect(store.getState().toasts[0]?.kind).toBe("warning");
  });

  it("advance result is dropped when a newer envelope already won the race", async () => {
    // Regression: iter-1 unconditionally called setActivity(result)
    // after withConflictHandler, which clobbered a fresher in-memory
    // version that arrived on the ws stream during the round-trip.
    // Now the advance result is routed through applyMutationResult,
    // which has the same version guard as applyReconnectResync.
    const store = createChildStore();
    store.setState({
      ...store.getState(),
      activity: fakeActivity({ state: "approved", version: 1 }),
      token: "tok",
    });

    // The mutation returns version 2 (the slow advance round-trip)…
    const advanceResult = fakeActivity({ state: "running", version: 2 });
    const fetchImpl = vi
      .fn<Parameters<FetchLike>, ReturnType<FetchLike>>()
      .mockResolvedValue(jsonResponse(200, advanceResult));
    const api = new ApiClient({ fetchImpl, getToken: () => store.getState().token });
    const cur = store.getState().activity!;
    const result = await withConflictHandler({
      mutation: () => api.advance(cur.id, cur.version),
      refetch: () => api.getActivity(cur.id).catch(() => null),
      onConflict: (conflict, fresh) => {
        store.getState().applyVersionConflict(conflict, fresh);
      },
    });
    expect(result).not.toBeNull();

    // …but during the round-trip a fresher envelope (v5) lands.
    const newer = fakeActivity({ state: "running", version: 5 });
    store.getState().applyEnvelope({
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: newer as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(store.getState().activity?.version).toBe(5);

    // Now the App's "if (result !== null)" branch fires. With the new
    // version guard this MUST NOT regress to v2.
    if (result !== null) {
      store.getState().applyMutationResult(result);
    }
    expect(store.getState().activity?.version).toBe(5);
    expect(store.getState().activity?.state).toBe("running");
  });
});
