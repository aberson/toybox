// Pure-reducer tests for the parent store. We exercise the standalone
// functions (not the zustand-wrapped store) so vitest's default node env
// is enough — no jsdom required.

import { describe, expect, it } from "vitest";

import type { Activity, ParentTokenResponse, ToyActionRow } from "./api";
import {
  applyEnvelope,
  applyProposedExpired,
  applyRejectedTopics,
  applySwitch,
  applyToyActionEnvelope,
  applyVersionConflict,
  clearToyActions,
  dismissToast,
  INITIAL_STATE,
  isTerminalState,
  MAX_TOASTS,
  pushToast,
  setActive,
  setHealth,
  setMicState,
  setToken,
  setToyActions,
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
    trigger_phrase: null,
    persona_reasoning: null,
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

  it("trivial setters (mic, ws, active) thread their argument through", () => {
    // setMicState, setWsState, setActive are unbranching shallow
    // copies. One smoke test covers all three; replaces three trivial
    // single-line tests that the iteration-1 review flagged as redundant.
    const a = setMicState(INITIAL_STATE, "capturing");
    expect(a.micState).toBe("capturing");
    const b = setWsState(a, "open");
    expect(b.wsState).toBe("open");
    const c = setActive(b, fakeActivity());
    expect(c.active?.id).toBe("act-1");
    const d = setActive(c, null);
    expect(d.active).toBeNull();
  });

  it("applyEnvelope updates active from approved activity.state envelope", () => {
    const fresh = fakeActivity({ version: 2, state: "approved" });
    const next = applyEnvelope(INITIAL_STATE, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: fresh as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(next.active?.id).toBe("act-1");
    expect(next.active?.state).toBe("approved");
  });

  it("applyEnvelope ignores stale version of same active row", () => {
    const cur = fakeActivity({ version: 5, state: "running" });
    const seeded = setActive(INITIAL_STATE, cur);
    const stale = fakeActivity({ version: 2, state: "approved" });
    const next = applyEnvelope(seeded, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: stale as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(next.active?.version).toBe(5);
    expect(next.active?.state).toBe("running");
  });

  it("applyEnvelope drops terminal updates in both no-current and different-id branches", () => {
    // Branch 1: no active row, terminal-state envelope arrives → ignored
    // (no slot populated; the row is gone before it ever landed).
    const dismissed = fakeActivity({ id: "x", state: "dismissed", version: 9 });
    const branch1 = applyEnvelope(INITIAL_STATE, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: dismissed as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(branch1.active).toBeNull();
    expect(branch1.proposedList).toHaveLength(0);

    // Branch 2: a *different* activity is active, a terminal-state envelope
    // for some unrelated id arrives → the existing one is preserved.
    const seeded = setActive(INITIAL_STATE, fakeActivity({ id: "act-1", state: "running", version: 4 }));
    const otherDone = fakeActivity({ id: "act-2", state: "ended", version: 1 });
    const branch2 = applyEnvelope(seeded, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: otherDone as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(branch2.active?.id).toBe("act-1");
    expect(branch2.active?.state).toBe("running");
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

  it("applyVersionConflict refreshes active slot and pushes a toast", () => {
    // J7: a running-state refetch routes into the active slot via
    // applyEnvelopeToNewSlots (was: the legacy single-slot ``activity``).
    const fresh = fakeActivity({ version: 7, state: "running" });
    const next = applyVersionConflict(
      INITIAL_STATE,
      { code: "version_conflict", current_version: 7, current_state: "running" },
      fresh,
    );
    expect(next.active?.version).toBe(7);
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
    expect(next.active).toBeNull();
    expect(next.proposedList).toHaveLength(0);
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

// Phase F Step F8: per-toy action grid reducer coverage. The grid
// surface depends on three load-bearing behaviors:
//   1. ``setToyActions`` (REST seed) wholesale replaces the inner map
//      so refetching can't leave stale slots from a prior toy.
//   2. ``applyToyActionEnvelope`` (WS push) latest-wins per slot
//      across queued → running → done, carrying over previously-
//      known seed/image_path when the worker omits the field.
//   3. ``clearToyActions`` removes a toy's slot map entirely so the
//      memory footprint stays bounded across many archive cycles.

function fakeRow(overrides: Partial<ToyActionRow> = {}): ToyActionRow {
  return {
    toy_id: "toy-1",
    slot: "looking",
    status: "queued",
    image_path: null,
    seed: null,
    error_msg: null,
    updated_at: "2026-05-06T10:00:00Z",
    ...overrides,
  };
}

describe("toyActions reducers", () => {
  it("setToyActions seeds the inner map keyed by slot", () => {
    const rows: ToyActionRow[] = [
      fakeRow({ slot: "idle", status: "done", image_path: "data/x.png" }),
      fakeRow({ slot: "looking", status: "running" }),
    ];
    const next = setToyActions(INITIAL_STATE, "toy-1", rows);
    expect(Object.keys(next.toyActions["toy-1"] ?? {})).toEqual([
      "idle",
      "looking",
    ]);
    expect(next.toyActions["toy-1"]?.["idle"]?.status).toBe("done");
  });

  it("setToyActions wholesale replaces an existing inner map", () => {
    const seeded = setToyActions(INITIAL_STATE, "toy-1", [
      fakeRow({ slot: "idle", status: "done" }),
      fakeRow({ slot: "looking", status: "queued" }),
    ]);
    const replaced = setToyActions(seeded, "toy-1", [
      fakeRow({ slot: "jumping", status: "done" }),
    ]);
    expect(Object.keys(replaced.toyActions["toy-1"] ?? {})).toEqual([
      "jumping",
    ]);
    // Old slots are gone — replace, not merge.
    expect(replaced.toyActions["toy-1"]?.["idle"]).toBeUndefined();
  });

  it("applyToyActionEnvelope merges done after running after queued (latest wins)", () => {
    // The worker emits queued → running → done in order. The reducer
    // must take the latest envelope's status as the source of truth
    // while carrying forward fields the intermediate envelopes omit
    // (image_path is only known on done).
    let s = INITIAL_STATE;
    s = applyToyActionEnvelope(s, {
      topic: "toy_actions",
      ts: "2026-05-06T10:00:00Z",
      payload: { toy_id: "toy-1", slot: "looking", status: "queued" },
      schema_version: 1,
    });
    expect(s.toyActions["toy-1"]?.["looking"]?.status).toBe("queued");

    s = applyToyActionEnvelope(s, {
      topic: "toy_actions",
      ts: "2026-05-06T10:00:01Z",
      payload: { toy_id: "toy-1", slot: "looking", status: "running" },
      schema_version: 1,
    });
    expect(s.toyActions["toy-1"]?.["looking"]?.status).toBe("running");

    s = applyToyActionEnvelope(s, {
      topic: "toy_actions",
      ts: "2026-05-06T10:00:02Z",
      payload: {
        toy_id: "toy-1",
        slot: "looking",
        status: "done",
        image_path: "data/images/toy_actions/toy-1/looking.png",
      },
      schema_version: 1,
    });
    expect(s.toyActions["toy-1"]?.["looking"]?.status).toBe("done");
    expect(s.toyActions["toy-1"]?.["looking"]?.image_path).toBe(
      "data/images/toy_actions/toy-1/looking.png",
    );
  });

  it("applyToyActionEnvelope failed envelope captures error_msg", () => {
    const next = applyToyActionEnvelope(INITIAL_STATE, {
      topic: "toy_actions",
      ts: "2026-05-06T10:00:00Z",
      payload: {
        toy_id: "toy-1",
        slot: "jumping",
        status: "failed",
        error: "CUDA OOM",
      },
      schema_version: 1,
    });
    expect(next.toyActions["toy-1"]?.["jumping"]?.status).toBe("failed");
    expect(next.toyActions["toy-1"]?.["jumping"]?.error_msg).toBe("CUDA OOM");
  });

  it("applyToyActionEnvelope ignores non-toy_actions topics", () => {
    const next = applyToyActionEnvelope(INITIAL_STATE, {
      topic: "system",
      ts: "2026-05-06T10:00:00Z",
      payload: { toy_id: "toy-1", slot: "looking", status: "done" },
      schema_version: 1,
    });
    expect(next).toBe(INITIAL_STATE);
  });

  it("applyEnvelope routes toy_actions topic into the slot map", () => {
    const next = applyEnvelope(INITIAL_STATE, {
      topic: "toy_actions",
      ts: "2026-05-06T10:00:00Z",
      payload: { toy_id: "toy-1", slot: "idle", status: "queued" },
      schema_version: 1,
    });
    expect(next.toyActions["toy-1"]?.["idle"]?.status).toBe("queued");
  });

  it("clearToyActions removes one toy's map without touching others", () => {
    let s = INITIAL_STATE;
    s = setToyActions(s, "toy-1", [fakeRow({ slot: "idle" })]);
    s = setToyActions(s, "toy-2", [fakeRow({ toy_id: "toy-2", slot: "idle" })]);
    const next = clearToyActions(s, "toy-1");
    expect(next.toyActions["toy-1"]).toBeUndefined();
    expect(next.toyActions["toy-2"]).toBeDefined();
  });

  it("applyToyActionEnvelope drops malformed payloads", () => {
    // No toy_id → no-op.
    const a = applyToyActionEnvelope(INITIAL_STATE, {
      topic: "toy_actions",
      ts: "2026-05-06T10:00:00Z",
      payload: { slot: "idle", status: "queued" },
      schema_version: 1,
    });
    expect(a).toBe(INITIAL_STATE);
    // Unknown status → no-op (defensive against schema drift).
    const b = applyToyActionEnvelope(INITIAL_STATE, {
      topic: "toy_actions",
      ts: "2026-05-06T10:00:00Z",
      payload: {
        toy_id: "toy-1",
        slot: "idle",
        status: "completely_made_up",
      },
      schema_version: 1,
    });
    expect(b).toBe(INITIAL_STATE);
  });
});

// =====================================================================
// Phase J6/J7: play-queue store. The dashboard's single-slot
// ``activity`` shape was replaced by two parallel slots:
//
//   * ``proposedList: Activity[]`` — the scrolling suggestion queue
//     (newest first, up to 5 from the REST seed, then mutated by ws).
//   * ``active: Activity | null`` — the currently-playing card.
//
// J7 removed the legacy ``activity`` slot; only ``proposedList`` and
// ``active`` are populated by envelopes/mutations/refetches now.
// =====================================================================

describe("parent store — Phase J6 play-queue additions", () => {
  describe("initial state", () => {
    it("INITIAL_STATE includes empty proposedList and null active", () => {
      // Both new slots ship as empty defaults so the dashboard's
      // first paint (before any REST seed or ws envelope) renders an
      // empty queue and no active card without nullable threading.
      expect(INITIAL_STATE.proposedList).toEqual([]);
      expect(INITIAL_STATE.active).toBeNull();
    });
  });

  describe("applyEnvelope routes to proposedList + active", () => {
    it("envelope state=proposed upserts a fresh row into proposedList", () => {
      const fresh = fakeActivity({
        id: "act-1",
        state: "proposed",
        version: 1,
      });
      const next = applyEnvelope(INITIAL_STATE, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: fresh as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.proposedList).toHaveLength(1);
      expect(next.proposedList[0]?.id).toBe("act-1");
      expect(next.proposedList[0]?.state).toBe("proposed");
    });

    it("envelope state=proposed replaces same-id row by version (newer wins)", () => {
      const v1 = fakeActivity({ id: "act-1", state: "proposed", version: 1 });
      const seeded = applyEnvelope(INITIAL_STATE, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: v1 as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      const v2 = fakeActivity({
        id: "act-1",
        state: "proposed",
        version: 2,
        title: "v2 title",
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:01Z",
        payload: v2 as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.proposedList).toHaveLength(1);
      expect(next.proposedList[0]?.version).toBe(2);
      expect(next.proposedList[0]?.title).toBe("v2 title");
    });

    it("envelope state=proposed clears active when id matches", () => {
      // Edge case: the same id flipped state from approved back to
      // proposed (e.g. a regenerate). The old ``active`` slot must
      // drop the row because it is now a queue item, not playing.
      const active = fakeActivity({
        id: "act-1",
        state: "approved",
        version: 3,
      });
      const seeded = { ...INITIAL_STATE, active };
      const reproposed = fakeActivity({
        id: "act-1",
        state: "proposed",
        version: 4,
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: reproposed as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.active).toBeNull();
      expect(next.proposedList).toHaveLength(1);
      expect(next.proposedList[0]?.id).toBe("act-1");
    });

    it("envelope state=approved sets active and removes from proposedList", () => {
      // The parent tapped Approve on a queue item: that row should
      // move from proposedList into the active slot.
      const proposed = fakeActivity({
        id: "act-1",
        state: "proposed",
        version: 1,
      });
      const seeded = {
        ...INITIAL_STATE,
        proposedList: [proposed],
      };
      const approved = fakeActivity({
        id: "act-1",
        state: "approved",
        version: 2,
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: approved as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.active?.id).toBe("act-1");
      expect(next.active?.state).toBe("approved");
      expect(next.proposedList).toHaveLength(0);
    });

    it("envelope state=running updates active across approved → running", () => {
      const approved = fakeActivity({
        id: "act-1",
        state: "approved",
        version: 2,
      });
      const seeded = { ...INITIAL_STATE, active: approved };
      const running = fakeActivity({
        id: "act-1",
        state: "running",
        version: 3,
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: running as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.active?.state).toBe("running");
      expect(next.active?.version).toBe(3);
    });

    it("envelope state=paused updates active", () => {
      const running = fakeActivity({
        id: "act-1",
        state: "running",
        version: 3,
      });
      const seeded = { ...INITIAL_STATE, active: running };
      const paused = fakeActivity({
        id: "act-1",
        state: "paused",
        version: 4,
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: paused as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.active?.state).toBe("paused");
      expect(next.active?.version).toBe(4);
    });

    it("envelope state=completed updates active", () => {
      const running = fakeActivity({
        id: "act-1",
        state: "running",
        version: 5,
      });
      const seeded = { ...INITIAL_STATE, active: running };
      const completed = fakeActivity({
        id: "act-1",
        state: "completed",
        version: 6,
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: completed as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.active?.state).toBe("completed");
      expect(next.active?.version).toBe(6);
    });

    it("envelope state=dismissed removes from proposedList and clears active if matches", () => {
      const proposed = fakeActivity({
        id: "act-1",
        state: "proposed",
        version: 1,
      });
      const seeded = {
        ...INITIAL_STATE,
        proposedList: [proposed],
        active: proposed,
      };
      const dismissed = fakeActivity({
        id: "act-1",
        state: "dismissed",
        version: 2,
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: dismissed as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.proposedList).toHaveLength(0);
      expect(next.active).toBeNull();
    });

    it("envelope state=didnt_work removes from proposedList and clears active if matches", () => {
      const running = fakeActivity({
        id: "act-1",
        state: "running",
        version: 3,
      });
      const seeded = {
        ...INITIAL_STATE,
        proposedList: [running],
        active: running,
      };
      const didntWork = fakeActivity({
        id: "act-1",
        state: "didnt_work",
        version: 4,
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: didntWork as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.proposedList).toHaveLength(0);
      expect(next.active).toBeNull();
    });

    it("envelope state=ended removes from proposedList and clears active if matches", () => {
      const running = fakeActivity({
        id: "act-1",
        state: "running",
        version: 3,
      });
      const seeded = {
        ...INITIAL_STATE,
        proposedList: [running],
        active: running,
      };
      const ended = fakeActivity({
        id: "act-1",
        state: "ended",
        version: 4,
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: ended as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.proposedList).toHaveLength(0);
      expect(next.active).toBeNull();
    });

    it("terminal-state envelope on different-id active leaves active alone", () => {
      // The active card is act-1; an unrelated act-2 terminates →
      // act-1 active stays put, proposedList unchanged.
      const active = fakeActivity({
        id: "act-1",
        state: "running",
        version: 3,
      });
      const seeded = { ...INITIAL_STATE, active };
      const otherEnded = fakeActivity({
        id: "act-2",
        state: "ended",
        version: 1,
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: otherEnded as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.active?.id).toBe("act-1");
      expect(next.active?.state).toBe("running");
    });

    it("newer-version envelope wins for same id in proposedList", () => {
      // Out-of-order envelope arrival: v3 already in proposedList,
      // then v2 arrives late → v2 ignored, v3 retained.
      const v3 = fakeActivity({ id: "act-1", state: "proposed", version: 3 });
      const seeded = { ...INITIAL_STATE, proposedList: [v3] };
      const v2 = fakeActivity({
        id: "act-1",
        state: "proposed",
        version: 2,
        title: "stale",
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: v2 as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.proposedList).toHaveLength(1);
      expect(next.proposedList[0]?.version).toBe(3);
      expect(next.proposedList[0]?.title).not.toBe("stale");
    });

    it("newer-version envelope wins for same id in active", () => {
      const v3 = fakeActivity({ id: "act-1", state: "running", version: 3 });
      const seeded = { ...INITIAL_STATE, active: v3 };
      const v2 = fakeActivity({
        id: "act-1",
        state: "approved",
        version: 2,
        title: "stale",
      });
      const next = applyEnvelope(seeded, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: v2 as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.active?.version).toBe(3);
      expect(next.active?.state).toBe("running");
      expect(next.active?.title).not.toBe("stale");
    });

    it("J7: only the new slots are populated by applyEnvelope (no legacy mirror)", () => {
      // J7 removed the legacy single-slot ``activity`` field. The
      // ParentState shape no longer carries it; this regression test
      // pins that an envelope routes ONLY to the new ``active`` slot
      // (or ``proposedList`` per state) — there is no parallel write
      // to a back-compat field.
      const fresh = fakeActivity({
        id: "act-1",
        state: "approved",
        version: 2,
      });
      const next = applyEnvelope(INITIAL_STATE, {
        topic: "activity.state",
        ts: "2026-05-12T10:00:00Z",
        payload: fresh as unknown as Record<string, unknown>,
        schema_version: 1,
      });
      expect(next.active?.id).toBe("act-1");
      expect(next.active?.state).toBe("approved");
      expect(next.proposedList).toHaveLength(0);
      // Sanity: a TS-only field probe would also catch this — the
      // ParentState type no longer has an ``activity`` key.
      expect("activity" in next).toBe(false);
    });
  });

  describe("applyProposedExpired", () => {
    it("removes the row with the matching id from proposedList", () => {
      const a = fakeActivity({ id: "act-1", state: "proposed", version: 1 });
      const b = fakeActivity({ id: "act-2", state: "proposed", version: 1 });
      const seeded = { ...INITIAL_STATE, proposedList: [a, b] };
      const next = applyProposedExpired(seeded, "act-1");
      expect(next.proposedList).toHaveLength(1);
      expect(next.proposedList[0]?.id).toBe("act-2");
    });

    it("is a no-op when id is not present", () => {
      const a = fakeActivity({ id: "act-1", state: "proposed", version: 1 });
      const seeded = { ...INITIAL_STATE, proposedList: [a] };
      const next = applyProposedExpired(seeded, "act-99");
      expect(next.proposedList).toHaveLength(1);
      expect(next.proposedList[0]?.id).toBe("act-1");
    });
  });

  describe("applySwitch", () => {
    it("clears active then sets to approve result", () => {
      // The "switch" gesture ends the current active and immediately
      // approves a queue item: net result is the new approve target
      // in active.
      const oldActive = fakeActivity({
        id: "act-1",
        state: "running",
        version: 3,
      });
      const ended = fakeActivity({
        id: "act-1",
        state: "ended",
        version: 4,
      });
      const newApproved = fakeActivity({
        id: "act-2",
        state: "approved",
        version: 2,
      });
      const seeded = { ...INITIAL_STATE, active: oldActive };
      const next = applySwitch(seeded, ended, newApproved);
      expect(next.active?.id).toBe("act-2");
      expect(next.active?.state).toBe("approved");
    });

    it("removes the new active from proposedList when it was queued", () => {
      const oldActive = fakeActivity({
        id: "act-1",
        state: "running",
        version: 3,
      });
      const queuedB = fakeActivity({
        id: "act-2",
        state: "proposed",
        version: 1,
      });
      const queuedC = fakeActivity({
        id: "act-3",
        state: "proposed",
        version: 1,
      });
      const ended = fakeActivity({
        id: "act-1",
        state: "ended",
        version: 4,
      });
      const newApproved = fakeActivity({
        id: "act-2",
        state: "approved",
        version: 2,
      });
      const seeded = {
        ...INITIAL_STATE,
        active: oldActive,
        proposedList: [queuedB, queuedC],
      };
      const next = applySwitch(seeded, ended, newApproved);
      expect(next.active?.id).toBe("act-2");
      expect(next.proposedList).toHaveLength(1);
      expect(next.proposedList[0]?.id).toBe("act-3");
    });

    it("tolerates null endResult (just sets active from approve)", () => {
      // Defensive branch: callers may pass null when there was no
      // prior active to end (e.g. queue ramped up after empty active).
      const newApproved = fakeActivity({
        id: "act-2",
        state: "approved",
        version: 2,
      });
      const next = applySwitch(INITIAL_STATE, null, newApproved);
      expect(next.active?.id).toBe("act-2");
    });

    it("tolerates null approveResult (just clears active)", () => {
      const oldActive = fakeActivity({
        id: "act-1",
        state: "running",
        version: 3,
      });
      const ended = fakeActivity({
        id: "act-1",
        state: "ended",
        version: 4,
      });
      const seeded = { ...INITIAL_STATE, active: oldActive };
      const next = applySwitch(seeded, ended, null);
      expect(next.active).toBeNull();
    });
  });
});
