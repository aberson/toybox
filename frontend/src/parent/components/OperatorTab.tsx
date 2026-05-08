// Step 24: Operator dashboard tab. Renders a structured view of the
// /api/metrics snapshot. Auto-refresh strategy:
// 1. On mount, GET /api/metrics for the first paint.
// 2. While mounted, subscribe to ``metrics`` ws envelopes via the
//    onEnvelope prop — each envelope replaces the snapshot in state.
// 3. If the ws is unavailable (no envelopes within ``stalenessThresholdMs``),
//    fall back to a REST poll every ``pollIntervalMs``.
// AbortController cancels any in-flight fetch on unmount.
//
// Layout: a CSS grid of cards so the dashboard fits on roughly half a
// page on a typical desktop screen. Each section is a card; the grid
// auto-fits as many columns as the viewport allows.

import type { CSSProperties, JSX } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { isAbortError } from "../api";
import type {
  ApiClient,
  ListeningMode,
  MetricsSnapshot,
} from "../api";
import type { Envelope } from "../ws";

export interface OperatorTabProps {
  api: Pick<ApiClient, "getMetrics" | "setListeningMode" | "setMicEnabled">;
  // Caller wires this from the ws layer: register a listener for
  // ``metrics`` envelopes and return an unsubscribe function. When the
  // ws path is unavailable (e.g. tests, kiosk-only build), pass a
  // function that returns a no-op unsubscribe; the REST poll below
  // takes over.
  subscribeToMetrics?: (
    handler: (envelope: Envelope) => void,
  ) => () => void;
  // Defaults are tuned for v1: snapshot every 30s on the wire, REST
  // poll every 30s if the ws stalls. Tests pass shorter values.
  pollIntervalMs?: number;
  stalenessThresholdMs?: number;
}

const DEFAULT_POLL_INTERVAL_MS = 30_000;
const DEFAULT_STALENESS_THRESHOLD_MS = 60_000;

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

// Scoped CSS for compact tables — applied via a `<style>` block keyed
// off the ``operator-tab`` data-testid so the rules don't leak into the
// rest of the parent UI.
const SCOPED_CSS = `
[data-testid="operator-tab"] table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}
[data-testid="operator-tab"] td {
  padding: 2px 6px;
  vertical-align: top;
}
[data-testid="operator-tab"] td:first-child {
  color: #6b7280;
  white-space: nowrap;
}
[data-testid="operator-tab"] td:nth-child(2) {
  font-variant-numeric: tabular-nums;
  word-break: break-word;
}
[data-testid="operator-tab"] h2 {
  font-size: 16px;
  margin: 0 0 4px 0;
}
`;

function formatTimestamp(iso: string | null): string {
  if (iso === null) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(2);
}

function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function isListeningMode(value: number): value is ListeningMode {
  return value === 1 || value === 2 || value === 3 || value === 4 || value === 5;
}

interface ListeningModeControlProps {
  api: Pick<ApiClient, "setListeningMode">;
  currentMode: number;
  onModeChanged: (mode: ListeningMode) => void;
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
          data-testid="mic-mute-toggle"
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

export function OperatorTab(props: OperatorTabProps): JSX.Element {
  const {
    api,
    subscribeToMetrics,
    pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
    stalenessThresholdMs = DEFAULT_STALENESS_THRESHOLD_MS,
  } = props;

  const [snapshot, setSnapshot] = useState<MetricsSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);
  const [source, setSource] = useState<"ws" | "rest" | null>(null);

  // Refs so the polling effect can read fresh values without re-running
  // on every snapshot update.
  const lastUpdateRef = useRef<number | null>(null);
  lastUpdateRef.current = lastUpdate;

  const fetchOnce = useCallback(
    async (signal: AbortSignal): Promise<void> => {
      try {
        const result = await api.getMetrics({ signal });
        setSnapshot(result);
        setLastUpdate(Date.now());
        setSource("rest");
        setError(null);
      } catch (err) {
        if (isAbortError(err)) return;
        const message = err instanceof Error ? err.message : "metrics fetch failed";
        setError(message);
      }
    },
    [api],
  );

  // Initial fetch + REST poll. The poll only fires the next request when
  // the ws hasn't recently delivered a snapshot.
  useEffect(() => {
    const controller = new AbortController();
    void fetchOnce(controller.signal);

    const intervalId = window.setInterval(() => {
      const last = lastUpdateRef.current;
      const now = Date.now();
      if (last !== null && now - last < stalenessThresholdMs) {
        // Fresh ws snapshot is still in hand — skip this poll tick.
        return;
      }
      void fetchOnce(controller.signal);
    }, pollIntervalMs);

    return () => {
      controller.abort();
      window.clearInterval(intervalId);
    };
  }, [fetchOnce, pollIntervalMs, stalenessThresholdMs]);

