import type { ChangeEvent, JSX } from "react";
import { useState } from "react";

import type { Activity, RewardType, RoleAssignment } from "../api";

// Phase L L9: reward dropdown options surfaced on the SuggestionCard.
// Wire strings match the L1 ``RewardType`` Literal alias verbatim
// (``picture | joke | song | random``); the visible label is the
// title-cased variant. Order is "random first" so the default option
// is also the first one rendered.
const REWARD_OPTIONS: ReadonlyArray<{ value: RewardType; label: string }> = [
  { value: "random", label: "Random" },
  { value: "picture", label: "Picture" },
  { value: "joke", label: "Joke" },
  { value: "song", label: "Song" },
];

export interface SuggestionCardBusy {
  approve: boolean;
  skip: boolean;
  dismiss: boolean;
  // Phase K K7: re-roll buttons. Optional on the busy struct so
  // existing call sites that don't pass them default to idle without
  // a type break. The PlayQueueList wires both alongside the existing
  // approve/skip/dismiss flags.
  recast?: boolean;
  newActivity?: boolean;
}

export interface SuggestionCardProps {
  activity: Activity;
  // Phase L L9: the parent picks a per-activity reward type from a
  // local dropdown on the card. The selected value rides ``onApprove``
  // so the L4 backend records it on the approve payload. Pre-L9
  // callers (e.g. kiosk-side surfaces) that mount the card and pass a
  // zero-arg lambda would break the type, so we made the parameter
  // required — every existing caller in this tree (PlayQueueList) is
  // updated in the same step.
  onApprove: (rewardType: RewardType) => Promise<void>;
  onSkip: () => Promise<void>;
  onDismiss: () => Promise<void>;
  // Phase K K7: "New cast" calls ``recastActivity`` (re-rolls the
  // role slots; same activity id, bumped version). "New activity"
  // dismisses + proposes fresh (mirrors the existing onSkip /
  // ``regenerate`` precedent). Both optional so non-K7 callers can
  // omit them; the card hides the buttons when the handler isn't
  // wired AND the activity has no roles, so a kiosk-side surface
  // could still mount the card without these handlers.
  onRecast?: () => Promise<void>;
  onNewActivity?: () => Promise<void>;
  // Optional in-flight flags so rapid double-clicks can't fire two
  // mutations with the same If-Match-Version. Defaults to all-idle.
  busy?: SuggestionCardBusy;
  // Phase L L9: eligibility plumbing for the reward dropdown. All
  // three are optional; when none are supplied (or any of the picture
  // / jokes / songs lanes is eligible) the dropdown is enabled and no
  // hint renders.
  //
  // ``activeRewardsCount`` is the number of ACTIVE (active=true,
  // archived=false) picture rewards in the library. ``null`` means
  // "unknown" — we default to "rewards are available" rather than
  // disabling the dropdown, leaning on the L4 backend's fallback
  // chain to gracefully degrade if the picture pool is actually
  // empty. ``jokesEnabled`` / ``songsEnabled`` are the App-lifted
  // master toggles (see RewardsSection). When all three lanes are
  // ineligible (0 active pictures AND jokes off AND songs off), the
  // ``<select>`` is disabled and a "No rewards configured" hint
  // renders below.
  activeRewardsCount?: number | null;
  jokesEnabled?: boolean;
  songsEnabled?: boolean;
}

// Phase K K7: prefer the backend-rendered ``cast_summary`` string when
// it's populated (avoids client-side role-name pretty-printing drift
// from server). Fall back to building from ``activity.roles`` for
// activities that came in over a pre-K5 envelope without the field.
// Returns null when no cast info is available so the card can render
// nothing rather than an empty "cast:" line.
function renderCastLabel(activity: Activity): string | null {
  const summary = activity.cast_summary;
  if (typeof summary === "string" && summary !== "") {
    return summary;
  }
  const roles = activity.roles;
  if (roles === undefined || roles === null) return null;
  const entries: RoleAssignment[] = Object.values(roles);
  if (entries.length === 0) return null;
  // Local fallback. Title-case the snake_case role name so a
  // pre-K5 envelope still renders something readable, even without
  // the backend's display-name table.
  return entries
    .map((entry) => {
      const label = entry.role_name
        .split("_")
        .map((part) => (part.length === 0 ? "" : part[0]!.toUpperCase() + part.slice(1)))
        .join(" ");
      return `${label}: ${entry.display_name}`;
    })
    .join(", ");
}

