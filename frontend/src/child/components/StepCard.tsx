import { useEffect, useRef, type JSX } from "react";

import type { Activity, ActivityStep } from "../api";
import { getVoiceProfile, type PersonaMetadata } from "../persona-voice";
import type { VoiceProfile } from "../tts";
import { ChoiceButton, type ChoiceResult } from "./ChoiceButton";
import { ClickableText } from "./ClickableText";
import { JokeStep, replayJoke } from "./JokeStep";
import { NextStepButton } from "./NextStepButton";
import { ReadMeButton } from "./ReadMeButton";
import { RewardStep } from "./RewardStep";
import { SongPlayer } from "./SongPlayer";
import { ToyActionSprite } from "./ToyActionSprite";

export interface StepCardProps {
  activity: Activity;
  // Phase G G4: action callbacks for the bottom button row. The
  // STEP-CARD picks which one to render based on whether the current
  // step is a branch point; the parent (``App.tsx``) supplies both
  // and threads version-conflict / store updates the same way for
  // either path. Optional so existing tests that mount StepCard for
  // pure layout assertions don't have to thread no-op callbacks
  // through; when omitted, the action row is hidden (matches the
  // previous behavior of rendering the avatar + body only).
  onAdvance?: () => void;
  onChoose?: (choiceIndex: number) => Promise<ChoiceResult>;
  // Linear-advance busy flag — the App's existing ``busyAdvance``
  // state. Choice-button busy is local to each ChoiceButton, but the
  // linear NextStepButton needs the parent's flag to disable while a
  // POST is in flight.
  advanceBusy?: boolean;
  // Phase G G4: index of the ChoiceButton that currently has a POST
  // in flight, or null if none. App lifts this state so EVERY rendered
  // ChoiceButton (including the in-flight one) gates its click handler
  // on it — preventing the sibling-tap race where a kid taps "Choice
  // A" then "Choice B" and both fire competing POSTs (the first wins
  // on version, the second 409s and the first becomes the kid's
  // "final" answer, opposite of UX expectation). Optional so existing
  // tests that mount StepCard without an App don't have to thread it.
  choosingIndex?: number | null;
  // Phase K K9: parent-controlled feature flags. Drilled through App →
  // StepCard so word-level taps and the Read Me button can be turned
  // off without redeploying. Optional + default false so existing
  // tests that mount StepCard without an App (e.g. F7's pure-layout
  // suite) don't have to thread no-op flags through — the kiosk's
  // production path always supplies real values from the bootstrap
  // fetch (see ``App.tsx``'s ``featureFlags`` state).
  clickableWordsEnabled?: boolean;
  readMeButtonEnabled?: boolean;
  // Phase K K12: content-master flags for song + joke step kinds. When
  // a step of kind=song renders AND songsEnabled is false, the kiosk
  // auto-advances silently (no meta-message, the kid never sees the
  // skipped step). Same for jokes. Optional + default true so
  // existing tests / non-K-content steps don't have to thread these.
  // The kiosk's production path always supplies real values from the
  // bootstrap fetch (``featureFlags.songs_enabled`` etc.).
  songsEnabled?: boolean;
  jokesEnabled?: boolean;
}

// Step kinds that should get a Read Me button. ``song`` is excluded —
// the audio surface is owned by K12's SongPlayer (a song step renders
// a bundled MP3, and a competing TTS read-aloud would interrupt the
// song). The wire schema for ``ActivityStep.kind`` is not yet declared
// on the ``ActivityStep`` interface (K12 lands the new kinds); for K9
// we read ``step.kind`` defensively from the wire envelope as a
// string and default to "text" when absent, matching the plan's
// "text is the implicit default kind" contract.
const READ_ME_ELIGIBLE_KINDS = new Set<string>(["text", "fork", "joke"]);

function resolvePersonaMetadata(activity: Activity): PersonaMetadata | null {
  // Defensive: ``activity.metadata`` is ``Record<string, unknown>`` on
  // the wire; the persona blob may be absent, malformed, or partially
  // hydrated. ``getVoiceProfile`` tolerates ``null`` and returns the
  // default profile, so any path here that can't produce a typed
  // PersonaMetadata returns ``null`` instead of throwing.
  const meta = activity.metadata as Record<string, unknown> | undefined;
  if (meta === undefined || meta === null) return null;
  const persona = meta["persona"];
  if (typeof persona !== "object" || persona === null) return null;
  return persona as PersonaMetadata;
}

