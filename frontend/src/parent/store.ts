// Zustand store for the parent UI. Holds auth state, capability
// reason, mic indicator, the active activity, ws connection state, and
// a capped toast queue. Reducers are exported as standalone pure
// functions so vitest can exercise them without spinning up zustand.

import { create } from "zustand";

import type {
  Activity,
  HealthResponse,
  ParentTokenResponse,
  ToyActionRow,
  ToyActionStatus,
  VersionConflictBody,
} from "./api";
import type { Envelope, WsState } from "./ws";

export type MicState = "paused" | "capturing" | "error";

export interface Toast {
  id: number;
  kind: "info" | "warning" | "error";
  message: string;
}

// Cap the toast queue so a flapping reconnect or a 409 storm can't
// grow the array without bound. Oldest entries drop first.
export const MAX_TOASTS = 20;

export interface ParentState {
  token: string | null;
  tokenExpiresAt: number | null;
  capabilityReason: string | null;
  micState: MicState;
  activity: Activity | null;
  wsState: WsState;
  toasts: Toast[];
  // monotonically increasing counter so toast IDs are stable
  nextToastId: number;
  // Phase F Step F8: per-toy slot status map. Outer key is the toy_id;
  // inner key is the slot string. The grid component reads this to
  // render live status badges as the worker emits ``toy_actions``
  // envelopes. ``setToyActions`` (REST seed) wholesale replaces the
  // inner map for one toy; ``applyToyActionEnvelope`` (WS push)
  // merges one slot at a time.
  toyActions: Record<string, Record<string, ToyActionRow>>;
}

export const INITIAL_STATE: ParentState = {
  token: null,
  tokenExpiresAt: null,
  capabilityReason: null,
  micState: "paused",
  activity: null,
  wsState: "idle",
  toasts: [],
  nextToastId: 1,
  toyActions: {},
};

export function setToken(
  state: ParentState,
  resp: ParentTokenResponse,
): ParentState {
  return {
    ...state,
    token: resp.token,
    tokenExpiresAt: resp.expires_at,
  };
}

export function setHealth(
  state: ParentState,
  health: HealthResponse,
): ParentState {
  return { ...state, capabilityReason: health.capability_reason };
}

export function setMicState(state: ParentState, mic: MicState): ParentState {
  return { ...state, micState: mic };
}

export function setWsState(state: ParentState, ws: WsState): ParentState {
  return { ...state, wsState: ws };
}

export function setActivity(
  state: ParentState,
  activity: Activity | null,
): ParentState {
  return { ...state, activity };
}

// Apply an incoming ws envelope to the store. Only `activity.state`
// envelopes mutate the active activity today; other topics route to
// dedicated reducers (capability via system, etc.).
export function applyEnvelope(
  state: ParentState,
  env: Envelope,
): ParentState {
  if (env.topic === "activity.state") {
    const candidate = env.payload as unknown as Activity;
    if (
      typeof candidate?.id === "string" &&
      typeof candidate?.state === "string" &&
      typeof candidate?.version === "number"
    ) {
      // Newer-version-of-same-id wins; otherwise replace if no current
      // activity or the current one is in a terminal state.
      const cur = state.activity;
      const incomingIsActive = !isTerminalState(candidate.state);
      if (cur === null) {
        return incomingIsActive ? { ...state, activity: candidate } : state;
      }
      if (cur.id === candidate.id) {
        if (candidate.version >= cur.version) {
          return { ...state, activity: candidate };
        }
        return state;
      }
      // Different id: take the new one if it's active, ignore terminal updates
      if (incomingIsActive) {
        return { ...state, activity: candidate };
      }
      return state;
    }
  }
  if (env.topic === "system") {
    const reason = env.payload["capability_reason"];
    if (typeof reason === "string" || reason === null) {
      return { ...state, capabilityReason: reason ?? null };
    }
  }
  if (env.topic === "toy_actions") {
    return applyToyActionEnvelope(state, env);
  }
  return state;
}

// Phase F Step F8: REST-seed helper. The grid loads the initial 10
// rows via ``api.listToyActions``; this reducer drops them into the
// per-toy slot map so a ws envelope arriving mid-render (e.g. a
// ``running`` push for a slot the REST snapshot reported as
// ``queued``) can latest-wins-merge over the seed without flicker.
//
// Replaces the existing inner map for ``toyId`` wholesale — REST is
// always the freshest authoritative snapshot at the moment it
// returns. Once seeded, ``applyToyActionEnvelope`` mutates per-slot.
export function setToyActions(
  state: ParentState,
  toyId: string,
  rows: ToyActionRow[],
): ParentState {
  const inner: Record<string, ToyActionRow> = {};
  for (const row of rows) {
    inner[row.slot] = row;
  }
  return {
    ...state,
    toyActions: { ...state.toyActions, [toyId]: inner },
  };
}

// Phase F Step F8: drop a toy's inner map entirely. Used when the
// parent archives a toy (the grid hides on archived rows; clearing
// the slot map keeps memory bounded across many archive cycles).
export function clearToyActions(
  state: ParentState,
  toyId: string,
): ParentState {
  if (!(toyId in state.toyActions)) return state;
  const next = { ...state.toyActions };
  delete next[toyId];
  return { ...state, toyActions: next };
}

