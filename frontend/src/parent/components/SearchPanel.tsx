// Phase R Step R4: activity + template search panel.
//
// Renders a persistent search input pinned above the PlayQueueList.
// When the input is non-empty, results replace the queue view; clearing
// restores the normal queue.  Results are split into two sections:
// "Past activities" (DB LIKE scan on summary.title) and "Templates"
// (in-memory substring match on id/title).
//
// Debounce: 300 ms via setTimeout/clearTimeout — no extra library.

import type { CSSProperties, JSX } from "react";
import { useEffect, useRef, useState } from "react";

import type { ApiClient, PastActivityResult, ProposePayload, SearchResponse, TemplateResult } from "../api";
import { ApiError } from "../api";

// ---------------------------------------------------------------------------
// Style constants (inline, matching surrounding components)
// ---------------------------------------------------------------------------

const INPUT_STYLE: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "6px 10px",
  border: "1px solid #d1d5db",
  borderRadius: 6,
  fontSize: 14,
  lineHeight: 1.4,
  outline: "none",
  marginBottom: 8,
};

const SECTION_HEADING_STYLE: CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  color: "#6b7280",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  margin: "8px 0 4px 0",
};

const RESULT_ROW_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "6px 8px",
  borderRadius: 5,
  background: "#f9fafb",
  marginBottom: 4,
  gap: 8,
};

const STATE_BADGE_STYLE: CSSProperties = {
  fontSize: 11,
  padding: "1px 6px",
  borderRadius: 10,
  background: "#e5e7eb",
  color: "#374151",
  whiteSpace: "nowrap",
  flexShrink: 0,
};

const ACTION_BTN_STYLE: CSSProperties = {
  fontSize: 12,
  padding: "3px 10px",
  borderRadius: 5,
  border: "1px solid #6366f1",
  background: "#6366f1",
  color: "#fff",
  cursor: "pointer",
  whiteSpace: "nowrap",
  flexShrink: 0,
};

const STATUS_TEXT_STYLE: CSSProperties = {
  fontSize: 13,
  color: "#6b7280",
  padding: "8px 0",
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SearchPanelProps {
  /** Controlled search query string from App.tsx */
  searchQuery: string;
  /** Notify parent when the user changes the input */
  onSearchQueryChange: (q: string) => void;
  /** ApiClient instance (threaded from App.tsx) */
  api: Pick<ApiClient, "searchActivities">;
  /**
   * Called when the user clicks "Play again" or "Try this".
   * ``templateId`` is null when the past activity has no template_id.
   */
  onPropose: (payload: Partial<ProposePayload> & { template_id?: string | null }) => void;
  /**
   * Debounce delay in milliseconds (default 300).  Set to 0 in tests to
   * bypass the wait and assert on results immediately.
   */
  debounceMs?: number;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SearchPanel({
  searchQuery,
  onSearchQueryChange,
  api,
  onPropose,
  debounceMs = 300,
}: SearchPanelProps): JSX.Element {
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Debounced search effect.
  useEffect(() => {
    const q = searchQuery.trim();

    // Clear previous timer.
    if (debounceRef.current !== null) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    // Abort any in-flight request.
    if (abortRef.current !== null) {
      abortRef.current.abort();
      abortRef.current = null;
    }

    if (q.length === 0) {
      setResults(null);
      setLoading(false);
      setError(false);
      return;
    }

    debounceRef.current = setTimeout(() => {
      const aborter = new AbortController();
      abortRef.current = aborter;
      setLoading(true);
      setError(false);

      api
        .searchActivities(q, { signal: aborter.signal })
        .then((resp) => {
          if (aborter.signal.aborted) return;
          setResults(resp);
          setLoading(false);
        })
        .catch((err: unknown) => {
          if (aborter.signal.aborted) return;
          if (err instanceof ApiError && err.status === 422) {
            // Too-short or invalid query — treat as no results.
            setResults({ past_activities: [], templates: [] });
          } else {
            setError(true);
          }
          setLoading(false);
        });
    }, debounceMs);

    return () => {
      if (debounceRef.current !== null) {
        clearTimeout(debounceRef.current);
      }
    };
  }, [searchQuery, api, debounceMs]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (debounceRef.current !== null) clearTimeout(debounceRef.current);
      if (abortRef.current !== null) abortRef.current.abort();
    };
  }, []);

  const handlePastActivityPropose = (act: PastActivityResult): void => {
    onPropose({ template_id: act.template_id ?? null });
  };

  const handleTemplatePropose = (tmpl: TemplateResult): void => {
    onPropose({ template_id: tmpl.id });
  };

  const q = searchQuery.trim();
  const hasResults =
    results !== null &&
    (results.past_activities.length > 0 || results.templates.length > 0);
  const isEmpty = results !== null && !hasResults && !loading && !error;

  return (
    <div data-testid="search-panel">
      <input
        type="text"
        aria-label="Search activities"
        placeholder="Search activities..."
        value={searchQuery}
        onChange={(e) => onSearchQueryChange(e.target.value)}
        style={INPUT_STYLE}
        data-testid="search-input"
      />

      {loading && (
        <p style={STATUS_TEXT_STYLE} data-testid="search-loading">
          Searching...
        </p>
      )}

      {!loading && error && (
        <p style={{ ...STATUS_TEXT_STYLE, color: "#ef4444" }} data-testid="search-error">
          Search unavailable
        </p>
      )}

      {!loading && isEmpty && q.length > 0 && (
        <p style={STATUS_TEXT_STYLE} data-testid="search-empty">
          No results for &ldquo;{q}&rdquo;
        </p>
      )}

      {!loading && !error && results !== null && (
        <>
          {results.past_activities.length > 0 && (
            <section data-testid="past-activities-section">
              <p style={SECTION_HEADING_STYLE}>Past activities</p>
              {results.past_activities.map((act) => (
                <div key={act.id} style={RESULT_ROW_STYLE} data-testid="past-activity-row">
                  <span style={{ fontSize: 13, color: "#111827", flex: 1, minWidth: 0 }}>
                    {act.title ?? act.id}
                  </span>
                  <span style={STATE_BADGE_STYLE} data-testid="state-badge">
                    {act.state}
                  </span>
                  <button
                    type="button"
                    style={ACTION_BTN_STYLE}
                    onClick={() => handlePastActivityPropose(act)}
                    data-testid="play-again-btn"
                  >
                    Play again
                  </button>
                </div>
              ))}
            </section>
          )}

          {results.templates.length > 0 && (
            <section data-testid="templates-section">
              <p style={SECTION_HEADING_STYLE}>Templates</p>
              {results.templates.map((tmpl) => (
                <div key={tmpl.id} style={RESULT_ROW_STYLE} data-testid="template-row">
                  <span style={{ fontSize: 13, color: "#111827", flex: 1, minWidth: 0 }}>
                    {tmpl.title}
                  </span>
                  <span style={STATE_BADGE_STYLE} data-testid="intent-badge">
                    {tmpl.intent}
                  </span>
                  <button
                    type="button"
                    style={ACTION_BTN_STYLE}
                    onClick={() => handleTemplatePropose(tmpl)}
                    data-testid="try-this-btn"
                  >
                    Try this
                  </button>
                </div>
              ))}
            </section>
          )}
        </>
      )}
    </div>
  );
}
