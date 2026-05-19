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
  // L follow-up Change E: third arg is the specific picture-reward id
  // (or null for "(any)" / non-picture types).
  onApprove: Mock<[Activity, RewardType, string | null], Promise<void>>;
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
    _rewardId: string | null,
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

// Phase O Step O1: PlayQueueList accepts a new optional
// ``filterCategory`` prop (``"adventures" | "elements" |
// "feelings-friends"`` or undefined). In O1 the prop is plumbed
// through the type signature but is a runtime no-op — the list still
// renders every row regardless of which value is passed. O2 will
// activate the filter; this suite pins the "prop accepted but no-op"
// contract so O2 has something to flip green.
//
// The type-level expectations are enforced by ``tsc`` (the test file
// passes ``filterCategory="adventures"`` etc. to the component; if the
// prop is missing from the public interface, the test file fails to
// compile under ``npm run typecheck`` which the project's CI gate
// runs alongside vitest). Runtime expectations are pinned by the
// counts below.
describe("PlayQueueList filterCategory prop (Phase O Step O1)", () => {
  it("accepts filterCategory='adventures' without crashing", () => {
    const handlers = buildHandlers();
    render(
      <PlayQueueList
        active={null}
        proposedList={[fakeActivity({ id: "p-adv" })]}
        cadenceSeconds={30}
        filterCategory="adventures"
        {...handlers}
      />,
    );
    expect(screen.getByTestId("play-queue-list")).toBeTruthy();
    // Row still renders — filter is a no-op in O1.
    expect(screen.queryAllByTestId("play-queue-row")).toHaveLength(1);
  });

  it("accepts filterCategory='elements' without crashing", () => {
    const handlers = buildHandlers();
    render(
      <PlayQueueList
        active={null}
        proposedList={[fakeActivity({ id: "p-elem" })]}
        cadenceSeconds={30}
        filterCategory="elements"
        {...handlers}
      />,
    );
    expect(screen.getByTestId("play-queue-list")).toBeTruthy();
    expect(screen.queryAllByTestId("play-queue-row")).toHaveLength(1);
  });

  it("accepts filterCategory='feelings-friends' without crashing", () => {
    const handlers = buildHandlers();
    render(
      <PlayQueueList
        active={null}
        proposedList={[fakeActivity({ id: "p-ff" })]}
        cadenceSeconds={30}
        filterCategory="feelings-friends"
        {...handlers}
      />,
    );
    expect(screen.getByTestId("play-queue-list")).toBeTruthy();
    expect(screen.queryAllByTestId("play-queue-row")).toHaveLength(1);
  });

  // O1 no-op tests for type-signature-acceptance retained above
  // (filterCategory='adventures', 'elements', 'feelings-friends'
  // accept-without-crash). The O1-era "4 mixed rows still render all
  // 4" test was SUPERSEDED by the O2 activation suite below — see
  // "PlayQueueList filterCategory prop (Phase O Step O2 — filter
  // activation)".

  it("with filterCategory undefined and 3 mixed rows, all 3 still render (pass-through)", () => {
    // Pass-through pin: when the prop is undefined the helper must
    // bypass categorize() entirely and render the full ``proposedList``
    // unchanged. The same 3 mixed-category rows the O2 activation
    // suite uses below — but with no filter, all 3 must render.
    const handlers = buildHandlers();
    const rows = [
      fakeActivity({ id: "p-adv-1", title: "Castle escape" }),
      fakeActivity({ id: "p-elem-1", title: "Cardboard rocket" }),
      fakeActivity({ id: "p-ff-1", title: "Talk it out" }),
    ];
    render(
      <PlayQueueList
        active={null}
        proposedList={rows}
        cadenceSeconds={30}
        {...handlers}
      />,
    );
    expect(screen.queryAllByTestId("play-queue-row")).toHaveLength(3);
  });
});

// Phase O Step O2 — filter activation. The ``filterCategory`` prop
// (plumbed by O1, no-op until now) MUST filter ``proposedList`` via
// ``categorize()`` before rendering. The O1 "with 4 mixed-category
// rows + filterCategory='adventures', still renders all 4" no-op
// assertion was SUPERSEDED by this suite — that test was rewritten
// above as the pass-through (undefined) check and is no longer a
// no-op assertion.
//
// The fixture helper below constructs three activities — one per
// content category (Adventures, Elements, Feelings & Friends) — using
// the Phase O typed wire-shape additions (``recommended_themes`` on
// the activity, ``element_id`` on steps). Each test passes
// ``filterCategory`` and asserts exactly one row renders, identified
// by ``data-activity-id`` on the suggestion-card.
//
// Type-shape note: the fake activities below set
// ``recommended_themes`` directly + steps with ``element_id`` —
// fields the Phase O O2 wire-shape widening adds to Activity /
// ActivityStep. If the dev hasn't yet widened the parent ``Activity``
// + ``ActivityStep`` types (via the codegen or by hand), this file
// fails to typecheck before any assertion fires. That is the desired
// red for the TDD red→green cycle.

interface ActivityStepWithElement {
  seq: number;
  body: string;
  sfx: string | null;
  expected_action: string | null;
  current: boolean;
  element_id: string | null;
}

