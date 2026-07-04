import { useEffect, useRef, type JSX } from "react";

import type { Activity, ActivityStep } from "../api";
import {
  readSpokenAudioUrl,
  readSpokenChoiceAudioUrls,
  readSpokenPunchlineAudioUrl,
  readSpokenSetupAudioUrl,
} from "../clip-audio";
import { getVoiceProfile, type PersonaMetadata } from "../persona-voice";
import type { VoiceProfile } from "../tts";
import { ChoiceButton, type ChoiceResult } from "./ChoiceButton";
import { ChoiceReadButton } from "./ChoiceReadButton";
import { ClickableText } from "./ClickableText";
import { ElementCard } from "./ElementCard";
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
  // Phase R Step R2: spoken text character limit for the Read Me button.
  // Passed through from App.tsx's bootstrap-fetched setting. ``0``
  // means no truncation (off). Optional + defaults to 0 so existing
  // tests that mount StepCard without an App don't need to thread it.
  spokenTextLimit?: number;
  // When true the cast sprites prefer the Claude-authored ``<slot>.svg``
  // (idle self-animating) over the PNG — set when the operator picked the
  // ``claude_svg`` image-gen mode. Threaded from App.tsx's bootstrap
  // fetch; optional + default false so layout-only tests don't pay a
  // wasted ``.svg`` 404.
  preferSvg?: boolean;
  // Phase Z Z5: neural-voice gate for the clip-playback surfaces
  // (ReadMeButton / ChoiceReadButton / JokeStep / reward jokes).
  // Optional + DEFAULT TRUE — unlike the K-flags above, absence means
  // "clips on" because the Z4 wire already carries clip URLs and the
  // gate's parent flag only ships in Z6 (which threads the fetched
  // value from App). Off → every surface uses Web Speech directly, no
  // clip attempts.
  neuralVoiceEnabled?: boolean;
}

// Step kinds that should get a Read Me button. ``song`` is excluded —
// the audio surface is owned by K12's SongPlayer (a song step renders
// a bundled MP3, and a competing TTS read-aloud would interrupt the
// song). The wire schema for ``ActivityStep.kind`` is not yet declared
// on the ``ActivityStep`` interface (K12 lands the new kinds); for K9
// we read ``step.kind`` defensively from the wire envelope as a
// string and default to "text" when absent, matching the plan's
// "text is the implicit default kind" contract.
// Phase W Step W4: "adventure_beat" renders through the default text/fork
// path (body + choices + Next), so it is Read-Me eligible like text steps.
// Phase W Step W5: "boss_fight" is the interactive climax beat — it renders
// through the SAME default body + choices path (plus a static "BOSS" banner),
// so its body should be read aloud like an adventure_beat.
const READ_ME_ELIGIBLE_KINDS = new Set<string>([
  "text",
  "fork",
  "joke",
  "adventure_beat",
  "boss_fight",
]);

// Phase W Step W5: the kiosk step.kind for the boss-fight climax beat. The
// backend (adventure engine) stamps this on the final generated beat when
// the household ``boss_fights_enabled`` flag is on. Kept as a constant so
// the render branch + the test target the same literal.
const BOSS_FIGHT_KIND = "boss_fight";

// Phase W Step W5: STATIC boss-fight banner styling. Deliberately uses NO
// animation / transition / strobe — a darker, bold, high-contrast framing
// only — so it cannot flash for motion- or photosensitive children. The
// no-flashing requirement (Phase S / SWR a11y convention) is satisfied
// structurally: there is nothing here for prefers-reduced-motion to disable.
const BOSS_BANNER_STYLE = {
  display: "inline-block",
  padding: "6px 18px",
  borderRadius: 12,
  background: "#2a1245",
  color: "#ffd166",
  fontSize: "clamp(1rem, min(3vw, 3.5vh), 1.6rem)",
  fontWeight: 900,
  letterSpacing: 3,
  textTransform: "uppercase",
  border: "2px solid #ffd166",
  boxShadow: "0 4px 16px rgba(0,0,0,0.35)",
} as const;

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

