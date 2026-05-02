// Pure-reducer tests for the child kiosk store. We exercise the
// standalone functions (not the zustand-wrapped store) so vitest's
// default node env is enough — no jsdom required.

import { describe, expect, it } from "vitest";

import type { Activity, ParentTokenResponse } from "./api";
import {
  applyEnvelope,
  applyMutationResult,
  applyReconnectResync,
  applyRejectedTopics,
  applyVersionConflict,
  currentStepSeq,
  dismissToast,
  INITIAL_STATE,
  isRenderable,
  isTerminalState,
  MAX_TOASTS,
  pushToast,
  setActivity,
  setToken,
  shouldFireSuccessSfx,
  shouldFireTransitionSfx,
} from "./store";

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
    steps: [],
    metadata: {},
    ...overrides,
  };
}

describe("child store reducers", () => {
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

  it("setActivity(null) clears the in-memory activity", () => {
    // The non-null setActivity round-trip is exercised by every other
    // test in this file (and by flow.test.ts). The null-clear case is
    // the only behavior worth a dedicated assertion: the "all done"
    // path resets to idle by passing null, and the App's dismiss
    // handler relies on it.
    const seeded = setActivity(INITIAL_STATE, fakeActivity());
    expect(seeded.activity).not.toBeNull();
    const cleared = setActivity(seeded, null);
    expect(cleared.activity).toBeNull();
  });

  it("isTerminalState recognizes the four terminal states", () => {
    for (const s of ["completed", "ended", "dismissed", "didnt_work"]) {
      expect(isTerminalState(s)).toBe(true);
    }
    for (const s of ["proposed", "approved", "running"]) {
      expect(isTerminalState(s)).toBe(false);
    }
  });

  it("isRenderable matches the kiosk's accept set", () => {
    for (const s of ["approved", "running", "completed", "ended"]) {
      expect(isRenderable(s)).toBe(true);
    }
    for (const s of ["proposed", "dismissed", "didnt_work"]) {
      expect(isRenderable(s)).toBe(false);
    }
  });

  it("currentStepSeq returns 0 when no step is current and the seq when one is", () => {
    expect(currentStepSeq(null)).toBe(0);
    const noneCurrent = fakeActivity({
      steps: [
        { seq: 1, body: "a", sfx: null, expected_action: null, current: false },
        { seq: 2, body: "b", sfx: null, expected_action: null, current: false },
      ],
    });
    expect(currentStepSeq(noneCurrent)).toBe(0);
    const second = fakeActivity({
      steps: [
        { seq: 1, body: "a", sfx: null, expected_action: null, current: false },
        { seq: 2, body: "b", sfx: null, expected_action: null, current: true },
      ],
    });
    expect(currentStepSeq(second)).toBe(2);
  });

  it("applyEnvelope ignores `proposed` envelopes when there is no current activity", () => {
    // The kiosk waits for the parent to approve before showing
    // anything; a bare `proposed` envelope must NOT take over.
    const proposed = fakeActivity({ state: "proposed", version: 1 });
    const next = applyEnvelope(INITIAL_STATE, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: proposed as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(next.activity).toBeNull();
  });

  it("applyEnvelope adopts an `approved` envelope when no activity is current", () => {
    const approved = fakeActivity({ state: "approved", version: 2 });
    const next = applyEnvelope(INITIAL_STATE, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: approved as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(next.activity?.id).toBe("act-1");
    expect(next.activity?.state).toBe("approved");
  });

  it("applyEnvelope updates the same activity to a newer version", () => {
    const cur = fakeActivity({ state: "running", version: 5 });
    const seeded = setActivity(INITIAL_STATE, cur);
    const newer = fakeActivity({ state: "running", version: 6 });
    const next = applyEnvelope(seeded, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: newer as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(next.activity?.version).toBe(6);
  });

  it("applyEnvelope ignores stale (lower-version) updates of the same id", () => {
    const cur = fakeActivity({ state: "running", version: 5 });
    const seeded = setActivity(INITIAL_STATE, cur);
    const stale = fakeActivity({ state: "approved", version: 2 });
    const next = applyEnvelope(seeded, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: stale as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(next.activity?.version).toBe(5);
    expect(next.activity?.state).toBe("running");
  });

  it("applyEnvelope ignores non-activity.state topics", () => {
    const seeded = setActivity(INITIAL_STATE, fakeActivity({ version: 7 }));
    const next = applyEnvelope(seeded, {
      topic: "system",
      ts: "2026-05-02T10:00:00Z",
      payload: { capability_reason: "circuit_open" },
      schema_version: 1,
    });
    expect(next).toBe(seeded);
  });

  it("applyEnvelope ignores activity.state envelopes with invalid payload shape", () => {
    const next = applyEnvelope(INITIAL_STATE, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: { bogus: true },
      schema_version: 1,
    });
    expect(next).toBe(INITIAL_STATE);
  });

  it("applyEnvelope adopts a different-id renderable envelope but ignores a terminal one", () => {
    const seeded = setActivity(
      INITIAL_STATE,
      fakeActivity({ id: "act-1", state: "running", version: 4 }),
    );
    const otherRunning = fakeActivity({
      id: "act-2",
      state: "running",
      version: 1,
    });
    const next = applyEnvelope(seeded, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: otherRunning as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(next.activity?.id).toBe("act-2");

    const otherDone = fakeActivity({
      id: "act-3",
      state: "dismissed",
      version: 1,
    });
    const ignored = applyEnvelope(next, {
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: otherDone as unknown as Record<string, unknown>,
      schema_version: 1,
    });
    expect(ignored.activity?.id).toBe("act-2");
  });

  it("applyReconnectResync skips the GET if the in-memory version is fresher", () => {
    // Race the parent UI flagged in Step 9: while a reconnect GET is
    // in flight, a newer envelope can arrive. The version guard
    // prevents the GET from clobbering the newer state.
    const fresh = fakeActivity({ id: "act-1", state: "running", version: 7 });
    const seeded = setActivity(INITIAL_STATE, fresh);
    const stale = fakeActivity({ id: "act-1", state: "approved", version: 3 });
    const next = applyReconnectResync(seeded, stale);
    expect(next.activity?.version).toBe(7);
    expect(next.activity?.state).toBe("running");
  });

  it("applyReconnectResync adopts when newer or for a different id", () => {
    const cur = fakeActivity({ id: "act-1", state: "approved", version: 3 });
    const seeded = setActivity(INITIAL_STATE, cur);
    const newer = fakeActivity({ id: "act-1", state: "running", version: 4 });
    const next = applyReconnectResync(seeded, newer);
    expect(next.activity?.version).toBe(4);
    const otherId = fakeActivity({ id: "act-2", state: "running", version: 1 });
    const next2 = applyReconnectResync(next, otherId);
    expect(next2.activity?.id).toBe("act-2");
  });

  it("applyReconnectResync is a no-op when fresh is null", () => {
    const seeded = setActivity(INITIAL_STATE, fakeActivity());
    const next = applyReconnectResync(seeded, null);
    expect(next).toBe(seeded);
  });

  it("applyMutationResult drops the response when in-memory is fresher", () => {
    // Regression: iter-1 used unconditional setActivity(result) after
    // an advance round-trip, which clobbered a fresher ws envelope
    // that arrived mid-flight. The version guard mirrors
    // applyReconnectResync.
    const fresher = fakeActivity({ id: "act-1", state: "running", version: 7 });
    const seeded = setActivity(INITIAL_STATE, fresher);
    const stale = fakeActivity({ id: "act-1", state: "approved", version: 3 });
    const next = applyMutationResult(seeded, stale);
    expect(next.activity?.version).toBe(7);
    expect(next.activity?.state).toBe("running");
  });

  it("applyMutationResult adopts equal-version (atomic state advance)", () => {
    // The advance endpoint can return the SAME version with a
    // different `current` step; the reducer should accept that since
    // there's no real regression risk (state is the same).
    const cur = fakeActivity({ id: "act-1", state: "approved", version: 1 });
    const seeded = setActivity(INITIAL_STATE, cur);
    const advanced = fakeActivity({ id: "act-1", state: "running", version: 1 });
    const next = applyMutationResult(seeded, advanced);
    expect(next.activity?.state).toBe("running");
  });

  it("applyMutationResult adopts a different-id response", () => {
    const cur = fakeActivity({ id: "act-1", version: 9 });
    const seeded = setActivity(INITIAL_STATE, cur);
    const other = fakeActivity({ id: "act-2", version: 1 });
    const next = applyMutationResult(seeded, other);
    expect(next.activity?.id).toBe("act-2");
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

  it("pushToast caps the queue at MAX_TOASTS, dropping oldest first", () => {
    let s = INITIAL_STATE;
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
      {
        code: "version_conflict",
        current_version: 7,
        current_state: "running",
      },
      fresh,
    );
    expect(next.activity?.version).toBe(7);
    expect(next.toasts.length).toBe(1);
    expect(next.toasts[0]?.kind).toBe("warning");
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
    const next = applyRejectedTopics(INITIAL_STATE, ["bogus.topic"]);
    expect(next.toasts.length).toBe(1);
    expect(next.toasts[0]?.message).toContain("bogus.topic");
  });

  it("shouldFireTransitionSfx does NOT fire on initial render", () => {
    // Regression: iter-1 initialized prevStepSeqRef.current to 0, so
    // the first envelope with current_step_seq=1 satisfied
    // `seq > prev (1>0) && prev >= 0 (0>=0)` and played
    // transition.wav for a NON-transition. The fix uses a -1 sentinel.
    expect(shouldFireTransitionSfx(-1, 0)).toBe(false);
    expect(shouldFireTransitionSfx(-1, 1)).toBe(false);
    expect(shouldFireTransitionSfx(-1, 5)).toBe(false);
    // After the first envelope the ref is 0 (no step current). The
    // first real transition (0 → 1) MUST fire.
    expect(shouldFireTransitionSfx(0, 1)).toBe(true);
    // Subsequent transitions fire for any monotonic increase.
    expect(shouldFireTransitionSfx(1, 2)).toBe(true);
    expect(shouldFireTransitionSfx(2, 3)).toBe(true);
    // Same seq must NOT fire (re-render with no actual advance).
    expect(shouldFireTransitionSfx(2, 2)).toBe(false);
    // Lower seq (envelope reorder, recovered terminal state) must not fire.
    expect(shouldFireTransitionSfx(3, 1)).toBe(false);
  });

  it("shouldFireSuccessSfx does NOT fire when attaching to a stale terminal state", () => {
    // Regression: iter-1 fired success.wav whenever the kiosk attached
    // to an already-completed activity (page reload mid-completion).
    // The hasSeenAny gate stops that.
    expect(
      shouldFireSuccessSfx({
        prevTerminal: false,
        nextTerminal: true,
        hasSeenAny: false,
      }),
    ).toBe(false);
    // After at least one observation, the next terminal-edge fires it.
    expect(
      shouldFireSuccessSfx({
        prevTerminal: false,
        nextTerminal: true,
        hasSeenAny: true,
      }),
    ).toBe(true);
    // Already-terminal: do NOT re-fire.
    expect(
      shouldFireSuccessSfx({
        prevTerminal: true,
        nextTerminal: true,
        hasSeenAny: true,
      }),
    ).toBe(false);
    // Non-terminal state: never fire.
    expect(
      shouldFireSuccessSfx({
        prevTerminal: false,
        nextTerminal: false,
        hasSeenAny: true,
      }),
    ).toBe(false);
  });
});
