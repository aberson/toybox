// Phase O Step O2 — pure-helper that buckets a runtime Activity into one
// of three parent-UI content categories: Adventures, Elements, or
// Feelings & Friends. The Phase O parent-dashboard PlaySubTab routes
// PlayQueueList to one of four sub-tabs (the fourth — "all" — passes
// ``filterCategory: undefined`` and bypasses this helper entirely).
//
// Precedence (per documentation/phase-o-plan.md §2):
//
//   Elements > Feelings & Friends > Adventures
//
// Rules:
//
//   * Any ``step.element_id`` non-null → ``"elements"`` (a Periodic-
//     Table-bearing activity always lands in the Elements tab,
//     regardless of its ``recommended_themes``).
//   * Else ``activity.recommended_themes.includes("feelings")`` →
//     ``"feelings-friends"``. Case-sensitive literal match per
//     plan §2 (the Theme StrEnum's wire value is always lowercase
//     ASCII; a capitalized variant would never appear on the wire).
//   * Else → ``"adventures"`` (the documented "everything else" bucket;
//     also the default when an activity has no steps or no template).
//
// Implementation notes:
//
//   * ``Activity`` is imported from ``../api`` (the parent UI's
//     hand-rolled wire shape). Phase O Step O2 widened that interface
//     to include the optional ``recommended_themes`` + per-step
//     ``element_id`` fields; categorize() reads them defensively
//     (treating ``undefined`` as the empty / null value) so pre-O2
//     activity envelopes still categorize cleanly as "adventures".
//
//   * Pure-function: no React, no DOM, no module-level state. Lives in
//     ``components/`` alongside its consumer (PlayQueueList) so the
//     dependency is local; the parallel SUT in ``shared/types.ts``
//     (also Phase O) provides the codegen-emitted typed contract for
//     future cross-route consumers.

import type { CatalogEntry } from "../../shared/types";
import type { Activity } from "../api";

export type ActivityCategory = "adventures" | "elements" | "feelings-friends";

// Internal helper: bucket a theme list into a category, AFTER the
// Elements rule has already been excluded by the caller.
// Precedence: Elements > Feelings & Friends > Adventures.
//
// The Elements bucket is detected via the per-step ``element_id`` signal,
// which both code paths share: ``categorize()`` reads ``step.element_id``
// off the Activity envelope, and ``categorizeTemplate()`` reads the
// ``has_element`` boolean off the CatalogEntry wire (the backend derives
// it from the same ``element_id``). ``periodic_table`` is NOT a member of
// the Theme taxonomy, so it never appears in ``themes`` — this helper
// only covers the Feelings/Adventures split that remains once Elements
// has been handled.
function categoryFromThemes(themes: readonly string[]): ActivityCategory {
  if (themes.includes("feelings")) {
    return "feelings-friends";
  }
  return "adventures";
}

export function categorize(activity: Activity): ActivityCategory {
  const steps = activity.steps ?? [];
  // Elements rule wins regardless of recommended_themes. A step's
  // ``element_id`` is ``null`` on non-element steps; we treat both
  // ``null`` and ``undefined`` (legacy envelopes that pre-date M3)
  // as "no element".
  for (const step of steps) {
    if (step.element_id !== null && step.element_id !== undefined) {
      return "elements";
    }
  }
  const themes = activity.recommended_themes ?? [];
  return categoryFromThemes(themes);
}

// Phase T Step T3 — filter a CatalogEntry against one of the three
// parent-UI content sub-tabs. Returns true when the entry belongs to
// the supplied category. When ``filterCategory`` is ``undefined`` (the
// "All" sub-tab), always returns true.
//
// Element templates are detected via the ``has_element`` flag the backend
// emits (true when any template step carries an ``element_id``) — the same
// authoritative signal ``categorize()`` and ``generator._filter_by_category``
// use. ``periodic_table`` is NOT a Theme enum member, so element templates
// carry ordinary themes (e.g. ``friendship``/``silly``); bucketing them by a
// non-existent theme silently emptied the Elements catalog tab (SWR Step 4).
// Precedence matches ``categorize()``: Elements > Feelings > Adventures.
export function categorizeTemplate(
  entry: CatalogEntry,
  filterCategory: ActivityCategory | undefined,
): boolean {
  if (filterCategory === undefined) return true;
  const actual = entry.has_element
    ? "elements"
    : categoryFromThemes(entry.themes);
  return actual === filterCategory;
}
