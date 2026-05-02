// Zustand store for the child kiosk UI. Holds the parent-issued token,
// the active activity (only one at a time on the kiosk), the ws
// connection state, and a small toast queue used for 409 / rejected-
// topic surfacing. Reducers are exported as standalone pure functions
// so vitest can exercise them without spinning up zustand.

import { create } from "zustand";

import type {
  Activity,
  ParentTokenResponse,
  VersionConflictBody,
} from "./api";
import type { Envelope, WsState } from "./ws";

export interface Toast {
  id: number;
  kind: "info" | "warning" | "error";
  message: string;
}

// Cap the toast queue so a flapping reconnect or a 409 storm can't
// grow the array without bound. Oldest entries drop first.
export const MAX_TOASTS = 20;

export interface ChildState {
  token: string | null;
  tokenExpiresAt: number | null;
  activity: Activity | null;
  wsState: WsState;
  toasts: Toast[];
  // monotonically increasing counter so toast IDs are stable
  nextToastId: number;
}

export const INITIAL_STATE: ChildState = {
  token: null,
  tokenExpiresAt: null,
  activity: null,
  wsState: "idle",
  toasts: [],
  nextToastId: 1,
};

export function setToken(
  state: ChildState,
  resp: ParentTokenResponse,
): ChildState {
  return {
    ...state,
    token: resp.token,
    tokenExpiresAt: resp.expires_at,
  };
}

export function setWsState(state: ChildState, ws: WsState): ChildState {
  return { ...state, wsState: ws };
}

export function setActivity(
  state: ChildState,
  activity: Activity | null,
): ChildState {
  return { ...state, activity };
}

const TERMINAL_STATES = new Set([
  "completed",
  "ended",
  "dismissed",
  "didnt_work",
]);

export function isTerminalState(state: string): boolean {
  return TERMINAL_STATES.has(state);
}

// The kiosk only renders an activity once the parent has approved it.
// We treat anything other than `proposed`/`dismissed`/`didnt_work` as
// renderable: approved/running drive the live view, completed/ended
// drive the "all done" screen.
const RENDERABLE_STATES = new Set([
  "approved",
  "running",
  "completed",
  "ended",
]);

export function isRenderable(state: string): boolean {
  return RENDERABLE_STATES.has(state);
}

// Given the activity's steps, return the seq of the step flagged
// `current` (or 0 if none — happens for `approved` activities pre-advance).
// The kiosk uses this to detect step transitions (current_step_seq
// monotonic increase => fire SFX).
export function currentStepSeq(activity: Activity | null): number {
  if (activity === null) return 0;
  const cur = activity.steps.find((s) => s.current);
  return cur?.seq ?? 0;
}

// Pure decision helper for the kiosk's transition-SFX effect. Returns
// true iff the new step seq represents a real transition we should
// announce. ``prev === -1`` is the sentinel meaning "no step ever
// seen"; we record but do NOT fire on the first sighting (which would
// otherwise turn the initial render of an approved activity into a
// false transition).
export function shouldFireTransitionSfx(prev: number, next: number): boolean {
  return next > prev && prev >= 0;
}

// Pure decision helper for the kiosk's success-SFX effect. Returns
// true iff this is a fresh transition INTO a completed/ended state.
// ``hasSeenAny`` guards against attaching to an already-terminal
// activity (e.g. on a page reload mid-completion) playing
// ``success.wav`` for a stale terminal state.
export function shouldFireSuccessSfx(args: {
  prevTerminal: boolean;
  nextTerminal: boolean;
  hasSeenAny: boolean;
}): boolean {
  return args.nextTerminal && !args.prevTerminal && args.hasSeenAny;
}

// Apply an incoming ws envelope to the store. Only `activity.state`
// envelopes mutate state today. The kiosk happily accepts any
// renderable update for the same activity id so it stays in lockstep
// with the parent. Stale (lower-version) updates for the SAME id are
// dropped to avoid envelope-reorder regressions.
export function applyEnvelope(
  state: ChildState,
  env: Envelope,
): ChildState {
  if (env.topic !== "activity.state") return state;
  const candidate = env.payload as unknown as Activity;
  if (
    typeof candidate?.id !== "string" ||
    typeof candidate?.state !== "string" ||
    typeof candidate?.version !== "number"
  ) {
    return state;
  }
  const cur = state.activity;
  if (cur === null) {
    // First time we see anything: only attach if the parent has
    // approved it. A bare `proposed` envelope shouldn't take over the
    // kiosk; the kiosk waits for the play to actually start.
    return isRenderable(candidate.state)
      ? { ...state, activity: candidate }
      : state;
  }
  if (cur.id === candidate.id) {
    if (candidate.version >= cur.version) {
      return { ...state, activity: candidate };
    }
    return state;
  }
  // Different id: only adopt if the new one is renderable.
  if (isRenderable(candidate.state)) {
    return { ...state, activity: candidate };
  }
  return state;
}

