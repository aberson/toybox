// Phase H step H5: Settings sub-tab panel. Houses the listening-mode
// toggle, mic-mute toggle, image-gen-mode toggle, and the new global
// banned-themes editor. Replaces the toggle half of the pre-H5
// OperatorTab; the metrics-snapshot half moved to ``StatsPanel``.
//
// All three toggles are button-driven (write-on-click) with optimistic
// local updates, so SettingsPanel doesn't need to subscribe to the
// ``metrics`` ws topic. The one place a metrics value is consumed is
// the *initial* display state of the listening-mode + mic-mute toggles:
// a one-shot ``GET /api/metrics`` on mount seeds those. From then on
// the toggle components own their displayed state via the
// ``onModeChanged`` / ``onMicEnabledChanged`` callbacks.
//
// ImageGenModeToggle does its own GET-on-mount and PUT-on-click; it
// lives in this file (moved from OperatorTab.tsx) so the
// ``ImageGenModeToggle.test.tsx`` import path stays close.

import type { CSSProperties, JSX } from "react";
import { useCallback, useEffect, useState } from "react";

import { isAbortError } from "../api";
import type {
  ApiClient,
  GameComplexity,
  GameLinearity,
  ImageGenMode,
  ListeningMode,
  MetricsSnapshot,
  ParentInvolvement,
  PhaseKFeatureFlag,
  PhaseKFeatureFlags,
  PlayTargetDepth,
  QaGrading,
  SpokenTextLimit,
} from "../api";
import { BannedThemesSettings } from "./BannedThemesSettings";
import { BossFightsControl } from "./BossFightsControl";
import { GameComplexityControl } from "./GameComplexityControl";
import { GameLinearityControl } from "./GameLinearityControl";
import { ParentInvolvementControl } from "./ParentInvolvementControl";
import { PlayFeaturesControls } from "./PlayFeaturesControls";
import {
  PlayTargetDepthControl,
} from "./PlayQueueSettingsControls";
import { QaGradingControl } from "./QaGradingControl";
import { SpokenTextLimitControl } from "./SpokenTextLimitControl";
import { TranscriptRetentionControl } from "./TranscriptRetentionControl";

const GRID_STYLE: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
  gap: 12,
  marginTop: 8,
};

const CARD_STYLE: CSSProperties = {
  border: "1px solid #e5e7eb",
  borderRadius: 6,
  padding: 8,
  background: "#fff",
  minWidth: 0,
};

const SECTION_HEADING_STYLE: CSSProperties = {
  fontSize: 13,
  margin: "0 0 6px 0",
  color: "#374151",
  fontWeight: 600,
};

interface ListeningModeChoice {
  mode: ListeningMode;
  label: string;
}

const LISTENING_MODES: readonly ListeningModeChoice[] = [
  { mode: 1, label: "OFFLINE" },
  { mode: 2, label: "LOW" },
  { mode: 3, label: "DEFAULT" },
  { mode: 4, label: "HIGH" },
  { mode: 5, label: "INTENSE" },
];

function isListeningMode(value: number): value is ListeningMode {
  return value === 1 || value === 2 || value === 3 || value === 4 || value === 5;
}

interface ListeningModeControlProps {
  api: Pick<ApiClient, "setListeningMode">;
  currentMode: number;
  onModeChanged: (mode: ListeningMode) => void;
}

