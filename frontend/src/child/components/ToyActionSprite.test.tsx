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
    // src is the full computed URL; happy-dom prefixes it with the
    // test origin (``http://localhost:3000``) so we assert the suffix
    // — the load-bearing part is the path under ``/api/static/images``.
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-abc/looking.png",
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

  it("hides itself when the image fails to load (404)", () => {
    const { container } = render(
      <ToyActionSprite toyId="toy-missing" slot="jumping" />,
    );
    const img = screen.getByTestId("toy-action-sprite");
    // Simulate the browser's onError event firing — the asset 404s
    // (capability disabled, generation pending, generation failed)
    // so the component must drop itself from the DOM.
    fireEvent.error(img);
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
  it("appends ?v=<cacheKey> to the src when cacheKey is provided", () => {
    render(
      <ToyActionSprite toyId="t" slot="idle" cacheKey="seed-12345" />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.png?v=seed-12345",
    );
  });

  it("emits the bare URL with no query string when cacheKey is omitted (backwards-compat)", () => {
    render(<ToyActionSprite toyId="t" slot="idle" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // The kiosk and any existing callsite that doesn't pass cacheKey
    // must continue to get the bare URL — no stray ``?v=`` that would
    // bust the cache unnecessarily on every render.
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.png",
    );
  });

  it("URL-encodes the cacheKey value (space and ampersand)", () => {
    // ``encodeURIComponent`` is the contract: spaces become %20, ampersands
    // become %26 — guarantees the query string parses cleanly regardless
    // of what shape the upstream seed/version value takes.
    const { rerender } = render(
      <ToyActionSprite toyId="t" slot="idle" cacheKey="a b" />,
    );
    let img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.png?v=a%20b",
    );
    rerender(<ToyActionSprite toyId="t" slot="idle" cacheKey="a&b" />);
    img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.png?v=a%26b",
    );
  });
});