function readStepKind(stepBody: unknown): string {
  // Phase K K12 is the source of truth for ``step.kind``. Until then,
  // the wire shape may include the field already (engine work in
  // flight) or omit it entirely. Read defensively so a missing field
  // collapses to the implicit ``text`` default and the Read Me button
  // mounts on the steps the K9 plan calls out.
  if (typeof stepBody === "object" && stepBody !== null) {
    const rec = stepBody as Record<string, unknown>;
    const kind = rec["kind"];
    if (typeof kind === "string" && kind.length > 0) return kind;
  }
  return "text";
}

// Phase K K12: derive a song's audio URL from a step's metadata. Two
// supported sources (order matters — first non-empty wins):
//
//   1. ``step.metadata.audio_url`` — the backend has already
//      constructed the URL. K14's ``build_interjection_step`` is the
//      expected producer.
//   2. ``step.metadata.song_id`` — corpus id; the kiosk falls back to
//      ``/api/static/songs/audio/<id>.mp3``, mirroring the static-
//      images mount pattern in ``app.py``. The static mount itself
//      lands in K13 alongside the standalone-intent backend wire.
//
// Returns an empty string when neither key is present — SongPlayer
// then surfaces the error state after the 2s grace, the kiosk
// auto-Next's, and the kid sees the next step instead of a deadlock.
function readSongAudioUrl(step: ActivityStep | null): string {
  if (step === null) return "";
  const meta = step.metadata;
  if (typeof meta !== "object" || meta === null) return "";
  const url = meta["audio_url"];
  if (typeof url === "string" && url.length > 0) return url;
  const songId = meta["song_id"];
  if (typeof songId === "string" && songId.length > 0) {
    // Defensive: refuse anything that looks like an absolute URL
    // path-traversal payload. The corpus loader's kebab-slug regex
    // already gates ids server-side; this client-side belt-and-braces
    // keeps a malformed manifest from injecting an URL escape.
    if (!/^[a-z0-9-]+$/.test(songId)) return "";
    return `/api/static/songs/audio/${songId}.mp3`;
  }
  return "";
}

// Phase K K12: derive a joke's punchline from a step's metadata. K13
// (standalone) and K14 (interjection builder) populate
// ``metadata.punchline``; pre-K13 wire payloads omit it. Empty string
// collapses the JokeStep reveal beat so the kiosk renders a setup-
// only joke rather than crashing on a malformed envelope.
function readJokePunchline(step: ActivityStep | null): string {
  if (step === null) return "";
  const meta = step.metadata;
  if (typeof meta !== "object" || meta === null) return "";
  const p = meta["punchline"];
  if (typeof p === "string" && p.length > 0) return p;
  return "";
}

// Best-effort lookup for the toy display name on the activity envelope.
// The wire ``Activity`` doesn't (yet) carry a hydrated toy summary; if
// the parent attaches one under ``metadata.toys`` (a list of
// ``{id, display_name}`` objects) we surface the matching name for a11y.
// Falls back to undefined which makes ``ToyActionSprite`` render its
// alt as the bare slot key. Tolerant of any shape — the function never
// throws because a kiosk render path can't recover from one.
function lookupToyDisplayName(
  activity: Activity,
  toyId: string,
): string | undefined {
  const toys = activity.metadata?.["toys"];
  if (!Array.isArray(toys)) return undefined;
  for (const entry of toys) {
    if (typeof entry !== "object" || entry === null) continue;
    const rec = entry as Record<string, unknown>;
    if (rec["id"] === toyId) {
      const dn = rec["display_name"];
      if (typeof dn === "string" && dn.length > 0) return dn;
    }
  }
  return undefined;
}

// Phase K K1+ multi-toy sprite resolution: pick the cast member whose
// rendered display name appears earliest in the step body. Word-boundary
// regex avoids substring false-positives (e.g. "Bear" inside "Big Bear").
// Returns the fallback (activity-level ``toy_ids[0]``) when no role
// resolves — pre-K activities, role-less templates, or steps whose body
// names no cast member (transitional / parametric-only steps).
function resolveStepToyId(
  body: string | undefined,
  roles: Activity["roles"],
  fallback: string | null,
): string | null {
  if (!body || !roles) return fallback;
  let bestIdx = -1;
  let bestToyId: string | null = null;
  for (const role of Object.values(roles)) {
    if (!role.toy_id) continue;
    const name = role.display_name;
    if (!name) continue;
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = new RegExp(`\\b${escaped}\\b`).exec(body);
    if (match !== null && (bestIdx === -1 || match.index < bestIdx)) {
      bestIdx = match.index;
      bestToyId = role.toy_id;
    }
  }
  return bestToyId ?? fallback;
}

