// Phase K step K2 — kiosk bootstrap feature-flag fetch.
//
// Production-call coverage (code-quality §4 — new components require
// an integration test through the production caller). The kiosk's
// App.tsx bootstrap parallel-fetches the eight settings GETs and
// lifts the resolved values into component state, then surfaces them
// as data-* attributes on the kiosk root (the K2 wiring seam — later
// K-steps will replace the attributes with prop drilling into
// StepCard / ChoiceButton).
//
// Two tests pin the contract:
//
//   1. Happy path — all eight endpoints respond with non-default
//      values; the data-flag-* attributes on <main> reflect the
//      fetched values exactly. This is the silent-wiring guard: a
//      regression where the fetch fires but the result is never
//      assigned would leave the data attrs at the defaults despite
//      the fetch responses.
//
//   2. Per-endpoint rejection — one flag's GET 500s; the other seven
//      land their fetched values and the rejected flag stays at its
//      optimistic default. Catches a regression where one bad endpoint
//      poisons the whole bootstrap.

import {
  cleanup,
  render,
  waitFor,
} from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  delete (window as unknown as { __TOYBOX_KIOSK_PIN__?: string })
    .__TOYBOX_KIOSK_PIN__;
});

// Eight kebab paths, in the order ``KIOSK_FEATURE_FLAG_PATHS`` declares
// them. Local copy here so the production constant isn't its own
// test fixture (defending against a "test passes only because both
// halves are imported from the same file" failure mode).
const FLAG_PATHS: ReadonlyArray<readonly [string, string]> = [
  ["jokes_enabled", "/api/settings/jokes-enabled"],
  ["songs_enabled", "/api/settings/songs-enabled"],
  ["play_standalone_enabled", "/api/settings/play-standalone-enabled"],
  ["play_embedded_enabled", "/api/settings/play-embedded-enabled"],
  ["play_endings_enabled", "/api/settings/play-endings-enabled"],
  ["play_spontaneity_enabled", "/api/settings/play-spontaneity-enabled"],
  ["clickable_words_enabled", "/api/settings/clickable-words-enabled"],
  ["read_me_button_enabled", "/api/settings/read-me-button-enabled"],
];

interface FetchStubArgs {
  // Optional per-path override of the value returned. When a path is
  // absent from the map, the seeded backend defaults are used (seven
  // true, one false for play-spontaneity-enabled).
  flagValues?: Partial<Record<string, boolean>>;
  // Optional per-path status override — pass 500 to make that
  // endpoint reject and prove the per-flag rejection is isolated.
  flagStatuses?: Partial<Record<string, number>>;
}

