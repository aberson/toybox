// Zustand store for the parent UI. Holds auth state, capability
// reason, mic indicator, the play-queue (``proposedList`` + ``active``)
// slots, ws connection state, and a capped toast queue. Reducers are
// exported as standalone pure functions so vitest can exercise them
// without spinning up zustand.
//
// Phase J step J7: the legacy single-slot ``activity`` was deleted.
// Consumers now read ``state.active`` for the running card and
// ``state.proposedList`` for the suggestion queue. The new-slot reducer
// helper ``applyEnvelopeToNewSlots`` (inlined into ``applyEnvelope``
// below) owns all routing — there is no longer a back-compat mirror.

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
  // Phase J step J6/J7: play-queue slots. ``proposedList`` is the
  // scrolling suggestion queue (newest first); ``active`` is the
  // currently-playing card. The legacy single-slot ``activity`` was
  // removed in J7 — all consumers now read these two slots directly.
  proposedList: Activity[];
  active: Activity | null;
}

export const INITIAL_STATE: ParentState = {
  token: null,
  tokenExpiresAt: null,
  capabilityReason: null,
  micState: "paused",
  wsState: "idle",
  toasts: [],
  nextToastId: 1,
  toyActions: {},
  proposedList: [],
  active: null,
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

export function setActive(
  state: ParentState,
  active: Activity | null,
): ParentState {
  return { ...state, active };
}

// Apply an incoming ws envelope to the store. ``activity.state``
// envelopes route into the play-queue slots (``proposedList`` +
// ``active``) via ``applyEnvelopeToNewSlots``; other topics route to
// dedicated reducers (capability via system, etc.).
//
// Phase J step J7: the legacy single-slot ``activity`` branch was
// removed — only the new-slot routing remains:
//
//   * state === "proposed"        → upsert into proposedList (newer
//                                    version wins), clear active if id
//                                    matches.
//   * state ∈ approved/running/   → set active (version-guarded),
//     paused/completed              remove from proposedList by id.
//   * state ∈ dismissed/ended/    → remove from proposedList, clear
//     didnt_work                    active if id matches.
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
      return applyEnvelopeToNewSlots(state, candidate);
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

// Phase J step J6/J7: fold an ``activity.state`` envelope into the
// ``proposedList`` + ``active`` slots. Version-guarded per slot so
// out-of-order arrivals don't regress either slot.
function applyEnvelopeToNewSlots(
  state: ParentState,
  candidate: Activity,
): ParentState {
  const id = candidate.id;
  if (candidate.state === "proposed") {
    // Upsert into proposedList; ignore older-version-of-same-id; if
    // the row was the live ``active`` (e.g. regenerate flipped it
    // back to proposed), clear active.
    const existing = state.proposedList.find((row) => row.id === id);
    let nextList: Activity[];
    if (existing !== undefined) {
      if (candidate.version < existing.version) {
        // Stale envelope: ignore the proposed-slot mutation but still
        // run the active-clearing branch below in case the row was
        // also living in active (unlikely but defensive).
        nextList = state.proposedList;
      } else {
        nextList = state.proposedList.map((row) =>
          row.id === id ? candidate : row,
        );
      }
    } else {
      // New row — push to the head (newest-first ordering).
      nextList = [candidate, ...state.proposedList];
    }
    const nextActive =
      state.active !== null && state.active.id === id ? null : state.active;
    return { ...state, proposedList: nextList, active: nextActive };
  }

  if (
    candidate.state === "approved" ||
    candidate.state === "running" ||
    candidate.state === "paused" ||
    candidate.state === "completed"
  ) {
    // Move into active (with version guard) and drop from proposedList
    // if present.
    const cur = state.active;
    let nextActive: Activity | null;
    if (cur === null || cur.id !== id) {
      nextActive = candidate;
    } else if (candidate.version >= cur.version) {
      nextActive = candidate;
    } else {
      // Stale envelope for the same-id active: keep current.
      nextActive = cur;
    }
    const nextList = state.proposedList.filter((row) => row.id !== id);
    return { ...state, active: nextActive, proposedList: nextList };
  }

  if (
    candidate.state === "dismissed" ||
    candidate.state === "ended" ||
    candidate.state === "didnt_work"
  ) {
    const nextList = state.proposedList.filter((row) => row.id !== id);
    const nextActive =
      state.active !== null && state.active.id === id ? null : state.active;
    return { ...state, proposedList: nextList, active: nextActive };
  }

  return state;
}

// Phase J step J6: remove a proposed row by id (e.g. after a TTL
// expiry on the UI side). No-op when id is not present in the list.
// Leaves ``active`` untouched.
export function applyProposedExpired(
  state: ParentState,
  id: string,
): ParentState {
  const filtered = state.proposedList.filter((row) => row.id !== id);
  if (filtered.length === state.proposedList.length) return state;
  return { ...state, proposedList: filtered };
}

// Phase J step J6: the "switch" gesture — end the current active and
// immediately approve a queue item. The reducer is intentionally
// tolerant of null on both arguments: ``endResult`` may be null when
// there was no prior active to end, and ``approveResult`` may be null
// when the approve call failed mid-switch (active stays cleared).
//
// Net behavior:
//   1. Clear ``active`` unconditionally (the prior card is done).
//   2. If ``approveResult`` is non-null, set active to it and remove
//      its id from proposedList.
//
// ``endResult`` is accepted for symmetry with the call site (which
// passes the end-mutation response so other reducers could opt in
// later), but this reducer does not mutate state from it today.
export function applySwitch(
  state: ParentState,
  // endResult is currently consumed only for symmetry with the
  // caller; the prefix-underscore is the TS-recommended way to tell
  // ``noUnusedParameters`` we deliberately ignored it.
  _endResult: Activity | null,
  approveResult: Activity | null,
): ParentState {
  const cleared: ParentState = { ...state, active: null };
  if (approveResult === null) return cleared;
  const filtered = cleared.proposedList.filter(
    (row) => row.id !== approveResult.id,
  );
  return { ...cleared, active: approveResult, proposedList: filtered };
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
  // J7: route the refetched row through the new-slot helper so a
  // conflict response correctly lands in proposedList / active based
  // on the row's current state. Pre-J7 this wrote to a legacy single
  // slot; now there is no such slot to write to.
  const next = fresh !== null ? applyEnvelopeToNewSlots(state, fresh) : state;
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
//
// J7: routes through the same new-slot helper as envelopes/mutations
// so the refetch lands in proposedList or active as the row's state
// dictates — the staleness guards inside ``applyEnvelopeToNewSlots``
// already drop an older-version refetch over a newer in-memory row.
export function applyReconnectResync(
  state: ParentState,
  fresh: Activity | null,
): ParentState {
  if (fresh === null) return state;
  return applyEnvelopeToNewSlots(state, fresh);
}

// Mutation-result helper: drop the response when a newer envelope has
// already arrived for the same activity. Mirrors the kiosk version
// guard so an in-flight mutation can't regress in-memory state when
// the ws stream pushes a fresher version mid-round-trip.
//
// J7: routes through the new-slot helper so a mutation result lands
// in active or proposedList according to its current state (e.g. a
// regenerate response that arrives with state=proposed correctly
// returns to the queue rather than the active slot).
export function applyMutationResult(
  state: ParentState,
  fresh: Activity,
): ParentState {
  return applyEnvelopeToNewSlots(state, fresh);
}

export interface ParentStore extends ParentState {
  setToken: (resp: ParentTokenResponse) => void;
  setHealth: (h: HealthResponse) => void;
  setMicState: (mic: MicState) => void;
  setWsState: (ws: WsState) => void;
  setActive: (a: Activity | null) => void;
  applyEnvelope: (env: Envelope) => void;
  applyVersionConflict: (
    conflict: VersionConflictBody,
    fresh: Activity | null,
  ) => void;
  applyRejectedTopics: (rejected: string[]) => void;
  applyReconnectResync: (fresh: Activity | null) => void;
  applyMutationResult: (fresh: Activity) => void;
  // Phase J step J9: switch-confirm flow routes both the end-old and
  // approve-new mutation results through one reducer so the active slot
  // never momentarily holds a stale row between the two operations.
  applySwitch: (
    endResult: Activity | null,
    approveResult: Activity | null,
  ) => void;
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
    setActive: (a) => set((s) => setActive(s, a)),
    applyEnvelope: (env) => set((s) => applyEnvelope(s, env)),
    applyVersionConflict: (conflict, fresh) =>
      set((s) => applyVersionConflict(s, conflict, fresh)),
    applyRejectedTopics: (rejected) =>
      set((s) => applyRejectedTopics(s, rejected)),
    applyReconnectResync: (fresh) =>
      set((s) => applyReconnectResync(s, fresh)),
    applyMutationResult: (fresh) =>
      set((s) => applyMutationResult(s, fresh)),
    applySwitch: (endResult, approveResult) =>
      set((s) => applySwitch(s, endResult, approveResult)),
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
