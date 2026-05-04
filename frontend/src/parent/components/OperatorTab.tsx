// Step 24: Operator dashboard tab. Renders a structured view of the
// /api/metrics snapshot. Auto-refresh strategy:
// 1. On mount, GET /api/metrics for the first paint.
// 2. While mounted, subscribe to ``metrics`` ws envelopes via the
//    onEnvelope prop — each envelope replaces the snapshot in state.
// 3. If the ws is unavailable (no envelopes within ``stalenessThresholdMs``),
//    fall back to a REST poll every ``pollIntervalMs``.
// AbortController cancels any in-flight fetch on unmount.

import type { JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { isAbortError } from "../api";
import type { ApiClient, MetricsSnapshot } from "../api";
import type { Envelope } from "../ws";

export interface OperatorTabProps {
  api: Pick<ApiClient, "getMetrics">;
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

  if (snapshot === null) {
    return (
      <section data-testid="operator-tab" style={{ padding: 16 }}>
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
    <section data-testid="operator-tab" style={{ padding: 16 }}>
      <h2>Operator</h2>
      <div
        data-testid="operator-last-update"
        style={{ fontSize: 12, color: "#666", marginBottom: 12 }}
      >
        last update: {formatTimestamp(snapshot.generated_at)}
        {source !== null ? ` (via ${source})` : ""}
      </div>
      {error !== null && (
        <div data-testid="operator-error" role="alert" style={{ color: "#a00" }}>
          {error}
        </div>
      )}

      <h3>Activities</h3>
      <table data-testid="operator-activities">
        <tbody>
          <tr>
            <td>proposed (current)</td>
            <td data-testid="m-activities-proposed">
              {snapshot.activities.proposed_current}
            </td>
          </tr>
          <tr>
            <td>approved (current)</td>
            <td data-testid="m-activities-approved">
              {snapshot.activities.approved_current}
            </td>
          </tr>
          <tr>
            <td>running (current)</td>
            <td data-testid="m-activities-running">
              {snapshot.activities.running_current}
            </td>
          </tr>
          <tr>
            <td>completed (current)</td>
            <td data-testid="m-activities-completed">
              {snapshot.activities.completed_current}
            </td>
          </tr>
          <tr>
            <td>ended (current)</td>
            <td data-testid="m-activities-ended">
              {snapshot.activities.ended_current}
            </td>
          </tr>
          <tr>
            <td>dismissed (current)</td>
            <td data-testid="m-activities-dismissed">
              {snapshot.activities.dismissed_current}
            </td>
          </tr>
          <tr>
            <td>didn't work (current)</td>
            <td data-testid="m-activities-didnt-work">
              {snapshot.activities.didnt_work_current}
            </td>
          </tr>
        </tbody>
      </table>

      <h3>Transcripts</h3>
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

      <h3>Audio</h3>
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
        </tbody>
      </table>

      <h3>AI</h3>
      <table data-testid="operator-ai">
        <tbody>
          <tr>
            <td>breaker</td>
            <td data-testid="m-ai-breaker">{snapshot.ai.breaker_state}</td>
          </tr>
          <tr>
            <td>breaker retry-after</td>
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
            <td>min interval throttle (s)</td>
            <td data-testid="m-ai-throttle">
              {snapshot.ai.min_interval_throttle_seconds.toFixed(1)}
            </td>
          </tr>
        </tbody>
      </table>

      <h3>Activity quality (24h)</h3>
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
            <td>judge-parent overlap rows</td>
            <td data-testid="m-quality-overlap">
              {snapshot.activity_quality.judge_parent_agreement.overlap_count}
            </td>
          </tr>
          <tr>
            <td>
              judge-parent agreement (
              {snapshot.activity_quality.judge_parent_agreement.metric_name})
            </td>
            <td data-testid="m-quality-agreement">
              {formatRate(
                snapshot.activity_quality.judge_parent_agreement.agreement_rate,
              )}
            </td>
          </tr>
          <tr>
            <td>safety auto-fails (24h)</td>
            <td data-testid="m-quality-safety-fails">
              {snapshot.activity_quality.safety_autofails_last_24h}
            </td>
          </tr>
        </tbody>
      </table>

      <h3>Eval gate</h3>
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
            <td>regressions detected</td>
            <td data-testid="m-eval-regressions">
              {snapshot.eval_gate.regressions_detected}
            </td>
          </tr>
        </tbody>
      </table>
      {snapshot.eval_gate.mean_dimension_scores !== null && (
        <table data-testid="operator-eval-baseline-scores">
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

      <div
        data-testid="operator-ws-subscribers"
        style={{ marginTop: 12, fontSize: 12, color: "#666" }}
      >
        ws subscribers: {snapshot.ws_subscribers}
      </div>
    </section>
  );
}
