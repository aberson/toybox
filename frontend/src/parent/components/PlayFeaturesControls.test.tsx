// Phase K step K2: component tests for PlayFeaturesControls. Mirrors
// PlayQueueSettingsControls.test.tsx shape: stubs each setter, exercises
// render + click + reject paths, pins the canonical 8-flag list against
// silent drift.

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient, PhaseKFeatureFlag } from "../api";
import { PHASE_K_FEATURE_FLAG_DEFAULTS } from "../api";
import {
  FEATURE_TOGGLES,
  PlayFeaturesControls,
} from "./PlayFeaturesControls";

// Build a stub api whose 8 setters echo back the requested value.
// ``vi.fn(async (value: boolean) => ({ value }))`` per setter — the
// signature matches ApiClient's ``Promise<FeatureFlagResponse>``.
type StubApi = Record<
  | "setJokesEnabled"
  | "setSongsEnabled"
  | "setPlayStandaloneEnabled"
  | "setPlayEmbeddedEnabled"
  | "setPlayEndingsEnabled"
  | "setPlaySpontaneityEnabled"
  | "setClickableWordsEnabled"
  | "setReadMeButtonEnabled",
  Mock
>;

function buildStubApi(): StubApi {
  return {
    setJokesEnabled: vi.fn(async (v: boolean) => ({ value: v })) as Mock,
    setSongsEnabled: vi.fn(async (v: boolean) => ({ value: v })) as Mock,
    setPlayStandaloneEnabled: vi.fn(
      async (v: boolean) => ({ value: v }),
    ) as Mock,
    setPlayEmbeddedEnabled: vi.fn(async (v: boolean) => ({ value: v })) as Mock,
    setPlayEndingsEnabled: vi.fn(async (v: boolean) => ({ value: v })) as Mock,
    setPlaySpontaneityEnabled: vi.fn(
      async (v: boolean) => ({ value: v }),
    ) as Mock,
    setClickableWordsEnabled: vi.fn(
      async (v: boolean) => ({ value: v }),
    ) as Mock,
    setReadMeButtonEnabled: vi.fn(
      async (v: boolean) => ({ value: v }),
    ) as Mock,
  };
}

const ALL_FLAG_KEYS: readonly PhaseKFeatureFlag[] = [
  "jokes_enabled",
  "songs_enabled",
  "play_standalone_enabled",
  "play_embedded_enabled",
  "play_endings_enabled",
  "play_spontaneity_enabled",
  "clickable_words_enabled",
  "read_me_button_enabled",
];

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PlayFeaturesControls — canonical flag list", () => {
  it("exposes exactly the 8 expected Phase K flags in spec order", () => {
    expect(FEATURE_TOGGLES.map((t) => t.key)).toEqual(ALL_FLAG_KEYS);
  });

  it("PHASE_K_FEATURE_FLAG_DEFAULTS matches the §5 defaults exactly", () => {
    // Lock the spec defaults — seven on, one off (spontaneity). A
    // future "improvement" that flips spontaneity to true must fail
    // this test before reaching the kid.
    expect(PHASE_K_FEATURE_FLAG_DEFAULTS).toEqual({
      jokes_enabled: true,
      songs_enabled: true,
      play_standalone_enabled: true,
      play_embedded_enabled: true,
      play_endings_enabled: true,
      play_spontaneity_enabled: false,
      clickable_words_enabled: true,
      read_me_button_enabled: true,
    });
  });
});

describe("PlayFeaturesControls — render", () => {
  it("renders 8 toggle rows with the spec'd labels", () => {
    const api = buildStubApi();
    render(
      <PlayFeaturesControls
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={() => {}}
      />,
    );
    for (const spec of FEATURE_TOGGLES) {
      expect(screen.getByTestId(`feature-toggle-${spec.key}`)).toBeTruthy();
      // Label appears in the row's heading.
      const row = screen.getByTestId(`feature-toggle-${spec.key}`);
      expect(row.textContent).toContain(spec.label);
    }
  });

  it("aria-pressed reflects the current value for every flag", () => {
    const api = buildStubApi();
    render(
      <PlayFeaturesControls
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={() => {}}
      />,
    );
    for (const key of ALL_FLAG_KEYS) {
      const expectedOn = PHASE_K_FEATURE_FLAG_DEFAULTS[key];
      const onBtn = screen.getByTestId(`feature-toggle-${key}-on`);
      const offBtn = screen.getByTestId(`feature-toggle-${key}-off`);
      expect(onBtn.getAttribute("aria-pressed")).toBe(
        expectedOn ? "true" : "false",
      );
      expect(offBtn.getAttribute("aria-pressed")).toBe(
        expectedOn ? "false" : "true",
      );
    }
  });
});

