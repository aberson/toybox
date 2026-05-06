// Phase F Step F7 — StepCard layout coverage. Asserts the sprite-
// branch logic: the new ``ToyActionSprite`` renders to the LEFT of
// the body text iff the current step has ``action_slot`` set AND the
// activity has a non-empty ``toy_ids``. Otherwise the kiosk renders
// the same body-only layout it shipped with before F7.
//
// Persona-avatar layout is asserted at the App level — StepCard is a
// child of App, and the avatar lives outside StepCard, so the layout
// regression covers ``<StepCard>`` rendering separately from the
// avatar element. (See ``App.test.tsx`` for the kiosk-level shape.)

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Activity, ActivityStep } from "../api";
import { StepCard } from "./StepCard";

afterEach(() => {
  cleanup();
});

function fakeStep(overrides: Partial<ActivityStep> = {}): ActivityStep {
  return {
    seq: 1,
    body: "Pretend to be a cat",
    sfx: null,
    expected_action: null,
    current: true,
    action_slot: null,
    ...overrides,
  };
}

function fakeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    id: "act-1",
    state: "running",
    version: 2,
    title: "Cat play",
    summary: null,
    persona_id: null,
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-05-02T10:00:00Z",
    started_at: null,
    ended_at: null,
    steps: [fakeStep()],
    metadata: {},
    ...overrides,
  };
}

describe("StepCard sprite branch", () => {
  it("renders the sprite when the current step has action_slot AND activity has toy_ids", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(sprite.dataset["slot"]).toBe("looking");
    expect(sprite.dataset["toyId"]).toBe("toy-abc");
    // URL composes from the toy_id + slot — the worker writes sprites
    // under ``data/images/toy_actions/<toy_id>/<slot>.png`` and the
    // backend's static-files mount lives at ``/api/static/images``.
    expect(sprite.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-abc/looking.png",
    );
  });

  it("hides the sprite when action_slot is null", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: null })],
    });
    render(<StepCard activity={activity} />);
    expect(screen.queryByTestId("toy-action-sprite")).toBeNull();
    // Body text still renders — sprite-absence does not regress the
    // kiosk's primary readable surface.
    expect(screen.getByTestId("step-text").textContent).toBe(
      "Pretend to be a cat",
    );
  });

  it("hides the sprite when toy_ids is empty", () => {
    const activity = fakeActivity({
      toy_ids: [],
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    expect(screen.queryByTestId("toy-action-sprite")).toBeNull();
  });

  it("hides the sprite when toy_ids is omitted from the wire payload", () => {
    // Pre-F7 / pre-codegen wire shape: backend response omits
    // ``toy_ids`` entirely. The kiosk must treat that the same as
    // an empty array (graceful fallback to the body-only layout).
    const activity = fakeActivity({
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    expect(screen.queryByTestId("toy-action-sprite")).toBeNull();
  });

  it("uses the FIRST toy_id deterministically when multiple are present", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-first", "toy-second", "toy-third"],
      steps: [fakeStep({ action_slot: "jumping" })],
    });
    render(<StepCard activity={activity} />);
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(sprite.dataset["toyId"]).toBe("toy-first");
  });

  it("passes the toy display name through to the sprite when metadata.toys is hydrated", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: "looking" })],
      metadata: {
        toys: [
          { id: "toy-abc", display_name: "Mr. Unicorn" },
          { id: "toy-xyz", display_name: "Other Toy" },
        ],
      },
    });
    render(<StepCard activity={activity} />);
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // alt = "<display_name> <slot>" per F7's a11y contract.
    expect(sprite.alt).toBe("Mr. Unicorn looking");
  });

  it("falls back to bare slot for alt when metadata.toys does not match", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: "idle" })],
      metadata: {
        // Different toy id — the kiosk can't resolve a display name
        // for ``toy-abc``, so the sprite alt falls back to the slot.
        toys: [{ id: "toy-other", display_name: "Some Other Toy" }],
      },
    });
    render(<StepCard activity={activity} />);
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(sprite.alt).toBe("idle");
  });

  it("places the sprite to the LEFT of the body text in the row layout", () => {
    const activity = fakeActivity({
      toy_ids: ["toy-abc"],
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    const row = screen.getByTestId("step-body-row");
    const children = Array.from(row.children);
    // Two children: sprite first (left), body text second.
    expect(children).toHaveLength(2);
    expect(children[0]?.getAttribute("data-testid")).toBe("toy-action-sprite");
    expect(children[1]?.getAttribute("data-testid")).toBe("step-text");
  });

  it("renders only the body text in the row when the sprite is hidden", () => {
    const activity = fakeActivity({
      toy_ids: [],
      steps: [fakeStep({ action_slot: "looking" })],
    });
    render(<StepCard activity={activity} />);
    const row = screen.getByTestId("step-body-row");
    const children = Array.from(row.children);
    expect(children).toHaveLength(1);
    expect(children[0]?.getAttribute("data-testid")).toBe("step-text");
  });
});
