// Phase M Step M3 — ElementCard isolation tests (iter-2 trim).
//
// Trimmed in iter-2 to assert ONLY what's unique to the component:
//   - The sprite URL composes correctly from ``elementId`` and the alt
//     attribute uses the ``name`` prop. The symbol/name/atomic-number
//     text rendering is exercised end-to-end via StepCard's integration
//     test and the wire-shape integration test, so we don't re-test it
//     here.
//   - Falling back to the bundled periodic-table avatar on the FIRST
//     onError. The asset is a Vite-imported PNG (iter-2 fix per
//     reviewer HIGH #2) — the test pins that the fallback ``src``
//     swaps to a non-empty string distinct from the per-element URL.
//   - The fallback fires AT MOST ONCE — a second onError must NOT
//     cause another src swap. Strengthened in iter-2 by capturing the
//     src after the first and second errors and asserting both
//     captures are byte-identical (proves exactly one state change,
//     not just a stable final value).

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ElementCard } from "./ElementCard";

afterEach(() => {
  cleanup();
});

describe("ElementCard", () => {
  it("composes the sprite URL from elementId and uses name as alt text", () => {
    render(
      <ElementCard
        elementId="au-79"
        symbol="Au"
        name="Gold"
        atomicNumber={79}
      />,
    );
    const sprite = screen.getByTestId("element-card-sprite") as HTMLImageElement;
    // The src must point at the per-element static-mount path — the
    // sole URL-composition contract of this component.
    expect(sprite.getAttribute("src")).toBe("/api/static/elements/au-79.png");
    // alt is derived from the name prop so screen readers announce the
    // element by name rather than the cryptic composite id.
    expect(sprite.getAttribute("alt")).toBe("Gold sprite");
    // data-element-id is the e2e-selector hook documented in the
    // component's TSX; pin it here since it's a wire-contract surface
    // (StepCard's integration test doesn't assert on this attribute).
    expect(screen.getByTestId("element-card").getAttribute("data-element-id")).toBe(
      "au-79",
    );
  });

  it("swaps to the bundled fallback sprite on the first onError", () => {
    render(
      <ElementCard
        elementId="zz-999"
        symbol="Zz"
        name="Unobtainium"
        atomicNumber={999}
      />,
    );
    const sprite = screen.getByTestId("element-card-sprite") as HTMLImageElement;
    // Initial src is the per-element path.
    expect(sprite.getAttribute("src")).toBe("/api/static/elements/zz-999.png");
    fireEvent.error(sprite);
    const afterFirstError = sprite.getAttribute("src");
    // After the fallback, src must be a non-empty string DISTINCT from
    // the per-element URL (Vite resolves the import to a same-process
    // file URL at vitest runtime, a hashed asset URL at build time —
    // both are non-empty and not the per-element path).
    expect(afterFirstError).not.toBeNull();
    expect(afterFirstError).not.toBe("");
    expect(afterFirstError).not.toBe("/api/static/elements/zz-999.png");
  });

  it("does NOT re-swap the src when a second onError fires (no infinite loop)", () => {
    render(
      <ElementCard
        elementId="zz-999"
        symbol="Zz"
        name="Unobtainium"
        atomicNumber={999}
      />,
    );
    const sprite = screen.getByTestId("element-card-sprite") as HTMLImageElement;
    fireEvent.error(sprite);
    const srcAfterFirst = sprite.getAttribute("src");
    expect(srcAfterFirst).not.toBeNull();
    // A second onError (e.g. the fallback image itself 404s in dev)
    // must NOT trigger another swap — otherwise React + the browser
    // could enter an infinite re-render loop on a misconfigured server.
    fireEvent.error(sprite);
    const srcAfterSecond = sprite.getAttribute("src");
    // Iter-2 strengthening: assert byte-identity across the two
    // captures so the test fails on ANY second state change, not just
    // a "src happens to land on the same final value" coincidence.
    expect(srcAfterSecond).toBe(srcAfterFirst);
  });
});