export function pushToast(
  state: ChildState,
  kind: Toast["kind"],
  message: string,
): ChildState {
  const toast: Toast = { id: state.nextToastId, kind, message };
  const combined = [...state.toasts, toast];
  // Trim from the front so oldest entries drop first when over cap.
  const trimmed =
    combined.length > MAX_TOASTS
      ? combined.slice(combined.length - MAX_TOASTS)
      : combined;
  return {
    ...state,
    toasts: trimmed,
    nextToastId: state.nextToastId + 1,
  };
}

export function dismissToast(state: ChildState, id: number): ChildState {
  return { ...state, toasts: state.toasts.filter((t) => t.id !== id) };
}

export function applyVersionConflict(
  state: ChildState,
  conflict: VersionConflictBody,
  fresh: Activity | null,
): ChildState {
  const next = fresh !== null ? { ...state, activity: fresh } : state;
  const message = `Out of sync (now v${conflict.current_version}, ${conflict.current_state}). Refreshed.`;
  return pushToast(next, "warning", message);
}

export function applyRejectedTopics(
  state: ChildState,
  rejected: string[],
): ChildState {
  if (rejected.length === 0) return state;
  return pushToast(
    state,
    "warning",
    `Subscription rejected for topics: ${rejected.join(", ")}`,
  );
}

// Reconnect-resync helper: only adopt the REST refetch if it isn't a
// stale read (e.g. the ws envelope already applied a newer version
// while the GET was in flight). This is the version guard mentioned
// in Step 9's open follow-up.
export function applyReconnectResync(
  state: ChildState,
  fresh: Activity | null,
): ChildState {
  if (fresh === null) return state;
  const cur = state.activity;
  if (cur !== null && cur.id === fresh.id && fresh.version < cur.version) {
    return state;
  }
  return { ...state, activity: fresh };
}

// Mutation-result helper: drop the response when a newer envelope has
// already arrived for the same activity. The advance round-trip can
// take long enough that the ws stream pushes a fresher state mid-flight;
// without this guard the unconditional ``setActivity(result)`` regresses
// the in-memory version. Mirrors the version guard in
// ``applyReconnectResync`` and ``applyEnvelope``.
export function applyMutationResult(
  state: ChildState,
  fresh: Activity,
): ChildState {
  const cur = state.activity;
  if (cur !== null && cur.id === fresh.id && fresh.version < cur.version) {
    return state;
  }
  return { ...state, activity: fresh };
}

export interface ChildStore extends ChildState {
  setToken: (resp: ParentTokenResponse) => void;
  setWsState: (ws: WsState) => void;
  setActivity: (a: Activity | null) => void;
  applyEnvelope: (env: Envelope) => void;
  applyVersionConflict: (
    conflict: VersionConflictBody,
    fresh: Activity | null,
  ) => void;
  applyRejectedTopics: (rejected: string[]) => void;
  applyReconnectResync: (fresh: Activity | null) => void;
  applyMutationResult: (fresh: Activity) => void;
  pushToast: (kind: Toast["kind"], message: string) => void;
  dismissToast: (id: number) => void;
}

export function createChildStore(initial: ChildState = INITIAL_STATE) {
  return create<ChildStore>((set) => ({
    ...initial,
    setToken: (resp) => set((s) => setToken(s, resp)),
    setWsState: (ws) => set((s) => setWsState(s, ws)),
    setActivity: (a) => set((s) => setActivity(s, a)),
    applyEnvelope: (env) => set((s) => applyEnvelope(s, env)),
    applyVersionConflict: (conflict, fresh) =>
      set((s) => applyVersionConflict(s, conflict, fresh)),
    applyRejectedTopics: (rejected) =>
      set((s) => applyRejectedTopics(s, rejected)),
    applyReconnectResync: (fresh) =>
      set((s) => applyReconnectResync(s, fresh)),
    applyMutationResult: (fresh) =>
      set((s) => applyMutationResult(s, fresh)),
    pushToast: (kind, message) => set((s) => pushToast(s, kind, message)),
    dismissToast: (id) => set((s) => dismissToast(s, id)),
  }));
}

// Process-singleton store the components import directly. Tests use
// `createChildStore()` to get an isolated copy.
export const useChildStore = createChildStore();