function stubKioskBootstrapFetch(args: FetchStubArgs = {}): {
  calls: string[];
} {
  const calls: string[] = [];
  const handler = async (
    input: string | URL | Request,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push(url);
    if (url.endsWith("/api/auth/parent")) {
      return new Response(
        JSON.stringify({
          token: "tok-k2",
          expires_at: 4102444800,
          subject: { kind: "parent" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/health")) {
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    for (const [, path] of FLAG_PATHS) {
      if (url.endsWith(path)) {
        const status = args.flagStatuses?.[path] ?? 200;
        if (status !== 200) {
          return new Response(
            JSON.stringify({ detail: "induced failure" }),
            { status, headers: { "Content-Type": "application/json" } },
          );
        }
        // Default body per the backend's seeded migration: seven
        // true + one false.
        const defaultByPath: Record<string, boolean> = {
          "/api/settings/jokes-enabled": true,
          "/api/settings/songs-enabled": true,
          "/api/settings/play-standalone-enabled": true,
          "/api/settings/play-embedded-enabled": true,
          "/api/settings/play-endings-enabled": true,
          "/api/settings/play-spontaneity-enabled": false,
          "/api/settings/clickable-words-enabled": true,
          "/api/settings/read-me-button-enabled": true,
        };
        const seeded = defaultByPath[path] ?? true;
        const value = args.flagValues?.[path] ?? seeded;
        return new Response(JSON.stringify({ value }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    return new Response("", { status: 404 });
  };
  const mock = vi.fn(handler) as unknown as Mock;
  vi.stubGlobal("fetch", mock);
  return { calls };
}

async function bootKioskWithPin(): Promise<void> {
  (window as unknown as { __TOYBOX_KIOSK_PIN__: string }).__TOYBOX_KIOSK_PIN__ =
    "1357";
  const { App } = await import("./App");
  render(<App />);
}

describe("Kiosk K2 bootstrap — feature flag parallel fetch", () => {
  it("fires GET for all eight settings endpoints after token issuance", async () => {
    const { calls } = stubKioskBootstrapFetch();
    await bootKioskWithPin();

    // Wait until every flag endpoint has been called at least once.
    // The eight endpoints fire in parallel via Promise.allSettled.
    await waitFor(() => {
      for (const [, path] of FLAG_PATHS) {
        expect(
          calls.some((u) => u.endsWith(path)),
          `expected GET ${path}`,
        ).toBe(true);
      }
    });
  });

  it("lifts the resolved values into the kiosk root data-flag-* attributes (happy path)", async () => {
    // Invert every flag from its seeded default — the test catches a
    // regression where the bootstrap fetches but never assigns
    // (component would stay on the optimistic defaults).
    stubKioskBootstrapFetch({
      flagValues: {
        "/api/settings/jokes-enabled": false,
        "/api/settings/songs-enabled": false,
        "/api/settings/play-standalone-enabled": false,
        "/api/settings/play-embedded-enabled": false,
        "/api/settings/play-endings-enabled": false,
        "/api/settings/play-spontaneity-enabled": true,
        "/api/settings/clickable-words-enabled": false,
        "/api/settings/read-me-button-enabled": false,
      },
    });
    await bootKioskWithPin();

    await waitFor(() => {
      const root = document.querySelector('[data-testid="child-root"]');
      expect(root).not.toBeNull();
      // Every flag landed the inverted value.
      expect(root!.getAttribute("data-flag-jokes-enabled")).toBe("false");
      expect(root!.getAttribute("data-flag-songs-enabled")).toBe("false");
      expect(
        root!.getAttribute("data-flag-play-standalone-enabled"),
      ).toBe("false");
      expect(root!.getAttribute("data-flag-play-embedded-enabled")).toBe(
        "false",
      );
      expect(root!.getAttribute("data-flag-play-endings-enabled")).toBe(
        "false",
      );
      expect(
        root!.getAttribute("data-flag-play-spontaneity-enabled"),
      ).toBe("true");
      expect(
        root!.getAttribute("data-flag-clickable-words-enabled"),
      ).toBe("false");
      expect(
        root!.getAttribute("data-flag-read-me-button-enabled"),
      ).toBe("false");
    });
  });

  it("isolates a single endpoint rejection — others land + bad one stays default", async () => {
    // jokes-enabled 500s; the other seven respond with their inverted
    // values. The kiosk state must reflect: jokes_enabled stays at
    // its optimistic default (true), the other seven flipped.
    stubKioskBootstrapFetch({
      flagStatuses: {
        "/api/settings/jokes-enabled": 500,
      },
      flagValues: {
        "/api/settings/songs-enabled": false,
        "/api/settings/play-standalone-enabled": false,
        "/api/settings/play-embedded-enabled": false,
        "/api/settings/play-endings-enabled": false,
        "/api/settings/play-spontaneity-enabled": true,
        "/api/settings/clickable-words-enabled": false,
        "/api/settings/read-me-button-enabled": false,
      },
    });
    // Suppress the expected per-flag warning so the test output stays
    // clean — the code under test emits it intentionally on the
    // rejection path.
    const consoleSpy = vi
      .spyOn(console, "warn")
      .mockImplementation(() => {});
    try {
      await bootKioskWithPin();

      await waitFor(() => {
        const root = document.querySelector('[data-testid="child-root"]');
        expect(root).not.toBeNull();
        // The rejected flag stayed at its optimistic default (true).
        expect(root!.getAttribute("data-flag-jokes-enabled")).toBe("true");
        // The other seven landed their inverted (resolved) values.
        expect(root!.getAttribute("data-flag-songs-enabled")).toBe("false");
        expect(
          root!.getAttribute("data-flag-play-spontaneity-enabled"),
        ).toBe("true");
        expect(
          root!.getAttribute("data-flag-clickable-words-enabled"),
        ).toBe("false");
      });

      // And the warning fired (best-effort — the order isn't
      // guaranteed across allSettled, but the rejected flag's name
      // must appear in at least one warning message).
      const calls = consoleSpy.mock.calls.map((c) => String(c[0]));
      expect(calls.some((m) => m.includes("jokes_enabled"))).toBe(true);
    } finally {
      consoleSpy.mockRestore();
    }
  });
});
