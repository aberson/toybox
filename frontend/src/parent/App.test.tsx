// Step 21 reviewer-finding regression: an AbortController cached in
// a useRef that was lazy-initialised once at module first render and
// reused across React 18 StrictMode's mount → cleanup → re-mount cycle
// would arrive at the second mount already-aborted. Every fetch with
// ``signal: aborter.signal`` then throws AbortError synchronously, the
// catch returns silently on ``isAbortError``, and ``authMode`` never
// advances past ``"bootstrap"`` — the UI sticks on a blank screen.
//
// This file pins that the bootstrap probe completes a) on a fresh
// mount and b) on a StrictMode-style remount (cleanup followed by a
// new mount), so a regression of the lazy-ref pattern fails fast.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// Stub ``window.fetch`` for the bootstrap status probe. We only need
// /api/auth/parent/status to succeed — the other paths (login/setup,
// health, ws) only fire after the user clears the PIN gate, so for a
// "did the probe land" test they never reach the network.
function stubAuthStatusFetch(body: {
  pin_set: boolean;
  locked: boolean;
  seconds_until_unlock: number;
}): Mock {
  const handler = async (
    input: string | URL | Request,
    _init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/auth/parent/status")) {
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    // Any other path is unexpected for these tests — return 404 so a
    // surprise call surfaces clearly rather than hanging.
    return new Response("", { status: 404 });
  };
  const fetchMock = vi.fn(handler) as unknown as Mock;
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("App bootstrap (StrictMode-safe)", () => {
  it("completes the bootstrap probe and renders the PinSetup screen", async () => {
    stubAuthStatusFetch({
      pin_set: false,
      locked: false,
      seconds_until_unlock: 0,
    });
    render(<App />);
    // Initial render shows the bootstrap placeholder.
    expect(screen.queryByTestId("pin-bootstrap")).toBeTruthy();
    // Probe succeeds → first-run setup screen mounts.
    await waitFor(() => {
      expect(screen.queryByTestId("pin-setup")).toBeTruthy();
    });
    expect(screen.queryByTestId("pin-bootstrap")).toBeNull();
  });

  it("under StrictMode, the second mount still completes the bootstrap probe", async () => {
    // StrictMode in dev double-invokes the effect — first mount, then
    // cleanup, then re-mount. The bug under test was that the second
    // mount inherited an already-aborted controller and the probe never
    // landed. Wrapping in <StrictMode> reproduces the cycle.
    const fetchMock = stubAuthStatusFetch({
      pin_set: true,
      locked: false,
      seconds_until_unlock: 0,
    });
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
    // The PinLogin screen is the post-bootstrap target when pin_set=true.
    await waitFor(
      () => {
        expect(screen.queryByTestId("pin-login")).toBeTruthy();
      },
      { timeout: 2000 },
    );
    expect(screen.queryByTestId("pin-bootstrap")).toBeNull();
    // And the probe ran at least once — even if StrictMode aborted the
    // first mount's request, the second mount's fresh AbortController
    // landed a successful response that drove the state transition.
    expect(fetchMock).toHaveBeenCalled();
  });

  it("manual unmount → remount completes the second bootstrap", async () => {
    // Belt-and-braces: do the cleanup → remount cycle ourselves so the
    // assertion targets the same lifecycle the StrictMode test exercises
    // but without relying on React's dev-mode double-invoke heuristic.
    stubAuthStatusFetch({
      pin_set: false,
      locked: false,
      seconds_until_unlock: 0,
    });
    const first = render(<App />);
    await waitFor(() => {
      expect(screen.queryByTestId("pin-setup")).toBeTruthy();
    });
    first.unmount();
    // Re-render a fresh instance; the new mount must complete its own
    // probe and reach pin-setup again, not stall on pin-bootstrap.
    render(<App />);
    await waitFor(() => {
      expect(screen.queryByTestId("pin-setup")).toBeTruthy();
    });
  });
});
