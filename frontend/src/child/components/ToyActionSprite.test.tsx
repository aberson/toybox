// Phase F Step F7 — vitest coverage for the ToyActionSprite component.
// The component is intentionally minimal so the tests focus on the
// load-bearing behaviors:
//   1. URL composition + alt text shape (with + without display name)
//   2. ``onError`` removes the element so a 404 renders gracefully
//   3. Phase V: CSS intro animation state machine (data-animating attr)
//
// The Phase V steady-state ``.webp`` swap (SVD-generated animated idle
// sprite) was removed after every generated webp came out garbled — the
// kiosk now renders the static ``.png`` for every slot. These tests pin
// the png-only contract so the broken swap can't silently return.

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

  // The idle slot used to swap png→webp after the intro animation. That
  // swap is gone: idle stays on the static png both at mount and after the
  // intro animation completes.
  it("keeps the idle slot on png both before and after the intro animation", () => {
    render(<ToyActionSprite toyId="toy-1" slot="idle" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/idle.png",
    );
    // animationend must NOT introduce a .webp (or any other) src swap.
    fireEvent.animationEnd(img);
    const imgAfter = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgAfter.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/idle.png",
    );
    expect(imgAfter.getAttribute("src")).not.toContain(".webp");
  });

  it("hides itself when the idle png 404s (single error → hidden)", () => {
    const { container } = render(
      <ToyActionSprite toyId="toy-missing" slot="idle" />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // No intermediate webp step — one png error goes straight to hidden.
    fireEvent.error(img);
    expect(
      container.querySelector('[data-testid="toy-action-sprite"]'),
    ).toBeNull();
  });

  it("hides itself when png fails to load for a non-idle slot", () => {
    const { container } = render(
      <ToyActionSprite toyId="toy-missing" slot="jumping" />,
    );
    const img = screen.getByTestId("toy-action-sprite");
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

  it("appends ?v=<cacheKey> to the png src when cacheKey is provided", () => {
    render(<ToyActionSprite toyId="t" slot="idle" cacheKey="seed-12345" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.png?v=seed-12345",
    );
  });

  it("emits the bare png URL with no query string when cacheKey is omitted", () => {
    render(<ToyActionSprite toyId="t" slot="idle" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.png",
    );
  });

  it("URL-encodes the cacheKey value (space and ampersand)", () => {
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

  // ─── Phase V: animation state machine tests ───────────────────────────────

  it("plays the intro animation on mount", () => {
    render(<ToyActionSprite toyId="toy-1" slot="looking" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // data-animating is set to the slot value on mount.
    expect(img.dataset["animating"]).toBe("looking");
  });

  it("clears the animating attribute after animation ends", () => {
    render(<ToyActionSprite toyId="toy-1" slot="jumping" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.dataset["animating"]).toBe("jumping");
    fireEvent.animationEnd(img);
    const imgAfter = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgAfter.hasAttribute("data-animating")).toBe(false);
  });

  it("stays on png src after non-idle intro animation ends", () => {
    render(<ToyActionSprite toyId="toy-1" slot="jumping" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/jumping.png",
    );
    fireEvent.animationEnd(img);
    const imgAfter = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // Verify the handler ran: data-animating should be absent.
    expect(imgAfter.hasAttribute("data-animating")).toBe(false);
    expect(imgAfter.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/jumping.png",
    );
  });

  it("replays the intro animation when slot prop changes", () => {
    const { rerender } = render(<ToyActionSprite toyId="toy-1" slot="idle" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // Initial state: animating with idle.
    expect(img.dataset["animating"]).toBe("idle");

    // Fire animationend to clear animating state.
    fireEvent.animationEnd(img);
    const imgAfterAnim = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgAfterAnim.hasAttribute("data-animating")).toBe(false);

    // Rerender with a new slot — data-animating should reappear with the new value.
    rerender(<ToyActionSprite toyId="toy-1" slot="jumping" />);
    const imgAfterRerender = screen.getByTestId(
      "toy-action-sprite",
    ) as HTMLImageElement;
    expect(imgAfterRerender.dataset["animating"]).toBe("jumping");
  });

  it("clears hidden state when the slot prop changes after a 404", () => {
    const { rerender, container } = render(
      <ToyActionSprite toyId="toy-1" slot="idle" />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    fireEvent.error(img);
    expect(
      container.querySelector('[data-testid="toy-action-sprite"]'),
    ).toBeNull();
    // A new slot should re-show the element (the new slot may have a sprite).
    rerender(<ToyActionSprite toyId="toy-1" slot="waving" />);
    const imgAfter = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgAfter.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/waving.png",
    );
  });

  // ─── Claude Images: preferSvg format chain ────────────────────────────────

  it("loads .svg first when preferSvg is true", () => {
    render(<ToyActionSprite toyId="toy-1" slot="idle" preferSvg />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/idle.svg",
    );
  });

  it("falls back from .svg to .png on a 404 when preferSvg is true", () => {
    render(<ToyActionSprite toyId="toy-1" slot="looking" preferSvg />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toContain("looking.svg");
    fireEvent.error(img);
    const after = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(after.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/looking.png",
    );
  });

  it("hides after both .svg and .png 404 when preferSvg is true", () => {
    const { container } = render(
      <ToyActionSprite toyId="toy-1" slot="idle" preferSvg />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    fireEvent.error(img); // svg 404 → png
    const png = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(png.getAttribute("src")).toContain("idle.png");
    fireEvent.error(png); // png 404 → hidden
    expect(
      container.querySelector('[data-testid="toy-action-sprite"]'),
    ).toBeNull();
  });

  it("keeps the idle .svg after the intro animation (no raster swap)", () => {
    render(<ToyActionSprite toyId="toy-1" slot="idle" preferSvg />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    fireEvent.animationEnd(img);
    const after = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(after.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/idle.svg",
    );
  });

  it("appends ?v=<cacheKey> to the .svg src when preferSvg + cacheKey", () => {
    render(<ToyActionSprite toyId="t" slot="idle" preferSvg cacheKey="s9" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.svg?v=s9",
    );
  });

  it("resets the format chain to png-only when preferSvg flips to false", () => {
    const { rerender } = render(
      <ToyActionSprite toyId="toy-1" slot="idle" preferSvg />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    fireEvent.error(img); // svg → png
    expect(
      (screen.getByTestId("toy-action-sprite") as HTMLImageElement).getAttribute(
        "src",
      ),
    ).toContain("idle.png");
    // Flip the flag off — chain resets to png-only from the first candidate.
    rerender(<ToyActionSprite toyId="toy-1" slot="idle" preferSvg={false} />);
    const after = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(after.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/idle.png",
    );
  });
});
