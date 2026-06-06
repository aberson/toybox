// Phase O Step O2 — pure-helper unit tests for ``categorize()``.
//
// Per ``documentation/phase-o-plan.md`` §2 (Categorize logic):
//
//   Precedence: Elements > Feelings & Friends > Adventures.
//
//   * Any ``step.element_id`` non-null → ``"elements"``.
//   * Else ``activity.recommended_themes.includes("feelings")`` →
//     ``"feelings-friends"``.
//   * Else → ``"adventures"``.
//
// These tests are runtime-pure (no React) so they sit alongside the
// SUT in components/. The vitest config defaults *.test.ts files to
// the ``node`` environment — no DOM needed.
//
// The SUT lives at frontend/src/parent/components/categorize.ts and
// is created in O2 — the import below currently resolves to a missing
// module, so this test file initially fails at module-resolution. Once
// O2 lands the helper + the categorize function, the assertions
// inside fire and pin the rules.
//
// Activity-shape construction note: the helper's signature accepts an
// ``Activity`` per ``../../shared/types``. The fields it reads are
// ``activity.steps[i].element_id`` (string | null) and
// ``activity.recommended_themes`` (string[]). Other fields on Activity
// are irrelevant to categorize() — we use a minimal ``ActivityLike``
// shape internally and ``as unknown as Activity`` at the call site so
// the test stays focused on the rules under test.

import { describe, expect, it } from "vitest";

import type { CatalogEntry } from "../../shared/types";
import type { Activity } from "../api";
import { categorize, categorizeTemplate } from "./categorize";

// Minimal shape: only the fields categorize() reads. The cast at the
// helper call below is the load-bearing line — if Activity (post O2
// wire-shape widening) lacks ``recommended_themes`` or steps lack
// ``element_id``, this test file fails to typecheck under
// ``npm run typecheck`` (which is the project's CI gate alongside
// vitest), surfacing the missing wire-shape addition.
interface ActivityLike {
  steps: ReadonlyArray<{
    seq: number;
    body: string;
    sfx: string | null;
    expected_action: string | null;
    current: boolean;
    element_id: string | null;
  }>;
  recommended_themes: string[];
}

function fakeActivity(overrides: Partial<ActivityLike> = {}): Activity {
  const base: ActivityLike = {
    steps: [
      {
        seq: 1,
        body: "step body",
        sfx: null,
        expected_action: null,
        current: false,
        element_id: null,
      },
    ],
    recommended_themes: [],
    ...overrides,
  };
  return base as unknown as Activity;
}

