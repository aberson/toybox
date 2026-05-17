// Component tests for the Phase J step J8 play-queue list.
//
// PlayQueueList owns the layout (pinned active + scrolling proposed
// rows) and the TTL fade machinery copied from TranscriptsManager. The
// component is purely a presentational shell over ``ActivityPanel`` +
// ``SuggestionCard`` — these tests pin the rendering matrix and the
// fade timing without going through the store.
//
// Fake-timer pattern: ``vi.setSystemTime`` pins Date.now so the row's
// ``created_at`` is positioned relative to a known wall-clock origin;
// ``vi.advanceTimersByTimeAsync`` then advances both the timer queue
// and the system time so the 1s tick + 600ms removal setTimeout fire
// in lockstep. Mirrors TranscriptsManager.test.tsx.

import {
  act,
  cleanup,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Mock } from "vitest";

import type { Activity, RewardType } from "../api";
import { PlayQueueList } from "./PlayQueueList";

function fakeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    id: "a-1",
    state: "proposed",
    version: 1,
    title: "Build a fort",
    summary: null,
    persona_id: null,
    intent_source: "request_play",
    child_ids: [],
    created_at: new Date().toISOString(),
    started_at: null,
    ended_at: null,
    steps: [
      { seq: 1, body: "Gather pillows", sfx: null, expected_action: null, current: false },
    ],
    metadata: {},
    trigger_phrase: null,
    persona_reasoning: null,
    ...overrides,
  };
}

function fakeActiveActivity(overrides: Partial<Activity> = {}): Activity {
  return fakeActivity({
    id: "active-1",
    state: "running",
    steps: [
      {
        seq: 1,
        body: "Build the fort",
        sfx: null,
        expected_action: null,
        current: true,
      },
    ],
    ...overrides,
  });
}

interface Handlers {
  // Phase L L9: onApprove now carries the reward-type selection
  // alongside the activity. Pre-L9 the signature was [Activity];
  // updated here so the mock matches the PlayQueueList prop.
  onApprove: Mock<[Activity, RewardType], Promise<void>>;
  onDismiss: Mock<[Activity], Promise<void>>;
  onRegenerate: Mock<[Activity], Promise<void>>;
  onEnd: Mock<[Activity], Promise<void>>;
  onStepBack: Mock<[Activity], Promise<void>>;
  onDidntWork: Mock<[Activity], Promise<void>>;
  onThumbsUp: Mock<[Activity], Promise<void>>;
  onRecast: Mock<[Activity], Promise<void>>;
  onNewActivity: Mock<[Activity], Promise<void>>;
}

function buildHandlers(): Handlers {
  const noop = async (_target: Activity): Promise<void> => undefined;
  const approveNoop = async (
    _target: Activity,
    _rewardType: RewardType,
  ): Promise<void> => undefined;
  return {
    onApprove: vi.fn(approveNoop),
    onDismiss: vi.fn(noop),
    onRegenerate: vi.fn(noop),
    onEnd: vi.fn(noop),
    onStepBack: vi.fn(noop),
    onDidntWork: vi.fn(noop),
    onThumbsUp: vi.fn(noop),
    onRecast: vi.fn(noop),
    onNewActivity: vi.fn(noop),
  };
}

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.restoreAllMocks();
});

describe("PlayQueueList layout matrix", () => {
  it("empty: no active and no proposed renders an empty container", () => {
    const handlers = buildHandlers();
    render(
      <PlayQueueList
        active={null}
        proposedList={[]}
        cadenceSeconds={30}
        {...handlers}
      />,
    );
    // Container is present (so the slot for the list still exists in
    // the DOM) but no rows / panel are rendered inside.
    expect(screen.getByTestId("play-queue-list")).toBeTruthy();
    expect(screen.queryByTestId("activity-panel")).toBeNull();
    expect(screen.queryByTestId("suggestion-card")).toBeNull();
    expect(screen.queryByTestId("play-queue-row")).toBeNull();
  });

  it("active only: ActivityPanel renders, no SuggestionCards", () => {
    const handlers = buildHandlers();
    render(
      <PlayQueueList
        active={fakeActiveActivity()}
        proposedList={[]}
        cadenceSeconds={30}
        {...handlers}
      />,
    );
    expect(screen.getByTestId("activity-panel")).toBeTruthy();
    expect(screen.queryByTestId("suggestion-card")).toBeNull();
  });

  it("proposed only: each row renders a SuggestionCard, no ActivityPanel", () => {
    const handlers = buildHandlers();
    const rows = [
      fakeActivity({ id: "p-1", title: "Option 1" }),
      fakeActivity({ id: "p-2", title: "Option 2" }),
      fakeActivity({ id: "p-3", title: "Option 3" }),
    ];
    render(
      <PlayQueueList
        active={null}
        proposedList={rows}
        cadenceSeconds={30}
        {...handlers}
      />,
    );
    expect(screen.queryByTestId("activity-panel")).toBeNull();
    expect(screen.getAllByTestId("suggestion-card")).toHaveLength(3);
  });

  it("both: ActivityPanel pinned at top + SuggestionCards below", () => {
    const handlers = buildHandlers();
    render(
      <PlayQueueList
        active={fakeActiveActivity()}
        proposedList={[
          fakeActivity({ id: "p-1" }),
          fakeActivity({ id: "p-2" }),
        ]}
        cadenceSeconds={30}
        {...handlers}
      />,
    );
    expect(screen.getByTestId("activity-panel")).toBeTruthy();
    expect(screen.getAllByTestId("suggestion-card")).toHaveLength(2);
    // Active row sits above the proposed rows. Assert DOM ordering by
    // index inside the list container.
    const list = screen.getByTestId("play-queue-list");
    const children = Array.from(list.children);
    // First child should be the ActivityPanel.
    expect(children[0]?.getAttribute("data-testid")).toBe("activity-panel");
    // Subsequent children should be play-queue-row wrappers.
    expect(children[1]?.getAttribute("data-testid")).toBe("play-queue-row");
    expect(children[2]?.getAttribute("data-testid")).toBe("play-queue-row");
  });
});

