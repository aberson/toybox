// Phase F Step F8: parent-facing 2x5 sprite grid + per-slot
// regenerate buttons + capability-disabled banner. Reuses the F7
// ``ToyActionSprite`` for the ``done`` cells; renders a status badge
// ("queued", "running…", "failed") for transient + terminal-fail
// states; renders an empty regenerate-only cell for ``not_started``
// and ``superseded``.
//
// Surface contract (per plan §F8):
//   1. 10 cells, in canonical ACTION_SLOTS order — the parent always
//      sees the same grid shape regardless of how many jobs have
//      enqueued.
//   2. Per-cell: sprite (if done) + small "regenerate" button below.
//      Buttons are disabled while a slot is in-flight (queued /
//      running) so a double-click can't enqueue a duplicate.
//   3. Top of grid: "regenerate all" + "N/10 done" count.
//   4. ``disabledReason`` (capability gate closed for ENV_DISABLED)
//      renders an operator-actionable banner and disables every button.
//   5. F.5-3a: ``compositeOnlyMode`` (capability gate closed for
//      NO_CUDA / LOW_VRAM / MISSING_CHECKPOINTS) renders a softer
//      banner explaining sprites will be lower fidelity; buttons
//      stay enabled (the worker dispatches to the composite path).

import type { JSX } from "react";

import { ToyActionSprite } from "../../child/components/ToyActionSprite";
import { ACTION_SLOTS } from "../api";
import type { ToyActionRow, ToyActionStatus } from "../api";

export interface ToyActionGridProps {
  // The toy whose actions this grid represents. Threaded through the
  // sprite component so each cell's URL composes correctly.
  toyId: string;
  // Ten rows in canonical ACTION_SLOTS order. The parent UI guarantees
  // this shape (server returns 10 rows; reducer merges per-slot); we
  // still tolerate fewer rows by rendering ``not_started`` placeholders
  // for the missing slots.
  actions: ToyActionRow[];
  // Optional toy display name. Forwarded to the ToyActionSprite for
  // accessible alt text on done cells.
  toyDisplayName?: string;
  // Per-slot regenerate handler. Fired with the slot key (e.g.
  // "looking"). Returning a Promise lets the caller drive an
  // optimistic-disable UI; the grid itself doesn't await.
  onRegenerateSlot: (slot: string) => void | Promise<void>;
  // "Regenerate all" handler. The grid wraps the click in a guard so
  // a double-click can't fire two enqueue rounds in a single render.
  onRegenerateAll: () => void | Promise<void>;
  // When set, renders an operator-actionable banner at the top and
  // disables every button. The capability gate (image-gen disabled
  // because no GPU / Phase E LLM is holding VRAM / etc.) is the
  // canonical caller. Existing ``done`` rows still render normally —
  // capability is only load-bearing for *new* generation requests.
  disabledReason?: string;
  // F.5-3a: when ``true``, the capability gate is closed for a
  // non-env-disabled reason (no CUDA / low VRAM / missing checkpoints)
  // and the worker is dispatching the Tier C composite fallback.
  // Renders a softer info banner; buttons stay enabled because
  // composite generation IS available.
  compositeOnlyMode?: boolean;
}

// Per-cell render branch. Returns the sprite for ``done``, a status
// badge for transient or terminal-fail rows, and null (empty cell)
// for the ``not_started`` / ``superseded`` placeholder.
function renderCellContents(
  toyId: string,
  row: ToyActionRow,
  toyDisplayName: string | undefined,
): JSX.Element | null {
  if (row.status === "done" && row.image_path !== null) {
    return (
      <ToyActionSprite
        toyId={toyId}
        slot={row.slot}
        toyDisplayName={toyDisplayName}
        size={88}
        style={{ margin: "0 auto" }}
      />
    );
  }
  if (row.status === "queued") {
    return (
      <div
        data-testid={`toy-action-status-${row.slot}`}
        data-status="queued"
        style={statusBadgeStyle("queued")}
      >
        queued
      </div>
    );
  }
  if (row.status === "running") {
    return (
      <div
        data-testid={`toy-action-status-${row.slot}`}
        data-status="running"
        style={statusBadgeStyle("running")}
      >
        running…
      </div>
    );
  }
  if (row.status === "failed") {
    return (
      <div
        data-testid={`toy-action-status-${row.slot}`}
        data-status="failed"
        title={row.error_msg ?? "generation failed"}
        style={statusBadgeStyle("failed")}
      >
        failed
      </div>
    );
  }
  // ``not_started`` and ``superseded`` render as an empty cell so the
  // regenerate button is the only affordance — there's nothing to
  // show until the worker picks the slot up.
  return null;
}

