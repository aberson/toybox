// Phase L Step L10 — Kiosk reward step.
//
// Renders a step of ``kind: "reward"`` by branching on
// ``metadata.reward_kind``:
//
//   * ``picture`` — large pixel-art-style image (mirrors ToyActionSprite's
//     visual treatment at 240px) with the picked CSS animation applied
//     and a caption underneath. Auto-advances after 6s OR on tap.
//   * ``joke``    — delegates to the existing ``<JokeStep>`` (K12). The
//     setup + punchline come from ``metadata.setup`` /
//     ``metadata.punchline`` (NOT ``step.body`` like a kind=joke step).
//   * ``song``    — delegates to the existing ``<SongPlayer>`` (K12).
//
// L4 backend writes the metadata shape this consumes (see
// ``api/activities.py::_terminal_advance``). We read defensively at
// every key — a malformed envelope should render *something* rather
// than crashing the kiosk on the final step of an activity.
//
// Delegation prop mapping. JokeStep wants ``setup, punchline,
// profile, clickableWordsEnabled``; SongPlayer wants ``src, title,
// onEnded``. Phase Z Z1: StepCard now threads its persona-resolved
// voice profile into this branch (``voiceProfile`` prop), so the joke
// reward speaks with the activity's persona voice like every other
// speech surface. We keep ``clickableWordsEnabled=false`` so the joke
// reward speaks (the K8 substrate is already unlocked by the kid's
// "I'm Ready!" tap earlier in the activity) without surfacing a
// tap-to-read affordance that competes with the auto-advance.

import { useEffect, useRef, type CSSProperties, type JSX } from "react";

import type { Animation } from "../../shared/types";
import { REWARD_ANIMATIONS } from "../animations/rewardAnimations";
import { DEFAULT_VOICE_PROFILE } from "../persona-voice";
import type { VoiceProfile } from "../tts";
import { JokeStep } from "./JokeStep";
import { NextStepButton } from "./NextStepButton";
import { SongPlayer } from "./SongPlayer";

// Auto-advance window for the picture reward — long enough for the
// kid to admire the picture + animation, short enough that the
// kiosk doesn't deadlock after the celebration beat. Joke + song
// rewards own their own advance affordance (JokeStep is paired
// with the linear NextStepButton at the StepCard level; SongPlayer
// has its own Next button gated on ``onended``).
const PICTURE_AUTO_ADVANCE_MS = 6000;

// One reward step's worth of metadata. Mirrors the L4 backend wire
// shape; consumers read it defensively (every field may be absent
// or null on a malformed envelope). The wire type on
// ``ActivityStep.metadata`` is ``Record<string, unknown>`` so the
// caller has to narrow — RewardStep accepts the wire shape directly
// rather than forcing the caller to pre-narrow, mirroring the
// existing K12 dispatch pattern in StepCard.
export type RewardKind = "picture" | "joke" | "song";

export interface RewardStepProps {
  // The raw ``step.metadata`` dict from the wire. Narrow shapes:
  //   reward_kind: "picture" | "joke" | "song"
  //   reward_id:   string
  //   image_url:   string | null         (picture only)
  //   animation:   Animation | null      (picture only)
  //   audio_url:   string | null         (song only)
  //   body:        string                (display name / song title /
  //                                       joke fallback body)
  //   setup:       string | null         (joke)
  //   punchline:   string | null         (joke)
  metadata: Record<string, unknown> | null | undefined;
  // Linear-advance callback (App's existing ``handleAdvance``).
  // Wired by StepCard so the picture reward's 6s timer + tap-to-
  // advance share the same signal as the rest of the kiosk's
  // step flow. Optional so layout-only tests can mount without
  // an App.
  onAdvance?: () => void;
  // Phase Z Z1: the persona-resolved voice profile, threaded by
  // StepCard (which resolves it once per render via
  // ``getVoiceProfile``). Drives the joke reward's spoken setup/
  // punchline so the reward beat matches the persona voice used by
  // every other speech surface. Optional so layout-only tests can
  // mount without a profile — the canonical DEFAULT_VOICE_PROFILE
  // from ``persona-voice.ts`` (one source of truth) is the fallback.
  voiceProfile?: VoiceProfile;
}

// Defensive readers — every key may be absent on a malformed
// envelope. Returning the appropriate "empty" sentinel keeps the
// kiosk render path total instead of throwing on a bad wire payload.
function readString(meta: Record<string, unknown>, key: string): string {
  const v = meta[key];
  return typeof v === "string" ? v : "";
}

function readStringOrNull(
  meta: Record<string, unknown>,
  key: string,
): string | null {
  const v = meta[key];
  return typeof v === "string" && v.length > 0 ? v : null;
}

function readAnimation(meta: Record<string, unknown>): Animation | null {
  const v = meta["animation"];
  if (typeof v !== "string") return null;
  // Type guard via the shared ANIMATION_OPTIONS surface would be
  // ideal, but importing the parent-side list pulls a parent-only
  // module into the kiosk bundle. The REWARD_ANIMATIONS keys are
  // the authoritative kiosk-side enumeration of Animation members.
  if (v in REWARD_ANIMATIONS) return v as Animation;
  return null;
}

function readRewardKind(
  meta: Record<string, unknown>,
): RewardKind | null {
  const v = meta["reward_kind"];
  if (v === "picture" || v === "joke" || v === "song") return v;
  return null;
}

