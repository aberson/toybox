// Step 21 left the kiosk with `window.__TOYBOX_KIOSK_PIN__` as the only
// PIN source. That variable doesn't survive a page reload, so there was
// no working dev path to configure the kiosk PIN — DevTools assignments
// vanish before the next mount runs the bootstrap effect. The bootstrap
// now also reads `localStorage["toybox.kiosk.pin"]` as a durable
// fallback. The window var still wins because the test harness sets it
// pre-mount and existing flows depend on that precedence.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const STORAGE_KEY = "toybox.kiosk.pin";

interface FetchCall {
  url: string;
  init?: RequestInit;
}

interface FetchStubOptions {
  authStatus?: number;
  authBody?: unknown;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  delete (window as unknown as { __TOYBOX_KIOSK_PIN__?: string })
    .__TOYBOX_KIOSK_PIN__;
});

function stubFetchCapturingAuthCalls(
  options: FetchStubOptions = {},
): { calls: FetchCall[] } {
  const calls: FetchCall[] = [];
  const handler = async (
    input: string | URL | Request,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ url, init });
    if (url.endsWith("/api/auth/parent")) {
      const status = options.authStatus ?? 200;
      const body =
        options.authBody ??
        (status === 200
          ? {
              token: "tok-123",
              expires_at: 4102444800,
              subject: { kind: "parent" },
            }
          : { detail: { code: "pin_invalid", attempts_remaining: 4 } });
      return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    // Anything else (e.g. ws upgrades go through a different path; activity
    // refetches don't fire without an envelope) is unexpected for these
    // tests — surface it as 404 so a stray call shows up clearly.
    return new Response("", { status: 404 });
  };
  const mock = vi.fn(handler) as unknown as Mock;
  vi.stubGlobal("fetch", mock);
  return { calls };
}

function findAuthCall(calls: FetchCall[]): FetchCall | undefined {
  return calls.find((c) => c.url.endsWith("/api/auth/parent"));
}

function readToastsText(container: Element): string {
  return container.querySelector('[data-testid="toasts"]')?.textContent ?? "";
}

function pinPromptVisible(container: Element): boolean {
  return container.querySelector('[data-testid="kiosk-pin-prompt"]') !== null;
}

describe("Child kiosk bootstrap PIN sources", () => {
  it("uses window.__TOYBOX_KIOSK_PIN__ when set (test-harness path)", async () => {
    (
      window as unknown as { __TOYBOX_KIOSK_PIN__: string }
    ).__TOYBOX_KIOSK_PIN__ = "1357";
    const { calls } = stubFetchCapturingAuthCalls();

    render(<App />);

    await waitFor(() => {
      const authCall = findAuthCall(calls);
      expect(authCall).toBeTruthy();
      expect(JSON.parse(String(authCall!.init?.body))).toEqual({ pin: "1357" });
    });
  });

  it("falls back to localStorage when window var is unset", async () => {
    window.localStorage.setItem(STORAGE_KEY, "2468");
    const { calls } = stubFetchCapturingAuthCalls();

    render(<App />);

    await waitFor(() => {
      const authCall = findAuthCall(calls);
      expect(authCall).toBeTruthy();
      expect(JSON.parse(String(authCall!.init?.body))).toEqual({ pin: "2468" });
    });
  });

  it("window var takes precedence over localStorage", async () => {
    window.localStorage.setItem(STORAGE_KEY, "9999");
    (
      window as unknown as { __TOYBOX_KIOSK_PIN__: string }
    ).__TOYBOX_KIOSK_PIN__ = "1357";
    const { calls } = stubFetchCapturingAuthCalls();

    render(<App />);

    await waitFor(() => {
      const authCall = findAuthCall(calls);
      expect(authCall).toBeTruthy();
      expect(JSON.parse(String(authCall!.init?.body))).toEqual({ pin: "1357" });
    });
  });

  it("renders the PIN prompt and skips the network when no PIN is configured", async () => {
    const { calls } = stubFetchCapturingAuthCalls();

    const result = render(<App />);

    await waitFor(() => {
      expect(pinPromptVisible(result.container)).toBe(true);
    });
    // No auth fetch should have fired — the form must come up before any 422.
    expect(findAuthCall(calls)).toBeUndefined();
    // And no toast — the prompt is the actionable surface.
    expect(readToastsText(result.container)).toBe("");
  });

  it("ignores localStorage values that don't match the 4-12 digit format", async () => {
    window.localStorage.setItem(STORAGE_KEY, "abc");
    const { calls } = stubFetchCapturingAuthCalls();

    const result = render(<App />);

    await waitFor(() => {
      expect(pinPromptVisible(result.container)).toBe(true);
    });
    expect(findAuthCall(calls)).toBeUndefined();
  });

  it("submitting a valid PIN saves to localStorage and re-runs the bootstrap", async () => {
    const { calls } = stubFetchCapturingAuthCalls();

    const result = render(<App />);

    await waitFor(() => {
      expect(pinPromptVisible(result.container)).toBe(true);
    });

    const input = screen.getByTestId(
      "kiosk-pin-prompt-input",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "2750" } });
    fireEvent.click(screen.getByTestId("kiosk-pin-prompt-submit"));

    await waitFor(() => {
      const authCall = findAuthCall(calls);
      expect(authCall).toBeTruthy();
      expect(JSON.parse(String(authCall!.init?.body))).toEqual({ pin: "2750" });
    });
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("2750");
    // Prompt is gone once auth succeeds.
    await waitFor(() => {
      expect(pinPromptVisible(result.container)).toBe(false);
    });
  });

  it("backend 401 clears the cached PIN and re-shows the prompt with an error", async () => {
    window.localStorage.setItem(STORAGE_KEY, "9999");
    const { calls } = stubFetchCapturingAuthCalls({ authStatus: 401 });

    const result = render(<App />);

    // Bootstrap should have tried the cached PIN once...
    await waitFor(() => {
      expect(findAuthCall(calls)).toBeTruthy();
    });
    // ...then the prompt should appear with the wrong-PIN message.
    await waitFor(() => {
      expect(pinPromptVisible(result.container)).toBe(true);
    });
    expect(
      screen.getByTestId("kiosk-pin-prompt-server-error").textContent,
    ).toMatch(/Wrong PIN/);
    // The bad cached value must have been cleared so the next submit
    // doesn't re-pick the same stale string on a remount.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
