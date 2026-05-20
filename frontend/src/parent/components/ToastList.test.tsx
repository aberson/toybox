import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useParentStore } from "../store";

import { ToastList } from "./ToastList";

describe("ToastList", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useParentStore.setState((s) => ({ ...s, toasts: [], nextToastId: 1 }));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("renders nothing when toasts is empty", () => {
    const { container } = render(<ToastList toasts={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("auto-dismisses an info toast after 5s", () => {
    useParentStore.getState().pushToast("info", "activity ended");
    const toasts = useParentStore.getState().toasts;
    render(<ToastList toasts={toasts} />);
    expect(screen.getByText("activity ended")).not.toBeNull();

    act(() => {
      vi.advanceTimersByTime(4999);
    });
    expect(useParentStore.getState().toasts).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(useParentStore.getState().toasts).toHaveLength(0);
  });

  it("keeps a warning toast past 5s (sticky)", () => {
    useParentStore.getState().pushToast("warning", "version conflict");
    const toasts = useParentStore.getState().toasts;
    render(<ToastList toasts={toasts} />);

    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(useParentStore.getState().toasts).toHaveLength(1);
    expect(useParentStore.getState().toasts[0]?.message).toBe(
      "version conflict",
    );
  });

  it("keeps an error toast past 5s (sticky)", () => {
    useParentStore.getState().pushToast("error", "trigger failed");
    const toasts = useParentStore.getState().toasts;
    render(<ToastList toasts={toasts} />);

    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(useParentStore.getState().toasts).toHaveLength(1);
  });

  it("dismiss button removes a toast immediately", async () => {
    useParentStore.getState().pushToast("info", "first");
    const toasts = useParentStore.getState().toasts;
    render(<ToastList toasts={toasts} />);

    const button = screen.getByRole("button", { name: /dismiss/i });
    act(() => {
      button.click();
    });
    expect(useParentStore.getState().toasts).toHaveLength(0);
  });

  it("auto-dismisses multiple info toasts independently", () => {
    useParentStore.getState().pushToast("info", "first");
    useParentStore.getState().pushToast("info", "second");
    const toasts = useParentStore.getState().toasts;
    render(<ToastList toasts={toasts} />);
    expect(useParentStore.getState().toasts).toHaveLength(2);

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(useParentStore.getState().toasts).toHaveLength(0);
  });

  it("mixed kinds: info expires, warning stays", () => {
    useParentStore.getState().pushToast("info", "activity ended");
    useParentStore.getState().pushToast("warning", "stay put");
    const toasts = useParentStore.getState().toasts;
    render(<ToastList toasts={toasts} />);
    expect(useParentStore.getState().toasts).toHaveLength(2);

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    const remaining = useParentStore.getState().toasts;
    expect(remaining).toHaveLength(1);
    expect(remaining[0]?.message).toBe("stay put");
  });
});
