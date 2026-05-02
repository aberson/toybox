import { describe, expect, it, vi } from "vitest";

import {
  BASE_BACKOFF_MS,
  computeBackoffMs,
  PARENT_TOPICS,
  ParentWsClient,
} from "./ws";
import type { Envelope } from "./ws";

describe("computeBackoffMs", () => {
  it("never returns a non-positive delay", () => {
    for (let i = 0; i < 10; i += 1) {
      const v = computeBackoffMs(i, () => Math.random());
      expect(v).toBeGreaterThan(0);
    }
  });

  it("schedule is monotonically non-decreasing at the median, caps at 30s", () => {
    // random=0.5 => zero-jitter, so this also covers the "no shift at
    // median" and "caps past schedule end" assertions.
    const med = (n: number): number => computeBackoffMs(n, () => 0.5);
    expect(med(0)).toBe(1_000);
    expect(med(1)).toBe(2_000);
    expect(med(2)).toBe(4_000);
    expect(med(3)).toBe(8_000);
    expect(med(4)).toBe(16_000);
    expect(med(5)).toBe(30_000);
    expect(med(6)).toBe(30_000);
    expect(med(99)).toBe(30_000);
  });

  it("respects the +/- 25% jitter envelope (exact bounds, no slop)", () => {
    // random=0 => (0*2-1)=-1 => 1 - 0.25 = 0.75
    const min = computeBackoffMs(2, () => 0);
    // random=just-under-1 => +1 => 1 + 0.25 = 1.25
    const max = computeBackoffMs(2, () => 0.999_999);
    const base = BASE_BACKOFF_MS[2]!;
    // Exact rounded bounds. Math.round(4000*0.75) = 3000;
    // Math.round(4000*1.249999) = 5000.
    expect(min).toBe(Math.round(base * 0.75));
    expect(max).toBe(Math.round(base * (1 + 0.25 * (2 * 0.999_999 - 1))));
    // And the bounds must straddle the base — a sign-inversion regression
    // would put both above (or both below) base.
    expect(min).toBeLessThan(base);
    expect(max).toBeGreaterThan(base);
  });
});

// Minimal WebSocket double for tests. Implements only what the client
// touches: send/close + the four handler slots. Tests use the
// `triggerXxx` helpers to push lifecycle events into the client.
interface FakeSocket {
  onopen: WebSocket["onopen"];
  onmessage: WebSocket["onmessage"];
  onclose: WebSocket["onclose"];
  onerror: WebSocket["onerror"];
  send: (data: string) => void;
  close: () => void;
  triggerOpen(): void;
  triggerMessage(data: unknown): void;
  triggerClose(): void;
  sent: string[];
  closed: boolean;
}

function makeFakeSocket(): FakeSocket {
  const sent: string[] = [];
  const sock: FakeSocket = {
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
    sent,
    closed: false,
    send: (data: string) => {
      sent.push(data);
    },
    close: () => {
      sock.closed = true;
    },
    triggerOpen: () => {
      sock.onopen?.call(sock as unknown as WebSocket, new Event("open"));
    },
    triggerMessage: (data: unknown) => {
      const payload =
        typeof data === "string" ? data : JSON.stringify(data);
      sock.onmessage?.call(
        sock as unknown as WebSocket,
        { data: payload } as MessageEvent,
      );
    },
    triggerClose: () => {
      sock.onclose?.call(sock as unknown as WebSocket, new CloseEvent("close"));
    },
  };
  return sock;
}

// The ParentWsClient signature wants a real WebSocket; the runtime
// only exercises the subset our FakeSocket supplies.
function asWs(s: FakeSocket): WebSocket {
  return s as unknown as WebSocket;
}