// Step 23: the "why this?" panel renders the trigger phrase that fired
// the suggestion plus the persona-match rationale. Both are pulled off
// the activity wire shape (``trigger_phrase``, ``persona_reasoning``).
// The intent (``intent_source``) is also surfaced as a third row when
// available, since the slot/intent drove the template selection.
export function SuggestionCard(props: SuggestionCardProps): JSX.Element {
  const {
    activity,
    onApprove,
    onSkip,
    onDismiss,
    onRecast,
    onNewActivity,
    activeRewardsCount,
    jokesEnabled,
    songsEnabled,
  } = props;
  const busy: SuggestionCardBusy = props.busy ?? {
    approve: false,
    skip: false,
    dismiss: false,
  };
  const busyRecast = busy.recast ?? false;
  const busyNewActivity = busy.newActivity ?? false;
  const [whyOpen, setWhyOpen] = useState(false);
  // Phase L L9: per-card reward selection. ``random`` is the L4 wire
  // default; mirroring it here as the initial state keeps the dropdown
  // UI default and the backend default aligned.
  const [rewardType, setRewardType] = useState<RewardType>("random");

  // Eligibility for each reward lane. ``null`` activeRewardsCount is
  // "unknown" — treat as eligible (don't disable) and let the L4
  // resolver fall back silently if the picture pool is actually empty.
  // jokes/songs corpora are bundled with the kiosk binary so a runtime
  // empty-corpus check isn't needed client-side; only the master
  // toggle matters here.
  const pictureEligible =
    activeRewardsCount === undefined ||
    activeRewardsCount === null ||
    activeRewardsCount > 0;
  const jokeEligible = jokesEnabled !== false;
  const songEligible = songsEnabled !== false;
  const anyRewardEligible = pictureEligible || jokeEligible || songEligible;
  const rewardDisabled = !anyRewardEligible;

  const handleRewardChange = (e: ChangeEvent<HTMLSelectElement>): void => {
    setRewardType(e.target.value as RewardType);
  };
  const title = activity.title ?? activity.summary ?? "Untitled activity";
  const castLabel = renderCastLabel(activity);
  // Phase K K7: re-roll buttons disable when the activity isn't in
  // the ``proposed`` state. The backend recast endpoint returns 409
  // ``recast_only_when_proposed`` otherwise, so we mirror that guard
  // client-side to avoid a doomed round-trip. ``newActivity`` shares
  // the same gate — once the parent has approved (or anything past
  // proposed), the "swap for a different idea" affordance no longer
  // makes sense.
  const rerollDisabledByState = activity.state !== "proposed";
  const personaMeta = (activity.metadata as Record<string, unknown>)["persona"];
  const personaName =
    typeof personaMeta === "object" &&
    personaMeta !== null &&
    typeof (personaMeta as Record<string, unknown>)["display_name"] === "string"
      ? ((personaMeta as Record<string, unknown>)["display_name"] as string)
      : null;

  const triggerPhrase = activity.trigger_phrase;
  const personaReasoning = activity.persona_reasoning;
  const intentSource = activity.intent_source;

  return (
    <section
      data-testid="suggestion-card"
      data-activity-id={activity.id}
      style={{
        border: "1px solid #ccc",
        borderRadius: 6,
        padding: 16,
        margin: "12px 0",
        background: "#fafafa",
      }}
    >
      <h2 style={{ margin: "0 0 8px 0", fontSize: 17 }}>{title}</h2>
      {castLabel !== null && (
        <p
          data-testid="suggestion-cast"
          style={{ margin: "0 0 4px 0", color: "#444", fontSize: 13 }}
        >
          cast: {castLabel}
        </p>
      )}
      {personaName !== null && (
        <p
          data-testid="suggestion-persona"
          style={{ margin: "0 0 4px 0", color: "#1769aa", fontSize: 13 }}
        >
          persona: {personaName}
        </p>
      )}
      <p style={{ margin: "0 0 8px 0", color: "#555", fontSize: 13 }}>
        v{activity.version} · {activity.state}
      </p>
      {activity.steps.length > 0 && (
        <ol
          style={{ marginTop: 8, fontSize: 14 }}
          data-testid="suggestion-steps"
        >
          {activity.steps.map((s) => (
            <li key={s.seq}>{s.body}</li>
          ))}
        </ol>
      )}
      {/* Phase L L9: per-card reward dropdown. The selected value is
          local state; on approve it rides the existing ``onApprove``
          callback through to ``ApiClient.approve``'s new ``rewardType``
          parameter and lands in the L4 ApproveRequest. The dropdown
          disables itself + surfaces a hint when all three lanes
          (picture / joke / song) are ineligible — in that mode the
          parent gets no useful choice, so we make the absence
          explicit. A native ``<select>`` (rather than a styled radio
          group) is intentional: lightweight, keyboard-accessible by
          default, and matches the operator-tooling vibe of the rest
          of the parent UI. */}
      <div
        data-testid="reward-row"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginTop: 12,
          fontSize: 13,
        }}
      >
        <label
          htmlFor={`reward-select-${activity.id}`}
          style={{ color: "#374151" }}
        >
          Reward:
        </label>
        <select
          id={`reward-select-${activity.id}`}
          data-testid="reward-select"
          aria-label="Reward type"
          value={rewardType}
          disabled={rewardDisabled}
          onChange={handleRewardChange}
          style={{ fontSize: 13, padding: "2px 4px" }}
        >
          {REWARD_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        {rewardDisabled && (
          <span
            data-testid="reward-disabled-hint"
            style={{ color: "#6b7280", fontSize: 12 }}
          >
            No rewards configured
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button
          type="button"
          data-testid="approve-button"
          disabled={busy.approve}
          onClick={() => {
            void onApprove(rewardType);
          }}
        >
          {busy.approve ? "approving..." : "approve"}
        </button>
        <button
          type="button"
          data-testid="skip-button"
          disabled={busy.skip}
          onClick={() => {
            void onSkip();
          }}
        >
          {busy.skip ? "swapping..." : "try a different one"}
        </button>
        <button
          type="button"
          data-testid="dismiss-button"
          disabled={busy.dismiss}
          onClick={() => {
            void onDismiss();
          }}
        >
          {busy.dismiss ? "dismissing..." : "dismiss"}
        </button>
        {onRecast !== undefined && (
          <button
            type="button"
            data-testid="recast-button"
            aria-label="Re-roll cast for this activity"
            disabled={rerollDisabledByState || busyRecast}
            onClick={() => {
              void onRecast();
            }}
          >
            {busyRecast ? "rerolling..." : "new cast"}
          </button>
        )}
        {onNewActivity !== undefined && (
          <button
            type="button"
            data-testid="new-activity-button"
            aria-label="Dismiss and propose a new activity"
            disabled={rerollDisabledByState || busyNewActivity}
            onClick={() => {
              void onNewActivity();
            }}
          >
            {busyNewActivity ? "swapping..." : "new activity"}
          </button>
        )}
        <button
          type="button"
          data-testid="why-toggle"
          aria-expanded={whyOpen}
          onClick={() => setWhyOpen((prev) => !prev)}
        >
          why this?
        </button>
      </div>
      {whyOpen && (
        <div
          data-testid="why-panel"
          style={{
            marginTop: 10,
            padding: 8,
            background: "#eef",
            fontSize: 13,
          }}
        >
          {/* Step 23: render trigger phrase, persona rationale, and the
              intent that drove template selection. ``trigger_phrase`` is
              null when the activity was proposed manually (no transcript
              match); we show a soft "no trigger" line in that case so
              parents don't see an empty section. ``persona_reasoning``
              is always populated by the backend (synthesised default
              when the propose call didn't supply one).

              ``typeof === "string"`` (not ``!== null``) because activities
              that arrive via the ``activity.state`` WS envelope have these
              fields stripped as PII (api/activities.py:_emit_state), so
              they reach the frontend as ``undefined`` rather than
              ``null``. A plain ``!== null`` check would let undefined
              through and the template literal would render the literal
              string "undefined". (#111) */}
          <div data-testid="why-trigger" style={{ marginBottom: 4 }}>
            <strong>trigger:</strong>{" "}
            {typeof triggerPhrase === "string" && triggerPhrase !== ""
              ? `"${triggerPhrase}"`
              : "(no trigger — proposed manually)"}
          </div>
          <div data-testid="why-persona" style={{ marginBottom: 4 }}>
            <strong>persona:</strong>{" "}
            {typeof personaReasoning === "string" && personaReasoning !== ""
              ? personaReasoning
              : "matched on intent"}
          </div>
          {intentSource !== null && intentSource !== "" && (
            <div data-testid="why-intent">
              <strong>intent:</strong> {intentSource}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
