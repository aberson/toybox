// Phase T Step T3 — CatalogPanel: offline template catalog browse view.
//
// Fetches GET /api/catalog once on mount and renders a filterable list
// of template cards. Two layers of filter:
//
//   1. ``filterCategory`` (prop): narrows to Adventures / Elements /
//      Feelings & Friends via ``categorizeTemplate()``. Undefined ==
//      "All" sub-tab (show everything).
//
//   2. Theme chip row: one active chip at a time; tapping again
//      deselects. Derived from the union of themes across currently
//      visible entries after the category filter.
//
// Each card has a "Launch" button that calls ``api.propose()`` with the
// template pinned via ``template_id``. A simple in-component toast shows
// "Proposed!" on success and "Failed — retry?" on error.

import type { CSSProperties, JSX } from "react";
import { useEffect, useState } from "react";

import type { CatalogEntry, CatalogResponse } from "../../shared/types";
import type { ApiClient } from "../api";
import { ApiError } from "../api";
import { categorizeTemplate } from "./categorize";
import type { ActivityCategory } from "./categorize";

// ---------------------------------------------------------------------------
// Style constants
// ---------------------------------------------------------------------------

const PANEL_STYLE: CSSProperties = {
  marginTop: 8,
};

const LOADING_STYLE: CSSProperties = {
  padding: "16px 0",
  color: "#6b7280",
  fontSize: 14,
};

const ERROR_STYLE: CSSProperties = {
  padding: "12px 0",
  color: "#b91c1c",
  fontSize: 14,
};

const EMPTY_STYLE: CSSProperties = {
  padding: "16px 0",
  color: "#6b7280",
  fontSize: 14,
};

const CHIP_ROW_STYLE: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  marginBottom: 12,
};

const CARD_STYLE: CSSProperties = {
  border: "1px solid #e5e7eb",
  borderRadius: 8,
  padding: "12px 14px",
  marginBottom: 10,
  background: "#fff",
};

const CARD_TITLE_STYLE: CSSProperties = {
  fontWeight: 600,
  fontSize: 15,
  marginBottom: 6,
};

const BADGE_STYLE: CSSProperties = {
  display: "inline-block",
  padding: "2px 8px",
  borderRadius: 999,
  fontSize: 11,
  fontWeight: 600,
  background: "#dbeafe",
  color: "#1e3a8a",
  marginRight: 8,
  marginBottom: 4,
};

const META_STYLE: CSSProperties = {
  fontSize: 12,
  color: "#6b7280",
  marginTop: 4,
  marginBottom: 8,
};

const LAUNCH_BTN_STYLE: CSSProperties = {
  padding: "5px 14px",
  fontSize: 13,
  fontWeight: 600,
  borderRadius: 6,
  border: "1px solid #2563eb",
  background: "#2563eb",
  color: "#fff",
  cursor: "pointer",
};

const TOAST_STYLE: CSSProperties = {
  display: "inline-block",
  marginLeft: 10,
  fontSize: 12,
  color: "#15803d",
};

const TOAST_ERROR_STYLE: CSSProperties = {
  ...TOAST_STYLE,
  color: "#b91c1c",
};

function chipStyle(active: boolean): CSSProperties {
  return {
    padding: "3px 10px",
    fontSize: 12,
    fontWeight: active ? 600 : 500,
    borderRadius: 999,
    border: active ? "1px solid #2563eb" : "1px solid #d1d5db",
    background: active ? "#dbeafe" : "#f9fafb",
    color: active ? "#1e3a8a" : "#374151",
    cursor: "pointer",
  };
}

// ---------------------------------------------------------------------------
// Sub-component: a single template card
// ---------------------------------------------------------------------------

interface CardProps {
  entry: CatalogEntry;
  api: ApiClient;
}

