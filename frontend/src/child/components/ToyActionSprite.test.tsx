// Phase F Step F7 — vitest coverage for the ToyActionSprite component.
// The component is intentionally minimal so the tests focus on the
// load-bearing behaviors:
//   1. URL composition + alt text shape (with + without display name)
//   2. ``onError`` removes the element so a 404 renders gracefully
//   3. Phase V: CSS intro animation state machine (data-animating attr)

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ToyActionSprite } from "./ToyActionSprite";

afterEach(() => {
  cleanup();
});

describe("ToyActionSprite", () => {
  // Phase V BREAKING CHANGE: initial src is .png (not .webp) because the
  // intro animation plays with the png; webp is only loaded after
  // animationend fires for the idle slot.
  it("renders an <img> with the correct src + alt when toyId and slot are set", () => {
    render(
      <ToyActionSprite
        toyId="toy-abc"
        slot="looking"
        toyDisplayName="Mr. Unicorn"
      />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // Phase V: initial src is .png during the intro animation phase.
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

  // Phase V: WebP fallback fires only during idle steady state (after
  // animationend transitions format to webp for idle slot).
  // Restructured: render with slot="idle", fire animationend to trigger
  // webp, then fire error to test webp→png fallback.
  it("falls back to png on webp 404", () => {
    render(<ToyActionSprite toyId="toy-missing" slot="idle" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // During intro animation, format is png.
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-missing/idle.png",
    );
    // Fire animationend to transition idle slot to webp steady state.
    fireEvent.animationEnd(img);
    const imgAfterAnim = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgAfterAnim.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-missing/idle.webp",
    );
    // Now fire error to test webp→png fallback.
    fireEvent.error(imgAfterAnim);
    const imgAfterError = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgAfterError.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-missing/idle.png",
    );
  });

  it("hides itself when png fails to load for a non-idle slot", () => {
    const { container } = render(
      <ToyActionSprite toyId="toy-missing" slot="jumping" />,
    );
    const img = screen.getByTestId("toy-action-sprite");
    // Phase V: non-idle slot has no webp phase — one error event goes
    // straight from png to hidden.
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

  // Cache-bust contract: Phase V BREAKING CHANGE — initial src is .png (not .webp).
  it("appends ?v=<cacheKey> to the initial png src when cacheKey is provided", () => {
    render(
      <ToyActionSprite toyId="t" slot="idle" cacheKey="seed-12345" />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // Phase V: initial src is .png during intro animation.
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.png?v=seed-12345",
    );
  });

  // Phase V BREAKING CHANGE: initial src is .png (not .webp).
  it("emits the bare png URL with no query string when cacheKey is omitted", () => {
    render(<ToyActionSprite toyId="t" slot="idle" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // Phase V: initial src is .png during intro animation.
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.png",
    );
  });

  // Phase V BREAKING CHANGE: initial src is .png (not .webp); cacheKey applies
  // to whichever format is currently active (png during intro, webp after idle
  // animationend).
  it("URL-encodes the cacheKey value (space and ampersand)", () => {
    const { rerender } = render(
      <ToyActionSprite toyId="t" slot="idle" cacheKey="a b" />,
    );
    let img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // Phase V: initial src is .png during intro animation.
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.png?v=a%20b",
    );
    rerender(<ToyActionSprite toyId="t" slot="idle" cacheKey="a&b" />);
    img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.png?v=a%26b",
    );
  });

  it("applies cacheKey to webp src after idle animationEnd", () => {
    render(<ToyActionSprite toyId="t" slot="idle" cacheKey="seed-xyz" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.png?v=seed-xyz",
    );
    fireEvent.animationEnd(img);
    const imgAfter = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgAfter.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/t/idle.webp?v=seed-xyz",
    );
  });

  it("hides itself after both webp and png fail in idle steady state", () => {
    const { container } = render(
      <ToyActionSprite toyId="toy-missing" slot="idle" />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // Fire animationend to enter idle steady-state webp.
    fireEvent.animationEnd(img);
    const imgWebp = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgWebp.getAttribute("src")).toContain(".webp");
    // webp 404 → falls back to png.
    fireEvent.error(imgWebp);
    const imgPng = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgPng.getAttribute("src")).toContain(".png");
    // png 404 → hidden.
    fireEvent.error(imgPng);
    expect(
      container.querySelector('[data-testid="toy-action-sprite"]'),
    ).toBeNull();
  });

  // ─── Phase V: NEW animation state machine tests ───────────────────────────

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

  it("transitions to webp src after idle intro animation", () => {
    render(<ToyActionSprite toyId="toy-1" slot="idle" />);
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // Initial src is .png during intro animation.
    expect(img.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/idle.png",
    );
    // Fire animationend — idle slot transitions to webp steady state.
    fireEvent.animationEnd(img);
    const imgAfter = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgAfter.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/idle.webp",
    );
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
    // Non-idle slots stay on .png after intro completes.
    expect(imgAfter.getAttribute("src")).toBe(
      "/api/static/images/toy_actions/toy-1/jumping.png",
    );
  });

  it("replays the intro animation when slot prop changes", () => {
    const { rerender } = render(
      <ToyActionSprite toyId="toy-1" slot="idle" />,
    );
    const img = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    // Initial state: animating with idle.
    expect(img.dataset["animating"]).toBe("idle");

    // Fire animationend to clear animating state.
    fireEvent.animationEnd(img);
    const imgAfterAnim = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgAfterAnim.hasAttribute("data-animating")).toBe(false);

    // Rerender with a new slot — data-animating should reappear with the new value.
    rerender(<ToyActionSprite toyId="toy-1" slot="jumping" />);
    const imgAfterRerender = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(imgAfterRerender.dataset["animating"]).toBe("jumping");
  });
});