// Phase M Step M3: derive ElementCard props from a step's wire shape.
// The backend denormalizes the corpus fields into ``step.metadata``
// (``element_symbol`` / ``element_name`` / ``element_atomic_number``)
// alongside the top-level ``element_id`` so the kiosk doesn't need a
// separate ``/api/elements/<id>`` fetch — same idiom as song's
// ``metadata.audio_url`` / joke's ``metadata.punchline``. Returns
// ``null`` when ``element_id`` is absent / null, OR when the metadata
// lacks the denormalized fields (defensive: a future serializer
// regression that drops the enrichment shouldn't crash the kiosk;
// ElementCard renders the fallback avatar in that case anyway via
// onError, but we still need the four props to mount the component).
interface ElementCardData {
  elementId: string;
  symbol: string;
  name: string;
  atomicNumber: number;
}

function readElementCardData(step: ActivityStep | null): ElementCardData | null {
  if (step === null) return null;
  const elementId = step.element_id;
  if (typeof elementId !== "string" || elementId.length === 0) return null;
  const meta = step.metadata;
  // Pull the denormalized fields out of metadata defensively. Each
  // field is read independently so a partial wire payload still
  // surfaces what's available — ElementCard tolerates empty strings
  // (renders the sprite + whatever text fields are populated).
  let symbol = "";
  let name = "";
  let atomicNumber = 0;
  if (typeof meta === "object" && meta !== null) {
    const recMeta = meta as Record<string, unknown>;
    const s = recMeta["element_symbol"];
    if (typeof s === "string") symbol = s;
    const n = recMeta["element_name"];
    if (typeof n === "string") name = n;
    const an = recMeta["element_atomic_number"];
    if (typeof an === "number") atomicNumber = an;
  }
  return { elementId, symbol, name, atomicNumber };
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

// Roles that show ALWAYS on the step card — friends, helpers, guides,
// and other "along for the ride" cast members. Source of truth for the
// classification is the operator's UAT feedback: "friend types or
// guides, almost all, should be along for the ride."
const ALWAYS_VISIBLE_ROLES: ReadonlySet<string> = new Set([
  "friend",
  "sidekick",
  "helper_townsperson",
  "quest_giver",
  "guide_mentor",
  "needs_saving",
  "trickster",
  "frenemy",
]);

// Roles that show ONLY when the current step's rendered body names
// them — bosses/antagonists shouldn't loom on the card until their part.
const CONTEXT_DEPENDENT_ROLES: ReadonlySet<string> = new Set([
  "boss_mini_boss",
  "big_bad_boss",
]);

// Deterministic left/right side assignment per toy id so the layout is
// stable across re-renders (a kid hitting reload sees the same arrangement)
// and roughly balanced across the activity's cast.
function deterministicSide(toyId: string): "left" | "right" {
  let hash = 0;
  for (let i = 0; i < toyId.length; i++) {
    hash = (hash + toyId.charCodeAt(i)) % 1000;
  }
  return hash % 2 === 0 ? "left" : "right";
}

// Sprite size shrinks with cast count so 4-toy steps still fit on
// iPad portrait. Per-toy size is the same so the heaviest case caps the
// step's vertical real estate.
function spriteSizeForCount(count: number): number {
  if (count <= 1) return 112;
  if (count <= 3) return 84;
  return 64;
}

interface CastMember {
  toyId: string;
  displayName: string;
  side: "left" | "right";
}

// Multi-toy cast resolution. Returns every cast member that should
// render at the current step: all ALWAYS_VISIBLE_ROLES toys + any
// CONTEXT_DEPENDENT_ROLES toy whose display name is named in the step
// body. Unknown role names (forward-compat for a future role taxonomy
// extension) are included conservatively. Falls back to a single-sprite
// list built from ``activity.toy_ids[0]`` for pre-K activities (no
// roles map) so the F7-era single-sprite behavior is preserved.
function resolveStepCast(
  activity: Activity,
  stepBody: string | undefined,
): CastMember[] {
  const roles = activity.roles;
  if (!roles || Object.keys(roles).length === 0) {
    const fallback =
      activity.toy_ids !== undefined && activity.toy_ids.length > 0
        ? (activity.toy_ids[0] ?? null)
        : null;
    if (fallback === null) return [];
    return [
      {
        toyId: fallback,
        displayName: lookupToyDisplayName(activity, fallback) ?? "",
        side: deterministicSide(fallback),
      },
    ];
  }
  const cast: CastMember[] = [];
  const seen = new Set<string>();
  for (const role of Object.values(roles)) {
    if (!role.toy_id) continue;
    if (seen.has(role.toy_id)) continue;
    // ALWAYS_VISIBLE: include unconditionally.
    // CONTEXT_DEPENDENT: include only if the rendered body names them.
    // Unknown role-name (forward-compat for a future role added without
    // updating these sets): include unconditionally — better to over-show
    // than to silently hide a new role.
    const classification: "always" | "context" | "unknown" =
      ALWAYS_VISIBLE_ROLES.has(role.role_name)
        ? "always"
        : CONTEXT_DEPENDENT_ROLES.has(role.role_name)
          ? "context"
          : "unknown";
    if (classification === "context") {
      if (!stepBody) continue;
      const escaped = role.display_name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      if (!new RegExp(`\\b${escaped}\\b`).test(stepBody)) continue;
    }
    seen.add(role.toy_id);
    cast.push({
      toyId: role.toy_id,
      displayName: role.display_name,
      side: deterministicSide(role.toy_id),
    });
  }
  return cast;
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

  // Phase F Step F7 + multi-toy cast (post-O UAT). Sprites render
  // alongside the body text. Pre-K activities (no ``activity.roles``)
  // get the single-sprite F7-era fallback from ``toy_ids[0]``. K+
  // activities with a cast surface ALL friend/helper/guide-type roles
  // every step; boss-style roles only render on steps where the body
  // names them.
  //
  // ``action_slot`` defaults to ``"idle"`` when the template doesn't
  // specify one — pre-fix the kiosk omitted sprites entirely on
  // non-action steps, which hid the cast for most step kinds. Operator
  // feedback: cast should be visible across the whole activity.
  const slotFromStep =
    previewStep !== null && typeof previewStep.action_slot === "string"
      ? previewStep.action_slot
      : null;
  const slot = slotFromStep ?? "idle";
  const cast = resolveStepCast(activity, previewStep?.body);
  const spriteSize = spriteSizeForCount(cast.length);
  const leftCast = cast.filter((m) => m.side === "left");
  const rightCast = cast.filter((m) => m.side === "right");

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
  // Prefer the .svg sprite for the cast when set. Default false so the
  // common (non-claude_svg) path loads .png directly (no .svg 404 churn).
  const preferSvg = props.preferSvg === true;
  // Phase Z Z5: neural-voice gate (default TRUE — see the prop doc) +
  // the per-step clip URLs from the Z4 wire shape, read once per render
  // through the shared accessors (clip-audio.ts owns the key literals).
  // ALL clips read from ``previewStep`` — the ONE step object whose
  // text the speech surfaces render. That single source is safe for
  // the choice bubbles too: ``choices`` above only renders when
  // ``currentStep !== null``, and in that case ``previewStep`` IS
  // ``currentStep`` by construction (``previewStep = currentStep ??
  // steps[0]``), so the URL list stays index-aligned with the SAME
  // step the labels come from. Reading the choice list from
  // ``currentStep`` separately would be a two-source split that can
  // never diverge — and therefore can never be tested.
  const neuralVoiceEnabled = props.neuralVoiceEnabled !== false;
  const stepClipUrl = readSpokenAudioUrl(previewStep?.metadata);
  const jokeSetupClipUrl = readSpokenSetupAudioUrl(previewStep?.metadata);
  const jokePunchlineClipUrl = readSpokenPunchlineAudioUrl(
    previewStep?.metadata,
  );
  const choiceClipUrls = readSpokenChoiceAudioUrls(previewStep?.metadata);
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
        // ``position: relative`` was originally the K9 positioning
        // contract for ``ReadMeButton`` (absolute → pinned to this
        // section's bottom-left). #137 moved both Read Me variants to
        // ``position: fixed`` (anchored to the viewport, not this
        // section) because the section's intrinsic height varied across
        // step kinds and the absolute-pinned button drifted to mid-
        // screen on fork cards. The relative is retained here as a
        // harmless stacking-context isolator — no current consumer
        // requires it, but removing it is out of scope for #137.
        position: "relative",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        // Section gap shrinks on short viewports so fork steps (with 3+
        // choice buttons) and element steps (with ElementCard on top)
        // keep every affordance reachable without scrolling.
        gap: "clamp(8px, 2vh, 24px)",
        maxWidth: 1100,
        // Phase S S1: card surface treatment — a subtle rounded card
        // lifts the step content off the persona gradient so the text
        // stays readable regardless of background hue. The padding
        // increase (was "0 24px") gives the body more breathing room.
        padding: "clamp(16px, 3vh, 32px) clamp(24px, 4vw, 48px)",
        background: "rgba(255,255,255,0.82)",
        borderRadius: 20,
        boxShadow: "0 8px 32px rgba(0,0,0,0.25), 0 2px 8px rgba(0,0,0,0.15)",
        border: "1.5px solid rgba(0,0,0,0.10)",
      }}
    >
      {totalSteps > 0 && (
        <div
          style={{
            color: "#777",
            fontSize: "clamp(14px, min(2vw, 2.4vh), 22px)",
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
          voiceProfile={voiceProfile}
          // Phase Z Z5: reward jokes read their own clip URLs from the
          // metadata prop above; only the gate threads through.
          neuralVoiceEnabled={neuralVoiceEnabled}
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
          // Phase Z Z5: neural clips for the two beats (Z4 wire shape);
          // JokeStep sequences setup → beat → punchline on both the
          // clip and Web Speech paths and falls back per beat.
          setupClipUrl={jokeSetupClipUrl}
          punchlineClipUrl={jokePunchlineClipUrl}
          neuralVoiceEnabled={neuralVoiceEnabled}
        />
      )}
      {/*
        Phase M Step M3: Periodic Table element card. Renders inline
        ABOVE the step body whenever ``step.element_id`` is non-null —
        regardless of step kind. Iter-1 added a step-kind gate
        (text/fork only) intended to prevent ElementCard from competing
        with SongPlayer / JokeStep / RewardStep chrome; reviewers
        flagged that as silent suppression (kiosk shows nothing,
        template author expected a card to render). Per the iter-2
        prompt and the M3 plan §5.3 which does NOT restrict ElementCard
        to specific kinds, the gate is removed. If we later want to
        forbid ``element_id`` on song/joke/reward steps, that's a
        validator-side gate (out of scope for M3).
      */}
      {(() => {
        const elementData = readElementCardData(previewStep);
        if (elementData === null) return null;
        return (
          <ElementCard
            elementId={elementData.elementId}
            symbol={elementData.symbol}
            name={elementData.name}
            atomicNumber={elementData.atomicNumber}
          />
        );
      })()}
      {/*
        Phase W Step W5: boss-fight CLIMAX banner. Renders a clear, STATIC
        "BOSS" frame above the beat body so the kid knows this is the big
        moment. No animation/strobe (a11y: nothing for
        prefers-reduced-motion to disable). The beat body + the "how do you
        defeat the boss" choices render below through the SAME default
        body-row + choice-stack path the adventure_beat uses.
      */}
      {stepKind === BOSS_FIGHT_KIND && (
        <div
          data-testid="boss-fight-banner"
          data-boss-fight="true"
          style={{ textAlign: "center" }}
        >
          <span style={BOSS_BANNER_STYLE}>⚔ Boss Fight ⚔</span>
        </div>
      )}
      {stepKind !== "song" && stepKind !== "joke" && stepKind !== "reward" && (
        <div
          data-testid="step-body-row"
          style={{
            display: "flex",
            flexDirection: "row",
            alignItems: "center",
            justifyContent: "center",
            // Gap between sprite columns + body text; collapses
            // harmlessly to zero visual when neither column has sprites.
            gap: 24,
            width: "100%",
          }}
        >
          {leftCast.length > 0 && (
            <div
              data-testid="step-cast-left"
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 12,
                flexShrink: 0,
              }}
            >
              {leftCast.map((member) => (
                <ToyActionSprite
                  key={member.toyId}
                  toyId={member.toyId}
                  slot={slot}
                  toyDisplayName={
                    member.displayName.length > 0 ? member.displayName : undefined
                  }
                  size={spriteSize}
                  preferSvg={preferSvg}
                />
              ))}
            </div>
          )}
          <h1
            data-testid="step-text"
            style={{
              margin: 0,
              // ``flex: 1`` lets the body text fill the remaining row
              // width when the sprite is present, and naturally take
              // the full row when it isn't. Font-size clamp uses
              // min(vw, vh) so portrait-tablet viewports shrink the
              // body text to keep the NextStepButton on screen below
              // the ElementCard + body text on element activity steps.
              //
              // Phase S S1: minimum lowered from 1.5rem → 1.2rem to
              // keep very small viewports from going tiny, but the
              // responsive middle band is bumped (min(4vw,5vh) →
              // min(4.5vw,5.5vh)) to hit ~1.25rem+ at arm's-length
              // iPad portrait widths (~768px = ~34.5px body). The 3rem
              // cap is unchanged — prevents oversized text on wide
              // desktop viewports.
              flex: 1,
              fontSize: "clamp(1.2rem, min(4.5vw, 5.5vh), 3rem)",
              lineHeight: 1.2,
              fontWeight: 700,
              color: "#1a1a2e",
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
          {rightCast.length > 0 && (
            <div
              data-testid="step-cast-right"
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 12,
                flexShrink: 0,
              }}
            >
              {rightCast.map((member) => (
                <ToyActionSprite
                  key={member.toyId}
                  toyId={member.toyId}
                  slot={slot}
                  toyDisplayName={
                    member.displayName.length > 0 ? member.displayName : undefined
                  }
                  size={spriteSize}
                  preferSvg={preferSvg}
                />
              ))}
            </div>
          )}
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
            // Gap + marginTop shrink with viewport height so a 3-choice
            // stack stays on screen on iPad portrait.
            gap: "clamp(6px, 1.5vh, 16px)",
            width: "100%",
            maxWidth: 600,
            marginTop: "clamp(8px, 1.5vh, 24px)",
          }}
        >
          {choices.map((choice) => {
            const choiceButton = (
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
            );
            // Read-aloud split: when the household Read Me flag is on,
            // each option gets its own read-aloud bubble NEXT TO the
            // choice pill (the bottom-left "?" reads only the prompt
            // body). The bubble is a SIBLING inside a row wrapper —
            // never a child of the ChoiceButton (nested <button>s are
            // invalid HTML and a read tap must not advance the
            // activity). Flag off → the bare ChoiceButton renders with
            // no wrapper, keeping the pre-split DOM byte-identical
            // (K9 convention: an absent flag adds NO DOM nodes).
            if (!readMeButtonEnabled) return choiceButton;
            return (
              <div
                key={choice.choice_index}
                data-testid="choice-row"
                style={{
                  display: "flex",
                  flexDirection: "row",
                  alignItems: "center",
                  gap: 10,
                  width: "100%",
                }}
              >
                {choiceButton}
                <ChoiceReadButton
                  label={choice.label}
                  choiceIndex={choice.choice_index}
                  profile={voiceProfile}
                  enabled={readMeButtonEnabled}
                  limit={props.spokenTextLimit ?? 0}
                  // Phase Z Z5: the ``choice_index``-aligned entry of
                  // ``spoken_choice_audio_urls`` (the serializer derives
                  // choice_index from array position, so it doubles as
                  // the list index). Missing/short list → null → the
                  // bubble stays on the Web Speech path.
                  clipUrl={choiceClipUrls[choice.choice_index] ?? null}
                  neuralVoiceEnabled={neuralVoiceEnabled}
                />
              </div>
            );
          })}
        </div>
      )}
      {/*
        Phase R Step R3: Q&A gating. When the current step has a
        ``question`` field and ``question_pending`` is true, show the
        question text and replace the Next button with "Waiting for
        parent…". The parent's ActivityPanel resolves the question via
        the approve-question endpoint, which emits a WS envelope that
        sets question_pending=false and lets the Next button re-appear.
      */}
      {currentStep !== null && typeof currentStep.question === "string" && currentStep.question.length > 0 && (
        <div
          data-testid="step-question"
          style={{
            marginTop: "clamp(8px, 2vh, 20px)",
            padding: "12px 16px",
            background: "#fff8e1",
            border: "1px solid #ffe082",
            borderRadius: 8,
            fontSize: "clamp(1rem, min(2.5vw, 3vh), 1.5rem)",
            color: "#5d4037",
            maxWidth: 600,
            textAlign: "center",
          }}
        >
          {currentStep.question}
        </div>
      )}
      {stepKind !== "song" && stepKind !== "reward" && choices === null && props.onAdvance !== undefined && (
        currentStep !== null && currentStep.question_pending === true
          ? (
            <div
              data-testid="waiting-for-parent"
              style={{
                marginTop: "clamp(8px, 2vh, 20px)",
                color: "#888",
                fontSize: "clamp(1rem, min(2.5vw, 3vh), 1.5rem)",
                fontStyle: "italic",
              }}
            >
              Waiting for parent…
            </div>
          )
          : (
            <NextStepButton
              onClick={props.onAdvance}
              busy={props.advanceBusy === true}
            />
          )
      )}
      {/*
        Phase K K9 / K12: watermarked Read Me bubble. Self-positioning
        via ``position: fixed`` anchored to the viewport's bottom-left
        (#137; was ``position: absolute`` inside this section's
        ``position: relative`` until fork-step drift surfaced). Mounted
        only on text / fork / joke step kinds — song steps own the
        audio surface (K12). On joke steps we wire a custom replay path
        that speaks BOTH the setup and the punchline back-to-back (the
        ReadMeButton's stock ``text`` would only re-speak the setup;
        ``replayJoke`` queues both utterances).
      */}
      {showReadMe && stepKind === "joke" && (
        <JokeReadMeButton
          setup={readMeText}
          punchline={readJokePunchline(previewStep)}
          profile={voiceProfile}
          enabled={readMeButtonEnabled}
          // Phase Z Z5: the replay plays the same clips the autoplay
          // used (sequential setup → punchline; per-beat fallback).
          setupClipUrl={jokeSetupClipUrl}
          punchlineClipUrl={jokePunchlineClipUrl}
          neuralVoiceEnabled={neuralVoiceEnabled}
        />
      )}
      {showReadMe && stepKind !== "joke" && (
        <ReadMeButton
          text={readMeText}
          profile={voiceProfile}
          enabled={readMeButtonEnabled}
          limit={props.spokenTextLimit ?? 0}
          // Phase Z Z5: neural clip for the step body (Z4 wire shape);
          // full-text clip first, truncated Web Speech fallback.
          clipUrl={stepClipUrl}
          neuralVoiceEnabled={neuralVoiceEnabled}
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
  // Phase Z Z5: clip URLs + gate for the replay path — threaded to
  // ``replayJoke`` so a tap replays the same neural clips the autoplay
  // used (sequential, per-beat Web Speech fallback).
  setupClipUrl?: string | null;
  punchlineClipUrl?: string | null;
  neuralVoiceEnabled?: boolean;
}

function JokeReadMeButton(props: JokeReadMeButtonProps): JSX.Element | null {
  const {
    setup,
    punchline,
    profile,
    enabled,
    setupClipUrl = null,
    punchlineClipUrl = null,
    neuralVoiceEnabled = true,
  } = props;
  if (!enabled) return null;
  const handleClick = (): void => {
    // ``replayJoke`` itself takes audio focus (cancel()/playClip's
    // interrupt) before issuing the two beats, so a rapid double-tap
    // surfaces cleanly as "restart from the setup."
    replayJoke(setup, punchline, profile, {
      setupClipUrl,
      punchlineClipUrl,
      neuralVoiceEnabled,
    });
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

// Visual treatment matches ReadMeButton's FIXED_BOTTOM_LEFT_STYLE
// (kept inline rather than imported so a future ReadMeButton refactor
// doesn't drag this component along by accident — K12's joke variant
// is its own affordance even though it visually mirrors K9's).
// ``position: fixed`` anchors to the viewport; see #137 + the K9
// component header for the rationale.
const JOKE_READ_ME_STYLE = {
  position: "fixed",
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