describe("PlayQueueList TTL fade machinery", () => {
  it("fades a row past its TTL and removes it after the 600ms transition", async () => {
    const handlers = buildHandlers();
    const baseNow = new Date("2026-05-12T12:00:00Z").getTime();
    vi.useFakeTimers();
    vi.setSystemTime(baseNow);
    // 100 seconds old, cadence 10s → TTL = 3 × 10 = 30s. So the row
    // is well past its TTL and should fade on the first tick.
    const createdAt = new Date(baseNow - 100_000).toISOString();
    const row = fakeActivity({
      id: "p-old",
      title: "Stale suggestion",
      created_at: createdAt,
    });
    render(
      <PlayQueueList
        active={null}
        proposedList={[row]}
        cadenceSeconds={10}
        {...handlers}
      />,
    );
    // Pre-tick: row exists with no fading flag.
    expect(screen.getByTestId("play-queue-row").getAttribute("data-fading")).toBe(
      "false",
    );
    // Advance 1s — the tick fires, flips the row into fadingIds, and
    // queues the 600ms removal.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    const fadingRow = screen.getByTestId("play-queue-row");
    expect(fadingRow.getAttribute("data-fading")).toBe("true");
    expect((fadingRow as HTMLElement).style.opacity).toBe("0");

    // Advance past the 600ms transition — row is removed from the DOM.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(screen.queryByTestId("play-queue-row")).toBeNull();
  });

  it("does not fade rows when cadenceSeconds=0 (fade disabled)", async () => {
    const handlers = buildHandlers();
    const baseNow = new Date("2026-05-12T12:00:00Z").getTime();
    vi.useFakeTimers();
    vi.setSystemTime(baseNow);
    // Row is 1000s old — past any plausible TTL under any non-zero
    // cadence — but cadence is 0 so the tick is disabled entirely.
    const ancient = new Date(baseNow - 1_000_000).toISOString();
    const row = fakeActivity({
      id: "p-eternal",
      title: "Always visible",
      created_at: ancient,
    });
    render(
      <PlayQueueList
        active={null}
        proposedList={[row]}
        cadenceSeconds={0}
        {...handlers}
      />,
    );
    // Advance an arbitrarily long time — the row should NEVER fade
    // and should NEVER be removed.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    const persistedRow = screen.getByTestId("play-queue-row");
    expect(persistedRow.getAttribute("data-fading")).toBe("false");
    expect((persistedRow as HTMLElement).style.opacity).not.toBe("0");
  });

  it("clears removal timeouts on unmount (no leftover setTimeout queue)", async () => {
    const handlers = buildHandlers();
    const baseNow = new Date("2026-05-12T12:00:00Z").getTime();
    vi.useFakeTimers();
    vi.setSystemTime(baseNow);
    const row = fakeActivity({
      id: "p-unmount",
      created_at: new Date(baseNow - 100_000).toISOString(),
    });
    const { unmount } = render(
      <PlayQueueList
        active={null}
        proposedList={[row]}
        cadenceSeconds={10}
        {...handlers}
      />,
    );
    // Advance enough to flip the row into the fading set and queue the
    // 600ms removal setTimeout — but unmount BEFORE the 600ms elapses.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    unmount();
    // After unmount, no pending fake timers should still fire. The
    // cleanest cross-runtime assertion is ``getTimerCount`` — both the
    // 1s interval and any queued removal setTimeout should be 0
    // (cleared by the effect's cleanup).
    expect(vi.getTimerCount()).toBe(0);
  });

  it("SuggestionCard rendered inside shows 'try a different one' label", () => {
    const handlers = buildHandlers();
    render(
      <PlayQueueList
        active={null}
        proposedList={[fakeActivity({ id: "p-label" })]}
        cadenceSeconds={30}
        {...handlers}
      />,
    );
    // The skip-button (J8 relabel: "skip" → "try a different one")
    // text must reflect the new copy. ``data-testid`` is unchanged so
    // the existing wiring still works.
    const button = screen.getByTestId("skip-button");
    expect(button.textContent).toContain("try a different one");
  });
});