function ListeningModeControl(props: ListeningModeControlProps): JSX.Element {
  const { api, currentMode, onModeChanged } = props;
  const [pendingMode, setPendingMode] = useState<ListeningMode | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Show the optimistic pending mode while the PUT is in flight, then
  // fall back to whatever the snapshot says (which the parent updates
  // from the response).
  const displayedMode = pendingMode ?? currentMode;

  const handleClick = useCallback(
    (mode: ListeningMode): void => {
      if (pendingMode !== null) return;
      setPendingMode(mode);
      setError(null);
      api
        .setListeningMode(mode)
        .then((resp) => {
          if (isListeningMode(resp.mode)) {
            onModeChanged(resp.mode);
          }
          setPendingMode(null);
        })
        .catch((err: unknown) => {
          if (isAbortError(err)) return;
          const message =
            err instanceof Error ? err.message : "set listening mode failed";
          setError(message);
          setPendingMode(null);
        });
    },
    [api, onModeChanged, pendingMode],
  );

  return (
    <section data-testid="operator-listening-mode" style={CARD_STYLE}>
      <h3 style={SECTION_HEADING_STYLE}>Listening mode</h3>
      <p
        style={{
          fontSize: 11,
          color: "#6b7280",
          margin: "0 0 8px 0",
          lineHeight: 1.4,
        }}
      >
        Controls how aggressively the kiosk escalates to Claude for
        suggestions. OFFLINE never escalates; the mic still records
        transcripts. Use the mic mute toggle to stop recording.
      </p>
      <div
        data-testid="listening-mode-buttons"
        style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
      >
        {LISTENING_MODES.map(({ mode, label }) => {
          const active = displayedMode === mode;
          const pending = pendingMode === mode;
          return (
            <button
              key={mode}
              type="button"
              data-testid={`listening-mode-btn-${mode}`}
              data-active={active ? "true" : "false"}
              disabled={pendingMode !== null}
              onClick={() => handleClick(mode)}
              style={{
                fontSize: 11,
                padding: "4px 8px",
                borderRadius: 4,
                border: active ? "1px solid #2563eb" : "1px solid #d1d5db",
                background: active ? "#dbeafe" : "#fff",
                color: active ? "#1e3a8a" : "#374151",
                cursor: pendingMode !== null ? "default" : "pointer",
                fontWeight: active ? 600 : 400,
                opacity: pendingMode !== null && !pending ? 0.6 : 1,
              }}
            >
              {mode} {label}
            </button>
          );
        })}
      </div>
      {error !== null && (
        <div
          data-testid="listening-mode-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 11, marginTop: 6 }}
        >
          {error}
        </div>
      )}
    </section>
  );
}

interface MicMuteControlProps {
  api: Pick<ApiClient, "setMicEnabled">;
  enabled: boolean;
  onMicEnabledChanged: (enabled: boolean) => void;
}

function MicMuteControl(props: MicMuteControlProps): JSX.Element {
  const { api, enabled, onMicEnabledChanged } = props;
  const [pending, setPending] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const handleToggle = useCallback((): void => {
    if (pending) return;
    const next = !enabled;
    setPending(true);
    setError(null);
    api
      .setMicEnabled(next)
      .then((resp) => {
        onMicEnabledChanged(resp.enabled);
        setPending(false);
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        const message =
          err instanceof Error ? err.message : "set mic enabled failed";
        setError(message);
        setPending(false);
      });
  }, [api, enabled, onMicEnabledChanged, pending]);

  return (
    <section data-testid="operator-mic-mute" style={CARD_STYLE}>
      <h3 style={SECTION_HEADING_STYLE}>Mic</h3>
      <p
        style={{
          fontSize: 11,
          color: "#6b7280",
          margin: "0 0 8px 0",
          lineHeight: 1.4,
        }}
      >
        Hard mute switch. While muted, the kiosk drains audio but skips
        transcript persistence + ws emit. Independent of listening mode.
      </p>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button
          type="button"
          data-testid="operator-mic-mute-toggle"
          data-mic-enabled={enabled ? "true" : "false"}
          disabled={pending}
          onClick={handleToggle}
          style={{
            fontSize: 12,
            padding: "4px 10px",
            borderRadius: 4,
            border: enabled ? "1px solid #16a34a" : "1px solid #b91c1c",
            background: enabled ? "#dcfce7" : "#fee2e2",
            color: enabled ? "#14532d" : "#7f1d1d",
            fontWeight: 600,
            cursor: pending ? "default" : "pointer",
            opacity: pending ? 0.6 : 1,
          }}
        >
          {enabled ? "● listening" : "○ muted"}
        </button>
        <span
          data-testid="mic-mute-status"
          style={{ fontSize: 11, color: "#374151" }}
        >
          {enabled ? "click to mute" : "click to unmute"}
        </span>
      </div>
      {error !== null && (
        <div
          data-testid="mic-mute-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 11, marginTop: 6 }}
        >
          {error}
        </div>
      )}
    </section>
  );
}

interface ImageGenModeChoice {
  mode: ImageGenMode;
  label: string;
  description: string;
}