// The kiosk renders the step flagged `current: true`. While the
// activity is `approved` (parent has approved but the child hasn't
// pressed the button yet) no step is current — we render the first
// step's body as a "ready to start" hint so the kiosk isn't blank.
export function StepCard(props: StepCardProps): JSX.Element {
  const { activity } = props;
  const currentStep = activity.steps.find((s) => s.current) ?? null;
  const previewStep = currentStep ?? activity.steps[0] ?? null;
  const totalSteps = activity.steps.length;
  const currentIndex =
    currentStep !== null
      ? activity.steps.findIndex((s) => s.seq === currentStep.seq) + 1
      : 0;
  // Phase G G4: branching step? If the current step has a non-empty
  // ``choices`` list (rendered by the backend serializer from
  // ``activity_steps.choices_json`` per the plan §"How choices reach
  // the kiosk"), we render one button per choice instead of the bare
  // NextStepButton. The check guards against both ``undefined`` (the
  // pre-G3 wire shape that omits the field) and ``null`` (the G3 wire
  // shape on linear steps).
  const choices =
    currentStep !== null &&
    Array.isArray(currentStep.choices) &&
    currentStep.choices.length > 0
      ? currentStep.choices
      : null;

  // Phase F Step F7 sprite resolution. The sprite renders when the
  // step opts in via ``action_slot`` and we can resolve a toy id.
  //
  // Phase K K1+ adds multi-toy role substitution into step bodies
  // (e.g. ``{quest_giver}`` → "Bowser"). The activity-level
  // ``toy_ids[0]`` is the persona-matched primary toy and is often
  // unrelated to the cast — using it for every step shows the wrong
  // toy whenever the body names a different role's display name.
  // ``resolveStepToyId`` scans ``activity.roles`` for the role whose
  // display name appears earliest in the rendered body and returns
  // that role's toy_id; falls back to ``toy_ids[0]`` for pre-K
  // activities or steps whose body names no cast member.
  const slot =
    previewStep !== null && typeof previewStep.action_slot === "string"
      ? previewStep.action_slot
      : null;
  const fallbackToyId =
    activity.toy_ids !== undefined && activity.toy_ids.length > 0
      ? (activity.toy_ids[0] ?? null)
      : null;
  const toyId = resolveStepToyId(previewStep?.body, activity.roles, fallbackToyId);
  const showSprite = slot !== null && toyId !== null;
  const toyDisplayName =
    showSprite && toyId !== null ? lookupToyDisplayName(activity, toyId) : undefined;

  // Phase K K9: voice profile resolution + flag-driven affordances. The
  // resolver tolerates a null persona and returns the default profile;
  // ``ReadMeButton`` and ``ClickableText`` both render no-op when their
  // respective flag is false, so the kiosk's behavior collapses to
  // pre-K9 layout when both are off.
  const personaMeta = resolvePersonaMetadata(activity);
  const voiceProfile = getVoiceProfile(personaMeta);
  const clickableWordsEnabled = props.clickableWordsEnabled === true;
  const readMeButtonEnabled = props.readMeButtonEnabled === true;
  const stepKind = readStepKind(previewStep);
  // Phase K K12: content-master flags. Default to ``true`` when the
  // prop isn't threaded — preserves the K9-era StepCard semantics for
  // existing tests / non-K-content step renders.
  const songsEnabled = props.songsEnabled !== false;
  const jokesEnabled = props.jokesEnabled !== false;
  // Phase K K12: auto-advance gating. When a song/joke step renders
  // and its content master is OFF, the kiosk silently advances past
  // it. This must be a STEP-LEVEL gate (active on ``currentStep``),
  // not a preview-step gate — the kiosk's "approved but not yet
  // started" state shows the first step as preview, and auto-advancing
  // through it would defeat the kid's "I'm Ready" tap. The gate fires
  // only when ``currentStep !== null`` (i.e. the activity is actually
  // running).
  const songDisabled = stepKind === "song" && !songsEnabled;
  const jokeDisabled = stepKind === "joke" && !jokesEnabled;
  const autoAdvance =
    currentStep !== null &&
    (songDisabled || jokeDisabled) &&
    props.onAdvance !== undefined;
  // ``previewStep === null`` would surface as the "Get ready..." default
  // text — no actual step body to read aloud, so we suppress the Read
  // Me affordance until a step lands.
  const readMeText = previewStep?.body ?? "";
  const showReadMe =
    readMeButtonEnabled &&
    previewStep !== null &&
    readMeText.length > 0 &&
    READ_ME_ELIGIBLE_KINDS.has(stepKind);

  // Phase K K12: fire the auto-advance once on mount of a content-
  // disabled song/joke step. The ``didAdvanceRef`` guard prevents a
  // StrictMode double-effect from POSTing twice; the effect deps key
  // off the step's seq + kind so a *different* disabled step landing
  // in the same activity (rare but possible: two embedded songs) gets
  // its own advance call. The advance is fire-and-forget — App's
  // ``handleAdvance`` already has its own busy guard + conflict
  // handling. We don't await because StepCard is a render path, not
  // an async one.
  const didAdvanceRef = useRef<string | null>(null);
  // ``onAdvance`` is stable across renders (App memoizes via useCallback)
  // but TS can't see that — read it through the props ref so the
  // effect's deps stay minimal and stable.
  const onAdvance = props.onAdvance;
  useEffect(() => {
    if (!autoAdvance || onAdvance === undefined) return;
    // Key on the SPECIFIC step's identity (seq + kind) — a new
    // disabled step on the same activity should trigger its own
    // auto-advance call.
    const key = `${previewStep?.seq ?? "?"}-${stepKind}`;
    if (didAdvanceRef.current === key) return;
    didAdvanceRef.current = key;
    // Fire and forget. The advance handler is responsible for its
    // own error / toast surfacing. We do NOT cancel speech here —
    // the disabled step is invisible to the kid, so there's no
    // utterance to interrupt.
    try {
      onAdvance();
    } catch {
      // Same defensive swallow as elsewhere in the kiosk render
      // pipeline — a throwing parent callback shouldn't take down
      // the kiosk shell.
    }
  }, [autoAdvance, onAdvance, previewStep?.seq, stepKind]);

  // When the kiosk is silently skipping this step, render NOTHING
  // visible. The auto-advance effect above is the only side-effect.
  // We still surface a testid so vitest can assert the skip path.
  if (autoAdvance) {
    return (
      <section
        data-testid="step-card"
        data-step-seq={previewStep?.seq ?? 0}
        data-step-kind={stepKind}
        data-auto-advance="true"
        style={{ display: "none" }}
        aria-hidden="true"
      />
    );
  }

  return (
    <section
      data-testid="step-card"
      data-step-seq={previewStep?.seq ?? 0}
      data-current-index={currentIndex}
      data-step-kind={stepKind}
      style={{
        // ``position: relative`` is the K9 positioning contract — the
        // ``ReadMeButton`` renders ``position: absolute`` and pins to
        // this container's bottom-left. Adding it unconditionally is
        // safe: the prior layout was static-positioned, so a relative
        // wrapper doesn't change child sizing.
        position: "relative",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        gap: 24,
        maxWidth: 1100,
        padding: "0 24px",
      }}
    >
      {totalSteps > 0 && (
        <div
          style={{
            color: "#777",
            fontSize: 22,
            letterSpacing: 1,
            textTransform: "uppercase",
          }}
        >
          {/*
            Phase G G4: drop the "of N" denominator. Phase G makes the
            step count variable per template (3-20 nodes, branching
            paths that visit different subsets) — a literal "Step 3 of
            7" implies a fixed total that the kid won't actually
            traverse on a branched playthrough. The bare "Step N"
            keeps the progress hint without lying about the destination.
          */}
          {currentIndex > 0 ? `Step ${currentIndex}` : "Ready"}
        </div>
      )}
      {/*
        Phase K K12: dispatch on step.kind. ``song`` mounts the
        SongPlayer (which owns Next). ``joke`` mounts the JokeStep
        (setup + delayed punchline, both auto-spoken) PLUS the linear
        NextStepButton. ``text`` / ``fork`` (default) renders the
        existing body-row + choices/next path.

        Phase L L10: ``reward`` mounts the RewardStep, which branches
        internally on ``metadata.reward_kind`` (picture / joke / song).
        Like ``song``, the reward step owns its entire surface — no
        body-row, no linear NextStepButton, no ReadMeButton — so the
        downstream guards exclude both ``song`` and ``reward`` kinds.
      */}
      {stepKind === "reward" && previewStep !== null && (
        <RewardStep
          key={`reward-${previewStep.seq}`}
          metadata={previewStep.metadata ?? null}
          onAdvance={props.onAdvance}
        />
      )}
      {stepKind === "song" && previewStep !== null && (
        <SongPlayer
          // Key on seq so an advancing kiosk unmounts + remounts the
          // player when a new song step lands (e.g. multi-song
          // template). Without the key, React would attempt to
          // re-use the prior audio element and the new src wouldn't
          // trigger autoplay.
          key={`song-${previewStep.seq}`}
          src={readSongAudioUrl(previewStep)}
          title={previewStep.body}
          onEnded={props.onAdvance}
        />
      )}
      {stepKind === "joke" && previewStep !== null && (
        <JokeStep
          key={`joke-${previewStep.seq}`}
          setup={previewStep.body}
          punchline={readJokePunchline(previewStep)}
          profile={voiceProfile}
          clickableWordsEnabled={clickableWordsEnabled}
        />
      )}
      {stepKind !== "song" && stepKind !== "joke" && stepKind !== "reward" && (
        <div
          data-testid="step-body-row"
          style={{
            display: "flex",
            flexDirection: "row",
            alignItems: "center",
            justifyContent: "center",
            // Gap between sprite and body text when the sprite renders;
            // collapses harmlessly to zero visual when the sprite is
            // absent (single-child flex row).
            gap: 24,
            width: "100%",
          }}
        >
          {showSprite && slot !== null && toyId !== null && (
            <ToyActionSprite
              toyId={toyId}
              slot={slot}
              toyDisplayName={toyDisplayName}
            />
          )}
          <h1
            data-testid="step-text"
            style={{
              margin: 0,
              // ``flex: 1`` lets the body text fill the remaining row
              // width when the sprite is present, and naturally take
              // the full row when it isn't.
              flex: 1,
              fontSize: "clamp(2rem, 5vw, 4rem)",
              lineHeight: 1.15,
              fontWeight: 700,
              color: "#222",
            }}
          >
            {/*
              Phase K K9: wrap the visible step body in ClickableText so
              word taps speak the tapped word. The fallback chain stays
              identical to pre-K9 (previewStep.body → activity.title →
              "Get ready..."). When ``clickableWordsEnabled`` is false
              ClickableText renders a plain ``<span>`` — the kiosk's
              visible H1 layout is byte-identical to pre-K9.
            */}
            <ClickableText
              text={previewStep?.body ?? activity.title ?? "Get ready..."}
              profile={voiceProfile}
              enabled={clickableWordsEnabled}
            />
          </h1>
        </div>
      )}
      {/*
        Phase G G4: action button row. When the current step has
        ``choices`` we render a vertical stack of ChoiceButtons (one
        per branch); otherwise the existing NextStepButton. The
        callbacks are optional so layout-only tests that mount
        StepCard without an App don't have to thread no-ops; an
        omitted callback hides the button (the kiosk always supplies
        them in production via App.tsx).

        Phase K K12: skip the linear NextStepButton on song steps —
        SongPlayer owns its own Next button (enabled on onended). The
        joke path keeps the linear NextStepButton because the punchline
        reveal is timed, not interaction-gated.

        Phase L L10: reward steps also skip the linear NextStepButton.
        RewardStep owns its own advance affordance (6s auto-advance +
        tap on picture; SongPlayer's internal Next on song; the linear
        Next is mounted via the joke-reward's JokeStep delegation, so
        the kid still gets a tap target).
      */}
      {stepKind !== "song" && stepKind !== "reward" && choices !== null && props.onChoose !== undefined && (
        <div
          data-testid="choice-button-stack"
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 16,
            width: "100%",
            maxWidth: 600,
            marginTop: 24,
          }}
        >
          {choices.map((choice) => (
            <ChoiceButton
              key={choice.choice_index}
              label={choice.label}
              choiceIndex={choice.choice_index}
              onChoose={props.onChoose!}
              // Disable EVERY button (including the in-flight one) when
              // any choice is being processed. The in-flight button's
              // own internal ``busy`` already disables it locally, but
              // gating it via the App-level prop too is defensive and
              // means siblings + self share one disable signal — no
              // gap between the two for a tap to slip through.
              disabled={
                props.choosingIndex !== undefined &&
                props.choosingIndex !== null
              }
              // Phase K K9: thread the voice profile + flag so the
              // label renders as ClickableText. ChoiceButton handles
              // the off-state by rendering the bare label string —
              // pre-K9 layout intact.
              voiceProfile={voiceProfile}
              clickableWordsEnabled={clickableWordsEnabled}
            />
          ))}
        </div>
      )}
      {stepKind !== "song" && stepKind !== "reward" && choices === null && props.onAdvance !== undefined && (
        <NextStepButton
          onClick={props.onAdvance}
          busy={props.advanceBusy === true}
        />
      )}
      {/*
        Phase K K9 / K12: watermarked Read Me bubble. Self-positioning
        via ``position: absolute`` inside this section's ``position:
        relative`` container (set above). Mounted only on text / fork /
        joke step kinds — song steps own the audio surface (K12). On
        joke steps we wire a custom replay path that speaks BOTH the
        setup and the punchline back-to-back (the ReadMeButton's stock
        ``text`` would only re-speak the setup; ``replayJoke`` queues
        both utterances).
      */}
      {showReadMe && stepKind === "joke" && (
        <JokeReadMeButton
          setup={readMeText}
          punchline={readJokePunchline(previewStep)}
          profile={voiceProfile}
          enabled={readMeButtonEnabled}
        />
      )}
      {showReadMe && stepKind !== "joke" && (
        <ReadMeButton
          text={readMeText}
          profile={voiceProfile}
          enabled={readMeButtonEnabled}
        />
      )}
    </section>
  );
}