describe("ParentWsClient", () => {
  it("on open sends auth then subscribe in that exact order", () => {
    const socks: FakeSocket[] = [];
    const client = new ParentWsClient({
      url: "ws://x",
      getToken: () => "tok-1",
      onEnvelope: () => undefined,
      socketFactory: () => {
        const s = makeFakeSocket();
        socks.push(s);
        return asWs(s);
      },
    });
    client.start();
    socks[0]!.triggerOpen();
    expect(socks[0]!.sent).toHaveLength(2);
    const first = JSON.parse(socks[0]!.sent[0]!);
    const second = JSON.parse(socks[0]!.sent[1]!);
    expect(first).toEqual({ type: "auth", token: "tok-1" });
    expect(second).toEqual({ type: "subscribe", topics: PARENT_TOPICS });
  });

  it("getToken is re-read on every reconnect (stale-token regression)", () => {
    let token: string | null = "tok-1";
    const socks: FakeSocket[] = [];
    const client = new ParentWsClient({
      url: "ws://x",
      getToken: () => token,
      onEnvelope: () => undefined,
      socketFactory: () => {
        const s = makeFakeSocket();
        socks.push(s);
        return asWs(s);
      },
      setTimeoutImpl: ((fn: () => void) => {
        // Run reconnect immediately for tests.
        fn();
        return 0 as unknown as ReturnType<typeof setTimeout>;
      }) as typeof setTimeout,
      random: () => 0.5,
    });
    client.start();
    socks[0]!.triggerOpen();
    // Now rotate the token and force a reconnect.
    token = "tok-2";
    socks[0]!.triggerClose();
    // The reconnect will have created a second socket. Drive its open.
    expect(socks).toHaveLength(2);
    socks[1]!.triggerOpen();
    const auth = JSON.parse(socks[1]!.sent[0]!);
    expect(auth).toEqual({ type: "auth", token: "tok-2" });
  });

  it("onReconnect fires only on the second open, not the first", () => {
    const socks: FakeSocket[] = [];
    const onReconnect = vi.fn();
    const client = new ParentWsClient({
      url: "ws://x",
      getToken: () => "tok",
      onEnvelope: () => undefined,
      onReconnect,
      socketFactory: () => {
        const s = makeFakeSocket();
        socks.push(s);
        return asWs(s);
      },
      setTimeoutImpl: ((fn: () => void) => {
        fn();
        return 0 as unknown as ReturnType<typeof setTimeout>;
      }) as typeof setTimeout,
      random: () => 0.5,
    });
    client.start();
    socks[0]!.triggerOpen();
    expect(onReconnect).not.toHaveBeenCalled();
    socks[0]!.triggerClose();
    socks[1]!.triggerOpen();
    expect(onReconnect).toHaveBeenCalledTimes(1);
  });

  it("surfaces rejected topics on subscribed reply via onRejected", () => {
    const socks: FakeSocket[] = [];
    const onRejected = vi.fn();
    const client = new ParentWsClient({
      url: "ws://x",
      getToken: () => "tok",
      onEnvelope: () => undefined,
      onRejected,
      socketFactory: () => {
        const s = makeFakeSocket();
        socks.push(s);
        return asWs(s);
      },
    });
    client.start();
    socks[0]!.triggerOpen();
    socks[0]!.triggerMessage({
      type: "subscribed",
      topics: ["activity.state"],
      rejected: ["bad.topic", "also.bad"],
    });
    expect(onRejected).toHaveBeenCalledTimes(1);
    expect(onRejected).toHaveBeenCalledWith(["bad.topic", "also.bad"]);
  });

  it("does not crash and uses {} when an envelope payload is JSON null", () => {
    // The previous `?? {}` only caught undefined. JSON null bypassed
    // the cast and the store crashed reading payload["capability_reason"].
    const seen: Envelope[] = [];
    const socks: FakeSocket[] = [];
    const client = new ParentWsClient({
      url: "ws://x",
      getToken: () => "tok",
      onEnvelope: (env) => {
        seen.push(env);
      },
      socketFactory: () => {
        const s = makeFakeSocket();
        socks.push(s);
        return asWs(s);
      },
    });
    client.start();
    socks[0]!.triggerOpen();
    socks[0]!.triggerMessage({
      topic: "system",
      ts: "2026-05-02T10:00:00Z",
      payload: null,
      schema_version: 1,
    });
    expect(seen).toHaveLength(1);
    expect(seen[0]!.payload).toEqual({});
    // And the resulting envelope is safe to read keys from.
    expect(() => seen[0]!.payload["anything"]).not.toThrow();
  });

  it("non-object payloads (string, number) coerce to {} too", () => {
    const seen: Envelope[] = [];
    const socks: FakeSocket[] = [];
    const client = new ParentWsClient({
      url: "ws://x",
      getToken: () => "tok",
      onEnvelope: (env) => {
        seen.push(env);
      },
      socketFactory: () => {
        const s = makeFakeSocket();
        socks.push(s);
        return asWs(s);
      },
    });
    client.start();
    socks[0]!.triggerOpen();
    socks[0]!.triggerMessage({
      topic: "system",
      ts: "2026-05-02T10:00:00Z",
      payload: "garbage",
      schema_version: 1,
    });
    socks[0]!.triggerMessage({
      topic: "system",
      ts: "2026-05-02T10:00:00Z",
      payload: 42,
      schema_version: 1,
    });
    expect(seen).toHaveLength(2);
    expect(seen[0]!.payload).toEqual({});
    expect(seen[1]!.payload).toEqual({});
  });

  it("after stop(), the old socket cannot fire onEnvelope", () => {
    // Regression: in-flight onmessage callbacks fired AFTER close() if
    // we hadn't detached the handlers first.
    const seen: Envelope[] = [];
    const socks: FakeSocket[] = [];
    const client = new ParentWsClient({
      url: "ws://x",
      getToken: () => "tok",
      onEnvelope: (env) => {
        seen.push(env);
      },
      socketFactory: () => {
        const s = makeFakeSocket();
        socks.push(s);
        return asWs(s);
      },
    });
    client.start();
    socks[0]!.triggerOpen();
    client.stop();
    // After stop, handlers should be nulled so even if a queued
    // onmessage fires, the user callback is unreachable.
    socks[0]!.triggerMessage({
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: { id: "x", state: "running", version: 1 },
      schema_version: 1,
    });
    expect(seen).toHaveLength(0);
    expect(socks[0]!.closed).toBe(true);
  });

  it("getToken returning null means no auth message but still subscribe", () => {
    // Documented Phase A behavior: a missing token is silent at the
    // client; the server will close the socket. We assert the client
    // does not throw and does send the subscribe frame.
    const socks: FakeSocket[] = [];
    const client = new ParentWsClient({
      url: "ws://x",
      getToken: () => null,
      onEnvelope: () => undefined,
      socketFactory: () => {
        const s = makeFakeSocket();
        socks.push(s);
        return asWs(s);
      },
    });
    client.start();
    socks[0]!.triggerOpen();
    // Subscribe should still have been sent (length === 1). No auth.
    expect(socks[0]!.sent).toHaveLength(1);
    const msg = JSON.parse(socks[0]!.sent[0]!);
    expect(msg.type).toBe("subscribe");
  });
});