const IMAGE_GEN_MODES: readonly ImageGenModeChoice[] = [
  {
    mode: "cartoon",
    label: "Cartoon",
    description: "SD 1.5 stylized; uses GPU.",
  },
  {
    mode: "composite",
    label: "Composite (offline)",
    description: "Pillow templates + cutout; no GPU, fastest.",
  },
];

export interface ImageGenModeToggleProps {
  api: Pick<ApiClient, "getImageGenMode" | "setImageGenMode">;
}

export function ImageGenModeToggle(props: ImageGenModeToggleProps): JSX.Element {
  const { api } = props;
  const [mode, setMode] = useState<ImageGenMode | null>(null);
  // Tracks which button (if any) the operator just clicked so the
  // active-side label can render "Saving..." while the PUT is in
  // flight. ``null`` while idle.
  const [pendingMode, setPendingMode] = useState<ImageGenMode | null>(null);
  const [error, setError] = useState<string | null>(null);

  const busy = pendingMode !== null;

  // Initial GET: load the persisted mode on mount. AbortController
  // cancels the in-flight fetch on unmount so a remount doesn't fire
  // a stale setState.
  useEffect(() => {
    const controller = new AbortController();
    api
      .getImageGenMode({ signal: controller.signal })
      .then((resp) => {
        setMode(resp.mode);
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        const message =
          err instanceof Error ? err.message : "load image-gen mode failed";
        setError(message);
      });
    return () => {
      controller.abort();
    };
  }, [api]);

  const handleSelect = useCallback(
    (next: ImageGenMode): void => {
      if (busy) return;
      if (next === mode) return;
      setPendingMode(next);
      setError(null);
      api
        .setImageGenMode(next)
        .then((resp) => {
          setMode(resp.mode);
          setPendingMode(null);
        })
        .catch((err: unknown) => {
          if (isAbortError(err)) return;
          const message =
            err instanceof Error ? err.message : "set image-gen mode failed";
          setError(message);
          setPendingMode(null);
        });
    },
    [api, busy, mode],
  );

  return (
    <section data-testid="operator-image-gen-mode" style={CARD_STYLE}>
      <h3 style={SECTION_HEADING_STYLE}>Image-gen mode</h3>
      <p
        style={{
          fontSize: 11,
          color: "#6b7280",
          margin: "0 0 8px 0",
          lineHeight: 1.4,
        }}
      >
        Controls how toy action sprites are rendered. Cartoon uses the
        SD 1.5 stylized pipeline (GPU). Composite stitches Pillow
        templates and a rembg cutout — fastest, runs without a GPU.
      </p>
      <div
        data-testid="image-gen-mode-buttons"
        style={{ display: "flex", flexDirection: "column", gap: 4 }}
      >
        {IMAGE_GEN_MODES.map(({ mode: choice, label, description }) => {
          const active = mode === choice;
          const saving = pendingMode === choice;
          return (
            <button
              key={choice}
              type="button"
              data-testid={`image-gen-mode-btn-${choice}`}
              data-active={active ? "true" : "false"}
              disabled={busy}
              onClick={() => handleSelect(choice)}
              style={{
                fontSize: 12,
                padding: "6px 10px",
                borderRadius: 4,
                border: active ? "1px solid #2563eb" : "1px solid #d1d5db",
                background: active ? "#dbeafe" : "#fff",
                color: active ? "#1e3a8a" : "#374151",
                cursor: busy ? "default" : "pointer",
                fontWeight: active ? 600 : 400,
                opacity: busy && !saving && !active ? 0.6 : 1,
                textAlign: "left",
              }}
            >
              <div>{saving ? "Saving..." : label}</div>
              <div
                style={{
                  fontSize: 10,
                  color: "#6b7280",
                  fontWeight: 400,
                  marginTop: 2,
                }}
              >
                {description}
              </div>
            </button>
          );
        })}
      </div>
      {error !== null && (
        <div
          data-testid="image-gen-mode-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 11, marginTop: 6 }}
        >
          {error}
        </div>
      )}
    </section>
  );
}

