// Phase H step H1: Reusable Tabs + SubTabs component pair and
// useTabState hook for the parent app. This file is groundwork only —
// it is not yet mounted in App.tsx (H2 wires it).
//
// Both Tabs (large pills) and SubTabs (smaller pills) are *controlled*
// components: the consumer owns the selected key. They render a
// ``role="tablist"`` container with one ``role="tab"`` button per item,
// and the consumer wraps the rendered panel in its own
// ``role="tabpanel"`` so we don't constrain layout/composition.
//
// useTabState persists the selection in localStorage. It reads exactly
// once at mount (lazy initializer) and writes synchronously on every
// setValue. Invalid stored values fall back to ``defaultValue`` but the
// hook does NOT eagerly overwrite localStorage — we only rewrite it on
// the next explicit setValue, so a half-finished migration / future
// build with extra keys doesn't trample a value it doesn't yet
// recognize.

import type { CSSProperties, JSX } from "react";
import { useCallback, useState } from "react";

export interface TabItem<K extends string = string> {
  key: K;
  label: string;
}

export interface TabsProps<K extends string = string> {
  items: readonly TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
}

const TABLIST_STYLE: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 8,
  padding: 0,
  margin: 0,
};

const SUBTABLIST_STYLE: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  padding: 0,
  margin: 0,
};

function tabButtonStyle(active: boolean): CSSProperties {
  return {
    padding: "10px 18px",
    fontSize: 14,
    fontWeight: active ? 600 : 500,
    borderRadius: 999,
    border: active ? "1px solid #2563eb" : "1px solid #d1d5db",
    background: active ? "#dbeafe" : "#fff",
    color: active ? "#1e3a8a" : "#374151",
    cursor: active ? "default" : "pointer",
  };
}

function subTabButtonStyle(active: boolean): CSSProperties {
  return {
    padding: "6px 12px",
    fontSize: 12,
    fontWeight: active ? 600 : 500,
    borderRadius: 999,
    border: active ? "1px solid #2563eb" : "1px solid #d1d5db",
    background: active ? "#dbeafe" : "#fff",
    color: active ? "#1e3a8a" : "#374151",
    cursor: active ? "default" : "pointer",
  };
}

export function Tabs<K extends string = string>(
  props: TabsProps<K>,
): JSX.Element {
  const { items, value, onChange } = props;
  return (
    <div role="tablist" data-testid="tabs" style={TABLIST_STYLE}>
      {items.map((item) => {
        const active = item.key === value;
        return (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={active ? "true" : "false"}
            data-testid={`tab-${item.key}`}
            onClick={() => {
              if (!active) onChange(item.key);
            }}
            style={tabButtonStyle(active)}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

export function SubTabs<K extends string = string>(
  props: TabsProps<K>,
): JSX.Element {
  const { items, value, onChange } = props;
  return (
    <div role="tablist" data-testid="subtabs" style={SUBTABLIST_STYLE}>
      {items.map((item) => {
        const active = item.key === value;
        return (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={active ? "true" : "false"}
            data-testid={`subtab-${item.key}`}
            onClick={() => {
              if (!active) onChange(item.key);
            }}
            style={subTabButtonStyle(active)}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

export interface UseTabStateResult<T extends string> {
  value: T;
  setValue: (next: T) => void;
}

// Read localStorage defensively. SSR builds + locked-down browsers
// (Safari private mode used to throw on access) shouldn't crash the
// component tree.
function safeReadStorage(storageKey: string): string | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(storageKey);
  } catch {
    return null;
  }
}

function safeWriteStorage(storageKey: string, value: string): void {
  try {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(storageKey, value);
  } catch {
    // Best-effort: if the write fails we still update React state so
    // the UI reflects the user's choice for this session.
  }
}

export function useTabState<T extends string>(
  storageKey: string,
  defaultValue: T,
  validValues: readonly T[],
): UseTabStateResult<T> {
  // Lazy initializer: runs exactly once at mount. ``validValues`` is
  // captured here for the read; subsequent renders never re-read
  // localStorage so the consumer is the sole source of truth.
  const [value, setStateValue] = useState<T>(() => {
    const stored = safeReadStorage(storageKey);
    if (stored !== null && (validValues as readonly string[]).includes(stored)) {
      return stored as T;
    }
    // Stored value is missing or invalid. Return default but do NOT
    // overwrite — H1 spec keeps the bad value in storage so a future
    // build that adds it back as a valid key sees the user's pick.
    return defaultValue;
  });

  const setValue = useCallback(
    (next: T): void => {
      safeWriteStorage(storageKey, next);
      setStateValue(next);
    },
    [storageKey],
  );

  return { value, setValue };
}
