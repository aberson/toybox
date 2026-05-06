// Phase F Step F7 — kiosk-level layout regression. Mounts the
// persona avatar + StepCard side-by-side in the same flex column the
// production kiosk uses (see ``App.tsx`` ``FULL_BLEED_STYLE``) and
// asserts the avatar sits ABOVE the StepCard in DOM order. This is
// the closest we can get to a snapshot-style guard without resorting
// to a brittle DOM-tree snapshot — F7 changes ``StepCard``'s internal
// layout (sprite + body row) but must NOT touch the avatar's
// position relative to the rest of the kiosk shell.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Activity, ActivityStep } from "../api";
import { PersonaAvatar } from "./PersonaAvatar";
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
    action_slot: "looking",
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
    toy_ids: ["toy-abc"],
    ...overrides,
  };
}

describe("kiosk layout (PersonaAvatar + StepCard composition)", () => {
  it("renders the persona avatar above the StepCard in DOM order", () => {
    const activity = fakeActivity();
    // Mirrors App.tsx's active-activity render: avatar first, then
    // StepCard. F7's StepCard changes must not flip this ordering.
    const { container } = render(
      <main
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        <PersonaAvatar letter="M" size={240} />
        <StepCard activity={activity} />
      </main>,
    );
    const avatar = screen.getByTestId("persona-avatar");
    const card = screen.getByTestId("step-card");
    // ``compareDocumentPosition`` returns ``DOCUMENT_POSITION_FOLLOWING``
    // (4) when ``card`` follows ``avatar`` in document order. The
    // avatar must come FIRST so it sits at the top of the column.
    const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
    const rel = avatar.compareDocumentPosition(card);
    expect(rel & FOLLOWING).toBe(FOLLOWING);
    // Sanity: the parent main element should contain both as direct
    // children — F7 must not nest the avatar inside the StepCard.
    const main = container.querySelector("main");
    expect(main).not.toBeNull();
    const directChildren = Array.from(main!.children);
    expect(directChildren.includes(avatar)).toBe(true);
    expect(directChildren.includes(card)).toBe(true);
  });

  it("F7 sprite + body coexist inside StepCard without disturbing the avatar", () => {
    const activity = fakeActivity();
    render(
      <main>
        <PersonaAvatar letter="M" size={240} />
        <StepCard activity={activity} />
      </main>,
    );
    // Both avatar AND sprite render; they live in different subtrees.
    expect(screen.getByTestId("persona-avatar")).not.toBeNull();
    expect(screen.getByTestId("toy-action-sprite")).not.toBeNull();
    // The sprite is INSIDE the StepCard; the avatar is OUTSIDE it.
    const card = screen.getByTestId("step-card");
    const sprite = screen.getByTestId("toy-action-sprite");
    const avatar = screen.getByTestId("persona-avatar");
    expect(card.contains(sprite)).toBe(true);
    expect(card.contains(avatar)).toBe(false);
  });
});
