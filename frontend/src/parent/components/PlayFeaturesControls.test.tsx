// Phase K step K2 (Phase L step L8): component tests for
// PlayFeaturesControls. Mirrors PlayQueueSettingsControls.test.tsx
// shape: stubs each setter, exercises render + click + reject paths,
// pins the canonical flag list against silent drift.
//
// L8 reduced the FEATURE_TOGGLES list from FIVE entries to THREE: the
// ``jokes_enabled`` + ``songs_enabled`` master toggles moved out of
// this component and into ``RewardsSection`` (Kids & Toyboxes → Rewards).
// Phase Z Z6 then added the ``neural_voice_enabled`` toggle (→ FOUR).
// The error-path test that used to drive ``jokes_enabled`` now drives
// ``play_standalone_enabled`` — the wiring is identical so any
// surviving regression still surfaces here.

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

// Build a stub api whose setters echo back the requested value.
// ``vi.fn(async (value: boolean) => ({ value }))`` per setter — the
// signature matches ApiClient's ``Promise<FeatureFlagResponse>``.
type StubApi = Record<
  | "setPlayStandaloneEnabled"
  | "setClickableWordsEnabled"
  | "setReadMeButtonEnabled"
  | "setNeuralVoiceEnabled",
  Mock
>;

function buildStubApi(): StubApi {
  return {
    setPlayStandaloneEnabled: vi.fn(
      async (v: boolean) => ({ value: v }),
    ) as Mock,
    setClickableWordsEnabled: vi.fn(
      async (v: boolean) => ({ value: v }),
    ) as Mock,
    setReadMeButtonEnabled: vi.fn(
      async (v: boolean) => ({ value: v }),
    ) as Mock,
    setNeuralVoiceEnabled: vi.fn(
      async (v: boolean) => ({ value: v }),
    ) as Mock,
  };
}

// The four flags this component owns after Phase L Step L8 + Phase Z
// Z6 (``neural_voice_enabled``). ``jokes_enabled`` + ``songs_enabled``
// are validated by RewardsSection.test.tsx.
const ALL_FLAG_KEYS: readonly PhaseKFeatureFlag[] = [
  "play_standalone_enabled",
  "clickable_words_enabled",
  "read_me_button_enabled",
  "neural_voice_enabled",
];

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PlayFeaturesControls — canonical flag list", () => {
  it("exposes exactly the expected Phase L L8 flags in spec order", () => {
    expect(FEATURE_TOGGLES.map((t) => t.key)).toEqual(ALL_FLAG_KEYS);
  });

  it("FEATURE_TOGGLES contains exactly four entries (NOT six — L8 moved jokes/songs to RewardsSection)", () => {
    // Pin against accidental re-introduction of the master toggles
    // that L8 moved out. A regression that re-adds ``jokes_enabled``
    // or ``songs_enabled`` here would silently produce duplicate
    // toggles (one in PlayFeaturesControls, one in RewardsSection)
    // racing the same lifted state. Code-quality §2: one source of
    // truth per data-shape constant. Phase Z Z6 added the
    // ``neural_voice_enabled`` toggle (three → four).
    expect(FEATURE_TOGGLES).toHaveLength(4);
    expect(FEATURE_TOGGLES.map((t) => t.key)).not.toContain("jokes_enabled");
    expect(FEATURE_TOGGLES.map((t) => t.key)).not.toContain("songs_enabled");
  });

  it("PHASE_K_FEATURE_FLAG_DEFAULTS matches the §5 defaults exactly", () => {
    // Lock the surviving Phase K defaults. Phase L Step L5 removed the
    // three play-surface flags (embedded/endings/spontaneity); Phase L
    // Step L8 moved the joke/song masters into ``RewardsSection`` but
    // the shared defaults dict still keys on all five so the bootstrap
    // can seed every flag from one Promise.allSettled pass. Every
    // remaining flag defaults to On.
    expect(PHASE_K_FEATURE_FLAG_DEFAULTS).toEqual({
      jokes_enabled: true,
      songs_enabled: true,
      play_standalone_enabled: true,
      clickable_words_enabled: true,
      read_me_button_enabled: true,
      neural_voice_enabled: true,
    });
  });
});

describe("PlayFeaturesControls — render", () => {
  it("renders one toggle row per spec'd flag with the right label", () => {
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
  it("clicking Off on play_standalone_enabled calls setPlayStandaloneEnabled(false) + onValueChanged", async () => {
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
      screen.getByTestId("feature-toggle-play_standalone_enabled-off"),
    );
    await waitFor(() => {
      expect(api.setPlayStandaloneEnabled).toHaveBeenCalledWith(
        false,
        expect.anything(),
      );
      expect(onValueChanged).toHaveBeenCalledWith(
        "play_standalone_enabled",
        false,
      );
    });
  });

  it("clicking Off on clickable_words_enabled calls setClickableWordsEnabled(false) + onValueChanged", async () => {
    // Companion to the play_standalone_enabled click test above —
    // exercises a second flag's setter wiring so a copy-paste mismatch
    // between two adjacent rows surfaces here rather than via the
    // more-fan-out "every flag clicks once" matrix test further down.
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
      screen.getByTestId("feature-toggle-clickable_words_enabled-off"),
    );
    await waitFor(() => {
      expect(api.setClickableWordsEnabled).toHaveBeenCalledWith(
        false,
        expect.anything(),
      );
      expect(onValueChanged).toHaveBeenCalledWith(
        "clickable_words_enabled",
        false,
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
    // play_standalone_enabled defaults to on; clicking On again must
    // not PUT.
    fireEvent.click(
      screen.getByTestId("feature-toggle-play_standalone_enabled-on"),
    );
    // Give microtasks a tick.
    await Promise.resolve();
    expect(api.setPlayStandaloneEnabled).not.toHaveBeenCalled();
    expect(onValueChanged).not.toHaveBeenCalled();
  });

  it("clicking each surviving flag routes to the right setter exactly once", async () => {
    // Code-quality §3 (audit wire shape): one click per flag, assert
    // the right setter saw it. Catches a wiring regression where two
    // toggles share a setter (the most common silent-wiring fail when
    // copy-pasting near-identical rows).
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
    api.setPlayStandaloneEnabled = vi.fn(async () => {
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
    fireEvent.click(
      screen.getByTestId("feature-toggle-play_standalone_enabled-off"),
    );
    await waitFor(() => {
      const errorEl = screen.getByTestId(
        "feature-toggle-play_standalone_enabled-error",
      );
      expect(errorEl.textContent).toBe("network down");
    });
    // The parent never got an onValueChanged because the PUT failed.
    expect(onValueChanged).not.toHaveBeenCalled();
    // The row remains usable (no perma-disabled state) — the On
    // button should be re-pressed against the unchanged currentValue.
    expect(
      screen
        .getByTestId("feature-toggle-play_standalone_enabled-on")
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });
});