describe("categorize() — Phase O Step O2 (precedence: Elements > Feelings & Friends > Adventures)", () => {
  it("element_id set on steps[0] → 'elements' (regardless of recommended_themes)", () => {
    const activity = fakeActivity({
      steps: [
        {
          seq: 1,
          body: "Find Gold on your screen",
          sfx: null,
          expected_action: null,
          current: false,
          element_id: "au-79",
        },
      ],
      // Empty themes so the rule under test is purely the element gate.
      recommended_themes: [],
    });
    expect(categorize(activity)).toBe("elements");
  });

  it("all element_ids null + recommended_themes includes 'feelings' → 'feelings-friends'", () => {
    const activity = fakeActivity({
      steps: [
        {
          seq: 1,
          body: "Name a feeling",
          sfx: null,
          expected_action: null,
          current: false,
          element_id: null,
        },
        {
          seq: 2,
          body: "Tell your toy",
          sfx: null,
          expected_action: null,
          current: false,
          element_id: null,
        },
      ],
      recommended_themes: ["feelings"],
    });
    expect(categorize(activity)).toBe("feelings-friends");
  });

  it("all element_ids null + recommended_themes empty → 'adventures'", () => {
    const activity = fakeActivity({
      steps: [
        {
          seq: 1,
          body: "Build a fort",
          sfx: null,
          expected_action: null,
          current: false,
          element_id: null,
        },
      ],
      recommended_themes: [],
    });
    expect(categorize(activity)).toBe("adventures");
  });

  it("precedence pin: element_id 'h-1' AND recommended_themes ['feelings'] → 'elements' (Elements wins)", () => {
    // The plan-§2 precedence rule: when both signals fire, Elements
    // beats Feelings & Friends. Phase M's track design disjoints these
    // categories by construction, but the helper documents + tests
    // the precedence anyway so a future template edit that
    // accidentally mixes the two doesn't silently fall into the wrong
    // bucket.
    const activity = fakeActivity({
      steps: [
        {
          seq: 1,
          body: "Look at Hydrogen",
          sfx: null,
          expected_action: null,
          current: false,
          element_id: "h-1",
        },
      ],
      recommended_themes: ["feelings"],
    });
    expect(categorize(activity)).toBe("elements");
  });

  it("multi-theme list with 'feelings' present → 'feelings-friends'", () => {
    const activity = fakeActivity({
      steps: [
        {
          seq: 1,
          body: "Play together",
          sfx: null,
          expected_action: null,
          current: false,
          element_id: null,
        },
      ],
      // Multi-theme: 'play' is in the canonical theme taxonomy too —
      // the helper must match on 'feelings' regardless of other entries.
      recommended_themes: ["feelings", "play"],
    });
    expect(categorize(activity)).toBe("feelings-friends");
  });

  it("case-sensitive match: 'Feelings' (capitalized) → 'adventures'", () => {
    // Per plan §2: the rule is a literal includes("feelings") match.
    // The Theme enum's wire value is always lowercase ASCII per the
    // StrEnum convention in src/toybox/activities/themes.py — a
    // capitalized variant should never appear on the wire, so the
    // helper deliberately does NOT case-fold. This pin makes the
    // behaviour explicit: a future "be lenient about case" change
    // would need to also update the plan + this test.
    const activity = fakeActivity({
      steps: [
        {
          seq: 1,
          body: "x",
          sfx: null,
          expected_action: null,
          current: false,
          element_id: null,
        },
      ],
      recommended_themes: ["Feelings"],
    });
    expect(categorize(activity)).toBe("adventures");
  });

  it("element_id on step[2] only (not step[0]) → 'elements' (any step counts)", () => {
    // Phase M / Phase N generators set element_id on every step by
    // construction, but the rule per plan §2 is "any step with
    // non-null element_id" — pin that the helper iterates the full
    // ``steps`` array, not just the first row.
    const activity = fakeActivity({
      steps: [
        {
          seq: 1,
          body: "Intro",
          sfx: null,
          expected_action: null,
          current: false,
          element_id: null,
        },
        {
          seq: 2,
          body: "Fork",
          sfx: null,
          expected_action: null,
          current: false,
          element_id: null,
        },
        {
          seq: 3,
          body: "Look at Gold",
          sfx: null,
          expected_action: null,
          current: false,
          element_id: "au-79",
        },
      ],
      recommended_themes: [],
    });
    expect(categorize(activity)).toBe("elements");
  });

  it("empty steps + empty themes → 'adventures' (default fallback)", () => {
    // Defensive: a malformed activity with no steps should still
    // categorize cleanly rather than throw. Adventures is the
    // documented "everything else" bucket.
    const activity = fakeActivity({
      steps: [],
      recommended_themes: [],
    });
    expect(categorize(activity)).toBe("adventures");
  });
});

// ---------------------------------------------------------------------------
// categorizeTemplate() — Phase T Step T3
// ---------------------------------------------------------------------------

function fakeCatalogEntry(overrides: Partial<CatalogEntry> = {}): CatalogEntry {
  return {
    id: "t1",
    title: "Test Template",
    intent: "play",
    themes: [],
    step_count: 3,
    ...overrides,
  };
}

describe("categorizeTemplate() — Phase T Step T3", () => {
  it("filterCategory undefined → always returns true regardless of themes", () => {
    const entry = fakeCatalogEntry({ themes: ["feelings", "periodic_table"] });
    expect(categorizeTemplate(entry, undefined)).toBe(true);
  });

  it("periodic_table theme + filterCategory 'elements' → true", () => {
    const entry = fakeCatalogEntry({ themes: ["periodic_table", "science"] });
    expect(categorizeTemplate(entry, "elements")).toBe(true);
  });

  it("periodic_table theme + filterCategory 'adventures' → false", () => {
    const entry = fakeCatalogEntry({ themes: ["periodic_table"] });
    expect(categorizeTemplate(entry, "adventures")).toBe(false);
  });

  it("feelings theme + filterCategory 'feelings-friends' → true", () => {
    const entry = fakeCatalogEntry({ themes: ["feelings", "friendship"] });
    expect(categorizeTemplate(entry, "feelings-friends")).toBe(true);
  });

  it("feelings theme + filterCategory 'adventures' → false", () => {
    const entry = fakeCatalogEntry({ themes: ["feelings"] });
    expect(categorizeTemplate(entry, "adventures")).toBe(false);
  });

  it("no special themes + filterCategory 'adventures' → true (default bucket)", () => {
    const entry = fakeCatalogEntry({ themes: ["treasure", "exploration"] });
    expect(categorizeTemplate(entry, "adventures")).toBe(true);
  });

  it("no special themes + filterCategory 'elements' → false", () => {
    const entry = fakeCatalogEntry({ themes: ["treasure"] });
    expect(categorizeTemplate(entry, "elements")).toBe(false);
  });
});
