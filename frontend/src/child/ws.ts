// Child kiosk WebSocket client. Owns one socket; on open sends
// {type:"auth", token} then {type:"subscribe", topics:[...]}, surfaces
// envelopes/replies/state through callbacks, and reconnects with
// 1s,2s,4s,8s,16s,30s,30s... ±25% jitter. The token is fetched fresh
// on every (re)connect via getToken so an expired token doesn't loop.
//
// Mirrors frontend/src/parent/ws.ts deliberately — the kiosk only
// needs the activity.state topic today, but keeping the same
// lifecycle/reconnect/auth surface makes the two clients trivial to
// keep in sync. Topic enum lives at src/toybox/ws/topics.py.

export type WsState = "idle" | "connecting" | "open" | "closed";

export interface Envelope {
  topic: string;
  ts: string;
  payload: Record<string, unknown>;
  schema_version: number;
}

// The kiosk only needs activity.state to drive the active-step view.
// Adding more topics here is cheap; for v1 we keep the surface minimal.
export const CHILD_TOPICS: readonly string[] = ["activity.state"];

export const BASE_BACKOFF_MS: readonly number[] = [
  1_000, 2_000, 4_000, 8_000, 16_000, 30_000,
];
export const MAX_BACKOFF_MS = 30_000;
const JITTER_FRACTION = 0.25;

// Compute the next reconnect delay (ms) for the given attempt index
// (0 = first retry). Schedule doubles to a 30s cap, ±25% jitter. The
// optional `random` argument lets tests pin determinism.
export function computeBackoffMs(
  attempt: number,
  random: () => number = Math.random,
): number {
  const idx = Math.max(0, Math.floor(attempt));
  const base =
    idx < BASE_BACKOFF_MS.length ? BASE_BACKOFF_MS[idx]! : MAX_BACKOFF_MS;
  const jitter = (random() * 2 - 1) * JITTER_FRACTION;
  const result = Math.round(base * (1 + jitter));
  return Math.max(1, result);
}

export interface WsClientOptions {
  url: string;
  // Called on every (re)connect to fetch a fresh token. Returning null
  // skips the auth message; the server will then close the socket.
  getToken: () => string | null;
  topics?: readonly string[];
  onEnvelope: (envelope: Envelope) => void;
  onState?: (state: WsState) => void;
  onRejected?: (rejected: string[]) => void;
  onReconnect?: () => void;
  socketFactory?: (url: string) => WebSocket;
  setTimeoutImpl?: typeof setTimeout;
  clearTimeoutImpl?: typeof clearTimeout;
  random?: () => number;
}

export class ChildWsClient {
  private readonly opts: Required<
    Pick<WsClientOptions, "url" | "getToken" | "onEnvelope">
  > &
    Pick<
      WsClientOptions,
      | "onState"
      | "onRejected"
      | "onReconnect"
      | "socketFactory"
      | "setTimeoutImpl"
      | "clearTimeoutImpl"
      | "random"
    > & { topics: readonly string[] };

  private socket: WebSocket | null = null;
  private state: WsState = "idle";
  private attempt = 0;
  private hasEverConnected = false;
  private stopped = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(options: WsClientOptions) {
    this.opts = {
      url: options.url,
      getToken: options.getToken,
      topics: options.topics ?? CHILD_TOPICS,
      onEnvelope: options.onEnvelope,
      onState: options.onState,
      onRejected: options.onRejected,
      onReconnect: options.onReconnect,
      socketFactory: options.socketFactory,
      setTimeoutImpl: options.setTimeoutImpl,
      clearTimeoutImpl: options.clearTimeoutImpl,
      random: options.random,
    };
  }

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.reconnectTimer !== null) {
      const clear = this.opts.clearTimeoutImpl ?? clearTimeout;
      clear(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket !== null) {
      const sock = this.socket;
      sock.onopen = null;
      sock.onmessage = null;
      sock.onclose = null;
      sock.onerror = null;
      try {
        sock.close();
      } catch {
        // ignore
      }
      this.socket = null;
    }
    this.setState("closed");
  }

  getState(): WsState {
    return this.state;
  }

  private setState(next: WsState): void {
    if (this.state === next) return;
    this.state = next;
    this.opts.onState?.(next);
  }

  private connect(): void {
    this.setState("connecting");
    const factory =
      this.opts.socketFactory ?? ((url: string) => new WebSocket(url));
    let sock: WebSocket;
    try {
      sock = factory(this.opts.url);
    } catch {
      this.handleClose();
      return;
    }
    this.socket = sock;

    sock.onopen = () => {
      this.attempt = 0;
      // Server contract: client sends {"type":"auth", token} as the
      // first message. Pull a fresh token each open so a stale value
      // from constructor capture isn't reused after TTL.
      const token = this.opts.getToken();
      try {
        if (typeof token === "string" && token.length > 0) {
          sock.send(JSON.stringify({ type: "auth", token }));
        }
        sock.send(
          JSON.stringify({ type: "subscribe", topics: this.opts.topics }),
        );
      } catch {
        // a send failure will surface as onclose
      }
      this.setState("open");
      const wasReconnect = this.hasEverConnected;
      this.hasEverConnected = true;
      if (wasReconnect) {
        this.opts.onReconnect?.();
      }
    };

    sock.onmessage = (event: MessageEvent) => {
      const data = event.data;
      if (typeof data !== "string") return;
      let parsed: unknown;
      try {
        parsed = JSON.parse(data);
      } catch {
        return;
      }
      if (typeof parsed !== "object" || parsed === null) return;
      const rec = parsed as Record<string, unknown>;
      const kind = rec["type"];
      if (kind === "ready" || kind === "subscribed") {
        const rejected = rec["rejected"];
        if (Array.isArray(rejected) && rejected.length > 0) {
          const strings = rejected.filter(
            (r): r is string => typeof r === "string",
          );
          if (strings.length > 0) {
            this.opts.onRejected?.(strings);
          }
        }
        return;
      }
      if (kind === "ping") {
        try {
          sock.send(JSON.stringify({ type: "pong" }));
        } catch {
          // ignore
        }
        return;
      }
      if (typeof rec["topic"] === "string" && typeof rec["ts"] === "string") {
        const rawPayload = rec["payload"];
        const payload: Record<string, unknown> =
          typeof rawPayload === "object" && rawPayload !== null
            ? (rawPayload as Record<string, unknown>)
            : {};
        const env: Envelope = {
          topic: String(rec["topic"]),
          ts: String(rec["ts"]),
          payload,
          schema_version: Number(rec["schema_version"] ?? 1),
        };
        this.opts.onEnvelope(env);
      }
    };

    sock.onerror = () => {
      // Let onclose drive reconnect.
    };

    sock.onclose = () => {
      this.handleClose();
    };
  }

  private handleClose(): void {
    this.socket = null;
    this.setState("closed");
    if (this.stopped) return;
    const setT = this.opts.setTimeoutImpl ?? setTimeout;
    const delay = computeBackoffMs(
      this.attempt,
      this.opts.random ?? Math.random,
    );
    this.attempt += 1;
    this.reconnectTimer = setT(() => {
      this.reconnectTimer = null;
      if (!this.stopped) this.connect();
    }, delay);
  }
}
