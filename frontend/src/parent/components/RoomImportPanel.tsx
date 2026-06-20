import type { JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, ROOM_TYPE_OPTIONS, isAbortError } from "../api";
import type { ApiClient, ImportRoomPlan, ProposedRoom } from "../api";

// Phase X Step X6: parent-facing listing-import UI, layered on the X5
// endpoints (POST /api/rooms/import/parse + /api/rooms/import/commit).
//
// Two phases, mirroring RoomIngestBulk's structure:
//
// Phase A — paste + parse:
//   The parent pastes a real-estate listing (Redfin-style HTML or a
//   plain newline list of photo URLs) into a textarea and clicks Parse.
//   We POST it to /api/rooms/import/parse (pure/offline server-side: no
//   network, no DB write) and get back proposed rooms + a de-duplicated
//   list of photo URLs.
//
// Phase B — review + commit:
//   We render an editable table, one row per proposed room. Each row
//   carries an editable display_name, a room_type dropdown
//   (ROOM_TYPE_OPTIONS — mirrors the backend vocabulary), a photo_url
//   picker populated from the parsed URLs (with a thumbnail preview +
//   a "Clear / N/A" option that sets photo_url=null), and an
//   active/"stay out" toggle. The Create button builds the
//   ImportRoomPlan[] and POSTs /api/rooms/import/commit; on success we
//   clear the panel and fire the rooms-refresh callback.
//
// The whole panel is collapsed behind an "Import from listing" button
// (the entry-point affordance) so it doesn't crowd the bulk-photo flow
// it sits alongside.
//
// AbortController plumbing matches RoomIngestBulk: one ref spans the
// mount, every fetch carries the same signal, unmount aborts in-flight,
// and there's no setState-on-unmounted.

const NA_PHOTO = "__na__";

// One editable table row. Distinct from the wire ``ImportRoomPlan`` so
// the picker's "N/A" sentinel doesn't leak onto the wire — we map
// ``photo_url`` back to ``null`` at commit time.
interface PlanRow {
  // Stable React key — proposals carry no id, so we mint one at parse
  // time. Never sent on the wire.
  key: string;
  display_name: string;
  room_type: string | null;
  active: boolean;
  // ``null`` is the N/A room (no image). Otherwise one of the parsed
  // ``photo_urls``.
  photo_url: string | null;
}

function proposedToRow(proposed: ProposedRoom, index: number): PlanRow {
  // The parser already returns a matching ``room_type`` from the
  // backend vocabulary; default it onto the dropdown when it's one we
  // recognise, else leave the row's type unset ("(no type)").
  const known = ROOM_TYPE_OPTIONS.some((o) => o.value === proposed.room_type);
  return {
    key: `proposed-${index}`,
    display_name: proposed.display_name,
    room_type: known ? proposed.room_type : null,
    active: true,
    photo_url: null,
  };
}

function errorMessage(err: unknown, verb: string): string {
  if (err instanceof ApiError) {
    const body = err.body;
    if (typeof body === "object" && body !== null) {
      const rec = body as Record<string, unknown>;
      const detail = "detail" in rec ? rec["detail"] : rec;
      if (typeof detail === "object" && detail !== null) {
        const d = detail as Record<string, unknown>;
        const code = d["code"];
        if (typeof code === "string") {
          return `${verb} failed: ${code}`;
        }
      }
    }
    return `${verb} failed: ${err.status}`;
  }
  if (err instanceof Error) {
    return `${verb} failed: ${err.message}`;
  }
  return `${verb} failed`;
}

export interface RoomImportPanelProps {
  api: ApiClient;
  // Fired after a successful commit so the parent surface refetches its
  // rooms list. RoomIngestBulk passes its ``refetchRooms`` here.
  onImported: () => void | Promise<void>;
}