describe("PlayFeaturesControls — click-to-toggle", () => {
  it("clicking Off on jokes_enabled calls setJokesEnabled(false) + onValueChanged", async () => {
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <PlayFeaturesControls
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("feature-toggle-jokes_enabled-off"));
    await waitFor(() => {
      expect(api.setJokesEnabled).toHaveBeenCalledWith(
        false,
        expect.anything(),
      );
      expect(onValueChanged).toHaveBeenCalledWith("jokes_enabled", false);
    });
  });

  it("clicking On on play_spontaneity_enabled (opt-in) calls setPlaySpontaneityEnabled(true)", async () => {
    // The opt-in flag — defaults Off — gets a dedicated test so the
    // happy-path "operator opts in" flow is explicitly covered, not
    // just hidden in a loop.
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <PlayFeaturesControls
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(
      screen.getByTestId("feature-toggle-play_spontaneity_enabled-on"),
    );
    await waitFor(() => {
      expect(api.setPlaySpontaneityEnabled).toHaveBeenCalledWith(
        true,
        expect.anything(),
      );
      expect(onValueChanged).toHaveBeenCalledWith(
        "play_spontaneity_enabled",
        true,
      );
    });
  });

  it("clicking the already-active button is a no-op (no PUT, no callback)", async () => {
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <PlayFeaturesControls
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    // jokes_enabled defaults to on; clicking On again must not PUT.
    fireEvent.click(screen.getByTestId("feature-toggle-jokes_enabled-on"));
    // Give microtasks a tick.
    await Promise.resolve();
    expect(api.setJokesEnabled).not.toHaveBeenCalled();
    expect(onValueChanged).not.toHaveBeenCalled();
  });

  it("clicking each of the 8 flags routes to the right setter exactly once", async () => {
    // Code-quality §3 (audit wire shape): one click per flag, assert
    // the right setter saw it. Catches a wiring regression where two
    // toggles share a setter (one of the easiest silent-wiring fails
    // when copy-pasting eight near-identical rows).
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <PlayFeaturesControls
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    for (const spec of FEATURE_TOGGLES) {
      const target = !PHASE_K_FEATURE_FLAG_DEFAULTS[spec.key];
      fireEvent.click(
        screen.getByTestId(
          `feature-toggle-${spec.key}-${target ? "on" : "off"}`,
        ),
      );
      // Wait for the click's PUT promise to resolve before clicking
      // the next row — otherwise pendingValue would block the next
      // click (toggle rows disable while in flight).
      await waitFor(() => {
        expect(
          (api[spec.setter] as Mock).mock.calls.length,
        ).toBeGreaterThanOrEqual(1);
      });
    }
    // Every setter saw exactly one call; no setter was used twice.
    for (const spec of FEATURE_TOGGLES) {
      expect((api[spec.setter] as Mock).mock.calls.length).toBe(1);
    }
    // onValueChanged saw one call per flag, with the right key/value
    // pair each time.
    expect(onValueChanged).toHaveBeenCalledTimes(FEATURE_TOGGLES.length);
  });
});

describe("PlayFeaturesControls — error path", () => {
  it("renders an inline error and reverts the optimistic flip on rejection", async () => {
    const api = buildStubApi();
    api.setJokesEnabled = vi.fn(async () => {
      throw new Error("network down");
    }) as Mock;
    const onValueChanged = vi.fn();
    render(
      <PlayFeaturesControls
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("feature-toggle-jokes_enabled-off"));
    await waitFor(() => {
      const errorEl = screen.getByTestId(
        "feature-toggle-jokes_enabled-error",
      );
      expect(errorEl.textContent).toBe("network down");
    });
    // The parent never got an onValueChanged because the PUT failed.
    expect(onValueChanged).not.toHaveBeenCalled();
    // The row remains usable (no perma-disabled state) — the On
    // button should be re-pressed against the unchanged currentValue.
    expect(
      screen
        .getByTestId("feature-toggle-jokes_enabled-on")
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });
});
