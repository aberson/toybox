import { useEffect, type JSX } from "react";

import { useParentStore } from "../store";

const TOAST_INFO_TTL_MS = 5000;

interface ToastShape {
  id: number;
  kind: "info" | "warning" | "error";
  message: string;
}

interface ToastItemProps {
  toast: ToastShape;
  onDismiss: (id: number) => void;
}

function ToastItem({ toast, onDismiss }: ToastItemProps): JSX.Element {
  // Auto-expire info toasts after 5s. Warning/error stay sticky so the
  // parent doesn't miss a real problem (e.g. mute error, version
  // conflict) by looking away for a few seconds.
  useEffect(() => {
    if (toast.kind !== "info") return;
    const timer = window.setTimeout(() => {
      onDismiss(toast.id);
    }, TOAST_INFO_TTL_MS);
    return () => window.clearTimeout(timer);
  }, [toast.id, toast.kind, onDismiss]);

  return (
    <div
      key={toast.id}
      role="status"
      data-toast-kind={toast.kind}
      data-testid={`toast-${toast.id}`}
      style={{
        padding: 8,
        margin: "4px 0",
        background:
          toast.kind === "error"
            ? "#fdecea"
            : toast.kind === "warning"
              ? "#fff8e1"
              : "#e3f2fd",
        border: "1px solid #ddd",
        borderRadius: 4,
        fontSize: 13,
      }}
    >
      {toast.message}
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        style={{ marginLeft: 8 }}
      >
        dismiss
      </button>
    </div>
  );
}

export interface ToastListProps {
  toasts: readonly ToastShape[];
}

export function ToastList({ toasts }: ToastListProps): JSX.Element | null {
  const dismissToast = useParentStore((s) => s.dismissToast);
  if (toasts.length === 0) return null;
  return (
    <div data-testid="toasts" style={{ marginTop: 16 }}>
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={dismissToast} />
      ))}
    </div>
  );
}
