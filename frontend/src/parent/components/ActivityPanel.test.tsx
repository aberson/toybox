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
