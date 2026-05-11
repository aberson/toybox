import type { CSSProperties, JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  extractPinInvalidDetail,
  extractPinLockedDetail,
  isAbortError,
} from "../api";
import type { ApiClient, TranscriptRow } from "../api";
import type { Envelope } from "../ws";

// Step 22: parent-facing transcript management UI.
//
// Surfaces:
// * Cursor-paginated list (most recent first) with "Load more" at the
//   bottom when the previous page returned a full window — mirrors the
//   ``ended_at`` cursor the backend exposes (no opaque next-page token).
// * Debounced search field (250ms). Switches to ``searchTranscripts``
//   when non-empty; falls back to the paginated list when cleared.
// * Local-timer fade-out (Phase I step I4): a 1s ``setInterval``
//   iterates the visible items, computes ``expires_at = ended_at +
//   retentionSeconds`` per row, marks expired rows as fading via inline
//   styles (opacity + max-height collapse over 600ms), and removes them
//   from local state when the transition finishes. Per-row delete is
//   gone — wipe-all is the only operator-initiated removal path.
// * "Wipe all" modal that re-prompts for the parent PIN. The PIN is
//   never persisted — it lives in modal state and clears on close.
//
// Lessons threaded through:
// * AbortController constructed inside the mount effect (NOT a shared
//   ref) so React 18 StrictMode's double-mount cycle gets a fresh,
//   un-aborted controller per mount. The mutation paths read the live
//   controller via ``aborterRef.current`` inside the callback — same
//   pattern as ``PinLogin`` / ``PinSetup``.
// * PIN input is digits-only on the way in (``digitsOnly``) so a stray
//   letter disappears mid-type rather than waiting for a 422.
// * Debounce uses ``setTimeout`` inside a ``useEffect``; no library
//   dependency.

const PIN_MIN = 4;
const PIN_MAX = 12;
const DEFAULT_PAGE_SIZE = 50;
const TEXT_PREVIEW_CHARS = 100;
const SEARCH_DEBOUNCE_MS = 250;
// Phase I step I4: shared inline ``transition`` string for the fade-out
// animation. Hoisted to keep the inline style sites within the project
// column norm and to keep both sites in lockstep (any tuning of the
// 600ms easing applies everywhere).
const FADE_TRANSITION =
  "opacity 600ms ease, max-height 600ms ease, margin 600ms ease, padding 600ms ease";

function digitsOnly(s: string): string {
  return s.replace(/\D+/g, "").slice(0, PIN_MAX);
}

