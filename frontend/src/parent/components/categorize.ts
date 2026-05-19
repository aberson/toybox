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

import type { Activity } from "../api";

export type ActivityCategory = "adventures" | "elements" | "feelings-friends";

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
  if (themes.includes("feelings")) {
    return "feelings-friends";
  }
  return "adventures";
}