function makeAdventureActivity(id: string, title: string): Activity {
  // No element_id on any step, no feelings theme → categorize() returns
  // "adventures".
  const step: ActivityStepWithElement = {
    seq: 1,
    body: "Build a fort",
    sfx: null,
    expected_action: null,
    current: false,
    element_id: null,
  };
  return fakeActivity({
    id,
    title,
    steps: [step] as unknown as Activity["steps"],
    recommended_themes: [],
  } as unknown as Partial<Activity>);
}

function makeElementActivity(id: string, title: string): Activity {
  // element_id non-null on at least one step → categorize() returns
  // "elements" (regardless of recommended_themes).
  const step: ActivityStepWithElement = {
    seq: 1,
    body: "Look at the Hydrogen card",
    sfx: null,
    expected_action: null,
    current: false,
    element_id: "h-1",
  };
  return fakeActivity({
    id,
    title,
    steps: [step] as unknown as Activity["steps"],
    recommended_themes: [],
  } as unknown as Partial<Activity>);
}

function makeFeelingsFriendsActivity(id: string, title: string): Activity {
  // No element_id, but recommended_themes includes "feelings" →
  // categorize() returns "feelings-friends".
  const step: ActivityStepWithElement = {
    seq: 1,
    body: "Talk about a big feeling",
    sfx: null,
    expected_action: null,
    current: false,
    element_id: null,
  };
  return fakeActivity({
    id,
    title,
    steps: [step] as unknown as Activity["steps"],
    recommended_themes: ["feelings"],
  } as unknown as Partial<Activity>);
}

describe("PlayQueueList filterCategory prop (Phase O Step O2 — filter activation)", () => {
  it("filterCategory='elements' renders only the element activity", () => {
    const handlers = buildHandlers();
    const rows = [
      makeAdventureActivity("p-adv-1", "Castle escape"),
      makeElementActivity("p-elem-1", "Hydrogen"),
      makeFeelingsFriendsActivity("p-ff-1", "Big feelings"),
    ];
    render(
      <PlayQueueList
        active={null}
        proposedList={rows}
        cadenceSeconds={30}
        filterCategory="elements"
        {...handlers}
      />,
    );
    const visibleRows = screen.queryAllByTestId("play-queue-row");
    expect(visibleRows).toHaveLength(1);
    expect(visibleRows[0]?.getAttribute("data-activity-id")).toBe("p-elem-1");
  });

  it("filterCategory='adventures' renders only the adventure activity", () => {
    const handlers = buildHandlers();
    const rows = [
      makeAdventureActivity("p-adv-1", "Castle escape"),
      makeElementActivity("p-elem-1", "Hydrogen"),
      makeFeelingsFriendsActivity("p-ff-1", "Big feelings"),
    ];
    render(
      <PlayQueueList
        active={null}
        proposedList={rows}
        cadenceSeconds={30}
        filterCategory="adventures"
        {...handlers}
      />,
    );
    const visibleRows = screen.queryAllByTestId("play-queue-row");
    expect(visibleRows).toHaveLength(1);
    expect(visibleRows[0]?.getAttribute("data-activity-id")).toBe("p-adv-1");
  });

  it("filterCategory='feelings-friends' renders only the SEL activity", () => {
    const handlers = buildHandlers();
    const rows = [
      makeAdventureActivity("p-adv-1", "Castle escape"),
      makeElementActivity("p-elem-1", "Hydrogen"),
      makeFeelingsFriendsActivity("p-ff-1", "Big feelings"),
    ];
    render(
      <PlayQueueList
        active={null}
        proposedList={rows}
        cadenceSeconds={30}
        filterCategory="feelings-friends"
        {...handlers}
      />,
    );
    const visibleRows = screen.queryAllByTestId("play-queue-row");
    expect(visibleRows).toHaveLength(1);
    expect(visibleRows[0]?.getAttribute("data-activity-id")).toBe("p-ff-1");
  });

  it("filterCategory=undefined renders all 3 mixed rows (pass-through)", () => {
    const handlers = buildHandlers();
    const rows = [
      makeAdventureActivity("p-adv-1", "Castle escape"),
      makeElementActivity("p-elem-1", "Hydrogen"),
      makeFeelingsFriendsActivity("p-ff-1", "Big feelings"),
    ];
    render(
      <PlayQueueList
        active={null}
        proposedList={rows}
        cadenceSeconds={30}
        {...handlers}
      />,
    );
    expect(screen.queryAllByTestId("play-queue-row")).toHaveLength(3);
  });

  it("filterCategory='elements' with no element activities shows the Elements empty-state copy", () => {
    // Per plan §3: empty-state copy per tab. When the filter eliminates
    // every row, PlayQueueList must surface the per-category empty-state
    // string (NOT the generic "no play ideas yet" string).
    const handlers = buildHandlers();
    const rows = [
      makeAdventureActivity("p-adv-1", "Castle escape"),
      makeFeelingsFriendsActivity("p-ff-1", "Big feelings"),
    ];
    render(
      <PlayQueueList
        active={null}
        proposedList={rows}
        cadenceSeconds={30}
        filterCategory="elements"
        {...handlers}
      />,
    );
    // All visible rows filtered out — empty-state surfaces.
    expect(screen.queryAllByTestId("play-queue-row")).toHaveLength(0);
    const emptyState = screen.getByTestId("play-queue-empty");
    expect(emptyState.textContent).toContain(
      "No element activities suggested yet.",
    );
  });
});