function statusBadgeStyle(status: ToyActionStatus): React.CSSProperties {
  const palette: Record<string, { bg: string; fg: string; border: string }> = {
    queued: { bg: "#eef2f7", fg: "#2c3e50", border: "#cdd5df" },
    running: { bg: "#e3f2fd", fg: "#0d47a1", border: "#90caf9" },
    failed: { bg: "#fdecea", fg: "#b71c1c", border: "#f5c2c0" },
  };
  const colors = palette[status] ?? palette["queued"]!;
  return {
    display: "inline-block",
    padding: "4px 8px",
    borderRadius: 4,
    fontSize: 11,
    fontWeight: 600,
    background: colors.bg,
    color: colors.fg,
    border: `1px solid ${colors.border}`,
  };
}

// "Regenerate" button enabled state. The button is disabled when the
// grid is capability-disabled OR the slot is currently in-flight
// (queued / running). A failed / done / not_started / superseded row
// is always re-runnable.
function isSlotActionable(status: ToyActionStatus): boolean {
  return status !== "queued" && status !== "running";
}

export function ToyActionGrid(props: ToyActionGridProps): JSX.Element {
  const {
    toyId,
    actions,
    toyDisplayName,
    onRegenerateSlot,
    onRegenerateAll,
    disabledReason,
    compositeOnlyMode,
  } = props;

  // Build the canonical-order display rows. We index by slot so out-
  // of-order arrays + missing slots both render correctly. A missing
  // slot synthesizes a ``not_started`` placeholder so the grid is
  // always 10 cells.
  const bySlot = new Map<string, ToyActionRow>();
  for (const row of actions) {
    bySlot.set(row.slot, row);
  }
  const displayRows: ToyActionRow[] = ACTION_SLOTS.map((slot) => {
    const existing = bySlot.get(slot);
    if (existing !== undefined) return existing;
    return {
      toy_id: toyId,
      slot,
      status: "not_started",
      image_path: null,
      seed: null,
      error_msg: null,
      updated_at: "",
    };
  });

  const doneCount = displayRows.filter((r) => r.status === "done").length;
  const isDisabled = disabledReason !== undefined && disabledReason.length > 0;

  return (
    <section
      data-testid="toy-action-grid"
      data-toy-id={toyId}
      style={{
        border: "1px solid #ddd",
        borderRadius: 6,
        padding: 12,
        marginTop: 12,
        background: "#fafafa",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
        <h3 style={{ margin: 0, fontSize: 14 }}>Action sprites</h3>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            data-testid="toy-action-grid-count"
            style={{ fontSize: 12, color: "#555" }}
          >
            {doneCount}/10 done
          </span>
          <button
            type="button"
            data-testid="toy-action-grid-regenerate-all"
            onClick={() => {
              void onRegenerateAll();
            }}
            disabled={isDisabled}
            style={{ fontSize: 12 }}
          >
            regenerate all
          </button>
        </div>
      </div>

      {isDisabled && (
        <p
          data-testid="toy-action-grid-disabled-banner"
          role="alert"
          style={{
            background: "#fff8e1",
            border: "1px solid #ffe082",
            padding: 8,
            borderRadius: 4,
            fontSize: 12,
            margin: "0 0 8px",
          }}
        >
          Image generation disabled: {disabledReason}
        </p>
      )}

      {!isDisabled && compositeOnlyMode === true && (
        <p
          data-testid="toy-action-grid-composite-only-banner"
          role="status"
          style={{
            background: "#e8f4f8",
            border: "1px solid #b6dce8",
            padding: 8,
            borderRadius: 4,
            fontSize: 12,
            margin: "0 0 8px",
          }}
        >
          running in composite-only mode — sprites will be lower fidelity
        </p>
      )}

      <div
        data-testid="toy-action-grid-cells"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(5, 1fr)",
          gap: 8,
        }}
      >
        {displayRows.map((row) => {
          const slotEnabled = !isDisabled && isSlotActionable(row.status);
          return (
            <div
              key={row.slot}
              data-testid={`toy-action-cell-${row.slot}`}
              data-slot={row.slot}
              data-status={row.status}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 4,
                padding: 6,
                border: "1px solid #eee",
                borderRadius: 4,
                background: "#fff",
                minHeight: 132,
              }}
            >
              <div
                style={{
                  width: "100%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexGrow: 1,
                }}
              >
                {renderCellContents(toyId, row, toyDisplayName)}
              </div>
              <div
                style={{
                  fontSize: 10,
                  color: "#666",
                  textTransform: "lowercase",
                }}
              >
                {row.slot}
              </div>
              <button
                type="button"
                data-testid={`toy-action-regenerate-${row.slot}`}
                onClick={() => {
                  void onRegenerateSlot(row.slot);
                }}
                disabled={!slotEnabled}
                style={{ fontSize: 10, padding: "2px 6px" }}
              >
                regenerate
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
}