export function RewardStep(props: RewardStepProps): JSX.Element | null {
  const { metadata, onAdvance, voiceProfile = DEFAULT_VOICE_PROFILE } = props;
  // Defensive: metadata may be null/undefined on a malformed
  // envelope. Render nothing rather than crashing — the StepCard
  // dispatch already gate the mount on kind === "reward" so a
  // missing metadata payload here is a backend bug, not a kiosk
  // expectation.
  if (metadata === null || metadata === undefined) {
    return (
      <div
        data-testid="reward-step"
        data-reward-kind=""
        style={{ display: "none" }}
        aria-hidden="true"
      />
    );
  }
  const rewardKind = readRewardKind(metadata);
  if (rewardKind === null) {
    return (
      <div
        data-testid="reward-step"
        data-reward-kind=""
        style={{ display: "none" }}
        aria-hidden="true"
      />
    );
  }

  if (rewardKind === "joke") {
    // The reward joke's setup/punchline ride on metadata.setup +
    // metadata.punchline (NOT ``step.body``, which carries the
    // display name for picture rewards). Fall back to ``body`` for
    // setup so a malformed envelope still surfaces something.
    const setup =
      readStringOrNull(metadata, "setup") ?? readString(metadata, "body");
    const punchline = readString(metadata, "punchline");
    return (
      <div
        data-testid="reward-step"
        data-reward-kind="joke"
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 24,
        }}
      >
        <JokeStep
          setup={setup}
          punchline={punchline}
          profile={voiceProfile}
          clickableWordsEnabled={false}
        />
        {/* Joke rewards don't have an internal Next affordance the way
            SongPlayer does — surface the linear NextStepButton so the
            kid can advance past the punchline reveal. Picture rewards
            use the 6s timer + tap-to-advance instead (see PictureReward
            below); song rewards rely on SongPlayer's built-in Next. */}
        {onAdvance !== undefined && (
          <NextStepButton onClick={onAdvance} busy={false} />
        )}
      </div>
    );
  }

  if (rewardKind === "song") {
    const audioUrl = readString(metadata, "audio_url");
    const title = readString(metadata, "body");
    return (
      <div data-testid="reward-step" data-reward-kind="song">
        <SongPlayer src={audioUrl} title={title} onEnded={onAdvance} />
      </div>
    );
  }

  // rewardKind === "picture"
  const imageUrl = readStringOrNull(metadata, "image_url");
  const animation = readAnimation(metadata);
  const body = readString(metadata, "body");
  return (
    <PictureReward
      imageUrl={imageUrl}
      animation={animation}
      body={body}
      onAdvance={onAdvance}
    />
  );
}

interface PictureRewardProps {
  imageUrl: string | null;
  animation: Animation | null;
  body: string;
  onAdvance?: () => void;
}

function PictureReward(props: PictureRewardProps): JSX.Element {
  const { imageUrl, animation, body, onAdvance } = props;
  // Track whether we've already fired advance so a fast tap +
  // timer firing in close succession only POSTs once. The
  // App-level ``handleAdvance`` already has its own busy guard,
  // but a local single-fire ref makes the intent explicit and
  // keeps a re-render mid-advance from re-queueing the same call.
  const advancedRef = useRef(false);
  const fireAdvance = (): void => {
    if (advancedRef.current) return;
    advancedRef.current = true;
    if (onAdvance === undefined) return;
    try {
      onAdvance();
    } catch {
      // Defensive: never crash the kiosk on a parent callback error.
    }
  };

  useEffect(() => {
    const id = setTimeout(fireAdvance, PICTURE_AUTO_ADVANCE_MS);
    return () => {
      clearTimeout(id);
    };
    // Fire once on mount; ``onAdvance`` is captured via the
    // closure-scoped fireAdvance which itself reads through the
    // ref guard, so a parent-side rebind of the callback doesn't
    // restart the timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const animationStyle: CSSProperties =
    animation !== null ? REWARD_ANIMATIONS[animation] : {};

  const imageStyle: CSSProperties = {
    width: 240,
    height: 240,
    flexShrink: 0,
    imageRendering: "pixelated",
    objectFit: "contain",
    background: "transparent",
    ...animationStyle,
  };

  return (
    <div
      data-testid="reward-step"
      data-reward-kind="picture"
      data-reward-animation={animation ?? ""}
      onClick={fireAdvance}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          fireAdvance();
        }
      }}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 24,
        textAlign: "center",
        cursor: "pointer",
        // ``outline: none`` here would hurt a11y; rely on the
        // default focus ring. The role=button + tabIndex make the
        // whole picture-reward surface tap/Enter/Space activatable.
      }}
    >
      {imageUrl !== null && (
        <img
          data-testid="reward-picture-image"
          src={imageUrl}
          alt={body.length > 0 ? body : "reward"}
          width={240}
          height={240}
          style={imageStyle}
        />
      )}
      {body.length > 0 && (
        <div
          data-testid="reward-picture-caption"
          style={{
            fontSize: "clamp(1.5rem, 4vw, 3rem)",
            fontWeight: 700,
            color: "#222",
            lineHeight: 1.2,
            maxWidth: 800,
          }}
        >
          {body}
        </div>
      )}
    </div>
  );
}
