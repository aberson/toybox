// Component tests for the Step 23 SuggestionCard "why this?" panel and
// the Phase K K7 cast list + re-roll buttons.

import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Activity, RewardType, RoleAssignment } from "../api";
import { SuggestionCard } from "./SuggestionCard";

function fakeRole(overrides: Partial<RoleAssignment> = {}): RoleAssignment {
  return {
    role_name: "quest_giver",
    toy_id: null,
    generic_descriptor: null,
    display_name: "Wise Owl",
    ...overrides,
  };
}

function fakeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    id: "a-1",
    state: "proposed",
    version: 1,
    title: "Unicorn Adventure",
    summary: null,
    persona_id: "p-unicorn",
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    ended_at: null,
    steps: [
      { seq: 1, body: "Step 1", sfx: null, expected_action: null, current: false },
    ],
    metadata: {
      persona: { display_name: "Sparkle Unicorn" },
    },
    trigger_phrase: "let's play unicorns",
    persona_reasoning: "Sparkle Unicorn picked for request_play",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SuggestionCard why-toggle", () => {
  it("clicking why-toggle expands the panel", () => {
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    expect(screen.getByTestId("why-panel")).toBeTruthy();
  });

  it("clicking why-toggle a second time collapses the panel", () => {
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    const toggle = screen.getByTestId("why-toggle");
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(screen.queryByTestId("why-panel")).toBeNull();
  });

  it("expanded panel surfaces the trigger phrase", () => {
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const trigger = screen.getByTestId("why-trigger");
    expect(trigger.textContent).toContain("let's play unicorns");
  });

  it("expanded panel surfaces the persona reasoning", () => {
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const persona = screen.getByTestId("why-persona");
    expect(persona.textContent).toContain(
      "Sparkle Unicorn picked for request_play",
    );
  });

  it("expanded panel surfaces the intent source", () => {
    // Step 23 spec: the slot/intent that drove the template selection
    // is rendered as a third row when available.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const intent = screen.getByTestId("why-intent");
    expect(intent.textContent).toContain("request_play");
  });

  it("renders fallback for null trigger_phrase (manual propose)", () => {
    // When the activity was proposed manually (not via a transcript
    // match), trigger_phrase is null. The panel still renders — a
    // soft "no trigger" line beats an empty section that erodes
    // parent trust in the why-this affordance.
    render(
      <SuggestionCard
        activity={fakeActivity({ trigger_phrase: null })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const trigger = screen.getByTestId("why-trigger");
    expect(trigger.textContent?.toLowerCase()).toContain("no trigger");
  });

  it("renders fallback when persona_reasoning is null", () => {
    render(
      <SuggestionCard
        activity={fakeActivity({ persona_reasoning: null })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const persona = screen.getByTestId("why-persona");
    expect(persona.textContent?.toLowerCase()).toContain("matched on intent");
  });

  it("renders fallback for undefined trigger_phrase (WS-stripped envelope)", () => {
    // Activities delivered through the ``activity.state`` WS envelope
    // have ``trigger_phrase`` stripped as PII (api/activities.py:
    // _emit_state). The field arrives as ``undefined`` rather than
    // ``null``, so the guard must catch both forms. Regression cover
    // for #111 — without this the panel renders the literal string
    // "undefined" for every live (WS-delivered) suggestion.
    render(
      <SuggestionCard
        activity={fakeActivity({ trigger_phrase: undefined })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const trigger = screen.getByTestId("why-trigger");
    expect(trigger.textContent?.toLowerCase()).toContain("no trigger");
    expect(trigger.textContent?.toLowerCase()).not.toContain("undefined");
  });

  it("renders fallback for undefined persona_reasoning (WS-stripped envelope)", () => {
    // Same shape as the trigger_phrase case — the WS envelope strips
    // ``persona_reasoning`` alongside ``trigger_phrase``. Regression
    // cover for #111.
    render(
      <SuggestionCard
        activity={fakeActivity({ persona_reasoning: undefined })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const persona = screen.getByTestId("why-persona");
    expect(persona.textContent?.toLowerCase()).toContain("matched on intent");
    expect(persona.textContent?.toLowerCase()).not.toContain("undefined");
  });

});

describe("SuggestionCard K7 cast list", () => {
  it("renders the cast_summary verbatim when populated", () => {
    // The backend produces ``cast_summary`` from the resolved roles
    // table; rendering it as a single string avoids client-side
    // role-name pretty-printing drift. This is the v1 spec'd path.
    render(
      <SuggestionCard
        activity={fakeActivity({
          cast_summary: "Quest Giver: Wise Owl, Friend: Captain Bear",
          roles: {
            quest_giver: fakeRole(),
            friend: fakeRole({ role_name: "friend", display_name: "Captain Bear" }),
          },
        })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    const cast = screen.getByTestId("suggestion-cast");
    expect(cast.textContent).toContain("Quest Giver: Wise Owl");
    expect(cast.textContent).toContain("Friend: Captain Bear");
  });

  it("falls back to building the cast list from roles when cast_summary missing", () => {
    // Pre-K5 activities (or those delivered via a WS envelope that
    // strips ``cast_summary``) still carry the structured ``roles``
    // map. The card should still render a usable label rather than
    // silently dropping the cast.
    render(
      <SuggestionCard
        activity={fakeActivity({
          cast_summary: undefined,
          roles: {
            quest_giver: fakeRole(),
          },
        })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    const cast = screen.getByTestId("suggestion-cast");
    expect(cast.textContent).toContain("Quest Giver: Wise Owl");
  });

  it("renders no cast section when roles is empty (role-less template)", () => {
    // Role-less templates (e.g. Phase F branching activities that
    // predate K5) ship with ``roles = {}`` and ``cast_summary = ""``.
    // The card must not render an empty "cast:" line.
    render(
      <SuggestionCard
        activity={fakeActivity({ roles: {}, cast_summary: "" })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    expect(screen.queryByTestId("suggestion-cast")).toBeNull();
  });

  it("renders no cast section when roles + cast_summary are both absent", () => {
    // Pre-K5 wire shape — neither field is present on the envelope.
    render(
      <SuggestionCard
        activity={fakeActivity({ roles: undefined, cast_summary: undefined })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    expect(screen.queryByTestId("suggestion-cast")).toBeNull();
  });
});

describe("SuggestionCard K7 re-roll buttons", () => {
  it('"New cast" button click invokes onRecast', () => {
    const onRecast = vi.fn(async () => undefined);
    render(
      <SuggestionCard
        activity={fakeActivity({
          cast_summary: "Quest Giver: Wise Owl",
          roles: { quest_giver: fakeRole() },
        })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        onRecast={onRecast}
        onNewActivity={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("recast-button"));
    expect(onRecast).toHaveBeenCalledTimes(1);
  });

  it('"New activity" button click invokes onNewActivity (dismiss + propose chain)', () => {
    const onNewActivity = vi.fn(async () => undefined);
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        onRecast={async () => undefined}
        onNewActivity={onNewActivity}
      />,
    );
    fireEvent.click(screen.getByTestId("new-activity-button"));
    expect(onNewActivity).toHaveBeenCalledTimes(1);
  });

  it("both re-roll buttons are disabled when activity.state !== 'proposed'", () => {
    // Once the parent approves (or anything past proposed), the
    // server's recast endpoint returns 409
    // ``recast_only_when_proposed``. The card mirrors that guard
    // client-side so the button greys out instead of firing a
    // doomed mutation.
    render(
      <SuggestionCard
        activity={fakeActivity({ state: "approved" })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        onRecast={async () => undefined}
        onNewActivity={async () => undefined}
      />,
    );
    const recast = screen.getByTestId("recast-button") as HTMLButtonElement;
    const newActivity = screen.getByTestId(
      "new-activity-button",
    ) as HTMLButtonElement;
    expect(recast.disabled).toBe(true);
    expect(newActivity.disabled).toBe(true);
  });

  it("both re-roll buttons are enabled in the proposed state", () => {
    render(
      <SuggestionCard
        activity={fakeActivity({ state: "proposed" })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        onRecast={async () => undefined}
        onNewActivity={async () => undefined}
      />,
    );
    expect((screen.getByTestId("recast-button") as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect(
      (screen.getByTestId("new-activity-button") as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it("re-roll buttons hide entirely when the handlers are not wired", () => {
    // Defensive: a non-K7 caller (e.g. a kiosk-side surface) that
    // mounts the card without re-roll handlers shouldn't see ghost
    // buttons. Mirrors the optional-handler pattern from the
    // existing surface.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    expect(screen.queryByTestId("recast-button")).toBeNull();
    expect(screen.queryByTestId("new-activity-button")).toBeNull();
  });

  it("recast button disables while busy.recast is true", () => {
    // Mid-recast: parent component sets ``busy.recast = true`` via
    // PlayQueueList's runGuarded. Button greys out so a rapid
    // double-click can't fire two If-Match-Version mutations.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        onRecast={async () => undefined}
        onNewActivity={async () => undefined}
        busy={{
          approve: false,
          skip: false,
          dismiss: false,
          recast: true,
          newActivity: false,
        }}
      />,
    );
    const recast = screen.getByTestId("recast-button") as HTMLButtonElement;
    expect(recast.disabled).toBe(true);
    expect(recast.textContent?.toLowerCase()).toContain("rerolling");
  });

  it("re-enables on next render when state stays 'proposed' after a 409 refetch", () => {
    // 409 conflict handling: ``withConflictHandler`` refetches the
    // activity + clears the busy flag in the store. The card then
    // re-renders with the same proposed state — the button must be
    // enabled again so the parent can retry. This is the "button
    // re-enables after refetch" branch of the K7 spec.
    const { rerender } = render(
      <SuggestionCard
        activity={fakeActivity({ state: "proposed", version: 1 })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        onRecast={async () => undefined}
        onNewActivity={async () => undefined}
        busy={{
          approve: false,
          skip: false,
          dismiss: false,
          recast: true,
          newActivity: false,
        }}
      />,
    );
    expect((screen.getByTestId("recast-button") as HTMLButtonElement).disabled).toBe(
      true,
    );
    // Refetch returns the same state but a bumped version (some
    // other client mutated it); busy flag clears.
    rerender(
      <SuggestionCard
        activity={fakeActivity({ state: "proposed", version: 2 })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        onRecast={async () => undefined}
        onNewActivity={async () => undefined}
        busy={{
          approve: false,
          skip: false,
          dismiss: false,
          recast: false,
          newActivity: false,
        }}
      />,
    );
    expect((screen.getByTestId("recast-button") as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it("stays disabled on next render when state moves past 'proposed' after a 409 refetch", () => {
    // The other branch of the K7 spec: if the activity got approved
    // between the recast click and the refetch, the button stays
    // greyed out (state guard wins over busy flag). Without this
    // the parent would see an enabled button that 409s on every
    // click.
    const { rerender } = render(
      <SuggestionCard
        activity={fakeActivity({ state: "proposed", version: 1 })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        onRecast={async () => undefined}
        onNewActivity={async () => undefined}
        busy={{
          approve: false,
          skip: false,
          dismiss: false,
          recast: true,
          newActivity: false,
        }}
      />,
    );
    rerender(
      <SuggestionCard
        activity={fakeActivity({ state: "approved", version: 2 })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        onRecast={async () => undefined}
        onNewActivity={async () => undefined}
        busy={{
          approve: false,
          skip: false,
          dismiss: false,
          recast: false,
          newActivity: false,
        }}
      />,
    );
    expect((screen.getByTestId("recast-button") as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(
      (screen.getByTestId("new-activity-button") as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

describe("SuggestionCard L9 reward dropdown", () => {
  it("renders four options with 'Random' selected by default", () => {
    // Spec: dropdown shows Random / Picture / Joke / Song, with
    // Random pre-selected on first render. Default matches the L4
    // wire default so the UI value + omit-default backend resolve
    // align — see L9 plan §default semantics.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    expect(select.value).toBe("random");
    const labels = Array.from(select.options).map((o) => o.textContent);
    expect(labels).toEqual(["Random", "Picture", "Joke", "Song"]);
    // values match the wire union literal
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual(["random", "picture", "joke", "song"]);
  });

  it("approving with the default selection passes 'random' to onApprove", async () => {
    const onApprove = vi.fn(
      async (_rewardType: RewardType): Promise<void> => undefined,
    );
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={onApprove}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("approve-button"));
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onApprove).toHaveBeenCalledWith("random");
  });

  it("selecting 'Picture' then approving passes 'picture' to onApprove", () => {
    const onApprove = vi.fn(
      async (_rewardType: RewardType): Promise<void> => undefined,
    );
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={onApprove}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "picture" } });
    expect(select.value).toBe("picture");
    fireEvent.click(screen.getByTestId("approve-button"));
    expect(onApprove).toHaveBeenCalledWith("picture");
  });

  it("disables dropdown + renders hint when ALL three lanes are ineligible", () => {
    // Picture pool empty (activeRewardsCount=0) AND jokes off AND
    // songs off → no useful selection available, so the dropdown
    // disables and a "No rewards configured" hint surfaces below.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewardsCount={0}
        jokesEnabled={false}
        songsEnabled={false}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    const hint = screen.getByTestId("reward-disabled-hint");
    expect(hint.textContent).toContain("No rewards configured");
  });

  it("enables dropdown + hides hint when ANY single lane is eligible", () => {
    // One active picture reward is enough to keep the dropdown
    // alive even when both joke + song masters are off. The L4
    // fallback chain handles a per-call empty-pool case silently
    // so the parent's choice isn't blocked.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewardsCount={1}
        jokesEnabled={false}
        songsEnabled={false}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    expect(select.disabled).toBe(false);
    expect(screen.queryByTestId("reward-disabled-hint")).toBeNull();
  });

  it("Picture option works regardless of joke/song eligibility", () => {
    // With pictures eligible but jokes/songs disabled, selecting
    // Picture must still ride through to onApprove. Pins that the
    // dropdown options are NOT individually disabled when their
    // lane is off — only the entire select disables when ALL three
    // lanes are off.
    const onApprove = vi.fn(
      async (_rewardType: RewardType): Promise<void> => undefined,
    );
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={onApprove}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewardsCount={3}
        jokesEnabled={false}
        songsEnabled={false}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "picture" } });
    fireEvent.click(screen.getByTestId("approve-button"));
    expect(onApprove).toHaveBeenCalledWith("picture");
  });

  it("select carries an accessible label", () => {
    // Accessibility: the spec requires either a visible <label> or
    // an aria-label. We ship both — a visible "Reward:" label tied
    // by htmlFor + a redundant aria-label so screen readers always
    // get a usable name.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    // getByLabelText walks both <label htmlFor> and aria-label so
    // hitting either branch counts.
    const select = screen.getByLabelText(/reward/i) as HTMLSelectElement;
    expect(select.tagName).toBe("SELECT");
    expect(select.getAttribute("aria-label")).toBe("Reward type");
  });

  it("treats null activeRewardsCount as 'unknown → enabled'", () => {
    // Per L9 spec: when activeRewardsCount is null (e.g. the bootstrap
    // listRewards fetch failed), the card defaults to "rewards are
    // available" and relies on the L4 backend fallback chain. This
    // guards against the failure mode where a slow / errored bootstrap
    // would otherwise paint a falsely-disabled dropdown.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewardsCount={null}
        jokesEnabled={false}
        songsEnabled={false}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    expect(select.disabled).toBe(false);
    expect(screen.queryByTestId("reward-disabled-hint")).toBeNull();
  });
});
