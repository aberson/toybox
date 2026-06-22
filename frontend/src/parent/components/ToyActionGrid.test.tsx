// Phase F Step F8: vitest coverage for the ToyActionGrid component.
// Per the plan §F8 done-when, we cover 9 cases:
//   1. Renders 10 cells in canonical ACTION_SLOTS order.
//   2. Done cell renders a sprite (reuses F7's component).
//   3. Running cell renders a status badge.
//   4. Queued cell renders a status badge + disables its regenerate
//      button so a double-click can't enqueue twice.
//   5. Failed cell renders a status badge + tooltip with error_msg.
//   6. "Regenerate all" button fires onRegenerateAll.
//   7. Per-slot "regenerate" button fires onRegenerateSlot(slot).
//   8. ``disabledReason`` renders a banner + disables every button.
//   9. Count summary shows "N/10 done" correctly.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ToyActionRow } from "../api";
import { ACTION_SLOTS } from "../api";
import { ToyActionGrid } from "./ToyActionGrid";

afterEach(() => {
  cleanup();
});

function fakeRow(overrides: Partial<ToyActionRow> = {}): ToyActionRow {
  return {
    toy_id: "toy-1",
    slot: "idle",
    status: "not_started",
    image_path: null,
    seed: null,
    error_msg: null,
    updated_at: "2026-05-06T10:00:00Z",
    ...overrides,
  };
}

function noop(): void {}