function TemplateCard({ entry, api }: CardProps): JSX.Element {
  const [toast, setToast] = useState<"proposed" | "error" | null>(null);

  function handleLaunch(): void {
    setToast(null);
    api
      .propose({
        intent: entry.intent,
        template_id: entry.id,
        hour: new Date().getHours(),
        seed: Math.floor(Math.random() * 1e6),
        use_recent_transcripts: false,
      })
      .then(() => {
        setToast("proposed");
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 0) {
          // Aborted — don't show error toast.
          return;
        }
        setToast("error");
      });
  }

  return (
    <div style={CARD_STYLE} data-testid={`catalog-card-${entry.id}`}>
      <div style={CARD_TITLE_STYLE} data-testid="catalog-card-title">
        {entry.title}
      </div>
      <div>
        <span style={BADGE_STYLE} data-testid="catalog-card-intent">
          {entry.intent}
        </span>
        {entry.themes.map((theme) => (
          <span key={theme} style={{ ...BADGE_STYLE, background: "#f3f4f6", color: "#374151" }}>
            {theme}
          </span>
        ))}
      </div>
      <div style={META_STYLE}>{entry.step_count} steps</div>
      <button
        type="button"
        style={LAUNCH_BTN_STYLE}
        data-testid="catalog-card-launch"
        onClick={handleLaunch}
      >
        Launch
      </button>
      {toast === "proposed" && (
        <span style={TOAST_STYLE} data-testid="catalog-card-toast-ok">
          Proposed!
        </span>
      )}
      {toast === "error" && (
        <span style={TOAST_ERROR_STYLE} data-testid="catalog-card-toast-err">
          Failed — retry?
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export interface CatalogPanelProps {
  filterCategory: ActivityCategory | undefined;
  api: ApiClient;
}

export function CatalogPanel({ filterCategory, api }: CatalogPanelProps): JSX.Element {
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [activeTheme, setActiveTheme] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setFetchError(null);
    api
      .getCatalog()
      .then((resp) => {
        if (!cancelled) {
          setCatalog(resp);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 0) return; // aborted
        setFetchError(
          err instanceof Error ? err.message : "Failed to load catalog",
        );
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  // Reset the theme chip selection whenever the category sub-tab changes.
  // If "feelings" was active on All and the user switches to Adventures,
  // the chip disappears from the row but the filter stays active, showing
  // zero cards with no way to deselect. Clear it on every filterCategory change.
  useEffect(() => {
    setActiveTheme(null);
  }, [filterCategory]);

  if (loading) {
    return (
      <div style={PANEL_STYLE}>
        <div style={LOADING_STYLE}>Loading catalog...</div>
      </div>
    );
  }

  if (fetchError !== null) {
    return (
      <div style={PANEL_STYLE}>
        <div style={ERROR_STYLE} data-testid="catalog-error">
          {fetchError}
        </div>
      </div>
    );
  }

  const allEntries = catalog?.entries ?? [];

  // Apply category filter.
  const categoryFiltered = allEntries.filter((e) =>
    categorizeTemplate(e, filterCategory),
  );

  // Derive theme chips from the category-filtered set.
  const allThemes = Array.from(
    new Set(categoryFiltered.flatMap((e) => e.themes)),
  ).sort();

  // Apply theme chip filter.
  const visible =
    activeTheme === null
      ? categoryFiltered
      : categoryFiltered.filter((e) => e.themes.includes(activeTheme));

  function handleChipClick(theme: string): void {
    setActiveTheme((prev) => (prev === theme ? null : theme));
  }

  return (
    <div style={PANEL_STYLE} data-testid="catalog-panel">
      {allThemes.length > 0 && (
        <div style={CHIP_ROW_STYLE} data-testid="catalog-theme-chips">
          {allThemes.map((theme) => (
            <button
              key={theme}
              type="button"
              style={chipStyle(activeTheme === theme)}
              data-testid={`catalog-chip-${theme}`}
              onClick={() => handleChipClick(theme)}
            >
              {theme}
            </button>
          ))}
        </div>
      )}
      {visible.length === 0 ? (
        <div style={EMPTY_STYLE} data-testid="catalog-empty">
          No templates found
        </div>
      ) : (
        visible.map((entry) => (
          <TemplateCard key={entry.id} entry={entry} api={api} />
        ))
      )}
    </div>
  );
}
