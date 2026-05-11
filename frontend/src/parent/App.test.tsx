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

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { useParentStore } from "./store";

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

// Phase H step H2: tab shell smoke. Once the operator has cleared the
// PIN gate, App.tsx renders the two-level tab shell. The bootstrap
// tests above don't cross that gate; these do — they drive the
// PinLogin flow with a stubbed verify endpoint, then assert the new
// tab IDs are present and switch as expected.
//
// We intentionally let the WS client construction fail silently — the
// happy-dom WebSocket will hit a closed state when it can't reach
// ws://localhost/ws, but ``setAuthMode("ready")`` fires synchronously
// inside ``continueBootstrap`` before the ws spins up, so the tab UI
// renders regardless. Toast plumbing is similarly tolerant.
//
// Tab unit behaviour (selection, persistence, aria-selected) is
// covered by ``components/Tabs.test.tsx``; this file only verifies the
// H2 wiring — that the right testids exist + the toggle paths swap
// content as expected.

function stubFullAuthFetch(opts: { pin_set: boolean }): Mock {
  const handler = async (
    input: string | URL | Request,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.endsWith("/api/auth/parent/status")) {
      return new Response(
        JSON.stringify({
          pin_set: opts.pin_set,
          locked: false,
          seconds_until_unlock: 0,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/auth/parent") && method === "POST") {
      return new Response(
        JSON.stringify({
          token: "test-token",
          expires_at: Math.floor(Date.now() / 1000) + 3600,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/metrics")) {
      return new Response(
        JSON.stringify({
          audio: {
            mic_device: null,
            mic_enabled: true,
            sample_rate: 16000,
            chunk_ms: 20,
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    // Anything else (e.g. TranscriptsManager's pagination probe) gets a
    // benign empty response so the post-login render doesn't surface
    // unrelated network noise as test failures.
    return new Response(JSON.stringify({ items: [], next: null }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  const fetchMock = vi.fn(handler) as unknown as Mock;
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function driveLoginToTabShell(): Promise<void> {
  // Wait for PinLogin to mount, type a PIN, click submit, then wait
  // for the tab shell. The verify endpoint stub returns a synthetic
  // token so handleAuthSuccess → continueBootstrap fires.
  await waitFor(() => {
    expect(screen.queryByTestId("pin-login")).toBeTruthy();
  });
  const input = screen.getByTestId("pin-login-pin-input") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "1234" } });
  fireEvent.click(screen.getByTestId("pin-login-submit"));
  await waitFor(() => {
    expect(screen.queryByTestId("tabs")).toBeTruthy();
  });
}

describe("App tab shell (post-PIN, H2)", () => {
  beforeEach(() => {
    // The parent store carries token + activity + ws state across
    // tests via a module-scoped Zustand singleton. Reset it between
    // tests so a previous login's token doesn't leak.
    useParentStore.setState({
      token: null,
      activity: null,
      wsState: "idle",
      health: null,
      toasts: [],
      capabilityReason: null,
    } as Partial<ReturnType<typeof useParentStore.getState>>);
    // Clear localStorage between tests so tab selections from one
    // test don't bleed into the next.
    window.localStorage.clear();
  });

  it("renders the three top tabs with Play selected by default", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    expect(screen.getByTestId("tab-play")).toBeTruthy();
    expect(screen.getByTestId("tab-kids-toyboxes")).toBeTruthy();
    expect(screen.getByTestId("tab-settings")).toBeTruthy();
    // Play is the default top tab; its sub-tabs are visible.
    expect(screen.getByTestId("subtab-play-ideas")).toBeTruthy();
    expect(screen.getByTestId("subtab-transcription")).toBeTruthy();
    // Kids & Toyboxes placeholder is NOT rendered while Play is active.
    expect(screen.queryByTestId("kids-toyboxes-placeholder")).toBeNull();
  });

  it("switching to Kids & Toyboxes shows its sub-tabs + placeholder", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    act(() => {
      fireEvent.click(screen.getByTestId("tab-kids-toyboxes"));
    });
    expect(screen.getByTestId("subtab-toys")).toBeTruthy();
    expect(screen.getByTestId("subtab-children")).toBeTruthy();
    expect(screen.getByTestId("subtab-rooms")).toBeTruthy();
    expect(screen.getByTestId("kids-toyboxes-placeholder")).toBeTruthy();
    // Play-tab content is no longer in the DOM.
    expect(screen.queryByTestId("subtab-play-ideas")).toBeNull();
  });

  it("switching to Settings shows its sub-tabs + placeholder", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    act(() => {
      fireEvent.click(screen.getByTestId("tab-settings"));
    });
    expect(screen.getByTestId("subtab-settings")).toBeTruthy();
    expect(screen.getByTestId("subtab-stats")).toBeTruthy();
    expect(screen.getByTestId("settings-placeholder")).toBeTruthy();
  });

  it("Play sub-tab persists in localStorage on Transcription select", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    act(() => {
      fireEvent.click(screen.getByTestId("subtab-transcription"));
    });
    // Persisted under the documented storage key — the H6 UAT relies
    // on this so a hard refresh comes back on the last-selected tab.
    expect(window.localStorage.getItem("toybox.parent.tabs.play")).toBe(
      "transcription",
    );
    // The TriggerButton (Play Ideas content) is no longer mounted.
    expect(screen.queryByTestId("trigger-button")).toBeNull();
  });

  // Plan R2: switching to another top tab during a running activity
  // hides the SuggestionCard / ActivityPanel; state itself is
  // preserved in the store so flipping back restores visibility. A
  // regression where someone moved the activity render under a tab
  // gate that ALSO unmounted the store slot would silently destroy
  // in-progress activity visibility — this pins both halves.
  it("preserves activity store state across top-tab switches", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    // Seed a non-null ``proposed`` activity directly into the
    // singleton store. ``proposed`` matches SUGGESTION_STATES so the
    // SuggestionCard renders. We bypass the propose() round-trip
    // because this test only cares about render gating, not the
    // mutation path (covered elsewhere).
    const seeded = {
      id: "act-r2",
      state: "proposed",
      version: 1,
      title: "R2 seeded activity",
      summary: null,
      persona_id: null,
      intent_source: null,
      child_ids: [],
      created_at: new Date().toISOString(),
      started_at: null,
      ended_at: null,
      steps: [],
      metadata: {},
      trigger_phrase: null,
      persona_reasoning: null,
    };
    act(() => {
      useParentStore.setState({
        activity: seeded,
      } as Partial<ReturnType<typeof useParentStore.getState>>);
    });
    // Play → Play Ideas is the default sub-tab; the card should mount.
    await waitFor(() => {
      expect(screen.queryByTestId("suggestion-card")).toBeTruthy();
    });
    // Switch to Settings — the card unmounts (gated by the play +
    // play-ideas selection), but the store slot is untouched.
    act(() => {
      fireEvent.click(screen.getByTestId("tab-settings"));
    });
    expect(screen.queryByTestId("suggestion-card")).toBeNull();
    expect(useParentStore.getState().activity).toBe(seeded);
    // Switch back to Play; default sub-tab is play-ideas so the card
    // re-mounts with the same seeded activity referentially intact.
    act(() => {
      fireEvent.click(screen.getByTestId("tab-play"));
    });
    expect(screen.queryByTestId("suggestion-card")).toBeTruthy();
    expect(useParentStore.getState().activity).toBe(seeded);
  });

  // Plan: toasts are cross-cutting transient feedback and MUST render
  // outside any tab gate, so a mute-error toast (Settings-driven) is
  // visible from Play and vice versa. A regression that nests the
  // toast list under a tabpanel would silently swallow these.
  it("renders toasts outside tab gates (visible across switches)", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    act(() => {
      useParentStore.setState({
        toasts: [{ id: 1, kind: "info", message: "hello toast" }],
      } as Partial<ReturnType<typeof useParentStore.getState>>);
    });
    // Default tab: Play → Play Ideas.
    await waitFor(() => {
      expect(screen.queryByTestId("toasts")).toBeTruthy();
    });
    // Settings: still visible.
    act(() => {
      fireEvent.click(screen.getByTestId("tab-settings"));
    });
    expect(screen.queryByTestId("toasts")).toBeTruthy();
    // Kids & Toyboxes: still visible.
    act(() => {
      fireEvent.click(screen.getByTestId("tab-kids-toyboxes"));
    });
    expect(screen.queryByTestId("toasts")).toBeTruthy();
  });
});