  // Subscribe to ws metrics envelopes when a subscriber is wired.
  useEffect(() => {
    if (subscribeToMetrics === undefined) return;
    const unsubscribe = subscribeToMetrics((envelope) => {
      if (envelope.topic !== "metrics") return;
      // Trust the wire shape — the backend builds it from the same
      // dataclasses MetricsSnapshot mirrors. A defensive cast suffices
      // here; callers that need stricter validation can layer a guard
      // around subscribeToMetrics.
      setSnapshot(envelope.payload as unknown as MetricsSnapshot);
      setLastUpdate(Date.now());
      setSource("ws");
      setError(null);
    });
    return unsubscribe;
  }, [subscribeToMetrics]);

  // Patch the local snapshot's listening mode after a successful PUT
  // so the dashboard doesn't appear stuck on the old value while we
  // wait for the next metrics envelope (~30s window).
  const handleListeningModeChanged = useCallback((mode: ListeningMode): void => {
    setSnapshot((s) =>
      s !== null ? { ...s, ai: { ...s.ai, listening_mode: mode } } : s,
    );
  }, []);

  const handleMicEnabledChanged = useCallback((enabled: boolean): void => {
    setSnapshot((s) =>
      s !== null ? { ...s, audio: { ...s.audio, mic_enabled: enabled } } : s,
    );
  }, []);

  const listeningCard = useMemo(
    () => (
      <ListeningModeControl
        api={api}
        currentMode={snapshot?.ai.listening_mode ?? 3}
        onModeChanged={handleListeningModeChanged}
      />
    ),
    [api, handleListeningModeChanged, snapshot?.ai.listening_mode],
  );

  const micMuteCard = useMemo(
    () => (
      <MicMuteControl
        api={api}
        enabled={snapshot?.audio.mic_enabled ?? true}
        onMicEnabledChanged={handleMicEnabledChanged}
      />
    ),
    [api, handleMicEnabledChanged, snapshot?.audio.mic_enabled],
  );

  if (snapshot === null) {
    return (
      <section data-testid="operator-tab" style={{ padding: 12, fontSize: 12 }}>
        <style>{SCOPED_CSS}</style>
        <h2>Operator</h2>
        {error !== null ? (
          <div data-testid="operator-error" role="alert" style={{ color: "#a00" }}>
            {error}
          </div>
        ) : (
          <div data-testid="operator-loading">loading metrics…</div>
        )}
      </section>
    );
  }

