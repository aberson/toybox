// Phase K step K2 iter-2 integration test: PlayFeaturesControls
// driven by a REAL ``ApiClient`` instance (not a vi.fn mock). Closes
// the silent-wiring gap that iter-1's mocked-setter tests missed.
//
// THE BUG THIS GUARDS AGAINST (iter-1 review HIGH finding):
//
//   The component grabbed setters via ``api[spec.setter]`` and called
//   them detached. ApiClient's methods are regular ``async setX() {
//     return this.request(...)
//   }`` (not arrow fields), so the detached invocation loses ``this``
//   and TypeScript-emitted ES2020 modules run in strict mode, making
//   ``this === undefined`` inside the body. Every toggle click would
//   throw ``Cannot read properties of undefined (reading 'request')``.
//
//   Mocked ``vi.fn`` setters never tripped this because their bodies
//   don't reference ``this``. Per code-quality.md §3+§4: tests with
//   mocks can't see producer-consumer drift; new components must have
//   an integration test through the production caller.
//
// WHAT THIS TEST DOES:
//
//   1. Constructs a real ``ApiClient`` (with an injected ``fetchImpl``
//      so we observe the wire shape without hitting a server).
//   2. Renders ``<PlayFeaturesControls>`` with the real client.
//   3. Clicks a toggle and asserts a PUT actually fired with the
//      expected URL + body — proving the method body executed all
//      the way through ``this.request(...)``.
//
//   Against iter-1's code, the click would synchronously raise
//   ``TypeError: Cannot read properties of undefined (reading 'request')``
//   inside the component's ``.then`` chain → no PUT seen by
//   ``fetchImpl`` → test FAILS. Against iter-2's ``setterFn.call(api, ...)``,
//   the method runs correctly → PUT observed → test PASSES.
//
// Mirrors App.bootstrap.test.tsx's stub-fetch pattern, scoped down
// to the single PUT we care about per click.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient, type PhaseKFeatureFlag } from "../api";
import { PHASE_K_FEATURE_FLAG_DEFAULTS } from "../api";
import { FEATURE_TOGGLES, PlayFeaturesControls } from "./PlayFeaturesControls";

interface ObservedRequest {
  url: string;
  method: string;
  body: string | null;
}

function buildRealApiClient(): {
  api: ApiClient;
  observed: ObservedRequest[];
  fetchImpl: Mock;
} {
  const observed: ObservedRequest[] = [];
  const fetchImpl = vi.fn(
    async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
      const url = typeof input === "string" ? input : input.toString();
      const method = (init?.method ?? "GET").toUpperCase();
      // Body in our PUTs is always a string (JSON.stringify(...)).
      const body =
        typeof init?.body === "string"
          ? init.body
          : init?.body !== undefined && init?.body !== null
            ? String(init.body)
            : null;
      observed.push({ url, method, body });
      // Echo the requested value back so the component's .then path
      // resolves naturally.
      let parsed: { value?: boolean } = {};
      if (body !== null && body.length > 0) {
        try {
          parsed = JSON.parse(body) as { value?: boolean };
        } catch {
          parsed = {};
        }
      }
      return new Response(JSON.stringify({ value: parsed.value ?? false }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  );
  const api = new ApiClient({ fetchImpl });
  return { api, observed, fetchImpl };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PlayFeaturesControls — integration with real ApiClient", () => {
  it("clicking Off on jokes_enabled fires PUT /api/settings/jokes-enabled with {value:false}", async () => {
    // This is the canary for the iter-1 this-binding bug. If the
    // setter is detached, the click raises a TypeError inside the
    // .then chain and no PUT ever reaches fetchImpl.
    const { api, observed, fetchImpl } = buildRealApiClient();
    const onValueChanged = vi.fn();
    render(
      <PlayFeaturesControls
        api={api}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("feature-toggle-jokes_enabled-off"));
    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
      expect(onValueChanged).toHaveBeenCalledWith("jokes_enabled", false);
    });
    // Exactly one PUT, exactly the right shape.
    const puts = observed.filter((r) => r.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(puts[0].url).toMatch(/\/api\/settings\/jokes-enabled$/);
    expect(puts[0].body).toBe(JSON.stringify({ value: false }));
  });

  it("clicking On on play_spontaneity_enabled (opt-in) fires PUT with {value:true}", async () => {
    // Pin the opt-in flag's wire shape too — different default
    // (false), so the displayed→target transition is the inverse of
    // the jokes_enabled case. If a future refactor flips the wire
    // body or the URL, this test fails alongside the jokes_enabled
    // one (both share the same code path).
    const { api, observed } = buildRealApiClient();
    const onValueChanged = vi.fn();
    render(
      <PlayFeaturesControls
        api={api}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(
      screen.getByTestId("feature-toggle-play_spontaneity_enabled-on"),
    );
    await waitFor(() => {
      expect(onValueChanged).toHaveBeenCalledWith(
        "play_spontaneity_enabled",
        true,
      );
    });
    const puts = observed.filter((r) => r.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(puts[0].url).toMatch(/\/api\/settings\/play-spontaneity-enabled$/);
    expect(puts[0].body).toBe(JSON.stringify({ value: true }));
  });

  it("every flag's setter routes to its kebab-case URL when invoked through the real client", async () => {
    // Exhaustive wire-shape audit (code-quality §3): one click per
    // flag, assert each lands at the right kebab-case endpoint with
    // the expected toggled value. Catches a copy-paste regression
    // where two setters share a URL — a class of silent-wiring fail
    // the iter-1 mock-based test couldn't see.
    const { api, observed } = buildRealApiClient();
    const onValueChanged = vi.fn();
    render(
      <PlayFeaturesControls
        api={api}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    // Map of snake_case flag key → expected kebab-case URL fragment.
    const expectedUrl: Record<PhaseKFeatureFlag, string> = {
      jokes_enabled: "/api/settings/jokes-enabled",
      songs_enabled: "/api/settings/songs-enabled",
      play_standalone_enabled: "/api/settings/play-standalone-enabled",
      play_embedded_enabled: "/api/settings/play-embedded-enabled",
      play_endings_enabled: "/api/settings/play-endings-enabled",
      play_spontaneity_enabled: "/api/settings/play-spontaneity-enabled",
      clickable_words_enabled: "/api/settings/clickable-words-enabled",
      read_me_button_enabled: "/api/settings/read-me-button-enabled",
    };
    for (const spec of FEATURE_TOGGLES) {
      const target = !PHASE_K_FEATURE_FLAG_DEFAULTS[spec.key];
      fireEvent.click(
        screen.getByTestId(
          `feature-toggle-${spec.key}-${target ? "on" : "off"}`,
        ),
      );
      // Wait for the PUT for this row to settle before clicking the
      // next; PlayFeaturesControls disables a row while in flight.
      // eslint-disable-next-line @typescript-eslint/no-loop-func
      await waitFor(() => {
        expect(
          observed.some(
            (r) => r.method === "PUT" && r.url.endsWith(expectedUrl[spec.key]),
          ),
        ).toBe(true);
      });
    }
    // Each URL was hit exactly once with the correct body.
    for (const spec of FEATURE_TOGGLES) {
      const target = !PHASE_K_FEATURE_FLAG_DEFAULTS[spec.key];
      const matches = observed.filter(
        (r) => r.method === "PUT" && r.url.endsWith(expectedUrl[spec.key]),
      );
      expect(matches).toHaveLength(1);
      expect(matches[0].body).toBe(JSON.stringify({ value: target }));
    }
  });
});