// Phase K K12: joke-specific Read Me button. The K9 stock ReadMeButton
// is built around a single ``text`` payload — for joke steps the kid
// needs to hear BOTH the setup AND the punchline. Rather than extend
// the K9 component with a "speak these lines" alternative (which
// would muddy its single-purpose contract), we wrap the same visual
// shell here and call ``replayJoke`` on tap. The visual treatment
// mirrors ReadMeButton's watermark style so the affordance is
// indistinguishable to the kid.
interface JokeReadMeButtonProps {
  setup: string;
  punchline: string;
  profile: VoiceProfile;
  enabled: boolean;
}

function JokeReadMeButton(props: JokeReadMeButtonProps): JSX.Element | null {
  const { setup, punchline, profile, enabled } = props;
  if (!enabled) return null;
  const handleClick = (): void => {
    // ``replayJoke`` itself calls cancel() before issuing the two
    // utterances, so a rapid double-tap surfaces cleanly as
    // "restart from the setup."
    replayJoke(setup, punchline, profile);
  };
  return (
    <>
      <style>{`
        .kiosk-joke-read-me-button {
          opacity: 0.6;
          transition: opacity 120ms ease-out;
        }
        .kiosk-joke-read-me-button:hover,
        .kiosk-joke-read-me-button:focus,
        .kiosk-joke-read-me-button:active {
          opacity: 1;
        }
      `}</style>
      <button
        type="button"
        data-testid="read-me-button"
        data-read-me-variant="joke"
        className="kiosk-joke-read-me-button"
        aria-label="Read Me"
        onClick={handleClick}
        style={JOKE_READ_ME_STYLE}
      >
        ?
      </button>
    </>
  );
}

// Visual treatment matches ReadMeButton's ABSOLUTE_BOTTOM_LEFT_STYLE
// (kept inline rather than imported so a future ReadMeButton refactor
// doesn't drag this component along by accident — K12's joke variant
// is its own affordance even though it visually mirrors K9's).
const JOKE_READ_ME_STYLE = {
  position: "absolute",
  bottom: 16,
  left: 16,
  width: 48,
  height: 48,
  minWidth: 48,
  minHeight: 48,
  borderRadius: "50%",
  border: "2px solid #1976d2",
  background: "white",
  color: "#1976d2",
  fontSize: 28,
  fontWeight: 700,
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 0,
  boxShadow: "0 2px 6px rgba(0,0,0,0.12)",
} as const;

