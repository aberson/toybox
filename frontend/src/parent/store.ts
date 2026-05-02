// Zustand store for the parent UI. Holds auth state, capability
// reason, mic indicator, the active activity, ws connection state, and
// a capped toast queue. Reducers are exported as standalone pure
// functions so vitest can exercise them without spinning up zustand.

import { create } from "zustand";

import type {
  Activity,
  HealthResponse,
  ParentTokenResponse,
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
  return state;
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
  pushToast: (kind: Toast["kind"], message: string) => void;
  dismissToast: (id: number) => void;
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
    pushToast: (kind, message) => set((s) => pushToast(s, kind, message)),
    dismissToast: (id) => set((s) => dismissToast(s, id)),
  }));
}

// Process-singleton store the components import directly. Tests use
// `createParentStore()` to get an isolated copy.
export const useParentStore = createParentStore();