export interface SettingsPanelProps {
  api: Pick<
    ApiClient,
    | "getMetrics"
    | "setListeningMode"
    | "setMicEnabled"
    | "getImageGenMode"
    | "setImageGenMode"
    | "getBannedThemesGlobal"
    | "setBannedThemesGlobal"
    | "setTranscriptRetention"
    | "setPlayTargetDepth"
    | "setPlayStandaloneEnabled"
    | "setClickableWordsEnabled"
    | "setReadMeButtonEnabled"
    | "setSpokenTextLimit"
    | "setParentInvolvement"
    | "setGameComplexity"
    | "setGameLinearity"
    | "setQaGrading"
    | "setBossFightsEnabled"
  >;
  // Phase I step I3: transcript retention picker source-of-truth. The
  // value lives in App.tsx (fetched once on mount via the
  // settings/transcript-retention GET) and is threaded through here so
  // the same state object also flows into TranscriptsManager (I4
  // consumes it for the fade animation). ``onRetentionChanged`` bubbles
  // a successful PUT response back up so App.tsx can update its state.
  currentRetentionSeconds: number;
  onRetentionChanged: (seconds: number) => void;
  // Phase J step J10: play-queue target depth picker. Value lives in
  // App.tsx (seeded by the bootstrap parallel-fetch) and threads
  // through here. The callback bubbles a successful PUT response back
  // up so the lifted state stays the source of truth.
  currentPlayTargetDepth: number;
  onPlayTargetDepthChanged: (value: PlayTargetDepth) => void;
  // Phase K step K2: feature flags lifted to App.tsx so the bootstrap
  // parallel-fetch can seed them once and SettingsPanel can bubble
  // each PUT response back up. The callback is a single (key, value)
  // pair so adding a flag is a single-line edit upstream.
  currentFeatureFlags: PhaseKFeatureFlags;
  onFeatureFlagChanged: (key: PhaseKFeatureFlag, value: boolean) => void;
  // Phase R Step R2: spoken text character limit. Value lives in
  // App.tsx (seeded by the bootstrap parallel-fetch) and threads
  // through here. The callback bubbles a successful PUT response back
  // up so the lifted state stays the source of truth.
  currentSpokenTextLimit: number;
  onSpokenTextLimitChanged: (value: SpokenTextLimit) => void;
  // Phase W Step W1: two household-scoped true-stub dials (parent
  // involvement + game complexity). Values live in App.tsx (seeded by
  // the bootstrap parallel-fetch) and thread through here; the callbacks
  // bubble a successful PUT response back up so the lifted state stays
  // the source of truth. PERSIST ONLY — wired to no behavior yet.
  currentParentInvolvement: string;
  onParentInvolvementChanged: (value: ParentInvolvement) => void;
  currentGameComplexity: string;
  onGameComplexityChanged: (value: GameComplexity) => void;
  // Phase W Step W2: household game-linearity dial. WIRED — the propose
  // path excludes branching templates when set to "linear". Value lives
  // in App.tsx (seeded by the bootstrap parallel-fetch) and threads
  // through here; the callback bubbles a successful PUT response back up.
  currentGameLinearity: string;
  onGameLinearityChanged: (value: GameLinearity) => void;
  // Phase W Step W3: household Q&A answer-grading dial. WIRED — the advance
  // path auto-grades a Q&A step's answer against the recent transcript
  // window when set to "lenient" / "strict". Value lives in App.tsx
  // (seeded by the bootstrap parallel-fetch) and threads through here; the
  // callback bubbles a successful PUT response back up.
  currentQaGrading: string;
  onQaGradingChanged: (value: QaGrading) => void;
  // Phase W Step W5: household boss-fights flag. WIRED — the adventure
  // engine emits a distinct boss_fight climax beat when this is on. Value
  // lives in App.tsx (seeded by the bootstrap parallel-fetch) and threads
  // through here; the callback bubbles a successful PUT response back up so
  // the lifted state stays the source of truth.
  currentBossFightsEnabled: boolean;
  onBossFightsEnabledChanged: (value: boolean) => void;
}

