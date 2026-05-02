// Pure-reducer tests for the parent store. We exercise the standalone
// functions (not the zustand-wrapped store) so vitest's default node env
// is enough — no jsdom required.

import { describe, expect, it } from "vitest";

import type { Activity, ParentTokenResponse } from "./api";
import {
  applyEnvelope,
  applyRejectedTopics,
  applyVersionConflict,
  dismissToast,
  INITIAL_STATE,
  isTerminalState,
  MAX_TOASTS,
  pushToast,
  setActivity,
  setHealth,
  setMicState,
  setToken,
  setWsState,
} from "./store";

function fakeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    id: "act-1",
    state: "proposed",
    version: 1,
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

describe("parent store reducers", () => {
  it("setToken stores token + expiry", () => {
    const resp: ParentTokenResponse = {
      token: "tok-abc",
      expires_at: 9999,
      subject: { kind: "parent" },
    };
    const next = setToken(INITIAL_STATE, resp);
    expect(next.token).toBe("tok-abc");
    expect(next.tokenExpiresAt).toBe(9999);
  });

  it("setHealth threads capability_reason through", () => {
    const next = setHealth(INITIAL_STATE, {
      ok: true,
      capability_reason: "claude_unreachable",
    });
    expect(next.capabilityReason).toBe("claude_unreachable");
  });

  it("trivial setters (mic, ws, activity) thread their argument through", () => {
    // setMicState, setWsState, setActivity are unbranching shallow
    // copies. One smoke test covers all three; replaces three trivial
    // single-line tests that the iteration-1 review flagged as redundant.
    const a = setMicState(INITIAL_STATE, "capturing");
    expect(a.micState).toBe("capturing");
    const b = setWsState(a, "open");
    expect(b.wsState).toBe("open");
    const c = setActivity(b, fakeActivity());
    expect(c.activity?.id).toBe("act-1");
    const d = setActivity(c, null);
    expect(d.activity).toBeNull();
  });

  it("applyEnvelope updates activity from activity.state envelope", () => {
    const fresh = fakeActivity({ version: 2, state: "approved" });
    const next = applyEnvelope(INITIAL_STATE, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: fresh as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(next.activity?.id).toBe("act-1");
    expect(next.activity?.state).toBe("approved");
  });

  it("applyEnvelope ignores stale version of same activity", () => {
    const cur = fakeActivity({ version: 5, state: "running" });
    const seeded = setActivity(INITIAL_STATE, cur);
    const stale = fakeActivity({ version: 2, state: "approved" });
    const next = applyEnvelope(seeded, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: stale as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(next.activity?.version).toBe(5);
    expect(next.activity?.state).toBe("running");
  });

  it("applyEnvelope drops terminal updates in both no-current and different-id branches", () => {
    // Branch 1: no current activity, terminal-state envelope arrives → ignored.
    const dismissed = fakeActivity({ id: "x", state: "dismissed", version: 9 });
    const branch1 = applyEnvelope(INITIAL_STATE, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: dismissed as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(branch1.activity).toBeNull();

    // Branch 2: a *different* activity is current, a terminal-state envelope
    // for some unrelated id arrives → the existing one is preserved.
    const seeded = setActivity(INITIAL_STATE, fakeActivity({ id: "act-1", state: "running", version: 4 }));
    const otherDone = fakeActivity({ id: "act-2", state: "ended", version: 1 });
    const branch2 = applyEnvelope(seeded, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: otherDone as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(branch2.activity?.id).toBe("act-1");
    expect(branch2.activity?.state).toBe("running");
  });

  it("applyEnvelope on system topic updates capability_reason", () => {
    const next = applyEnvelope(INITIAL_STATE, {
      topic: "system",
      ts: "2026-05-02T10:00:00Z",
      payload: { capability_reason: "circuit_open" },
      schema_version: 1,
    });
    expect(next.capabilityReason).toBe("circuit_open");
  });

  it("isTerminalState recognizes the four terminal states", () => {
    for (const s of ["completed", "ended", "dismissed", "didnt_work"]) {
      expect(isTerminalState(s)).toBe(true);
    }
    for (const s of ["proposed", "approved", "running"]) {
      expect(isTerminalState(s)).toBe(false);
    }
  });

  it("pushToast / dismissToast manage queue with stable ids", () => {
    const a = pushToast(INITIAL_STATE, "warning", "first");
    const b = pushToast(a, "error", "second");
    expect(b.toasts.length).toBe(2);
    expect(b.toasts[0]?.id).not.toBe(b.toasts[1]?.id);
    const c = dismissToast(b, b.toasts[0]!.id);
    expect(c.toasts.length).toBe(1);
    expect(c.toasts[0]?.message).toBe("second");
  });

  it("toast IDs stay distinct after a dismiss + re-push (counter is array-length-independent)", () => {
    // Reproduces the "ID-counter is independent from array length"
    // invariant flagged in the iteration-1 review.
    const s1 = pushToast(INITIAL_STATE, "info", "a");
    const s2 = pushToast(s1, "info", "b");
    const id0 = s2.toasts[0]!.id;
    const id1 = s2.toasts[1]!.id;
    const s3 = dismissToast(s2, id0);
    const s4 = pushToast(s3, "info", "c");
    expect(s4.toasts.length).toBe(2);
    // After dismiss+push, the surviving "b" keeps its original id and
    // the new "c" gets a *fresh* id distinct from BOTH id0 (dismissed)
    // and id1 (kept). Three distinct ids overall: id0, id1, newId.
    expect(s4.toasts[0]?.id).toBe(id1);
    const newId = s4.toasts[1]!.id;
    expect(newId).not.toBe(id0);
    expect(newId).not.toBe(id1);
    expect(new Set([id0, id1, newId]).size).toBe(3);
  });

  it("pushToast caps the queue at MAX_TOASTS, dropping oldest first", () => {
    let s = INITIAL_STATE;
    // Push MAX_TOASTS + 5 entries; only the most recent MAX_TOASTS survive.
    for (let i = 0; i < MAX_TOASTS + 5; i += 1) {
      s = pushToast(s, "info", `msg-${i}`);
    }
    expect(s.toasts.length).toBe(MAX_TOASTS);
    expect(s.toasts[0]?.message).toBe("msg-5");
    expect(s.toasts[MAX_TOASTS - 1]?.message).toBe(`msg-${MAX_TOASTS + 4}`);
  });

  it("applyVersionConflict refreshes activity and pushes a toast", () => {
    const fresh = fakeActivity({ version: 7, state: "running" });
    const next = applyVersionConflict(
      INITIAL_STATE,
      { code: "version_conflict", current_version: 7, current_state: "running" },
      fresh,
    );
    expect(next.activity?.version).toBe(7);
    expect(next.toasts.length).toBe(1);
    expect(next.toasts[0]?.kind).toBe("warning");
    expect(next.toasts[0]?.message.toLowerCase()).toContain("version conflict");
  });

  it("applyVersionConflict tolerates missing fresh activity", () => {
    const next = applyVersionConflict(
      INITIAL_STATE,
      { code: "version_conflict", current_version: 3, current_state: "ended" },
      null,
    );
    expect(next.activity).toBeNull();
    expect(next.toasts.length).toBe(1);
  });

  it("applyRejectedTopics is a no-op for empty list", () => {
    const next = applyRejectedTopics(INITIAL_STATE, []);
    expect(next).toBe(INITIAL_STATE);
  });

  it("applyRejectedTopics surfaces rejected names in a toast", () => {
    const next = applyRejectedTopics(INITIAL_STATE, ["bogus.topic", "system.fake"]);
    expect(next.toasts.length).toBe(1);
    expect(next.toasts[0]?.message).toContain("bogus.topic");
    expect(next.toasts[0]?.message).toContain("system.fake");
  });
});
