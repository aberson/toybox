import { describe, expect, it, vi } from "vitest";

import {
  BASE_BACKOFF_MS,
  CHILD_TOPICS,
  ChildWsClient,
  computeBackoffMs,
} from "./ws";
import type { Envelope } from "./ws";

describe("child computeBackoffMs", () => {
  it("never returns a non-positive delay", () => {
    for (let i = 0; i < 10; i += 1) {
      const v = computeBackoffMs(i, () => Math.random());
      expect(v).toBeGreaterThan(0);
    }
  });

  it("schedule is monotonically non-decreasing at the median, caps at 30s", () => {
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

  it("respects the +/- 25% jitter envelope", () => {
    const min = computeBackoffMs(2, () => 0);
    const max = computeBackoffMs(2, () => 0.999_999);
    const base = BASE_BACKOFF_MS[2]!;
    expect(min).toBe(Math.round(base * 0.75));
    expect(max).toBe(Math.round(base * (1 + 0.25 * (2 * 0.999_999 - 1))));
    expect(min).toBeLessThan(base);
    expect(max).toBeGreaterThan(base);
  });
});

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
      const payload = typeof data === "string" ? data : JSON.stringify(data);
      sock.onmessage?.call(
        sock as unknown as WebSocket,
        { data: payload } as MessageEvent,
      );
    },
    triggerClose: () => {
      sock.onclose?.call(
        sock as unknown as WebSocket,
        new CloseEvent("close"),
      );
    },
  };
  return sock;
}

function asWs(s: FakeSocket): WebSocket {
  return s as unknown as WebSocket;
}

describe("ChildWsClient", () => {
  it("on open sends auth then subscribe in that order with CHILD_TOPICS", () => {
    const socks: FakeSocket[] = [];
    const client = new ChildWsClient({
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
    expect(second).toEqual({ type: "subscribe", topics: CHILD_TOPICS });
  });

  it("getToken is re-read on reconnect (stale-token regression)", () => {
    let token: string | null = "tok-1";
    const socks: FakeSocket[] = [];
    const client = new ChildWsClient({
      url: "ws://x",
      getToken: () => token,
      onEnvelope: () => undefined,
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
    token = "tok-2";
    socks[0]!.triggerClose();
    expect(socks).toHaveLength(2);
    socks[1]!.triggerOpen();
    const auth = JSON.parse(socks[1]!.sent[0]!);
    expect(auth).toEqual({ type: "auth", token: "tok-2" });
  });

  it("onReconnect fires only on the second open, not the first", () => {
    const socks: FakeSocket[] = [];
    const onReconnect = vi.fn();
    const client = new ChildWsClient({
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
    const client = new ChildWsClient({
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
      rejected: ["bad.topic"],
    });
    expect(onRejected).toHaveBeenCalledTimes(1);
    expect(onRejected).toHaveBeenCalledWith(["bad.topic"]);
  });

  it("uses {} when an envelope payload is JSON null or non-object", () => {
    const seen: Envelope[] = [];
    const socks: FakeSocket[] = [];
    const client = new ChildWsClient({
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
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: null,
      schema_version: 1,
    });
    socks[0]!.triggerMessage({
      topic: "activity.state",
      ts: "2026-05-02T10:00:00Z",
      payload: "string-payload",
      schema_version: 1,
    });
    expect(seen).toHaveLength(2);
    expect(seen[0]!.payload).toEqual({});
    expect(seen[1]!.payload).toEqual({});
  });

  it("after stop(), the old socket cannot fire onEnvelope", () => {
    const seen: Envelope[] = [];
    const socks: FakeSocket[] = [];
    const client = new ChildWsClient({
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
    const socks: FakeSocket[] = [];
    const client = new ChildWsClient({
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
    expect(socks[0]!.sent).toHaveLength(1);
    const msg = JSON.parse(socks[0]!.sent[0]!);
    expect(msg.type).toBe("subscribe");
  });

  it("ping replies with pong", () => {
    const socks: FakeSocket[] = [];
    const client = new ChildWsClient({
      url: "ws://x",
      getToken: () => "tok",
      onEnvelope: () => undefined,
      socketFactory: () => {
        const s = makeFakeSocket();
        socks.push(s);
        return asWs(s);
      },
    });
    client.start();
    socks[0]!.triggerOpen();
    // Drain the auth+subscribe sends so we're testing the ping reply.
    const before = socks[0]!.sent.length;
    socks[0]!.triggerMessage({ type: "ping" });
    expect(socks[0]!.sent).toHaveLength(before + 1);
    const reply = JSON.parse(socks[0]!.sent[before]!);
    expect(reply).toEqual({ type: "pong" });
  });
});