// Settings sub-tab. Renders the three toggle cards + the global
// banned-themes editor. Performs one ``GET /api/metrics`` on mount to
// seed the listening-mode + mic-mute display values; from then on the
// optimistic ``onModeChanged`` / ``onMicEnabledChanged`` callbacks own
// the state. No ws fanout — the toggles are write-on-click.
export function SettingsPanel(props: SettingsPanelProps): JSX.Element {
  const {
    api,
    currentRetentionSeconds,
    onRetentionChanged,
    currentPlayTargetDepth,
    onPlayTargetDepthChanged,
    currentFeatureFlags,
    onFeatureFlagChanged,
    currentSpokenTextLimit,
    onSpokenTextLimitChanged,
    currentParentInvolvement,
    onParentInvolvementChanged,
    currentGameComplexity,
    onGameComplexityChanged,
    currentGameLinearity,
    onGameLinearityChanged,
    currentQaGrading,
    onQaGradingChanged,
    currentBossFightsEnabled,
    onBossFightsEnabledChanged,
  } = props;
  const [listeningMode, setListeningMode] = useState<number>(3);
  const [micEnabled, setMicEnabled] = useState<boolean>(true);
  const [seedError, setSeedError] = useState<string | null>(null);

  // Seed the toggle display values from the metrics snapshot once on
  // mount. Failure is non-fatal — the toggles fall back to their
  // hardcoded defaults (listening_mode=3 = DEFAULT, mic_enabled=true)
  // and a click still writes correctly via the PUT endpoints.
  useEffect(() => {
    const controller = new AbortController();
    api
      .getMetrics({ signal: controller.signal })
      .then((snap: MetricsSnapshot) => {
        setListeningMode(snap.ai.listening_mode);
        setMicEnabled(snap.audio.mic_enabled);
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        const message =
          err instanceof Error ? err.message : "metrics seed failed";
        setSeedError(message);
      });
    return () => {
      controller.abort();
    };
  }, [api]);

  const handleListeningModeChanged = useCallback(
    (mode: ListeningMode): void => {
      setListeningMode(mode);
    },
    [],
  );

  const handleMicEnabledChanged = useCallback((enabled: boolean): void => {
    setMicEnabled(enabled);
  }, []);

  return (
    <section
      data-testid="settings-panel"
      style={{ padding: 12, fontSize: 12 }}
    >
      <h2 style={{ fontSize: 16, margin: "0 0 4px 0" }}>Settings</h2>
      {seedError !== null && (
        <div
          data-testid="settings-panel-seed-error"
          role="alert"
          style={{ color: "#a00", fontSize: 11, marginBottom: 6 }}
        >
          {seedError}
        </div>
      )}
      <div style={GRID_STYLE}>
        <ListeningModeControl
          api={api}
          currentMode={listeningMode}
          onModeChanged={handleListeningModeChanged}
        />
        <MicMuteControl
          api={api}
          enabled={micEnabled}
          onMicEnabledChanged={handleMicEnabledChanged}
        />
        <ImageGenModeToggle api={api} />
        <TranscriptRetentionControl
          api={api}
          currentSeconds={currentRetentionSeconds}
          onSecondsChanged={onRetentionChanged}
        />
        <PlayTargetDepthControl
          api={api}
          currentValue={currentPlayTargetDepth}
          onValueChanged={onPlayTargetDepthChanged}
        />
        <SpokenTextLimitControl
          api={api}
          currentValue={currentSpokenTextLimit}
          onValueChanged={onSpokenTextLimitChanged}
        />
        <ParentInvolvementControl
          api={api}
          currentValue={currentParentInvolvement}
          onValueChanged={onParentInvolvementChanged}
        />
        <GameComplexityControl
          api={api}
          currentValue={currentGameComplexity}
          onValueChanged={onGameComplexityChanged}
        />
        <GameLinearityControl
          api={api}
          currentValue={currentGameLinearity}
          onValueChanged={onGameLinearityChanged}
        />
        <QaGradingControl
          api={api}
          currentValue={currentQaGrading}
          onValueChanged={onQaGradingChanged}
        />
        <BossFightsControl
          api={api}
          currentValue={currentBossFightsEnabled}
          onValueChanged={onBossFightsEnabledChanged}
        />
      </div>
      <div style={{ marginTop: 12 }}>
        <PlayFeaturesControls
          api={api}
          values={currentFeatureFlags}
          onValueChanged={onFeatureFlagChanged}
        />
      </div>
      <div style={{ marginTop: 12 }}>
        <BannedThemesSettings api={api} />
      </div>
    </section>
  );
}
