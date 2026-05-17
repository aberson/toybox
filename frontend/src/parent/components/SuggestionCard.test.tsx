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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
        onApprove={async (_rt, _rid) => undefined}
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
  it("renders five options with 'Random' selected by default", () => {
    // Spec (post L follow-up Change D): dropdown shows Random /
    // Picture / Joke / Song / None, with Random pre-selected on first
    // render. Default matches the L4 wire default so the UI value +
    // omit-default backend resolve align.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async (_rt, _rid) => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    expect(select.value).toBe("random");
    const labels = Array.from(select.options).map((o) => o.textContent);
    expect(labels).toEqual(["Random", "Picture", "Joke", "Song", "None"]);
    // values match the wire union literal
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual(["random", "picture", "joke", "song", "none"]);
  });

  it("approving with the default selection passes 'random' + null to onApprove", async () => {
    const onApprove = vi.fn(
      async (
        _rewardType: RewardType,
        _rewardId: string | null,
      ): Promise<void> => undefined,
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
    // L follow-up Change E: second arg is the specific-pick id; null
    // for the default ("Random" type + "(any)" sentinel).
    expect(onApprove).toHaveBeenCalledWith("random", null);
  });

  it("selecting 'Picture' then approving passes 'picture' to onApprove", () => {
    const onApprove = vi.fn(
      async (
        _rewardType: RewardType,
        _rewardId: string | null,
      ): Promise<void> => undefined,
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
    // No specific reward selected → rewardId still null.
    expect(onApprove).toHaveBeenCalledWith("picture", null);
  });

  it("when ALL three lanes are ineligible, dropdown stays enabled and only shows Random + None + hint", () => {
    // L follow-up Change A: per-lane filtering. With all three
    // content lanes off (no pictures AND jokes off AND songs off),
    // the Picture / Joke / Song options are hidden but Random + None
    // remain (always-on). The dropdown stays enabled (the parent can
    // still pick None to opt out, or Random — the L4 resolver
    // handles the all-empty case gracefully). Hint still renders so
    // the parent has the visual cue.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async (_rt, _rid) => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewardsCount={0}
        jokesEnabled={false}
        songsEnabled={false}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    expect(select.disabled).toBe(false);
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual(["random", "none"]);
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
        onApprove={async (_rt, _rid) => undefined}
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

  it("Change A: hides 'Joke' option when jokes_enabled is false", () => {
    // L follow-up Change A: per-toggle filter. With jokes off, the
    // Joke option vanishes from the dropdown; Picture + Song + Random
    // + None remain.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async (_rt, _rid) => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewardsCount={3}
        jokesEnabled={false}
        songsEnabled={true}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual(["random", "picture", "song", "none"]);
  });

  it("Change A: hides 'Song' option when songs_enabled is false", () => {
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async (_rt, _rid) => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewardsCount={3}
        jokesEnabled={true}
        songsEnabled={false}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual(["random", "picture", "joke", "none"]);
  });

  it("Change A: hides 'Picture' option when activeRewardsCount is 0", () => {
    // With zero active pictures but jokes/songs on, Picture hides.
    // Random / None remain (always-on); Joke + Song are still visible.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async (_rt, _rid) => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewardsCount={0}
        jokesEnabled={true}
        songsEnabled={true}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual(["random", "joke", "song", "none"]);
  });

  it("Picture option works regardless of joke/song eligibility", () => {
    // With pictures eligible but jokes/songs disabled, selecting
    // Picture must still ride through to onApprove.
    const onApprove = vi.fn(
      async (
        _rewardType: RewardType,
        _rewardId: string | null,
      ): Promise<void> => undefined,
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
    expect(onApprove).toHaveBeenCalledWith("picture", null);
  });

  it("select carries an accessible label", () => {
    // Accessibility: the spec requires either a visible <label> or
    // an aria-label. We ship both — a visible "Reward:" label tied
    // by htmlFor + a redundant aria-label so screen readers always
    // get a usable name.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async (_rt, _rid) => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    // getByLabelText walks both <label htmlFor> and aria-label so
    // hitting either branch counts.
    const select = screen.getByLabelText("Reward type") as HTMLSelectElement;
    expect(select.tagName).toBe("SELECT");
    expect(select.getAttribute("aria-label")).toBe("Reward type");
  });

  it("treats null activeRewardsCount as 'unknown → enabled'", () => {
    // Per L9 spec: when activeRewardsCount is null (e.g. the bootstrap
    // listRewards fetch failed), the card defaults to "rewards are
    // available" and relies on the L4 backend fallback chain.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async (_rt, _rid) => undefined}
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

describe("SuggestionCard L follow-up Change D — None option", () => {
  it("selecting 'None' passes 'none' + null to onApprove", () => {
    // The explicit opt-out. The backend's L4 wiring short-circuits
    // _insert_reward_step_as_current when reward_type === "none" and
    // the activity wraps cleanly with no reward step.
    const onApprove = vi.fn(
      async (
        _rewardType: RewardType,
        _rewardId: string | null,
      ): Promise<void> => undefined,
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
    fireEvent.change(select, { target: { value: "none" } });
    fireEvent.click(screen.getByTestId("approve-button"));
    expect(onApprove).toHaveBeenCalledWith("none", null);
  });

  it("'None' option is always rendered even when no other lanes are eligible", () => {
    // The opt-out must survive every filter — it's the parent's
    // way to say "no reward this activity" regardless of catalog
    // state or master toggles.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async (_rt, _rid) => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewardsCount={0}
        jokesEnabled={false}
        songsEnabled={false}
      />,
    );
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toContain("none");
  });
});

describe("SuggestionCard L follow-up Change E — specific picture pick", () => {
  it("second dropdown does not render unless 'Picture' is selected", () => {
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async (_rt, _rid) => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewards={[
          { id: "r-1", display_name: "Gold Trophy" },
          { id: "r-2", display_name: "Silver Star" },
        ]}
      />,
    );
    // Default selection is "Random" → second dropdown absent.
    expect(screen.queryByTestId("reward-id-select")).toBeNull();
    // Switch to Joke — still absent.
    const select = screen.getByTestId("reward-select") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "joke" } });
    expect(screen.queryByTestId("reward-id-select")).toBeNull();
    // Switch to Picture — appears.
    fireEvent.change(select, { target: { value: "picture" } });
    expect(screen.getByTestId("reward-id-select")).toBeTruthy();
  });

  it("second dropdown lists '(any)' + each active reward by display_name", () => {
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async (_rt, _rid) => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewards={[
          { id: "r-1", display_name: "Gold Trophy" },
          { id: "r-2", display_name: "Silver Star" },
        ]}
      />,
    );
    fireEvent.change(screen.getByTestId("reward-select"), {
      target: { value: "picture" },
    });
    const idSelect = screen.getByTestId(
      "reward-id-select",
    ) as HTMLSelectElement;
    const labels = Array.from(idSelect.options).map((o) => o.textContent);
    expect(labels).toEqual(["(any)", "Gold Trophy", "Silver Star"]);
    const values = Array.from(idSelect.options).map((o) => o.value);
    expect(values).toEqual(["", "r-1", "r-2"]);
  });

  it("'(any)' selection wire-side maps to null rewardId on approve", () => {
    const onApprove = vi.fn(
      async (
        _rewardType: RewardType,
        _rewardId: string | null,
      ): Promise<void> => undefined,
    );
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={onApprove}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewards={[{ id: "r-1", display_name: "Gold Trophy" }]}
      />,
    );
    fireEvent.change(screen.getByTestId("reward-select"), {
      target: { value: "picture" },
    });
    // Don't change the id dropdown — stays on "(any)".
    fireEvent.click(screen.getByTestId("approve-button"));
    expect(onApprove).toHaveBeenCalledWith("picture", null);
  });

  it("concrete pick wire-side maps to that reward id on approve", () => {
    const onApprove = vi.fn(
      async (
        _rewardType: RewardType,
        _rewardId: string | null,
      ): Promise<void> => undefined,
    );
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={onApprove}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewards={[
          { id: "r-1", display_name: "Gold Trophy" },
          { id: "r-2", display_name: "Silver Star" },
        ]}
      />,
    );
    fireEvent.change(screen.getByTestId("reward-select"), {
      target: { value: "picture" },
    });
    fireEvent.change(screen.getByTestId("reward-id-select"), {
      target: { value: "r-2" },
    });
    fireEvent.click(screen.getByTestId("approve-button"));
    expect(onApprove).toHaveBeenCalledWith("picture", "r-2");
  });

  it("switching back from Picture clears the wire id (rewardId null even if previously pinned)", () => {
    // Behavior contract: the second dropdown only matters when the
    // main dropdown is Picture. Switching to anything else → rewardId
    // null on the wire even if a concrete pick remains in local state.
    const onApprove = vi.fn(
      async (
        _rewardType: RewardType,
        _rewardId: string | null,
      ): Promise<void> => undefined,
    );
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={onApprove}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
        activeRewards={[{ id: "r-1", display_name: "Gold Trophy" }]}
      />,
    );
    fireEvent.change(screen.getByTestId("reward-select"), {
      target: { value: "picture" },
    });
    fireEvent.change(screen.getByTestId("reward-id-select"), {
      target: { value: "r-1" },
    });
    // Switch to Joke.
    fireEvent.change(screen.getByTestId("reward-select"), {
      target: { value: "joke" },
    });
    fireEvent.click(screen.getByTestId("approve-button"));
    expect(onApprove).toHaveBeenCalledWith("joke", null);
  });
});
