// Phase H step H1 unit tests for the reusable Tabs / SubTabs
// components and the useTabState hook. These exist before any
// consumer wires the component (H2 does that), so the tests stand on
// their own: they render the components directly, render the hook via
// a tiny harness component, and never touch App.tsx.

import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SubTabs, Tabs, useTabState } from "./Tabs";
import type { TabItem } from "./Tabs";

type TopKey = "play" | "kids" | "settings";

const TOP_ITEMS: readonly TabItem<TopKey>[] = [
  { key: "play", label: "Play" },
  { key: "kids", label: "Kids & Toyboxes" },
  { key: "settings", label: "Settings" },
];

const VALID_TOP: readonly TopKey[] = ["play", "kids", "settings"];

const STORAGE_KEY = "toybox.parent.tab.top";

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  window.localStorage.clear();
});

describe("useTabState", () => {
  it("returns defaultValue when localStorage is empty", () => {
    const { result } = renderHook(() =>
      useTabState<TopKey>(STORAGE_KEY, "play", VALID_TOP),
    );
    expect(result.current.value).toBe("play");
    // Empty-storage path must not eagerly write either.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("returns a pre-seeded valid value from localStorage", () => {
    window.localStorage.setItem(STORAGE_KEY, "settings");
    const { result } = renderHook(() =>
      useTabState<TopKey>(STORAGE_KEY, "play", VALID_TOP),
    );
    expect(result.current.value).toBe("settings");
  });

  it("falls back to defaultValue on an invalid stored value without overwriting", () => {
    window.localStorage.setItem(STORAGE_KEY, "bogus");
    const { result } = renderHook(() =>
      useTabState<TopKey>(STORAGE_KEY, "play", VALID_TOP),
    );
    expect(result.current.value).toBe("play");
    // Bad value still present — H1 spec: do not eagerly overwrite.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("bogus");

    act(() => {
      result.current.setValue("kids");
    });
    expect(result.current.value).toBe("kids");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("kids");
  });

  it("persists across remount via the same storageKey", () => {
    const first = renderHook(() =>
      useTabState<TopKey>(STORAGE_KEY, "play", VALID_TOP),
    );
    act(() => {
      first.result.current.setValue("settings");
    });
    expect(first.result.current.value).toBe("settings");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("settings");
    first.unmount();

    const second = renderHook(() =>
      useTabState<TopKey>(STORAGE_KEY, "play", VALID_TOP),
    );
    expect(second.result.current.value).toBe("settings");
  });
});

// The Tabs and SubTabs components share their contract, so we exercise
// them through a parameterized describe. The only differences are the
// data-testid prefix and the rendered visual size — neither affects
// the a11y/click behavior under test.
const VARIANTS: ReadonlyArray<{
  name: "Tabs" | "SubTabs";
  prefix: "tab" | "subtab";
  Component: typeof Tabs;
}> = [
  { name: "Tabs", prefix: "tab", Component: Tabs },
  { name: "SubTabs", prefix: "subtab", Component: SubTabs },
];

for (const { name, prefix, Component } of VARIANTS) {
  describe(name, () => {
    it("renders one role='tab' button per item with correct aria-selected", () => {
      render(
        <Component<TopKey>
          items={TOP_ITEMS}
          value="kids"
          onChange={() => undefined}
        />,
      );

      const buttons = screen.getAllByRole("tab");
      expect(buttons).toHaveLength(TOP_ITEMS.length);

      for (const item of TOP_ITEMS) {
        const btn = screen.getByTestId(`${prefix}-${item.key}`);
        const expected = item.key === "kids" ? "true" : "false";
        expect(btn.getAttribute("aria-selected")).toBe(expected);
      }
    });

    it("fires onChange with the clicked key when an unselected tab is clicked", () => {
      const calls: TopKey[] = [];
      render(
        <Component<TopKey>
          items={TOP_ITEMS}
          value="play"
          onChange={(k) => calls.push(k)}
        />,
      );

      fireEvent.click(screen.getByTestId(`${prefix}-settings`));
      expect(calls).toEqual(["settings"]);

      // Clicking the already-selected tab does not re-emit onChange —
      // the consumer doesn't need the noisy update, and keeping this
      // contract lets parent components rely on onChange as an edge.
      fireEvent.click(screen.getByTestId(`${prefix}-play`));
      expect(calls).toEqual(["settings"]);
    });

    it("exposes a data-testid on every button shaped as `<prefix>-<key>`", () => {
      render(
        <Component<TopKey>
          items={TOP_ITEMS}
          value="play"
          onChange={() => undefined}
        />,
      );
      for (const item of TOP_ITEMS) {
        expect(
          screen.getByTestId(`${prefix}-${item.key}`),
        ).toBeDefined();
      }
    });
  });
}
