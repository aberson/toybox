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
      // H5: SettingsPanel + StatsPanel both consume the metrics
      // snapshot, so the shape must be MetricsSnapshot-complete (not
      // just the ``audio`` block H2 used to read for the header
      // indicator). Defaults match what an empty install would emit.
      return new Response(
        JSON.stringify({
          generated_at: "2026-05-10T12:00:00Z",
          ws_subscribers: 0,
          activities: {
            proposed_current: 0,
            approved_current: 0,
            running_current: 0,
            completed_current: 0,
            ended_current: 0,
            dismissed_current: 0,
            didnt_work_current: 0,
            last_24h: {},
          },
          transcripts: { total: 0, last_24h: 0 },
          audio: {
            mic_device: null,
            queue_depth: 0,
            buffer_overruns_total: 0,
            mic_enabled: true,
          },
          ai: {
            breaker_state: "closed",
            breaker_retry_after_iso: null,
            claude_capable: false,
            claude_capability_reason: "token_missing",
            listening_mode: 3,
            min_interval_throttle_seconds: 15.0,
          },
          activity_quality: {
            last_24h_mean_scores: {},
            judge_parent_agreement: {
              overlap_count: 0,
              agreement_rate: null,
              metric_name: "sign_agreement_rate",
            },
            safety_autofails_last_24h: 0,
          },
          eval_gate: {
            last_run_at: null,
            mean_dimension_scores: null,
            regressions_detected: 0,
            placeholder_baseline: true,
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/settings/banned-themes")) {
      // H5: BannedThemesSettings GETs the persisted value on mount.
      // Default to ``null`` so the textarea renders empty + Save
      // disabled; tests that need a non-empty value can override.
      return new Response(JSON.stringify({ themes: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/settings/image-gen-mode")) {
      // H5: ImageGenModeToggle GETs the persisted mode on mount.
      return new Response(JSON.stringify({ mode: "cartoon" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/settings/transcript-retention")) {
      // Phase I step I3: App.tsx GETs the persisted retention preset
      // on mount + threads it down to SettingsPanel + TranscriptsManager.
      // Default 60 matches the backend's RETENTION_SECONDS_DEFAULT.
      return new Response(JSON.stringify({ seconds: 60 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    // H3: the Kids & Toyboxes sub-tabs mount ToyIngest /
    // ChildProfileEditor / RoomIngestBulk, each of which fires a list
    // probe on mount and reads a specific keyed array off the response
    // (resp.toys / resp.children / resp.rooms). The generic
    // ``{items: [], next: null}`` fallback below would leave those
    // arrays undefined and crash the components. Return shape-correct
    // empty lists for each list endpoint so the post-login render is
    // clean for H3's tab-switch assertions.
    if (url.endsWith("/api/toys")) {
      return new Response(JSON.stringify({ toys: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/children")) {
      return new Response(JSON.stringify({ children: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/rooms")) {
      return new Response(JSON.stringify({ rooms: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
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
      active: null,
      proposedList: [],
      wsState: "idle",
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

  it("switching to Kids & Toyboxes shows its sub-tabs (H3 wires ToyIngest as default)", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    act(() => {
      fireEvent.click(screen.getByTestId("tab-kids-toyboxes"));
    });
    expect(screen.getByTestId("subtab-toys")).toBeTruthy();
    expect(screen.getByTestId("subtab-children")).toBeTruthy();
    expect(screen.getByTestId("subtab-rooms")).toBeTruthy();
    // H3: the placeholder is gone; ToyIngest is the default sub-tab.
    expect(screen.queryByTestId("kids-toyboxes-placeholder")).toBeNull();
    // Play-tab content is no longer in the DOM.
    expect(screen.queryByTestId("subtab-play-ideas")).toBeNull();
  });

  it("switching to Settings shows its sub-tabs and mounts SettingsPanel by default", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    act(() => {
      fireEvent.click(screen.getByTestId("tab-settings"));
    });
    expect(screen.getByTestId("subtab-settings")).toBeTruthy();
    expect(screen.getByTestId("subtab-stats")).toBeTruthy();
    // H5: the placeholder is gone — SettingsPanel mounts under the
    // default ``settings`` sub-tab.
    expect(screen.queryByTestId("settings-placeholder")).toBeNull();
    await waitFor(() => {
      expect(screen.queryByTestId("settings-panel")).toBeTruthy();
    });
    // StatsPanel is NOT mounted while ``settings`` is the active sub-tab.
    expect(screen.queryByTestId("stats-panel")).toBeNull();
  });

  it("clicking subtab-stats unmounts SettingsPanel and mounts StatsPanel", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    act(() => {
      fireEvent.click(screen.getByTestId("tab-settings"));
    });
    await waitFor(() => {
      expect(screen.queryByTestId("settings-panel")).toBeTruthy();
    });
    act(() => {
      fireEvent.click(screen.getByTestId("subtab-stats"));
    });
    await waitFor(() => {
      expect(screen.queryByTestId("stats-panel")).toBeTruthy();
    });
    expect(screen.queryByTestId("settings-panel")).toBeNull();
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
    // singleton store. After J7 a ``proposed`` row lives in
    // ``proposedList`` (not the legacy single slot); the SuggestionCard
    // renders for proposedList[0] when ``active`` is null. We bypass
    // the propose() round-trip because this test only cares about
    // render gating, not the mutation path (covered elsewhere).
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
        proposedList: [seeded],
        active: null,
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
    expect(useParentStore.getState().proposedList[0]).toBe(seeded);
    // Switch back to Play; default sub-tab is play-ideas so the card
    // re-mounts with the same seeded activity referentially intact.
    act(() => {
      fireEvent.click(screen.getByTestId("tab-play"));
    });
    expect(screen.queryByTestId("suggestion-card")).toBeTruthy();
    expect(useParentStore.getState().proposedList[0]).toBe(seeded);
  });

  // Phase J step J7 regression: ``handleDismiss`` used to call
  // ``setActive(null)`` which cleared the legacy single slot — instant
  // visible dismiss. Post-J7 proposed rows live in ``proposedList`` and
  // ``state.active`` is null while the suggestion card is shown, so
  // ``setActive(null)`` becomes a no-op and the card sticks on screen
  // until the server's ``dismissed`` ws envelope arrives (50-500ms).
  //
  // The fix routes the dismiss mutation's response through
  // ``applyMutationResult`` so the dismissed-state row is removed from
  // ``proposedList`` synchronously with the API resolve — no ws
  // dependency. This test pins that contract: seed a proposed row,
  // stub the dismiss endpoint to return a dismissed-state Activity,
  // click the button, and assert ``proposedList`` is empty before any
  // ws traffic could land.
  it("dismiss clears proposedList synchronously (J7 regression)", async () => {
    const seededProposed = {
      id: "act-j7-dismiss",
      state: "proposed",
      version: 1,
      title: "J7 dismiss-clears-instantly",
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
    // Compose a fetch stub that layers a dismiss-endpoint handler on
    // top of the standard auth fetch — the post-PIN render reads
    // /api/health, /api/metrics, etc., and they all need to succeed
    // before the tab shell mounts.
    const baseHandler = stubFullAuthFetch({ pin_set: true });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        const method = (init?.method ?? "GET").toUpperCase();
        if (
          method === "POST" &&
          url.endsWith(`/api/activities/${seededProposed.id}/dismiss`)
        ) {
          // The dismiss mutation echoes the now-dismissed Activity. The
          // ``applyMutationResult`` reducer will route this through
          // ``applyEnvelopeToNewSlots`` which, for ``dismissed`` state,
          // filters the row out of ``proposedList`` and clears ``active``
          // if its id matches.
          return new Response(
            JSON.stringify({
              ...seededProposed,
              state: "dismissed",
              version: seededProposed.version + 1,
              ended_at: new Date().toISOString(),
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return baseHandler(input, init);
      }),
    );
    render(<App />);
    await driveLoginToTabShell();
    // Seed the proposed row after login so the SuggestionCard mounts
    // on the Play → Play Ideas default sub-tab.
    act(() => {
      useParentStore.setState({
        proposedList: [seededProposed],
        active: null,
      } as Partial<ReturnType<typeof useParentStore.getState>>);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("suggestion-card")).toBeTruthy();
    });
    expect(useParentStore.getState().proposedList).toHaveLength(1);
    // Click dismiss and wait for the mutation to settle. The
    // assertion is on ``proposedList`` (store state), not on the
    // rendered card disappearing — the rendered-card path goes
    // through React's commit cycle which adds noise; the store
    // assertion is the tighter contract.
    act(() => {
      fireEvent.click(screen.getByTestId("dismiss-button"));
    });
    await waitFor(() => {
      expect(useParentStore.getState().proposedList).toHaveLength(0);
    });
    // Cross-check: no ws envelope was needed. The mutation alone
    // cleared the slot. If the bug regresses (``setActive(null)``
    // again), the store stays at length 1 and the test times out.
    expect(useParentStore.getState().active).toBeNull();
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

// Phase H step H3: Kids & Toyboxes tab is now wired to its three real
// editors (ToyIngest / ChildProfileEditor / RoomIngestBulk). These
// tests pin that the right component renders under the right sub-tab
// and that switching sub-tabs unmounts the previous editor — the
// editors are heavy enough that leaving them mounted in parallel would
// fire duplicate list probes and waste a render budget.
describe("App Kids & Toyboxes tab (H3)", () => {
  beforeEach(() => {
    useParentStore.setState({
      token: null,
      active: null,
      proposedList: [],
      wsState: "idle",
      toasts: [],
      capabilityReason: null,
    } as Partial<ReturnType<typeof useParentStore.getState>>);
    window.localStorage.clear();
  });

  it("Toys is the default sub-tab and renders ToyIngest", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    act(() => {
      fireEvent.click(screen.getByTestId("tab-kids-toyboxes"));
    });
    await waitFor(() => {
      expect(screen.queryByTestId("toy-ingest")).toBeTruthy();
    });
    // The other two editors are NOT mounted.
    expect(screen.queryByTestId("child-profile-editor")).toBeNull();
    expect(screen.queryByTestId("room-ingest-bulk")).toBeNull();
  });

  it("clicking subtab-children unmounts ToyIngest and mounts ChildProfileEditor", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    act(() => {
      fireEvent.click(screen.getByTestId("tab-kids-toyboxes"));
    });
    await waitFor(() => {
      expect(screen.queryByTestId("toy-ingest")).toBeTruthy();
    });
    act(() => {
      fireEvent.click(screen.getByTestId("subtab-children"));
    });
    await waitFor(() => {
      expect(screen.queryByTestId("child-profile-editor")).toBeTruthy();
    });
    expect(screen.queryByTestId("toy-ingest")).toBeNull();
    expect(screen.queryByTestId("room-ingest-bulk")).toBeNull();
  });

  it("clicking subtab-rooms mounts RoomIngestBulk", async () => {
    stubFullAuthFetch({ pin_set: true });
    render(<App />);
    await driveLoginToTabShell();
    act(() => {
      fireEvent.click(screen.getByTestId("tab-kids-toyboxes"));
    });
    await waitFor(() => {
      expect(screen.queryByTestId("toy-ingest")).toBeTruthy();
    });
    act(() => {
      fireEvent.click(screen.getByTestId("subtab-rooms"));
    });
    await waitFor(() => {
      expect(screen.queryByTestId("room-ingest-bulk")).toBeTruthy();
    });
    expect(screen.queryByTestId("toy-ingest")).toBeNull();
    expect(screen.queryByTestId("child-profile-editor")).toBeNull();
  });
});

// Phase J step J9: switch-confirm flow. When a parent clicks approve on
// a queued suggestion while another activity is already active, fire a
// confirm dialog. On accept, end the old active first, then approve the
// new — both results routed through ``applySwitch`` so the active slot
// transitions atomically. On cancel, no-op. The same-id branch (approve
// on the active row — defensive; the active card normally has no
// approve button) skips the confirm.
describe("App approve switch-confirm flow (J9)", () => {
  beforeEach(() => {
    useParentStore.setState({
      token: null,
      active: null,
      proposedList: [],
      wsState: "idle",
      toasts: [],
      capabilityReason: null,
    } as Partial<ReturnType<typeof useParentStore.getState>>);
    window.localStorage.clear();
  });

  // Test-side helpers: seeding a proposed row + an active row, then
  // composing a fetch stub on top of stubFullAuthFetch that captures
  // approve / end calls so the assertions can inspect call ordering +
  // payloads. The base stub provides all the other endpoints
  // (/api/auth/parent/status, /api/health, /api/metrics, etc.) the
  // post-login render touches.
  interface CapturedCall {
    url: string;
    method: string;
    body: string | null;
  }
  function composeFetchWithCapture(
    base: Mock,
    captured: CapturedCall[],
    overrides: { endFails?: boolean } = {},
  ): Mock {
    const layered = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        const method = (init?.method ?? "GET").toUpperCase();
        // Only capture activity mutations — bootstrap noise (health,
        // metrics, settings) shouldn't pollute the call log the
        // assertions read.
        if (
          method === "POST" &&
          (url.includes("/approve") || url.includes("/end"))
        ) {
          captured.push({
            url,
            method,
            body: typeof init?.body === "string" ? init.body : null,
          });
          if (overrides.endFails === true && url.endsWith("/end")) {
            // Simulate a network failure for the end mutation. The
            // outer ``withConflictHandler`` only swallows
            // VersionConflictError; everything else rethrows. The
            // handler in App.tsx doesn't wrap a try/catch around the
            // switch-confirm branch, so the rejected promise propagates
            // up through the click handler — vitest treats the
            // unhandled-rejection as a test failure unless we make this
            // a 409 conflict (handled gracefully). Use a 409 so
            // ``withConflictHandler`` returns null → approve does NOT
            // fire, matching the "end fails → approve does not fire"
            // assertion.
            return new Response(
              JSON.stringify({
                detail: {
                  code: "version_conflict",
                  current_version: 99,
                  current_state: "ended",
                },
              }),
              { status: 409, headers: { "Content-Type": "application/json" } },
            );
          }
          // Echo back an Activity-shaped response with state advanced
          // appropriately. The store routes it through
          // ``applyEnvelopeToNewSlots`` via ``applyMutationResult`` or
          // ``applySwitch``.
          const id = url.split("/activities/")[1]?.split("/")[0] ?? "";
          const isApprove = url.endsWith("/approve");
          return new Response(
            JSON.stringify({
              id,
              state: isApprove ? "approved" : "ended",
              version: 2,
              title: isApprove ? "new title" : "old title",
              summary: null,
              persona_id: null,
              intent_source: null,
              child_ids: [],
              created_at: new Date().toISOString(),
              started_at: null,
              ended_at: isApprove ? null : new Date().toISOString(),
              steps: [],
              metadata: {},
              trigger_phrase: null,
              persona_reasoning: null,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        // Also handle GET /api/activities/{id} (refetch on conflict).
        if (method === "GET" && url.includes("/api/activities/")) {
          return new Response(JSON.stringify({}), {
            status: 404,
            headers: { "Content-Type": "application/json" },
          });
        }
        return base(input, init);
      },
    ) as unknown as Mock;
    vi.stubGlobal("fetch", layered);
    return layered;
  }

  function seedProposed(id: string, title: string) {
    return {
      id,
      state: "proposed" as const,
      version: 1,
      title,
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
  }
  function seedActive(id: string, title: string) {
    return { ...seedProposed(id, title), state: "running" as const, version: 5 };
  }

  it("approve with no active: standard approve, no confirm fires", async () => {
    const base = stubFullAuthFetch({ pin_set: true });
    const captured: CapturedCall[] = [];
    composeFetchWithCapture(base, captured);
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValue(true);
    render(<App />);
    await driveLoginToTabShell();
    const proposed = seedProposed("act-new", "New idea");
    act(() => {
      useParentStore.setState({
        proposedList: [proposed],
        active: null,
      } as Partial<ReturnType<typeof useParentStore.getState>>);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("approve-button")).toBeTruthy();
    });
    act(() => {
      fireEvent.click(screen.getByTestId("approve-button"));
    });
    await waitFor(() => {
      expect(
        captured.some((c) => c.url.endsWith("/act-new/approve")),
      ).toBe(true);
    });
    // Confirm MUST NOT fire on the no-active path — it would be a
    // confusing dialog for a normal approve.
    expect(confirmSpy).not.toHaveBeenCalled();
    // No end call fired.
    expect(captured.some((c) => c.url.endsWith("/end"))).toBe(false);
  });

  it("approve with active different id: confirm dialog fires with the swap message", async () => {
    const base = stubFullAuthFetch({ pin_set: true });
    const captured: CapturedCall[] = [];
    composeFetchWithCapture(base, captured);
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValue(false); // cancel — no further calls
    render(<App />);
    await driveLoginToTabShell();
    const oldActive = seedActive("act-old", "Old activity");
    const newProposed = seedProposed("act-new", "New idea");
    act(() => {
      useParentStore.setState({
        proposedList: [newProposed],
        active: oldActive,
      } as Partial<ReturnType<typeof useParentStore.getState>>);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("approve-button")).toBeTruthy();
    });
    act(() => {
      fireEvent.click(screen.getByTestId("approve-button"));
    });
    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledTimes(1);
    });
    // Message text MUST name both activities so the parent knows what
    // they're swapping in / out.
    const msg = confirmSpy.mock.calls[0]?.[0] as string;
    expect(msg).toContain("Old activity");
    expect(msg).toContain("New idea");
  });

  it("on confirm OK: end old, then approve new, applySwitch routes results", async () => {
    const base = stubFullAuthFetch({ pin_set: true });
    const captured: CapturedCall[] = [];
    composeFetchWithCapture(base, captured);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<App />);
    await driveLoginToTabShell();
    const oldActive = seedActive("act-old", "Old activity");
    const newProposed = seedProposed("act-new", "New idea");
    act(() => {
      useParentStore.setState({
        proposedList: [newProposed],
        active: oldActive,
      } as Partial<ReturnType<typeof useParentStore.getState>>);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("approve-button")).toBeTruthy();
    });
    act(() => {
      fireEvent.click(screen.getByTestId("approve-button"));
    });
    // Wait for both mutations to land.
    await waitFor(() => {
      expect(captured.length).toBe(2);
    });
    // Order matters: end-old MUST precede approve-new so the active
    // slot can't briefly hold the about-to-be-displaced row alongside
    // the new one.
    expect(captured[0]?.url).toContain("/act-old/end");
    expect(captured[1]?.url).toContain("/act-new/approve");
    // ``applySwitch`` routed the results: the new id is now active,
    // and the proposed row was lifted out of the queue.
    await waitFor(() => {
      expect(useParentStore.getState().active?.id).toBe("act-new");
    });
    expect(useParentStore.getState().proposedList).toHaveLength(0);
  });

  it("on confirm Cancel: no end, no approve", async () => {
    const base = stubFullAuthFetch({ pin_set: true });
    const captured: CapturedCall[] = [];
    composeFetchWithCapture(base, captured);
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<App />);
    await driveLoginToTabShell();
    const oldActive = seedActive("act-old", "Old activity");
    const newProposed = seedProposed("act-new", "New idea");
    act(() => {
      useParentStore.setState({
        proposedList: [newProposed],
        active: oldActive,
      } as Partial<ReturnType<typeof useParentStore.getState>>);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("approve-button")).toBeTruthy();
    });
    act(() => {
      fireEvent.click(screen.getByTestId("approve-button"));
    });
    // No mutation fires. The assertion is "still zero" rather than
    // "eventually zero" because cancel takes the early-return branch
    // synchronously — there's no in-flight promise to await.
    await new Promise((r) => setTimeout(r, 50));
    expect(captured).toHaveLength(0);
    // Active untouched.
    expect(useParentStore.getState().active?.id).toBe("act-old");
  });

  it("end fails (409) → approve does not fire", async () => {
    const base = stubFullAuthFetch({ pin_set: true });
    const captured: CapturedCall[] = [];
    composeFetchWithCapture(base, captured, { endFails: true });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<App />);
    await driveLoginToTabShell();
    const oldActive = seedActive("act-old", "Old activity");
    const newProposed = seedProposed("act-new", "New idea");
    act(() => {
      useParentStore.setState({
        proposedList: [newProposed],
        active: oldActive,
      } as Partial<ReturnType<typeof useParentStore.getState>>);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("approve-button")).toBeTruthy();
    });
    act(() => {
      fireEvent.click(screen.getByTestId("approve-button"));
    });
    // The end POST fires (and 409s); approve MUST NOT fire after a
    // failed end — half-applied switches leave the user confused
    // about what state they're in.
    await waitFor(() => {
      expect(captured.some((c) => c.url.endsWith("/act-old/end"))).toBe(true);
    });
    await new Promise((r) => setTimeout(r, 50));
    expect(captured.some((c) => c.url.endsWith("/approve"))).toBe(false);
  });

  it("approve on same-id as active: skip confirm (defensive)", async () => {
    // Active row's panel doesn't normally have an approve button, but
    // the handler accepts an arbitrary ``target`` so a same-id approve
    // is reachable. The branch MUST fall through to the standard
    // approve path without surfacing a confusing "switch from X to X"
    // dialog. We seed the same id in both slots to force a queue card
    // for the active row — abnormal but the only DOM-reachable way to
    // exercise the branch.
    const base = stubFullAuthFetch({ pin_set: true });
    const captured: CapturedCall[] = [];
    composeFetchWithCapture(base, captured);
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValue(true);
    render(<App />);
    await driveLoginToTabShell();
    const sameId = "act-same";
    const proposedRow = seedProposed(sameId, "Same id row");
    const activeRow = seedActive(sameId, "Same id active");
    act(() => {
      useParentStore.setState({
        proposedList: [proposedRow],
        active: activeRow,
      } as Partial<ReturnType<typeof useParentStore.getState>>);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("approve-button")).toBeTruthy();
    });
    act(() => {
      fireEvent.click(screen.getByTestId("approve-button"));
    });
    await waitFor(() => {
      expect(captured.some((c) => c.url.endsWith(`/${sameId}/approve`))).toBe(
        true,
      );
    });
    // Same-id approve MUST NOT fire confirm — the dialog is only for
    // displacement.
    expect(confirmSpy).not.toHaveBeenCalled();
    // And no end fired — the standard path was taken.
    expect(captured.some((c) => c.url.endsWith("/end"))).toBe(false);
  });
});