  return (
    <section data-testid="operator-tab" style={{ padding: 12, fontSize: 12 }}>
      <style>{SCOPED_CSS}</style>
      <h2>Operator</h2>
      <div
        data-testid="operator-last-update"
        style={{ fontSize: 11, color: "#6b7280", marginBottom: 4 }}
      >
        last update: {formatTimestamp(snapshot.generated_at)}
        {source !== null ? ` (via ${source})` : ""}
      </div>
      {error !== null && (
        <div data-testid="operator-error" role="alert" style={{ color: "#a00" }}>
          {error}
        </div>
      )}

      <div style={GRID_STYLE}>
        <section style={CARD_STYLE}>
          <h3 style={SECTION_HEADING_STYLE}>Activities</h3>
          <table data-testid="operator-activities">
            <tbody>
              <tr>
                <td>proposed</td>
                <td data-testid="m-activities-proposed">
                  {snapshot.activities.proposed_current}
                </td>
              </tr>
              <tr>
                <td>approved</td>
                <td data-testid="m-activities-approved">
                  {snapshot.activities.approved_current}
                </td>
              </tr>
              <tr>
                <td>running</td>
                <td data-testid="m-activities-running">
                  {snapshot.activities.running_current}
                </td>
              </tr>
              <tr>
                <td>completed</td>
                <td data-testid="m-activities-completed">
                  {snapshot.activities.completed_current}
                </td>
              </tr>
              <tr>
                <td>ended</td>
                <td data-testid="m-activities-ended">
                  {snapshot.activities.ended_current}
                </td>
              </tr>
              <tr>
                <td>dismissed</td>
                <td data-testid="m-activities-dismissed">
                  {snapshot.activities.dismissed_current}
                </td>
              </tr>
              <tr>
                <td>didn't work</td>
                <td data-testid="m-activities-didnt-work">
                  {snapshot.activities.didnt_work_current}
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        {listeningCard}

        {micMuteCard}

        <section style={CARD_STYLE}>
          <h3 style={SECTION_HEADING_STYLE}>Transcripts</h3>
          <table data-testid="operator-transcripts">
            <tbody>
              <tr>
                <td>total</td>
                <td data-testid="m-transcripts-total">{snapshot.transcripts.total}</td>
              </tr>
              <tr>
                <td>last 24h</td>
                <td data-testid="m-transcripts-24h">
                  {snapshot.transcripts.last_24h}
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section style={CARD_STYLE}>
          <h3 style={SECTION_HEADING_STYLE}>Audio</h3>
          <table data-testid="operator-audio">
            <tbody>
              <tr>
                <td>mic device</td>
                <td data-testid="m-audio-device">
                  {snapshot.audio.mic_device ?? "—"}
                </td>
              </tr>
              <tr>
                <td>queue depth</td>
                <td data-testid="m-audio-queue">{snapshot.audio.queue_depth}</td>
              </tr>
              <tr>
                <td>buffer overruns</td>
                <td data-testid="m-audio-overruns">
                  {snapshot.audio.buffer_overruns_total}
                </td>
              </tr>
              <tr>
                <td>mic enabled</td>
                <td data-testid="m-audio-mic-enabled">
                  {snapshot.audio.mic_enabled ? "yes" : "no"}
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section style={CARD_STYLE}>
          <h3 style={SECTION_HEADING_STYLE}>AI</h3>
          <table data-testid="operator-ai">
            <tbody>
              <tr>
                <td>breaker</td>
                <td data-testid="m-ai-breaker">{snapshot.ai.breaker_state}</td>
              </tr>
              <tr>
                <td>retry-after</td>
                <td data-testid="m-ai-retry-after">
                  {snapshot.ai.breaker_retry_after_iso ?? "—"}
                </td>
              </tr>
              <tr>
                <td>claude capable</td>
                <td data-testid="m-ai-capable">
                  {snapshot.ai.claude_capable ? "yes" : "no"}
                </td>
              </tr>
              <tr>
                <td>capability reason</td>
                <td data-testid="m-ai-capability-reason">
                  {snapshot.ai.claude_capability_reason ?? "—"}
                </td>
              </tr>
              <tr>
                <td>listening mode</td>
                <td data-testid="m-ai-listening-mode">
                  {snapshot.ai.listening_mode}
                </td>
              </tr>
              <tr>
                <td>throttle (s)</td>
                <td data-testid="m-ai-throttle">
                  {snapshot.ai.min_interval_throttle_seconds.toFixed(1)}
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section style={CARD_STYLE}>
          <h3 style={SECTION_HEADING_STYLE}>Activity quality (24h)</h3>
          <table data-testid="operator-activity-quality">
            <tbody>
              {Object.entries(snapshot.activity_quality.last_24h_mean_scores).map(
                ([key, value]) => (
                  <tr key={key}>
                    <td>{key}</td>
                    <td data-testid={`m-quality-${key.replace(/_/g, "-")}`}>
                      {formatScore(value)}
                    </td>
                  </tr>
                ),
              )}
              <tr>
                <td>overlap rows</td>
                <td data-testid="m-quality-overlap">
                  {snapshot.activity_quality.judge_parent_agreement.overlap_count}
                </td>
              </tr>
              <tr>
                <td>
                  agreement (
                  {snapshot.activity_quality.judge_parent_agreement.metric_name})
                </td>
                <td data-testid="m-quality-agreement">
                  {formatRate(
                    snapshot.activity_quality.judge_parent_agreement.agreement_rate,
                  )}
                </td>
              </tr>
              <tr>
                <td>safety auto-fails</td>
                <td data-testid="m-quality-safety-fails">
                  {snapshot.activity_quality.safety_autofails_last_24h}
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section style={CARD_STYLE}>
          <h3 style={SECTION_HEADING_STYLE}>Eval gate</h3>
          <table data-testid="operator-eval-gate">
            <tbody>
              <tr>
                <td>last run</td>
                <td data-testid="m-eval-last-run">
                  {formatTimestamp(snapshot.eval_gate.last_run_at)}
                </td>
              </tr>
              <tr>
                <td>placeholder baseline</td>
                <td data-testid="m-eval-placeholder">
                  {snapshot.eval_gate.placeholder_baseline ? "yes" : "no"}
                </td>
              </tr>
              <tr>
                <td>regressions</td>
                <td data-testid="m-eval-regressions">
                  {snapshot.eval_gate.regressions_detected}
                </td>
              </tr>
            </tbody>
          </table>
          {snapshot.eval_gate.mean_dimension_scores !== null && (
            <table data-testid="operator-eval-baseline-scores" style={{ marginTop: 4 }}>
              <tbody>
                {Object.entries(snapshot.eval_gate.mean_dimension_scores).map(
                  ([key, value]) => (
                    <tr key={key}>
                      <td>baseline {key}</td>
                      <td data-testid={`m-eval-baseline-${key.replace(/_/g, "-")}`}>
                        {formatScore(value)}
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          )}
        </section>
      </div>

      <div
        data-testid="operator-ws-subscribers"
        style={{ marginTop: 8, fontSize: 11, color: "#6b7280" }}
      >
        ws subscribers: {snapshot.ws_subscribers}
      </div>
    </section>
  );
}
