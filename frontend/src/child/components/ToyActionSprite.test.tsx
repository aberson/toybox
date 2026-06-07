// Phase F Step F7 — vitest coverage for the ToyActionSprite component.
// The component is intentionally minimal so the tests focus on the
// load-bearing behaviors:
//   1. URL composition + alt text shape (with + without display name)
//   2. ``onError`` removes the element so a 404 renders gracefully

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ToyActionSprite } from "./ToyActionSprite";

afterEach(() => {
  cleanup();
});

describe("ToyActionSprite", () => {
  it("renders an <img> with the correct src + alt when toyId and slot are set", () => {
    render(
      <ToyActionSprite
        toyId="toy-abc"
        slot="looking"
        toyDisplayName="Mr. Unicorn"
      />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // Phase U: initial src is .webp (WebP-first with PNG fallback).
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-abc/looking.webp",
    );
    expect(img.alt).toBe("Mr. Unicorn looking");
    // Default size is 112 px — the kiosk passes no size override.
    expect(img.getAttribute("width")).toBe("112");
    expect(img.getAttribute("height")).toBe("112");
    // Data attributes expose toy_id + slot for Playwright / DOM probes
    // without forcing tests to re-derive the URL shape.
    expect(img.dataset["toyId"]).toBe("toy-abc");
    expect(img.dataset["slot"]).toBe("looking");
  });

  // Phase U: two-stage fallback. First error (webp 404) falls back to png;
  // second error (png 404) hides the element.
  it("falls back to png on webp 404", () => {
    render(<ToyActionSprite toyId="toy-missing" slot="jumping" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-missing/jumping.webp",
    );
    fireEvent.error(img);
    const imgAfter = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgAfter.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-missing/jumping.png",
    );
  });

  it("hides itself when both webp and png fail to load", () => {
    const { container } = render(
      <ToyActionSprite toyId="toy-missing" slot="jumping" />,
    );
    const img = screen.getByTestId("toy-action-sprite");
    fireEvent.error(img);
    const imgAfterFirst = screen.getByTestId("toy-action-sprite");
    fireEvent.error(imgAfterFirst);
    expect(
      container.querySelector('[data-testid="toy-action-sprite"]'),
    ).toBeNull();
  });

  it("composes alt as '<display_name> <slot>' when toyDisplayName is provided", () => {
    render(
      <ToyActionSprite
        toyId="toy-1"
        slot="dancing"
        toyDisplayName="Princess Lyra"
      />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.alt).toBe("Princess Lyra dancing");
  });

  it("falls back to the bare slot for alt when toyDisplayName is omitted", () => {
    render(<ToyActionSprite toyId="toy-1" slot="idle" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.alt).toBe("idle");
  });

  // Cache-bust contract (image-gen mode toggle fix): when the worker
  // rewrites the same on-disk path with new bytes (e.g. operator flips
  // composite ⇄ cartoon and re-runs the slot), the browser keeps showing
  // the previously cached bitmap because the URL is byte-identical. The
  // ``cacheKey`` prop adds a ``?v=<value>`` query string so the browser
  // treats the post-regenerate URL as a distinct resource. ``ToyActionGrid``
  // threads ``row.seed`` as the cache key for done rows.
  // Phase U: cacheKey applies to the currently-attempted format (initially webp).
  it("appends ?v=<cacheKey> to the initial webp src when cacheKey is provided", () => {
    render(
      <ToyActionSprite toyId="t" slot="idle" cacheKey="seed-12345" />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.webp?v=seed-12345",
    );
  });

  it("emits the bare webp URL with no query string when cacheKey is omitted", () => {
    render(<ToyActionSprite toyId="t" slot="idle" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.webp",
    );
  });

  it("URL-encodes the cacheKey value (space and ampersand)", () => {
    const { rerender } = render(
      <ToyActionSprite toyId="t" slot="idle" cacheKey="a b" />,
    );
    let img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.webp?v=a%20b",
    );
    rerender(<ToyActionSprite toyId="t" slot="idle" cacheKey="a&b" />);
    img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.webp?v=a%26b",
    );
  });
});
