import type { JSX } from "react";

import type { Activity } from "../api";

export interface ActivityPanelBusy {
  regenerate: boolean;
  end: boolean;
  didntWork: boolean;
  thumbsUp: boolean;
  stepBack: boolean;
  // Phase K K15 Surface P: in-flight flags for the two parent-insert
  // buttons. Keep a rapid second click from racing the first with the
  // same version (same idea as ``regenerate``/``end`` flags). Optional
  // so older callers compile; defaults to false when omitted.
  insertJoke?: boolean;
  insertSong?: boolean;
  // Phase R Step R3: in-flight flag for the approve-question buttons.
  approveQuestion?: boolean;
}

export interface ActivityPanelProps {
  activity: Activity;
  onRegenerate: () => Promise<void>;
  onEnd: () => Promise<void>;
  onDidntWork: () => Promise<void>;
  // Step 15: thumbs-up writes parent_signal=+1 to the labeled_events row.
  // Optional so older callers compile; when absent the button is hidden.
  onThumbsUp?: () => Promise<void>;
  // Roll the kiosk back one step. Optional so older callers compile;
  // when absent the button is hidden.
  onStepBack?: () => Promise<void>;
  // Phase K K15 Surface P: parent inserts a joke / song interjection at
  // current_step+1 on a running/paused activity. Optional so older
  // callers compile; when absent the sidebar buttons are hidden. The
  // ``jokesEnabled`` / ``songsEnabled`` content-master flags grey each
  // button independently — the server backstops with a 409
  // ``content_disabled`` if the parent toggles a master off between
  // bootstrap and click.
  onInsertJoke?: () => Promise<void>;
  onInsertSong?: () => Promise<void>;
  jokesEnabled?: boolean;
  songsEnabled?: boolean;
  // Phase R Step R3: Q&A gating. Called when the parent clicks "Good
  // answer" or "Skip" on the current step's question banner. Optional
  // so older callers compile; when absent the question banner is
  // read-only (no buttons shown, the question text still appears).
  onApproveQuestion?: (result: "approved" | "skipped") => Promise<void>;
  // Optional in-flight flags. Same idea as SuggestionCard's busy: keep
  // a rapid second click from racing the first with the same version.
  busy?: ActivityPanelBusy;
}

const END_CONFIRM_MESSAGE = "End the activity?";