describe("ToyActionGrid", () => {
  it("renders 10 cells in canonical ACTION_SLOTS order", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
      />,
    );
    const cellsContainer = screen.getByTestId("toy-action-grid-cells");
    const cells = cellsContainer.querySelectorAll("[data-slot]");
    expect(cells.length).toBe(10);
    // The DOM order MUST match ACTION_SLOTS: that is the contract the
    // kiosk + parent UI both render against.
    const slots = Array.from(cells).map((c) =>
      c.getAttribute("data-slot"),
    );
    expect(slots).toEqual([...ACTION_SLOTS]);
  });

  it("renders the sprite for a done cell with image_path set", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        toyDisplayName="Mr. Unicorn"
        actions={[
          fakeRow({
            slot: "looking",
            status: "done",
            image_path: "data/images/toy_actions/toy-1/looking.png",
          }),
        ]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
      />,
    );
    const sprite = screen.getByTestId("toy-action-sprite");
    expect(sprite.getAttribute("data-slot")).toBe("looking");
    expect((sprite as HTMLImageElement).alt).toBe("Mr. Unicorn looking");
  });

  // Wire-shape: the stored image_path now varies by format. The grid must
  // thread preferSvg from the extension so a Claude-Images (.svg) row loads
  // the vector sprite and a local-pipeline (.png) row loads the raster.
  it("loads .svg first for a done row whose image_path ends in .svg", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[
          fakeRow({
            slot: "idle",
            status: "done",
            image_path: "data/images/toy_actions/toy-1/idle.svg",
            seed: 9,
          }),
        ]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
      />,
    );
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    expect(sprite.getAttribute("src") ?? "").toContain("/idle.svg");
  });

  it("loads .png directly for a done row whose image_path ends in .png", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[
          fakeRow({
            slot: "idle",
            status: "done",
            image_path: "data/images/toy_actions/toy-1/idle.png",
            seed: 9,
          }),
        ]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
      />,
    );
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    const src = sprite.getAttribute("src") ?? "";
    expect(src).toContain("/idle.png");
    expect(src).not.toContain(".svg");
  });

  it("renders a 'running...' status badge for a running cell", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[fakeRow({ slot: "jumping", status: "running" })]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
      />,
    );
    const badge = screen.getByTestId("toy-action-status-jumping");
    expect(badge.getAttribute("data-status")).toBe("running");
    expect(badge.textContent).toMatch(/running/i);
    // The regenerate button for an in-flight slot is disabled so a
    // double-click can't enqueue a duplicate.
    const button = screen.getByTestId(
      "toy-action-regenerate-jumping",
    ) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("renders a 'queued' status badge + disables that slot's regenerate button", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[fakeRow({ slot: "idle", status: "queued" })]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
      />,
    );
    const badge = screen.getByTestId("toy-action-status-idle");
    expect(badge.getAttribute("data-status")).toBe("queued");
    expect(badge.textContent).toMatch(/queued/i);
    const button = screen.getByTestId(
      "toy-action-regenerate-idle",
    ) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("renders a 'failed' status badge with the error_msg in the title attribute", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[
          fakeRow({
            slot: "cheering",
            status: "failed",
            error_msg: "interrupted by restart",
          }),
        ]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
      />,
    );
    const badge = screen.getByTestId("toy-action-status-cheering");
    expect(badge.getAttribute("data-status")).toBe("failed");
    expect(badge.getAttribute("title")).toBe("interrupted by restart");
    // The regenerate button stays enabled — the parent's recovery path.
    const button = screen.getByTestId(
      "toy-action-regenerate-cheering",
    ) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
  });

  it("'regenerate all' button fires onRegenerateAll once", () => {
    const onRegenerateAll = vi.fn();
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[]}
        onRegenerateAll={onRegenerateAll}
        onRegenerateSlot={noop}
      />,
    );
    fireEvent.click(screen.getByTestId("toy-action-grid-regenerate-all"));
    expect(onRegenerateAll).toHaveBeenCalledTimes(1);
  });

  it("per-slot 'regenerate' button fires onRegenerateSlot with the slot key", () => {
    const onRegenerateSlot = vi.fn();
    // ``not_started`` keeps the button enabled — that's the empty
    // pre-enqueue cell.
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[fakeRow({ slot: "waving", status: "not_started" })]}
        onRegenerateAll={noop}
        onRegenerateSlot={onRegenerateSlot}
      />,
    );
    fireEvent.click(screen.getByTestId("toy-action-regenerate-waving"));
    expect(onRegenerateSlot).toHaveBeenCalledTimes(1);
    expect(onRegenerateSlot).toHaveBeenCalledWith("waving");
  });

  it("disabledReason renders the banner + disables every button", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[
          fakeRow({ slot: "idle", status: "not_started" }),
          fakeRow({ slot: "looking", status: "failed", error_msg: "x" }),
        ]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
        disabledReason="GPU not available"
      />,
    );
    const banner = screen.getByTestId("toy-action-grid-disabled-banner");
    expect(banner.textContent).toMatch(/GPU not available/);
    expect(banner.textContent).toMatch(/Image generation disabled/);
    // ``regenerate all`` AND every per-slot button are disabled.
    expect(
      (
        screen.getByTestId(
          "toy-action-grid-regenerate-all",
        ) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    for (const slot of ACTION_SLOTS) {
      const button = screen.getByTestId(
        `toy-action-regenerate-${slot}`,
      ) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
    }
  });

  it("compositeOnlyMode renders the composite-only banner", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
        compositeOnlyMode={true}
      />,
    );
    const banner = screen.getByText(/running in composite-only mode/i);
    expect(banner).toBeTruthy();
  });

  it("no banner renders when compositeOnlyMode is false and disabledReason is unset", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
        compositeOnlyMode={false}
      />,
    );
    expect(screen.queryByTestId("toy-action-grid-composite-only-banner"))
      .toBeNull();
    expect(screen.queryByTestId("toy-action-grid-disabled-banner")).toBeNull();
  });

  it("compositeOnlyMode + disabledReason: only the disabled banner renders (mutually exclusive)", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
        compositeOnlyMode={true}
        disabledReason="GPU not available"
      />,
    );
    expect(screen.getByTestId("toy-action-grid-disabled-banner")).toBeTruthy();
    expect(screen.queryByTestId("toy-action-grid-composite-only-banner"))
      .toBeNull();
  });

  // Cache-bust contract (image-gen mode toggle fix): regenerating a slot
  // rewrites the same on-disk file with new bytes, so the parent UI must
  // bust the browser cache by appending ``?v=<seed>`` to the sprite URL.
  // ``ToyActionGrid`` threads ``row.seed`` (a number, optional) as the
  // cache key whenever a done row has a non-null seed.
  it("threads row.seed to the sprite as a cache-bust query param for done rows", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[
          fakeRow({
            slot: "looking",
            status: "done",
            image_path: "data/images/toy_actions/toy-1/looking.png",
            seed: 12345,
          }),
        ]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
      />,
    );
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    const src = sprite.getAttribute("src") ?? "";
    // Partial-match keeps the test stable across URL-shape tweaks (the
    // ``ToyActionSprite`` test pins the exact shape).
    expect(src).toContain("?v=12345");
  });

  it("does NOT append ?v= when a done row's seed is null (backwards-compat)", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[
          fakeRow({
            slot: "looking",
            status: "done",
            image_path: "data/images/toy_actions/toy-1/looking.png",
            seed: null,
          }),
        ]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
      />,
    );
    const sprite = screen.getByTestId("toy-action-sprite") as HTMLImageElement;
    const src = sprite.getAttribute("src") ?? "";
    // If seed is somehow null on a done row (legacy data, race), the
    // grid must fall back to the bare URL — the previous (pre-fix)
    // behavior — rather than emit a meaningless ``?v=null`` literal.
    expect(src).not.toContain("?v=");
  });

  it("count summary reports N/10 done correctly", () => {
    render(
      <ToyActionGrid
        toyId="toy-1"
        actions={[
          fakeRow({
            slot: "idle",
            status: "done",
            image_path: "data/x/idle.png",
          }),
          fakeRow({
            slot: "looking",
            status: "done",
            image_path: "data/x/looking.png",
          }),
          fakeRow({
            slot: "jumping",
            status: "done",
            image_path: "data/x/jumping.png",
          }),
          fakeRow({ slot: "cheering", status: "running" }),
          fakeRow({ slot: "thinking", status: "queued" }),
        ]}
        onRegenerateAll={noop}
        onRegenerateSlot={noop}
      />,
    );
    expect(screen.getByTestId("toy-action-grid-count").textContent).toBe(
      "3/10 done",
    );
  });
});