// Phase F Step F8: per-slot envelope merge. Latest envelope wins —
// the worker emits ``queued`` → ``running`` → ``done`` or
// ``failed`` in order, so we always take the incoming row and
// overwrite the previous slot entry. We DO carry over the previous
// row's ``seed`` and ``image_path`` when the envelope omits them,
// because the worker's intermediate ``running`` envelope doesn't
// re-emit fields that haven't changed.
//
// The envelope payload shape (per ``Topic.toy_actions`` docstring):
//   {toy_id, slot, status, image_path?, error?}
// Anything we can't parse defensively returns the previous state
// unchanged.
export function applyToyActionEnvelope(
  state: ParentState,
  env: Envelope,
): ParentState {
  if (env.topic !== "toy_actions") return state;
  const payload = env.payload;
  const toyId = payload["toy_id"];
  const slot = payload["slot"];
  const rawStatus = payload["status"];
  if (
    typeof toyId !== "string" ||
    typeof slot !== "string" ||
    typeof rawStatus !== "string"
  ) {
    return state;
  }
  if (!isToyActionStatus(rawStatus)) {
    return state;
  }
  const status: ToyActionStatus = rawStatus;
  const incomingImage =
    typeof payload["image_path"] === "string"
      ? (payload["image_path"] as string)
      : null;
  const incomingError =
    typeof payload["error"] === "string"
      ? (payload["error"] as string)
      : null;
  const incomingSeed =
    typeof payload["seed"] === "number" ? (payload["seed"] as number) : null;
  const incomingUpdatedAt =
    typeof payload["updated_at"] === "string"
      ? (payload["updated_at"] as string)
      : env.ts;

  const prevInner = state.toyActions[toyId] ?? {};
  const prevRow: ToyActionRow | undefined = prevInner[slot];
  // Carry-over: an intermediate ``running`` envelope shouldn't drop
  // a previously known seed/image. Latest-wins per FIELD, falling
  // back to the previous row when the envelope omits the field.
  const merged: ToyActionRow = {
    toy_id: toyId,
    slot,
    status,
    image_path: incomingImage ?? prevRow?.image_path ?? null,
    seed: incomingSeed ?? prevRow?.seed ?? null,
    error_msg: status === "failed" ? incomingError : null,
    updated_at: incomingUpdatedAt,
  };
  return {
    ...state,
    toyActions: {
      ...state.toyActions,
      [toyId]: { ...prevInner, [slot]: merged },
    },
  };
}

const TOY_ACTION_STATUSES: ReadonlySet<string> = new Set([
  "queued",
  "running",
  "done",
  "failed",
  "superseded",
  "not_started",
]);

function isToyActionStatus(value: string): value is ToyActionStatus {
  return TOY_ACTION_STATUSES.has(value);
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

export function pushToast(
  state: ParentState,
  kind: Toast["kind"],
  message: string,
): ParentState {
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

export function dismissToast(state: ParentState, id: number): ParentState {
  return { ...state, toasts: state.toasts.filter((t) => t.id !== id) };
}

export function applyVersionConflict(
  state: ParentState,
  conflict: VersionConflictBody,
  fresh: Activity | null,
): ParentState {
  const next = fresh !== null ? { ...state, activity: fresh } : state;
  const message = `Version conflict (now v${conflict.current_version}, state=${conflict.current_state}). Panel was refreshed.`;
  return pushToast(next, "warning", message);
}

export function applyRejectedTopics(
  state: ParentState,
  rejected: string[],
): ParentState {
  if (rejected.length === 0) return state;
  return pushToast(
    state,
    "warning",
    `Subscription rejected for topics: ${rejected.join(", ")}`,
  );
}

// Reconnect-resync helper: only adopt the REST refetch if it isn't a
// stale read (e.g. the ws envelope already applied a newer version
// while the GET was in flight). Mirrors the kiosk version guard.
export function applyReconnectResync(
  state: ParentState,
  fresh: Activity | null,
): ParentState {
  if (fresh === null) return state;
  const cur = state.activity;
  if (cur !== null && cur.id === fresh.id && fresh.version < cur.version) {
    return state;
  }
  return { ...state, activity: fresh };
}

// Mutation-result helper: drop the response when a newer envelope has
// already arrived for the same activity. Mirrors the kiosk version
// guard so an in-flight mutation can't regress in-memory state when
// the ws stream pushes a fresher version mid-round-trip.
export function applyMutationResult(
  state: ParentState,
  fresh: Activity,
): ParentState {
  const cur = state.activity;
  if (cur !== null && cur.id === fresh.id && fresh.version < cur.version) {
    return state;
  }
  return { ...state, activity: fresh };
}

export interface ParentStore extends ParentState {
  setToken: (resp: ParentTokenResponse) => void;
  setHealth: (h: HealthResponse) => void;
  setMicState: (mic: MicState) => void;
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
  // Phase F Step F8: per-toy action grid wiring.
  setToyActions: (toyId: string, rows: ToyActionRow[]) => void;
  clearToyActions: (toyId: string) => void;
}

export function createParentStore(initial: ParentState = INITIAL_STATE) {
  return create<ParentStore>((set) => ({
    ...initial,
    setToken: (resp) => set((s) => setToken(s, resp)),
    setHealth: (h) => set((s) => setHealth(s, h)),
    setMicState: (mic) => set((s) => setMicState(s, mic)),
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
    setToyActions: (toyId, rows) =>
      set((s) => setToyActions(s, toyId, rows)),
    clearToyActions: (toyId) => set((s) => clearToyActions(s, toyId)),
  }));
}

// Process-singleton store the components import directly. Tests use
// `createParentStore()` to get an isolated copy.
export const useParentStore = createParentStore();