export function ActivityPanel(props: ActivityPanelProps): JSX.Element {
  const {
    activity,
    onRegenerate,
    onEnd,
    onDidntWork,
    onThumbsUp,
    onStepBack,
    onInsertJoke,
    onInsertSong,
    jokesEnabled,
    songsEnabled,
    onApproveQuestion,
  } = props;
  const busy: ActivityPanelBusy = props.busy ?? {
    regenerate: false,
    end: false,
    didntWork: false,
    thumbsUp: false,
    stepBack: false,
  };
  // Phase R Step R3: Q&A gating — detect if the current step has a
  // pending question that the parent needs to resolve.
  const currentStep = activity.steps.find((s) => s.current) ?? null;
  const pendingQuestion: string | null =
    currentStep !== null &&
    typeof currentStep.question === "string" &&
    currentStep.question.length > 0 &&
    currentStep.question_pending === true
      ? currentStep.question
      : null;
  const approveQuestionBusy = busy.approveQuestion ?? false;
  const currentSeq = currentStep?.seq;
  const stepBackEnabled =
    currentSeq !== undefined &&
    currentSeq >= 2 &&
    (activity.state === "running" || activity.state === "paused");
  // Phase K K15 Surface P: insert buttons are only meaningful while the
  // activity is in flight (the server enforces this too: 409
  // ``insert_only_when_running_or_paused`` for other states). Each
  // button is independently greyed when its content master is OFF —
  // matches the parent UI's content-master semantics on the
  // SettingsPanel toggles.
  const insertGateOpen =
    activity.state === "running" || activity.state === "paused";
  const insertJokeEnabled =
    insertGateOpen && jokesEnabled !== false && !(busy.insertJoke ?? false);
  const insertSongEnabled =
    insertGateOpen && songsEnabled !== false && !(busy.insertSong ?? false);
  const title = activity.title ?? activity.summary ?? "Activity";
  const personaMeta = (activity.metadata as Record<string, unknown>)["persona"];
  const personaName =
    typeof personaMeta === "object" &&
    personaMeta !== null &&
    typeof (personaMeta as Record<string, unknown>)["display_name"] === "string"
      ? ((personaMeta as Record<string, unknown>)["display_name"] as string)
      : null;
  // Cast members from the resolved K5 role-slot map. Deduplicated on
  // toy_id (a toy filling two roles shows once); generic descriptors
  // are deduplicated on display_name. Ordered by role_name so the
  // surface is stable across renders.
  const castDisplayNames: string[] = (() => {
    if (!activity.roles) return [];
    const seen = new Set<string>();
    const out: string[] = [];
    const entries = Object.values(activity.roles).sort((a, b) =>
      a.role_name.localeCompare(b.role_name),
    );
    for (const role of entries) {
      const key = role.toy_id ?? `desc:${role.display_name}`;
      if (seen.has(key)) continue;
      if (role.display_name.length === 0) continue;
      seen.add(key);
      out.push(role.display_name);
    }
    return out;
  })();

  // Step 23: confirm dialog for the End button. ``window.confirm``
  // matches the ChildProfileEditor / TranscriptsManager sibling
  // pattern — synchronous, blocking, mockable via
  // ``vi.spyOn(window, "confirm")`` in tests. The handler is wrapped
  // here (not in the parent) so the panel owns the UX contract:
  // clicking End ALWAYS prompts; the parent just gets the confirmed
  // call.
  const handleEndClick = (): void => {
    if (!window.confirm(END_CONFIRM_MESSAGE)) {
      return;
    }
    void onEnd();
  };

  return (
    <section
      data-testid="activity-panel"
      data-activity-id={activity.id}
      data-activity-state={activity.state}
      style={{
        border: "1px solid #1769aa",
        borderRadius: 6,
        padding: 16,
        margin: "12px 0",
        background: "white",
      }}
    >
      <h2 style={{ margin: "0 0 8px 0", fontSize: 17 }}>{title}</h2>
      {personaName !== null && (
        <p
          data-testid="activity-persona"
          style={{ margin: "0 0 4px 0", color: "#1769aa", fontSize: 13 }}
        >
          persona: {personaName}
        </p>
      )}
      {castDisplayNames.length > 0 && (
        <p
          data-testid="activity-cast"
          style={{ margin: "0 0 4px 0", color: "#1769aa", fontSize: 13 }}
        >
          cast: {castDisplayNames.join(", ")}
        </p>
      )}
      <p style={{ margin: "0 0 8px 0", color: "#555", fontSize: 13 }}>
        state: {activity.state} · v{activity.version}
      </p>
      {activity.steps.length > 0 && (
        <ol style={{ marginTop: 8, fontSize: 14 }} data-testid="activity-steps">
          {activity.steps.map((s) => (
            <li
              key={s.seq}
              style={{ fontWeight: s.current ? 700 : 400 }}
              data-current={s.current ? "true" : undefined}
            >
              {s.body}
            </li>
          ))}
        </ol>
      )}
      {/*
        Phase R Step R3: Q&A gating. When the current step has a pending
        question, show the question text + "Good answer" / "Skip" buttons.
        The server gates advance on question_pending; clicking either button
        calls the approve-question endpoint which broadcasts a WS envelope
        so the child kiosk unhides the Next button.
      */}
      {pendingQuestion !== null && (
        <div
          data-testid="question-panel"
          style={{
            marginTop: 10,
            padding: "10px 14px",
            background: "#fff8e1",
            border: "1px solid #ffe082",
            borderRadius: 6,
          }}
        >
          <p
            data-testid="question-text"
            style={{ margin: "0 0 8px 0", fontSize: 14, color: "#5d4037" }}
          >
            Child Q&A: {pendingQuestion}
          </p>
          {onApproveQuestion !== undefined && (
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                data-testid="approve-question-button"
                disabled={approveQuestionBusy}
                onClick={() => {
                  void onApproveQuestion("approved");
                }}
              >
                {approveQuestionBusy ? "..." : "Good answer"}
              </button>
              <button
                type="button"
                data-testid="skip-question-button"
                disabled={approveQuestionBusy}
                onClick={() => {
                  void onApproveQuestion("skipped");
                }}
              >
                {approveQuestionBusy ? "..." : "Skip"}
              </button>
            </div>
          )}
        </div>
      )}
      {(onInsertJoke !== undefined || onInsertSong !== undefined) && (
        <div
          data-testid="activity-insert-sidebar"
          style={{ display: "flex", gap: 6, marginTop: 10 }}
        >
          {onInsertJoke !== undefined && (
            <button
              type="button"
              data-testid="insert-joke-button"
              aria-label="insert joke"
              title={
                !insertGateOpen
                  ? "available while running or paused"
                  : jokesEnabled === false
                    ? "jokes are turned off in Settings"
                    : "insert a joke at the next step"
              }
              disabled={!insertJokeEnabled}
              onClick={() => {
                void onInsertJoke();
              }}
            >
              {(busy.insertJoke ?? false) ? "..." : "+ joke"}
            </button>
          )}
          {onInsertSong !== undefined && (
            <button
              type="button"
              data-testid="insert-song-button"
              aria-label="insert song"
              title={
                !insertGateOpen
                  ? "available while running or paused"
                  : songsEnabled === false
                    ? "songs are turned off in Settings"
                    : "insert a song at the next step"
              }
              disabled={!insertSongEnabled}
              onClick={() => {
                void onInsertSong();
              }}
            >
              {(busy.insertSong ?? false) ? "..." : "+ song"}
            </button>
          )}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        {onThumbsUp !== undefined && (
          <button
            type="button"
            data-testid="thumbs-up-button"
            aria-label="thumbs up"
            disabled={busy.thumbsUp}
            onClick={() => {
              void onThumbsUp();
            }}
          >
            {busy.thumbsUp ? "..." : "thumbs up"}
          </button>
        )}
        {onStepBack !== undefined && (
          <button
            type="button"
            data-testid="step-back-button"
            disabled={busy.stepBack || !stepBackEnabled}
            onClick={() => {
              void onStepBack();
            }}
          >
            {busy.stepBack ? "stepping back..." : "step back"}
          </button>
        )}
        <button
          type="button"
          data-testid="regenerate-button"
          disabled={busy.regenerate}
          onClick={() => {
            void onRegenerate();
          }}
        >
          {busy.regenerate ? "skipping..." : "skip & try another"}
        </button>
        <button
          type="button"
          data-testid="end-button"
          disabled={busy.end}
          onClick={handleEndClick}
        >
          {busy.end ? "ending..." : "end"}
        </button>
        <button
          type="button"
          data-testid="didnt-work-button"
          disabled={busy.didntWork}
          onClick={() => {
            void onDidntWork();
          }}
        >
          {busy.didntWork ? "marking..." : "didn't work"}
        </button>
      </div>
    </section>
  );
}
