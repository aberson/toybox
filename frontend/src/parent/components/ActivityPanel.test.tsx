// Component tests for the Step 23 ActivityPanel End-confirm dialog.

import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Activity } from "../api";
import { ActivityPanel } from "./ActivityPanel";

function fakeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    id: "a-1",
    state: "running",
    version: 3,
    title: "Unicorn Adventure",
    summary: null,
    persona_id: "p-unicorn",
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    ended_at: null,
    steps: [
      { seq: 1, body: "Step 1", sfx: null, expected_action: null, current: true },
    ],
    metadata: {},
    trigger_phrase: null,
    persona_reasoning: null,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  // Default to "yes" for tests; per-test overrides flip to false.
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

function fakeActivityAtSeq(
  seq: number,
  overrides: Partial<Activity> = {},
): Activity {
  const steps = [1, 2, 3, 4, 5].map((i) => ({
    seq: i,
    body: `Step ${i}`,
    sfx: null,
    expected_action: null,
    current: i === seq,
  }));
  return fakeActivity({ steps, ...overrides });
}

describe("ActivityPanel cast row", () => {
  it("renders nothing when activity has no roles", () => {
    render(
      <ActivityPanel
        activity={fakeActivity()}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
      />,
    );
    expect(screen.queryByTestId("activity-cast")).toBeNull();
  });

  it("renders nothing when activity.roles is an empty record", () => {
    render(
      <ActivityPanel
        activity={fakeActivity({ roles: {} })}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
      />,
    );
    expect(screen.queryByTestId("activity-cast")).toBeNull();
  });

  it("renders comma-separated display names sorted by role_name", () => {
    render(
      <ActivityPanel
        activity={fakeActivity({
          roles: {
            quest_giver: {
              role_name: "quest_giver",
              toy_id: "t-owl",
              generic_descriptor: null,
              display_name: "Wise Owl",
            },
            friend: {
              role_name: "friend",
              toy_id: "t-bear",
              generic_descriptor: null,
              display_name: "Captain Bear",
            },
          },
        })}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
      />,
    );
    const cast = screen.getByTestId("activity-cast");
    expect(cast.textContent).toBe("cast: Captain Bear, Wise Owl");
  });

  it("deduplicates one toy filling two roles", () => {
    render(
      <ActivityPanel
        activity={fakeActivity({
          roles: {
            guide_mentor: {
              role_name: "guide_mentor",
              toy_id: "t-snowball",
              generic_descriptor: null,
              display_name: "Snowball",
            },
            friend: {
              role_name: "friend",
              toy_id: "t-snowball",
              generic_descriptor: null,
              display_name: "Snowball",
            },
          },
        })}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
      />,
    );
    const cast = screen.getByTestId("activity-cast");
    expect(cast.textContent).toBe("cast: Snowball");
  });

  it("includes generic-descriptor roles by display_name", () => {
    render(
      <ActivityPanel
        activity={fakeActivity({
          roles: {
            guide_mentor: {
              role_name: "guide_mentor",
              toy_id: null,
              generic_descriptor: "a kindly mentor",
              display_name: "a kindly mentor",
            },
          },
        })}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
      />,
    );
    const cast = screen.getByTestId("activity-cast");
    expect(cast.textContent).toBe("cast: a kindly mentor");
  });
});