function formatCountdown(totalSeconds: number): string {
  const safe = Math.max(0, Math.ceil(totalSeconds));
  const m = Math.floor(safe / 60);
  const s = safe % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function truncateText(text: string | null): string {
  if (text === null || text === "") return "(no text)";
  if (text.length <= TEXT_PREVIEW_CHARS) return text;
  return text.slice(0, TEXT_PREVIEW_CHARS) + "…";
}

function confidenceBadge(confidence: number | null): string {
  if (confidence === null) return "?";
  // Two decimals matches the resolution we surface elsewhere; clamp at
  // 1.0 so a stale row that overflowed still renders sanely.
  const safe = Math.max(0, Math.min(1, confidence));
  return safe.toFixed(2);
}

export interface TranscriptsManagerProps {
  api: ApiClient;
  // Optional ws subscriber for live ``transcript`` envelopes. When
  // wired (production: App.tsx fans envelopes through here), new rows
  // prepend into the visible list as the kiosk produces them. Tests
  // omit this and continue to drive the list via the api stub.
  subscribeToTranscripts?: (
    handler: (envelope: Envelope) => void,
  ) => () => void;
  // Phase I step I3: retention preset (seconds) threaded from App.tsx
  // via SettingsPanel's source-of-truth. I3 only declares the prop —
  // the consumption logic (1s tick + fade animation + setItems
  // removal) lands in I4 in the function body. Declared here in I3 so
  // App.tsx's prop pass typechecks; I4 then adds the body changes
  // without re-touching the interface block.
  retentionSeconds: number;
}

export function TranscriptsManager(
  props: TranscriptsManagerProps,
): JSX.Element {
  const { api, subscribeToTranscripts, retentionSeconds } = props;
  const [items, setItems] = useState<TranscriptRow[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [listError, setListError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState<string>("");
  const [activeQuery, setActiveQuery] = useState<string>("");
  const [hasMore, setHasMore] = useState<boolean>(false);
  const [loadingMore, setLoadingMore] = useState<boolean>(false);
  // Phase I step I4 fade machinery. ``fadingIds`` flips a row's inline
  // style to the opacity/max-height collapse on the next render; a
  // queued ``setTimeout`` removes the row from ``items`` 600ms later
  // when the CSS transition finishes. ``warnedIdsRef`` deduplicates
  // ``console.warn`` output for rows with malformed ``ended_at`` so a
  // corrupt row doesn't spam the console once per tick.
  const [fadingIds, setFadingIds] = useState<Set<string>>(
    () => new Set<string>(),
  );
  // Mirror of ``fadingIds`` for read access inside the tick. The tick's
  // ``useEffect`` deliberately does NOT depend on ``fadingIds`` (so the
  // setInterval cadence doesn't reset every fade); reading state from
  // the closure would freeze on the first render's empty set. The ref
  // is written from the ``setFadingIds`` updater so it stays in lockstep
  // with state without re-running the effect.
  const fadingIdsRef = useRef<Set<string>>(new Set<string>());
  const warnedIdsRef = useRef<Set<string>>(new Set<string>());
  // Handles for the 600ms removal ``setTimeout``s queued by the tick.
  // Tracked so the effect cleanup can ``clearTimeout`` them on unmount
  // (and on React 18 StrictMode dev double-mount). Each callback also
  // removes its own handle before mutating state so the set doesn't
  // leak across the manager's lifetime.
  const removalTimeoutsRef = useRef<Set<number>>(new Set<number>());

  const [wipeOpen, setWipeOpen] = useState<boolean>(false);
  const [wipePin, setWipePin] = useState<string>("");
  const [wipeSubmitting, setWipeSubmitting] = useState<boolean>(false);
  const [wipeError, setWipeError] = useState<string | null>(null);
  const [wipeLockSeconds, setWipeLockSeconds] = useState<number>(0);
  const [wipeSuccessCount, setWipeSuccessCount] = useState<number | null>(null);

  // AbortController spanning the manager's lifetime — fresh per mount,
  // aborted on unmount. Mutation paths read ``aborterRef.current``
  // synchronously when they fire so a late-mounted callback still
  // joins the live signal.
  const aborterRef = useRef<AbortController | null>(null);
  // Per-search aborter so two rapid debounced fetches don't race —
  // the older one is aborted as soon as the next one starts, otherwise
  // a late-arriving older response could overwrite a newer one.
  const searchAborterRef = useRef<AbortController | null>(null);
  useEffect(() => {
    const aborter = new AbortController();
    aborterRef.current = aborter;
    return () => {
      aborter.abort();
      if (aborterRef.current === aborter) {
        aborterRef.current = null;
      }
      // Also abort any in-flight search; the per-search controller
      // is independent of the lifetime one.
      searchAborterRef.current?.abort();
      searchAborterRef.current = null;
    };
  }, []);

  const refetchList = useCallback(
    async (query: string): Promise<void> => {
      // Cancel any prior in-flight list/search; only the most recent
      // call should ever paint into state.
      searchAborterRef.current?.abort();
      const aborter = new AbortController();
      searchAborterRef.current = aborter;
      setLoading(true);
      setListError(null);
      try {
        const response =
          query === ""
            ? await api.listTranscripts(
                { limit: DEFAULT_PAGE_SIZE },
                { signal: aborter.signal },
              )
            : await api.searchTranscripts(
                query,
                { limit: DEFAULT_PAGE_SIZE },
                { signal: aborter.signal },
              );
        setItems(response.items);
        // Search results don't paginate in v1 (the spec returns up to
        // ``limit`` matches without a cursor); only show "Load more"
        // for the unfiltered list and only when the page is full.
        setHasMore(query === "" && response.items.length >= DEFAULT_PAGE_SIZE);
      } catch (err) {
        if (isAbortError(err)) return;
        const message = err instanceof Error ? err.message : "load failed";
        setListError(message);
      } finally {
        // Only clear loading if this call is still the latest. A newer
        // call already flipped loading=true and we shouldn't undo it.
        if (searchAborterRef.current === aborter) {
          setLoading(false);
        }
      }
    },
    [api],
  );

  // Initial mount load — empty query -> paginated list.
  useEffect(() => {
    void refetchList("");
    // refetchList is stable (api is stable); we deliberately run only
    // on mount so the debounce effect drives subsequent reloads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounce: any change to ``searchInput`` schedules a 250ms refetch.
  // Cancelled by either the next change or unmount.
  useEffect(() => {
    const trimmed = searchInput.trim();
    // Skip the very-first run (initial mount already loads above) by
    // checking whether the active query already matches.
    if (trimmed === activeQuery) return;
    const handle = window.setTimeout(() => {
      setActiveQuery(trimmed);
      void refetchList(trimmed);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [searchInput, activeQuery, refetchList]);

  // Live ``transcript`` envelope subscription. New rows prepend into
  // the list as the kiosk produces them. Two guards:
  // * Skip live-prepend when the operator is searching (activeQuery
  //   non-empty) — the search results stay frozen until the search
  //   clears, otherwise an unrelated new transcript would pollute
  //   the result set without warning.
  // * Dedupe by id — defensive against the rare case where a ws
  //   envelope races a refetch.
  useEffect(() => {
    if (subscribeToTranscripts === undefined) return;
    const unsubscribe = subscribeToTranscripts((envelope) => {
      if (activeQuery !== "") return;
      const payload = envelope.payload as {
        id?: unknown;
        text?: unknown;
        confidence?: unknown;
        started_at?: unknown;
        ended_at?: unknown;
        language?: unknown;
      };
      if (typeof payload.id !== "string") return;
      // The ws envelope shape (pipeline.py:_handle_chunk) is a subset
      // of the REST TranscriptRow — session_id / mic_id /
      // triggered_intent aren't pushed live. Fill them with defaults
      // here so the prepended row satisfies the same TypeScript
      // contract the REST list does. Operator UI doesn't render
      // these fields, but the type system needs them.
      const row: TranscriptRow = {
        id: payload.id,
        session_id: "",
        mic_id: null,
        text: typeof payload.text === "string" ? payload.text : null,
        confidence:
          typeof payload.confidence === "number" ? payload.confidence : null,
        started_at:
          typeof payload.started_at === "string" ? payload.started_at : null,
        ended_at:
          typeof payload.ended_at === "string" ? payload.ended_at : null,
        language:
          typeof payload.language === "string" ? payload.language : "unknown",
        triggered_intent: null,
      };
      setItems((prev) => {
        if (prev.some((r) => r.id === row.id)) return prev;
        return [row, ...prev];
      });
    });
    return unsubscribe;
  }, [subscribeToTranscripts, activeQuery]);

  const loadMore = useCallback(async (): Promise<void> => {
    if (items.length === 0 || loadingMore) return;
    const aborter = aborterRef.current ?? new AbortController();
    const oldest = items[items.length - 1];
    if (oldest === undefined || oldest.ended_at === null) {
      // No usable cursor — bail rather than re-fetch the whole window.
      setHasMore(false);
      return;
    }
    setLoadingMore(true);
    try {
      const response = await api.listTranscripts(
        { limit: DEFAULT_PAGE_SIZE, before: oldest.ended_at },
        { signal: aborter.signal },
      );
      setItems((prev) => {
        // Dedupe by id — concurrent inserts could re-surface a row.
        const seen = new Set(prev.map((r) => r.id));
        return [
          ...prev,
          ...response.items.filter((r) => !seen.has(r.id)),
        ];
      });
      setHasMore(response.items.length >= DEFAULT_PAGE_SIZE);
    } catch (err) {
      if (isAbortError(err)) return;
      const message = err instanceof Error ? err.message : "load more failed";
      setListError(message);
    } finally {
      setLoadingMore(false);
    }
  }, [api, items, loadingMore]);

  // Phase I step I4: 1s tick that flips expired rows into the fading
  // set and queues their 600ms removal. ``row.ended_at === null``
  // (in-flight transcript) is skipped — the next WS push lands the
  // final row with a non-null ``ended_at`` which the next tick picks
  // up. Malformed (non-parseable) ``ended_at`` is skipped with a
  // single ``console.warn`` per row id, tracked via ``warnedIdsRef``
  // so a corrupt row doesn't spam the console.
  // Effect deps are ``[retentionSeconds]`` only — the interval must
  // be stable for the lifetime of the manager mount so cadence holds
  // across many fades. ``fadingIds`` is read via ``fadingIdsRef`` to
  // avoid pulling it into the deps array (which would reset the
  // interval on every fade and slide cadence).
  useEffect(() => {
    const tick = (): void => {
      const now = Date.now();
      setItems((current) => {
        for (const row of current) {
          if (row.ended_at === null) continue;
          if (fadingIdsRef.current.has(row.id)) continue;
          const expiresAtMs =
            new Date(row.ended_at).getTime() + retentionSeconds * 1000;
          if (Number.isNaN(expiresAtMs)) {
            if (!warnedIdsRef.current.has(row.id)) {
              warnedIdsRef.current.add(row.id);
              // eslint-disable-next-line no-console
              console.warn(
                "transcript has malformed ended_at, skipping fade",
                row.id,
                row.ended_at,
              );
            }
            continue;
          }
          if (expiresAtMs <= now) {
            // Capture the id in a const so the queued setTimeout
            // closes over a stable value (the row reference might be
            // GC'd by then).
            const expiringId = row.id;
            setFadingIds((prev) => {
              if (prev.has(expiringId)) return prev;
              const next = new Set(prev);
              next.add(expiringId);
              fadingIdsRef.current = next;
              return next;
            });
            const handle = window.setTimeout(() => {
              removalTimeoutsRef.current.delete(handle);
              setItems((curr) => curr.filter((r) => r.id !== expiringId));
              setFadingIds((prev) => {
                if (!prev.has(expiringId)) return prev;
                const next = new Set(prev);
                next.delete(expiringId);
                fadingIdsRef.current = next;
                return next;
              });
            }, 600);
            removalTimeoutsRef.current.add(handle);
          }
        }
        // No mutation to ``items`` here — only the fade state and the
        // queued removal touch ``items``. Return the same reference
        // so React skips a re-render from this setter.
        return current;
      });
    };
    const handle = window.setInterval(tick, 1000);
    // Capture the ref's Set once at effect-run so the cleanup closes
    // over the exact same instance the tick callbacks mutated. We
    // never reassign ``removalTimeoutsRef.current`` (only ``.add`` /
    // ``.delete`` / ``.clear`` on it), so this is safe and silences
    // the react-hooks/exhaustive-deps stale-ref warning.
    const removalTimeouts = removalTimeoutsRef.current;
    return () => {
      window.clearInterval(handle);
      // Cancel any in-flight 600ms removal timeouts. React 18 dev
      // StrictMode double-mounts this effect, and on real unmount
      // we don't want a stale timeout firing setItems against a
      // dead component.
      for (const t of removalTimeouts) {
        window.clearTimeout(t);
      }
      removalTimeouts.clear();
    };
  }, [retentionSeconds]);

  const openWipe = useCallback((): void => {
    setWipeOpen(true);
    setWipePin("");
    setWipeError(null);
    setWipeSuccessCount(null);
  }, []);

  const closeWipe = useCallback((): void => {
    setWipeOpen(false);
    setWipePin("");
    setWipeError(null);
  }, []);

  // Tick the wipe-modal lock countdown when engaged. Re-enables submit
  // on expiry. Mirror of the PinLogin pattern.
  useEffect(() => {
    if (wipeLockSeconds <= 0) return;
    const id = window.setInterval(() => {
      setWipeLockSeconds((prev) => {
        if (prev <= 1) {
          setWipeError(null);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, [wipeLockSeconds]);

  const submitWipe = useCallback(async (): Promise<void> => {
    if (wipeLockSeconds > 0) return;
    if (wipePin.length < PIN_MIN) {
      setWipeError(`PIN must be at least ${PIN_MIN} digits.`);
      return;
    }
    const aborter = aborterRef.current ?? new AbortController();
    setWipeSubmitting(true);
    setWipeError(null);
    try {
      const result = await api.wipeTranscripts(
        { pin: wipePin },
        { signal: aborter.signal },
      );
      setWipeSuccessCount(result.deleted);
      setWipeOpen(false);
      setWipePin("");
      // Refetch — the list should now be empty (or near-empty if a
      // late insert raced with the wipe). The active query stays as
      // the operator left it.
      await refetchList(activeQuery);
    } catch (err) {
      if (isAbortError(err)) return;
      const lockedDetail = extractPinLockedDetail(err);
      if (lockedDetail !== null) {
        setWipeLockSeconds(lockedDetail.seconds_until_unlock);
        setWipeError(
          `PIN locked. Try again in ${formatCountdown(lockedDetail.seconds_until_unlock)}.`,
        );
        setWipePin("");
        return;
      }
      const invalidDetail = extractPinInvalidDetail(err);
      if (invalidDetail !== null) {
        setWipeError(
          `Wrong PIN. ${invalidDetail.attempts_remaining} attempts remaining.`,
        );
        setWipePin("");
        return;
      }
      // Defensive: 412 ``pin_not_set`` is unreachable in normal boot
      // (the bind guard refuses to start without a stored PIN), but a
      // hand-edited DB or a stale tab racing setup can hit it. Surface
      // a recoverable message rather than a bare "wipe failed: 412".
      if (err instanceof ApiError && err.status === 412) {
        const body = err.body as
          | { detail?: { code?: unknown } | null }
          | null
          | undefined;
        if (body?.detail?.code === "pin_not_set") {
          setWipeError("No PIN configured — re-run setup.");
          setWipePin("");
          return;
        }
      }
      if (err instanceof ApiError) {
        setWipeError(`wipe failed: ${err.status}`);
      } else if (err instanceof Error) {
        setWipeError(`wipe failed: ${err.message}`);
      } else {
        setWipeError("wipe failed");
      }
    } finally {
      setWipeSubmitting(false);
    }
  }, [activeQuery, api, refetchList, wipeLockSeconds, wipePin]);

  return (
    <section
      data-testid="transcripts-manager"
      style={{
        border: "1px solid #ccc",
        borderRadius: 6,
        padding: 16,
        margin: "12px 0",
        background: "#fff",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 17 }}>Transcripts</h2>
        <input
          type="search"
          data-testid="transcripts-search-input"
          placeholder="search text..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          style={{ padding: 6, fontSize: 13, width: 220 }}
        />
      </div>
      <p
        data-testid="transcripts-listening-hint"
        style={{ fontSize: 12, color: "#6b7280", margin: "0 0 8px 0" }}
      >
        The kiosk records transcripts whenever the mic is on. Listening
        mode (Operator tab) controls AI escalation, not the mic. To stop
        recording, use the mic mute toggle in the Operator tab.
      </p>

      {loading && (
        <p
          data-testid="transcripts-loading"
          style={{ color: "#777", fontSize: 13 }}
        >
          loading...
        </p>
      )}
      {listError !== null && (
        <p
          data-testid="transcripts-list-error"
          role="alert"
          style={{ color: "#b71c1c", fontSize: 13 }}
        >
          {listError}
        </p>
      )}
      {wipeSuccessCount !== null && (
        <p
          data-testid="transcripts-wipe-success"
          role="status"
          style={{
            background: "#e8f5e9",
            border: "1px solid #c8e6c9",
            padding: 8,
            borderRadius: 4,
            fontSize: 13,
          }}
        >
          Deleted {wipeSuccessCount} transcripts.
        </p>
      )}

      {!loading && items.length === 0 && (
        <p
          data-testid="transcripts-empty"
          style={{ color: "#777", fontSize: 13 }}
        >
          {activeQuery === ""
            ? "No transcripts yet."
            : `No matches for "${activeQuery}".`}
        </p>
      )}

      {items.length > 0 && (
        <ul
          data-testid="transcripts-list"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {items.map((row) => {
            const isFading = fadingIds.has(row.id);
            // Non-fading rows still carry the ``transition`` so when
            // the flag flips on the next render, the opacity/height
            // collapse animates rather than snapping. ``max-height:
            // 0`` (not ``display: none``) lets the height transition
            // play smoothly to zero.
            const fadeStyle: CSSProperties = isFading
              ? {
                  opacity: 0,
                  maxHeight: 0,
                  marginTop: 0,
                  marginBottom: 0,
                  paddingTop: 0,
                  paddingBottom: 0,
                  overflow: "hidden",
                  transition: FADE_TRANSITION,
                }
              : {
                  transition: FADE_TRANSITION,
                };
            return (
              <li
                key={row.id}
                data-testid="transcript-row"
                data-transcript-id={row.id}
                data-fading={isFading ? "true" : "false"}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  justifyContent: "space-between",
                  padding: "8px 0",
                  borderBottom: "1px solid #eee",
                  gap: 8,
                  ...fadeStyle,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{ fontSize: 12, color: "#777" }}
                    data-testid="transcript-row-meta"
                  >
                    <span data-testid="transcript-row-time">
                      {row.started_at ?? "(no time)"}
                    </span>
                    <span
                      data-testid="transcript-row-confidence"
                      style={{ marginLeft: 8 }}
                    >
                      conf {confidenceBadge(row.confidence)}
                    </span>
                  </div>
                  <div
                    data-testid="transcript-row-text"
                    title={row.text ?? ""}
                    style={{ fontSize: 14, marginTop: 2 }}
                  >
                    {truncateText(row.text)}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {hasMore && (
        <div style={{ marginTop: 8 }}>
          <button
            type="button"
            data-testid="transcripts-load-more"
            disabled={loadingMore}
            onClick={() => {
              void loadMore();
            }}
          >
            {loadingMore ? "loading..." : "Load more"}
          </button>
        </div>
      )}

      <div
        style={{
          marginTop: 12,
          paddingTop: 12,
          borderTop: "1px solid #eee",
          display: "flex",
          justifyContent: "flex-end",
        }}
      >
        <button
          type="button"
          data-testid="transcripts-wipe-button"
          onClick={openWipe}
          style={{
            background: "#fdecea",
            border: "1px solid #f5c2c0",
            color: "#b71c1c",
            padding: "6px 12px",
            borderRadius: 4,
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          wipe all
        </button>
      </div>

      {wipeOpen && (
        <div
          data-testid="transcripts-wipe-modal"
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 100,
          }}
        >
          <div
            style={{
              background: "#fff",
              borderRadius: 6,
              padding: 16,
              width: 360,
              maxWidth: "90%",
            }}
          >
            <h3 style={{ marginTop: 0 }}>Wipe all transcripts</h3>
            <p style={{ fontSize: 13 }}>
              This will delete all {items.length} loaded transcripts (and
              any others on the server). This cannot be undone. Type your
              parent PIN to confirm.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void submitWipe();
              }}
            >
              <label
                htmlFor="transcripts-wipe-pin"
                style={{ display: "block", fontSize: 13 }}
              >
                Parent PIN
              </label>
              <input
                id="transcripts-wipe-pin"
                data-testid="transcripts-wipe-pin-input"
                type="password"
                inputMode="numeric"
                autoComplete="current-password"
                pattern="\d*"
                maxLength={PIN_MAX}
                value={wipePin}
                onChange={(e) => setWipePin(digitsOnly(e.target.value))}
                disabled={wipeSubmitting || wipeLockSeconds > 0}
                style={{ width: "100%", padding: 6, marginTop: 4 }}
              />
              {wipeLockSeconds > 0 && (
                <div
                  data-testid="transcripts-wipe-countdown"
                  role="status"
                  style={{
                    color: "#c0392b",
                    fontSize: 13,
                    marginTop: 8,
                  }}
                >
                  PIN locked. Try again in {formatCountdown(wipeLockSeconds)}.
                </div>
              )}
              {wipeLockSeconds === 0 && wipeError !== null && (
                <div
                  data-testid="transcripts-wipe-error"
                  role="alert"
                  style={{
                    color: "#c0392b",
                    fontSize: 13,
                    marginTop: 8,
                  }}
                >
                  {wipeError}
                </div>
              )}
              <div
                style={{
                  display: "flex",
                  justifyContent: "flex-end",
                  gap: 8,
                  marginTop: 12,
                }}
              >
                <button
                  type="button"
                  data-testid="transcripts-wipe-cancel"
                  onClick={closeWipe}
                  disabled={wipeSubmitting}
                >
                  cancel
                </button>
                <button
                  type="submit"
                  data-testid="transcripts-wipe-confirm"
                  disabled={wipeSubmitting || wipeLockSeconds > 0}
                  style={{
                    background: "#fdecea",
                    border: "1px solid #f5c2c0",
                    color: "#b71c1c",
                  }}
                >
                  {wipeSubmitting ? "wiping..." : "wipe all"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}
