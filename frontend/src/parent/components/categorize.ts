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

// Internal helper: bucket a theme list into a category.
// Precedence: Elements > Feelings & Friends > Adventures.
//
// For Activities, the "elements" bucket is detected via per-step
// ``element_id`` (see ``categorize()``). For CatalogEntry templates,
// there is no per-step element_id on the wire; instead element templates
// carry the ``"periodic_table"`` theme. This helper covers the theme
// dimension that both code paths share.
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
// Element templates carry the ``"periodic_table"`` theme by convention
// (set by generator.py on every element_microgame template). The
// ``"elements"`` bucket therefore matches on that theme — same
// precedence as ``categorize()`` (Elements > Feelings > Adventures).
export function categorizeTemplate(
  entry: CatalogEntry,
  filterCategory: ActivityCategory | undefined,
): boolean {
  if (filterCategory === undefined) return true;
  const actual = entry.themes.includes("periodic_table")
    ? "elements"
    : categoryFromThemes(entry.themes);
  return actual === filterCategory;
}