export function RoomImportPanel(props: RoomImportPanelProps): JSX.Element {
  const { api, onImported } = props;

  const [open, setOpen] = useState<boolean>(false);
  const [content, setContent] = useState<string>("");
  const [photoUrls, setPhotoUrls] = useState<string[]>([]);
  const [rows, setRows] = useState<PlanRow[]>([]);
  const [parsed, setParsed] = useState<boolean>(false);

  const [parsing, setParsing] = useState<boolean>(false);
  const [committing, setCommitting] = useState<boolean>(false);
  const [parseError, setParseError] = useState<string | null>(null);
  const [commitError, setCommitError] = useState<string | null>(null);

  // AbortController spanning one mount (see RoomIngestBulk for the
  // StrictMode rationale). Callbacks read ``aborterRef.current?.signal``
  // at call time so they always see the live controller.
  const aborterRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const aborter = new AbortController();
    aborterRef.current = aborter;
    return () => {
      aborter.abort();
      if (aborterRef.current === aborter) {
        aborterRef.current = null;
      }
    };
  }, []);

  const resetParse = useCallback((): void => {
    setPhotoUrls([]);
    setRows([]);
    setParsed(false);
    setParseError(null);
    setCommitError(null);
  }, []);

  const parse = useCallback(async (): Promise<void> => {
    if (parsing) return;
    const trimmed = content.trim();
    if (trimmed.length === 0) {
      setParseError("Paste a listing first.");
      return;
    }
    setParsing(true);
    setParseError(null);
    setCommitError(null);
    try {
      const resp = await api.parseListing(content, {
        signal: aborterRef.current?.signal,
      });
      setPhotoUrls(resp.photo_urls);
      setRows(resp.proposed_rooms.map(proposedToRow));
      setParsed(true);
    } catch (err) {
      if (isAbortError(err)) return;
      setParseError(errorMessage(err, "parse"));
    } finally {
      setParsing(false);
    }
  }, [api, content, parsing]);

  const updateRow = useCallback(
    (key: string, patch: Partial<PlanRow>): void => {
      setRows((prev) =>
        prev.map((row) => (row.key === key ? { ...row, ...patch } : row)),
      );
    },
    [],
  );

  const commit = useCallback(async (): Promise<void> => {
    if (committing) return;
    if (rows.length === 0) return;
    // Reject rows the parent blanked out — the backend rejects an empty
    // display_name anyway, so we surface it inline rather than 422-ing.
    const blank = rows.some((row) => row.display_name.trim().length === 0);
    if (blank) {
      setCommitError("Every room needs a name.");
      return;
    }
    setCommitting(true);
    setCommitError(null);
    const plan: ImportRoomPlan[] = rows.map((row) => ({
      display_name: row.display_name.trim(),
      room_type: row.room_type,
      active: row.active,
      photo_url: row.photo_url,
    }));
    try {
      await api.commitRoomImport(plan, {
        signal: aborterRef.current?.signal,
      });
      await onImported();
      // Clear the whole panel on success.
      setContent("");
      resetParse();
      setOpen(false);
    } catch (err) {
      if (isAbortError(err)) return;
      setCommitError(errorMessage(err, "create"));
    } finally {
      setCommitting(false);
    }
  }, [api, committing, onImported, resetParse, rows]);

  const busy = parsing || committing;

  return (
    <section
      data-testid="room-import-panel"
      style={{
        border: "1px solid #ccc",
        borderRadius: 6,
        padding: 16,
        margin: "12px 0",
        background: "#fff",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 17 }}>Import rooms from a listing</h2>
        <button
          type="button"
          data-testid="toggle-import-panel-button"
          aria-expanded={open}
          onClick={() => {
            if (open) {
              // Collapsing discards an in-progress parse so re-opening
              // starts clean.
              setContent("");
              resetParse();
            }
            setOpen((prev) => !prev);
          }}
        >
          {open ? "close" : "Import from listing"}
        </button>
      </div>

      {open && (
        <div data-testid="room-import-body" style={{ marginTop: 12 }}>
          <p style={{ color: "#666", fontSize: 12, margin: "0 0 8px" }}>
            Paste a home listing (the page HTML or just a list of photo
            URLs). We propose the rooms; you edit them and pick photos
            before creating.
          </p>

          <label
            htmlFor="listing-content"
            style={{ display: "block", fontSize: 13, marginBottom: 4 }}
          >
            Listing content
          </label>
          <textarea
            id="listing-content"
            data-testid="listing-content-input"
            rows={6}
            value={content}
            disabled={busy}
            placeholder="Paste listing HTML or photo URLs (one per line)…"
            onChange={(e) => setContent(e.target.value)}
            style={{ width: "100%", padding: 6, fontSize: 13, boxSizing: "border-box" }}
          />

          <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
            <button
              type="button"
              data-testid="parse-listing-button"
              onClick={() => void parse()}
              disabled={busy}
            >
              {parsing ? "parsing..." : "Parse"}
            </button>
            {parsed && (
              <button
                type="button"
                data-testid="reset-parse-button"
                onClick={resetParse}
                disabled={busy}
              >
                clear results
              </button>
            )}
          </div>

          {parseError !== null && (
            <p
              data-testid="import-parse-error"
              role="alert"
              style={{
                background: "#fdecea",
                border: "1px solid #f5c2c0",
                padding: 8,
                borderRadius: 4,
                fontSize: 13,
                margin: "8px 0",
              }}
            >
              {parseError}
            </p>
          )}

          {parsed && rows.length === 0 && (
            <p
              data-testid="import-empty"
              style={{ color: "#777", fontSize: 13, marginTop: 8 }}
            >
              No rooms found in that listing.
            </p>
          )}

          {parsed && rows.length > 0 && (
            <div data-testid="import-review" style={{ marginTop: 12 }}>
              <table
                data-testid="import-rooms-table"
                style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
              >
                <thead>
                  <tr style={{ textAlign: "left", color: "#666" }}>
                    <th style={{ padding: 4 }}>Name</th>
                    <th style={{ padding: 4 }}>Type</th>
                    <th style={{ padding: 4 }}>Photo</th>
                    <th style={{ padding: 4 }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const thumb = row.photo_url;
                    return (
                      <tr
                        key={row.key}
                        data-testid="import-room-row"
                        data-room-key={row.key}
                        style={{ borderTop: "1px solid #eee" }}
                      >
                        <td style={{ padding: 4 }}>
                          <input
                            data-testid="import-room-name"
                            type="text"
                            maxLength={40}
                            value={row.display_name}
                            onChange={(e) =>
                              updateRow(row.key, {
                                display_name: e.target.value,
                              })
                            }
                            style={{ width: "100%", padding: 4 }}
                          />
                        </td>
                        <td style={{ padding: 4 }}>
                          <select
                            data-testid="import-room-type"
                            value={row.room_type ?? ""}
                            onChange={(e) =>
                              updateRow(row.key, {
                                room_type:
                                  e.target.value.length === 0
                                    ? null
                                    : e.target.value,
                              })
                            }
                            style={{ width: "100%", padding: 4 }}
                          >
                            <option value="">(no type)</option>
                            {ROOM_TYPE_OPTIONS.map((opt) => (
                              <option key={opt.value} value={opt.value}>
                                {opt.label}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td style={{ padding: 4 }}>
                          <select
                            data-testid="import-room-photo"
                            value={row.photo_url ?? NA_PHOTO}
                            onChange={(e) =>
                              updateRow(row.key, {
                                photo_url:
                                  e.target.value === NA_PHOTO
                                    ? null
                                    : e.target.value,
                              })
                            }
                            style={{ width: "100%", padding: 4 }}
                          >
                            <option value={NA_PHOTO}>Clear / N/A</option>
                            {photoUrls.map((url) => (
                              <option key={url} value={url}>
                                {url}
                              </option>
                            ))}
                          </select>
                          {thumb !== null && (
                            <img
                              data-testid="import-room-thumb"
                              src={thumb}
                              alt=""
                              style={{
                                marginTop: 4,
                                width: 48,
                                height: 48,
                                objectFit: "cover",
                                borderRadius: 4,
                                border: "1px solid #eee",
                                display: "block",
                              }}
                            />
                          )}
                        </td>
                        <td style={{ padding: 4 }}>
                          <button
                            type="button"
                            data-testid="import-room-active"
                            aria-pressed={row.active}
                            onClick={() =>
                              updateRow(row.key, { active: !row.active })
                            }
                            title={
                              row.active
                                ? "Deactivate (room exists but stay out of play)"
                                : "Activate this room"
                            }
                          >
                            {row.active ? "active" : "stay out"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {commitError !== null && (
                <p
                  data-testid="import-commit-error"
                  role="alert"
                  style={{
                    background: "#fdecea",
                    border: "1px solid #f5c2c0",
                    padding: 8,
                    borderRadius: 4,
                    fontSize: 13,
                    margin: "8px 0",
                  }}
                >
                  {commitError}
                </p>
              )}

              <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
                <button
                  type="button"
                  data-testid="create-rooms-button"
                  onClick={() => void commit()}
                  disabled={busy}
                >
                  {committing ? "creating..." : "Create rooms"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