describe("ActivityPanel Step Back", () => {
  it("renders the Step Back button when onStepBack is supplied", () => {
    render(
      <ActivityPanel
        activity={fakeActivityAtSeq(2)}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
        onStepBack={async () => undefined}
      />,
    );
    const btn = screen.getByTestId("step-back-button") as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(false);
  });

  it("clicking Step Back fires onStepBack", () => {
    const onStepBack = vi.fn(async (): Promise<void> => undefined);
    render(
      <ActivityPanel
        activity={fakeActivityAtSeq(3)}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
        onStepBack={onStepBack}
      />,
    );
    fireEvent.click(screen.getByTestId("step-back-button"));
    expect(onStepBack).toHaveBeenCalledTimes(1);
  });

  it("disables Step Back when current seq is 1", () => {
    render(
      <ActivityPanel
        activity={fakeActivityAtSeq(1)}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
        onStepBack={async () => undefined}
      />,
    );
    const btn = screen.getByTestId("step-back-button") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("disables Step Back when activity state is approved", () => {
    render(
      <ActivityPanel
        activity={fakeActivityAtSeq(2, { state: "approved" })}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
        onStepBack={async () => undefined}
      />,
    );
    const btn = screen.getByTestId("step-back-button") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});

describe("ActivityPanel K15 parent-insert sidebar", () => {
  it("renders both insert buttons when handlers are supplied", () => {
    render(
      <ActivityPanel
        activity={fakeActivity()}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
        onInsertJoke={async () => undefined}
        onInsertSong={async () => undefined}
        jokesEnabled={true}
        songsEnabled={true}
      />,
    );
    expect(screen.getByTestId("insert-joke-button")).toBeTruthy();
    expect(screen.getByTestId("insert-song-button")).toBeTruthy();
  });

  it("hides the sidebar when no insert handlers are supplied", () => {
    render(
      <ActivityPanel
        activity={fakeActivity()}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
      />,
    );
    expect(screen.queryByTestId("activity-insert-sidebar")).toBeNull();
  });

  it("clicking insert-joke fires onInsertJoke", () => {
    const onInsertJoke = vi.fn(async (): Promise<void> => undefined);
    render(
      <ActivityPanel
        activity={fakeActivity()}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
        onInsertJoke={onInsertJoke}
        onInsertSong={async () => undefined}
        jokesEnabled={true}
        songsEnabled={true}
      />,
    );
    fireEvent.click(screen.getByTestId("insert-joke-button"));
    expect(onInsertJoke).toHaveBeenCalledTimes(1);
  });

  it("clicking insert-song fires onInsertSong", () => {
    const onInsertSong = vi.fn(async (): Promise<void> => undefined);
    render(
      <ActivityPanel
        activity={fakeActivity()}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
        onInsertJoke={async () => undefined}
        onInsertSong={onInsertSong}
        jokesEnabled={true}
        songsEnabled={true}
      />,
    );
    fireEvent.click(screen.getByTestId("insert-song-button"));
    expect(onInsertSong).toHaveBeenCalledTimes(1);
  });

  it("greys insert-joke when jokesEnabled is false", () => {
    render(
      <ActivityPanel
        activity={fakeActivity()}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
        onInsertJoke={async () => undefined}
        onInsertSong={async () => undefined}
        jokesEnabled={false}
        songsEnabled={true}
      />,
    );
    const jokeBtn = screen.getByTestId("insert-joke-button") as HTMLButtonElement;
    const songBtn = screen.getByTestId("insert-song-button") as HTMLButtonElement;
    expect(jokeBtn.disabled).toBe(true);
    expect(songBtn.disabled).toBe(false);
  });

  it("greys insert-song when songsEnabled is false", () => {
    render(
      <ActivityPanel
        activity={fakeActivity()}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
        onInsertJoke={async () => undefined}
        onInsertSong={async () => undefined}
        jokesEnabled={true}
        songsEnabled={false}
      />,
    );
    const songBtn = screen.getByTestId("insert-song-button") as HTMLButtonElement;
    expect(songBtn.disabled).toBe(true);
  });

  it("greys both insert buttons when activity state is approved", () => {
    render(
      <ActivityPanel
        activity={fakeActivity({ state: "approved" })}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
        onInsertJoke={async () => undefined}
        onInsertSong={async () => undefined}
        jokesEnabled={true}
        songsEnabled={true}
      />,
    );
    const jokeBtn = screen.getByTestId("insert-joke-button") as HTMLButtonElement;
    const songBtn = screen.getByTestId("insert-song-button") as HTMLButtonElement;
    expect(jokeBtn.disabled).toBe(true);
    expect(songBtn.disabled).toBe(true);
  });

  it("greys both insert buttons when activity state is paused (state allowed) -- not greyed", () => {
    render(
      <ActivityPanel
        activity={fakeActivity({ state: "paused" })}
        onRegenerate={async () => undefined}
        onEnd={async () => undefined}
        onDidntWork={async () => undefined}
        onInsertJoke={async () => undefined}
        onInsertSong={async () => undefined}
        jokesEnabled={true}
        songsEnabled={true}
      />,
    );
    const jokeBtn = screen.getByTestId("insert-joke-button") as HTMLButtonElement;
    expect(jokeBtn.disabled).toBe(false);
  });
});

describe("ActivityPanel End confirm", () => {
  it("clicking End opens a confirm dialog", () => {
    const onEnd = vi.fn(async (): Promise<void> => undefined);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <ActivityPanel
        activity={fakeActivity()}
        onRegenerate={async () => undefined}
        onEnd={onEnd}
        onDidntWork={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("end-button"));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy.mock.calls[0]?.[0]).toMatch(/end the activity/i);
  });

  it("Yes on the confirm calls onEnd", () => {
    const onEnd = vi.fn(async (): Promise<void> => undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <ActivityPanel
        activity={fakeActivity()}
        onRegenerate={async () => undefined}
        onEnd={onEnd}
        onDidntWork={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("end-button"));
    expect(onEnd).toHaveBeenCalledTimes(1);
  });

  it("Cancel on the confirm keeps the activity running", () => {
    // The user confirmed the modal with No — onEnd MUST NOT fire.
    // This is the load-bearing case for a parent who clicks End by
    // accident; we don't want a click-and-confirm UX without a way
    // back.
    const onEnd = vi.fn(async (): Promise<void> => undefined);
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <ActivityPanel
        activity={fakeActivity()}
        onRegenerate={async () => undefined}
        onEnd={onEnd}
        onDidntWork={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("end-button"));
    expect(onEnd).not.toHaveBeenCalled();
  });

});
