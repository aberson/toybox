// Phase L Step L8: component tests for RewardsSection. Mirrors the
// shape of PlayFeaturesControls.test.tsx — stubs each setter, asserts
// render + click + reject paths. The header carries two master
// toggles (jokes_enabled + songs_enabled) plus the L7 RewardIngest
// panel; we verify the toggles work and the panel mounts.

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ApiClient,
  PhaseKFeatureFlag,
  Reward,
  RewardListResponse,
} from "../api";
import { PHASE_K_FEATURE_FLAG_DEFAULTS } from "../api";
import {
  REWARD_MASTER_TOGGLES,
  RewardsSection,
} from "./RewardsSection";

// jsdom/happy-dom doesn't ship URL.createObjectURL — RewardIngest uses
// it for the file-picker preview, so stub it here too (RewardIngest
// has its own dedicated tests that exercise the upload path; here we
// just need the panel to mount cleanly inside RewardsSection).
beforeEach(() => {
  Object.defineProperty(URL, "createObjectURL", {
    writable: true,
    value: vi.fn().mockReturnValue("blob:mock-preview"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    writable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// Stub ApiClient surface: the two master-toggle setters + the
// RewardIngest list/upload/confirm/update methods. The full ApiClient
// type is wide; we cast through ``unknown`` so we only have to mock
// what the section actually touches.
interface StubApi {
  setJokesEnabled: Mock;
  setSongsEnabled: Mock;
  listRewards: Mock;
  uploadReward: Mock;
  confirmReward: Mock;
  updateReward: Mock;
}

function buildStubApi(rewards: Reward[] = []): StubApi {
  return {
    setJokesEnabled: vi.fn(async (value: boolean) => ({ value })) as Mock,
    setSongsEnabled: vi.fn(async (value: boolean) => ({ value })) as Mock,
    listRewards: vi.fn(
      async (): Promise<RewardListResponse> => ({ rewards }),
    ) as Mock,
    uploadReward: vi.fn() as Mock,
    confirmReward: vi.fn() as Mock,
    updateReward: vi.fn() as Mock,
  };
}

describe("RewardsSection — canonical master-toggle list", () => {
  it("declares exactly two master toggles: jokes_enabled then songs_enabled", () => {
    // Pin the toggle list size + order. L8 spec calls out "two
    // toggles"; a regression that adds a third without updating the
    // plan should fail here so the author re-reads the section
    // contract before shipping. Mirrors the PlayFeaturesControls
    // "FEATURE_TOGGLES = 3 entries" pin.
    expect(REWARD_MASTER_TOGGLES.map((t) => t.key)).toEqual([
      "jokes_enabled",
      "songs_enabled",
    ]);
  });

  it("hint copy matches the L8-spec strings exactly", () => {
    // Per L8 spec: "Jokes can fire as activity-end rewards (and
    // standalone if enabled)" / "Songs can fire as activity-end
    // rewards (and standalone if enabled)". Pin verbatim so a
    // well-meaning copy-edit doesn't drift the section's voice.
    const byKey = new Map(
      REWARD_MASTER_TOGGLES.map((t) => [t.key, t.hint] as const),
    );
    expect(byKey.get("jokes_enabled")).toBe(
      "Jokes can fire as activity-end rewards (and standalone if enabled)",
    );
    expect(byKey.get("songs_enabled")).toBe(
      "Songs can fire as activity-end rewards (and standalone if enabled)",
    );
  });
});

describe("RewardsSection — render", () => {
  it("renders the section header, both master toggles, and the RewardIngest panel", async () => {
    const api = buildStubApi();
    render(
      <RewardsSection
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={() => {}}
      />,
    );
    expect(screen.getByTestId("rewards-section")).toBeTruthy();
    expect(
      screen.getByTestId("reward-master-toggle-jokes_enabled"),
    ).toBeTruthy();
    expect(
      screen.getByTestId("reward-master-toggle-songs_enabled"),
    ).toBeTruthy();
    // The L7 RewardIngest panel mounts inside the section.
    await waitFor(() => {
      expect(screen.getByTestId("reward-ingest")).toBeTruthy();
    });
    // RewardIngest fired its list probe on mount.
    expect(api.listRewards).toHaveBeenCalled();
  });

  it("aria-pressed reflects the current value for both master toggles", () => {
    const api = buildStubApi();
    render(
      <RewardsSection
        api={api as unknown as ApiClient}
        values={{
          ...PHASE_K_FEATURE_FLAG_DEFAULTS,
          jokes_enabled: true,
          songs_enabled: false,
        }}
        onValueChanged={() => {}}
      />,
    );
    // jokes: On is pressed, Off is not.
    expect(
      screen
        .getByTestId("reward-master-toggle-jokes_enabled-on")
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen
        .getByTestId("reward-master-toggle-jokes_enabled-off")
        .getAttribute("aria-pressed"),
    ).toBe("false");
    // songs: Off is pressed, On is not.
    expect(
      screen
        .getByTestId("reward-master-toggle-songs_enabled-on")
        .getAttribute("aria-pressed"),
    ).toBe("false");
    expect(
      screen
        .getByTestId("reward-master-toggle-songs_enabled-off")
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("displays the L8 hint copy under each toggle label", () => {
    const api = buildStubApi();
    render(
      <RewardsSection
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={() => {}}
      />,
    );
    const jokesRow = screen.getByTestId("reward-master-toggle-jokes_enabled");
    const songsRow = screen.getByTestId("reward-master-toggle-songs_enabled");
    expect(jokesRow.textContent).toContain(
      "Jokes can fire as activity-end rewards (and standalone if enabled)",
    );
    expect(songsRow.textContent).toContain(
      "Songs can fire as activity-end rewards (and standalone if enabled)",
    );
  });
});

describe("RewardsSection — click-to-toggle", () => {
  it("clicking Off on jokes_enabled calls setJokesEnabled(false) + onValueChanged", async () => {
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <RewardsSection
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(
      screen.getByTestId("reward-master-toggle-jokes_enabled-off"),
    );
    await waitFor(() => {
      expect(api.setJokesEnabled).toHaveBeenCalledWith(
        false,
        expect.anything(),
      );
      expect(onValueChanged).toHaveBeenCalledWith("jokes_enabled", false);
    });
  });

  it("clicking Off on songs_enabled calls setSongsEnabled(false) + onValueChanged", async () => {
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <RewardsSection
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(
      screen.getByTestId("reward-master-toggle-songs_enabled-off"),
    );
    await waitFor(() => {
      expect(api.setSongsEnabled).toHaveBeenCalledWith(
        false,
        expect.anything(),
      );
      expect(onValueChanged).toHaveBeenCalledWith("songs_enabled", false);
    });
  });

  it("clicking the already-active button is a no-op (no PUT, no callback)", async () => {
    const api = buildStubApi();
    const onValueChanged = vi.fn();
    render(
      <RewardsSection
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    // jokes defaults to on; clicking On again must NOT fire a PUT.
    fireEvent.click(
      screen.getByTestId("reward-master-toggle-jokes_enabled-on"),
    );
    await Promise.resolve();
    expect(api.setJokesEnabled).not.toHaveBeenCalled();
    expect(onValueChanged).not.toHaveBeenCalled();
  });

  it("disables both buttons while a PUT is in flight + re-enables on response", async () => {
    // Capture the setter promise resolver so we can hold the PUT
    // open and observe the disabled state.
    let resolveJokes!: (resp: { value: boolean }) => void;
    const api = buildStubApi();
    api.setJokesEnabled = vi.fn(
      () =>
        new Promise<{ value: boolean }>((resolve) => {
          resolveJokes = resolve;
        }),
    ) as Mock;
    render(
      <RewardsSection
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={() => {}}
      />,
    );
    fireEvent.click(
      screen.getByTestId("reward-master-toggle-jokes_enabled-off"),
    );
    // While the PUT is in flight, both Off and On are disabled.
    const offBtn = screen.getByTestId(
      "reward-master-toggle-jokes_enabled-off",
    ) as HTMLButtonElement;
    const onBtn = screen.getByTestId(
      "reward-master-toggle-jokes_enabled-on",
    ) as HTMLButtonElement;
    expect(offBtn.disabled).toBe(true);
    expect(onBtn.disabled).toBe(true);
    // Resolve the PUT; the row re-enables.
    resolveJokes({ value: false });
    await waitFor(() => {
      expect(offBtn.disabled).toBe(false);
      expect(onBtn.disabled).toBe(false);
    });
  });

  it("reads current values from props, not internal state", async () => {
    // Pass jokes_enabled=false from the parent and verify the section
    // paints Off as pressed without ever firing a PUT. Catches a
    // regression where the section drifts to a local state copy and
    // ignores parent updates.
    const api = buildStubApi();
    render(
      <RewardsSection
        api={api as unknown as ApiClient}
        values={{
          ...PHASE_K_FEATURE_FLAG_DEFAULTS,
          jokes_enabled: false,
        }}
        onValueChanged={() => {}}
      />,
    );
    expect(
      screen
        .getByTestId("reward-master-toggle-jokes_enabled-off")
        .getAttribute("aria-pressed"),
    ).toBe("true");
    // No PUT just from mounting with a non-default value.
    expect(api.setJokesEnabled).not.toHaveBeenCalled();
  });
});

describe("RewardsSection — error path", () => {
  it("renders an inline error and reverts the optimistic flip on rejection", async () => {
    const api = buildStubApi();
    api.setJokesEnabled = vi.fn(async () => {
      throw new Error("network down");
    }) as Mock;
    const onValueChanged = vi.fn();
    render(
      <RewardsSection
        api={api as unknown as ApiClient}
        values={PHASE_K_FEATURE_FLAG_DEFAULTS}
        onValueChanged={onValueChanged}
      />,
    );
    fireEvent.click(
      screen.getByTestId("reward-master-toggle-jokes_enabled-off"),
    );
    await waitFor(() => {
      const errorEl = screen.getByTestId(
        "reward-master-toggle-jokes_enabled-error",
      );
      expect(errorEl.textContent).toBe("network down");
    });
    // Parent never saw a successful change.
    expect(onValueChanged).not.toHaveBeenCalled();
    // The lifted value (jokes_enabled=true) still drives display, so
    // On remains pressed.
    expect(
      screen
        .getByTestId("reward-master-toggle-jokes_enabled-on")
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });
});

describe("RewardsSection — keys provided per spec match the PhaseKFeatureFlag union", () => {
  it("every master-toggle key is a member of PhaseKFeatureFlag", () => {
    // Compile-time guarantee via the spec type, but pin at runtime
    // so a future widening of the union without adding the key here
    // surfaces in test output.
    const validKeys: ReadonlyArray<PhaseKFeatureFlag> = [
      "jokes_enabled",
      "songs_enabled",
      "play_standalone_enabled",
      "clickable_words_enabled",
      "read_me_button_enabled",
    ];
    for (const spec of REWARD_MASTER_TOGGLES) {
      expect(validKeys).toContain(spec.key);
    }
  });
});
