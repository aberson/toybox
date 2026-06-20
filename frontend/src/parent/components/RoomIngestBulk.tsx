import type { JSX } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  extractRoomInUseDetail,
  extractRoomNameCollisionDetail,
  imageUrl,
  isAbortError,
} from "../api";
import type {
  ApiClient,
  BulkPhoto,
  Room,
  RoomBulkUploadResponse,
  RoomConfirmBulkRequest,
  RoomNameCollisionDetail,
} from "../api";

// Step 17: parent-facing bulk room-photo ingest UI. Two phases.
//
// Phase A — multi-pick:
//   The parent picks up to 50 photos. We POST them as one multipart
//   batch to /api/rooms/upload-bulk. The response carries a
//   ``batch_id`` plus a per-photo entry — vision suggestion if it
//   succeeded, ``vision_error`` if the per-photo Claude call failed,
//   or ``error`` if the file was rejected (validation / dedup).
//
// Phase B — review-and-save:
//   We render tabs grouped by suggested room label (plus an
//   ``Unassigned`` tab for ``vision_error`` photos and ``error``
//   photos that the parent can still place manually). Each photo card
//   shows the thumbnail, a label dropdown ("create new room" / pick
//   existing), and editable feature chips. The Submit button at the
//   bottom POSTs all assignments to /api/rooms/confirm-bulk in one go.
//
// On 409 ``room_label_collision`` we surface a modal the parent can
// dismiss to rename — they don't lose the rest of their edits.
//
// AbortController plumbing matches ToyIngest: a single ref spans the
// component's lifetime, every fetch carries the same signal, unmount
// aborts whatever's in flight, and there's no setState-on-unmounted.

const UNASSIGNED_TAB = "Unassigned";
const BULK_CAP = 50;

interface PhotoRow {
  staging_id: string;
  filename: string;
  image_hash: string;
  // The label the parent currently has the photo assigned to. Edited
  // freely as the parent types into the new-room input; the partition
  // logic below intentionally does NOT key tabs off this — see
  // ``assigned_tab`` for why.
  label: string;
  // Sticky tab placement. Set once at row creation (vision suggestion
  // or UNASSIGNED) and only mutated by an explicit parent action that
  // is known not to need keyboard focus (the existing-room dropdown).
  // Keying tabs off ``label`` would move the row to a new tab on every
  // keystroke in the new-label input, the row would unmount from the
  // current tabpanel, and the input would lose focus mid-typing —
  // forcing the parent to re-click between every letter.
  assigned_tab: string;
  // Per-photo feature chips. Always strings (the wire shape wraps each
  // in ``{name}``; we flatten on submit).
  features: string[];
  // Source-of-truth for the parent's room choice. Either an existing
  // room id (means "use this existing room") or null (means "create
  // new room with display_name = label").
  use_existing_room_id: string | null;
  // Pre-existing per-photo errors we still surface to the parent.
  error: string | null;
  vision_error: string | null;
  existing_room: Room | null;
}

function bulkPhotoToRow(photo: BulkPhoto): PhotoRow {
  // Photos with ``error`` (validation_failed / duplicate_*) carry no
  // staging_id and can't be confirmed. They get a placeholder row so
  // the parent sees them under Unassigned and can choose to ignore.
  const labelFromSuggestion = photo.suggested?.suggested_room_label ?? null;
  const fallbackLabel = labelFromSuggestion ?? UNASSIGNED_TAB;
  const isUpstreamFailure = photo.error !== null || photo.vision_error !== null;
  const features =
    photo.suggested?.features.map((f) => f.name) ?? [];
  return {
    staging_id: photo.staging_id,
    filename: photo.filename,
    image_hash: photo.image_hash,
    label: fallbackLabel,
    assigned_tab: isUpstreamFailure ? UNASSIGNED_TAB : fallbackLabel,
    features,
    use_existing_room_id: null,
    error: photo.error,
    vision_error: photo.vision_error,
    existing_room: photo.existing_room,
  };
}

function partitionByTab(rows: PhotoRow[]): Map<string, PhotoRow[]> {
  const grouped = new Map<string, PhotoRow[]>();
  for (const row of rows) {
    const list = grouped.get(row.assigned_tab) ?? [];
    list.push(row);
    grouped.set(row.assigned_tab, list);
  }
  return grouped;
}

function uploadErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body;
    if (typeof body === "object" && body !== null) {
      const rec = body as Record<string, unknown>;
      const detail = "detail" in rec ? rec["detail"] : rec;
      if (typeof detail === "object" && detail !== null) {
        const d = detail as Record<string, unknown>;
        const code = d["code"];
        if (code === "bulk_cap_exceeded") {
          const max = typeof d["max_files"] === "number" ? d["max_files"] : 50;
          return `Too many files — pick at most ${max}.`;
        }
        if (code === "bulk_empty") {
          return "Pick at least one photo.";
        }
      }
    }
    return `upload failed: ${err.status}`;
  }
  if (err instanceof Error) {
    return `upload failed: ${err.message}`;
  }
  return "upload failed";
}

export interface RoomIngestBulkProps {
  api: ApiClient;
}

export function RoomIngestBulk(props: RoomIngestBulkProps): JSX.Element {
  const { api } = props;

  // Existing rooms — populates the "assign to existing room" dropdowns.
  const [rooms, setRooms] = useState<Room[]>([]);
  const [roomsLoading, setRoomsLoading] = useState<boolean>(true);
  const [roomsError, setRoomsError] = useState<string | null>(null);

  // Phase B state.
  const [batch, setBatch] = useState<RoomBulkUploadResponse | null>(null);
  const [photoRows, setPhotoRows] = useState<PhotoRow[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);

  const [uploading, setUploading] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [topError, setTopError] = useState<string | null>(null);
  const [collision, setCollision] =
    useState<RoomNameCollisionDetail | null>(null);

  // Inline edit state for an existing room in the list. Null = no row
  // is being edited; otherwise the room id whose form is open.
  const [editingRoomId, setEditingRoomId] = useState<string | null>(null);
  const [editRoomName, setEditRoomName] = useState<string>("");
  const [editRoomNotes, setEditRoomNotes] = useState<string>("");
  // Phase X Step X1: free-form category for the room being edited.
  const [editRoomType, setEditRoomType] = useState<string>("");
  const [editRoomSubmitting, setEditRoomSubmitting] = useState<boolean>(false);
  const [editRoomError, setEditRoomError] = useState<string | null>(null);
  const [deletingRoomId, setDeletingRoomId] = useState<string | null>(null);
  const [roomRowError, setRoomRowError] = useState<string | null>(null);
  // Phase X Step X1: id of the room whose active-toggle PATCH is in
  // flight (so we disable just that control), mirroring the toy UX.
  const [togglingActiveId, setTogglingActiveId] = useState<string | null>(null);

  // AbortController spanning one mount of the editor. Recreated on each
  // mount inside the useEffect below — under React 18 StrictMode the
  // mount→cleanup→remount cycle would otherwise leave us with a
  // permanently-aborted signal and every fetch would silently reject
  // with AbortError. Callbacks read `aborterRef.current?.signal` at
  // call time so they always see the live controller.
  const aborterRef = useRef<AbortController | null>(null);

  const refetchRooms = useCallback(async (): Promise<void> => {
    setRoomsLoading(true);
    try {
      const resp = await api.listRooms({
        signal: aborterRef.current?.signal,
      });
      setRooms(resp.rooms);
      setRoomsError(null);
    } catch (err) {
      if (isAbortError(err)) return;
      const message = err instanceof Error ? err.message : "load failed";
      setRoomsError(message);
    } finally {
      setRoomsLoading(false);
    }
  }, [api]);

  useEffect(() => {
    const aborter = new AbortController();
    aborterRef.current = aborter;
    void refetchRooms();
    return () => {
      aborter.abort();
      if (aborterRef.current === aborter) {
        aborterRef.current = null;
      }
    };
  }, [refetchRooms]);

  const resetPhase = useCallback((): void => {
    setBatch(null);
    setPhotoRows([]);
    setActiveTab(null);
    setTopError(null);
    setCollision(null);
  }, []);

  const handleFiles = useCallback(
    async (files: File[]): Promise<void> => {
      if (files.length === 0) return;
      // L2: short-circuit a >50-file pick BEFORE we POST. Without
      // this the browser uploads 60×15 MB blobs only for the backend
      // to 413; the parent should see the cap message immediately.
      if (files.length > BULK_CAP) {
        resetPhase();
        setTopError(
          `Too many files — pick at most ${BULK_CAP}. You picked ${files.length}.`,
        );
        return;
      }
      resetPhase();
      setUploading(true);
      try {
        const resp = await api.uploadRoomsBulk(files, {
          signal: aborterRef.current?.signal,
        });
        setBatch(resp);
        const rows = resp.photos.map(bulkPhotoToRow);
        setPhotoRows(rows);
        const tabs = Array.from(partitionByTab(rows).keys());
        setActiveTab(tabs[0] ?? null);
      } catch (err) {
        if (isAbortError(err)) return;
        setTopError(uploadErrorMessage(err));
      } finally {
        setUploading(false);
      }
    },
    [api, resetPhase],
  );

  const onFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>): void => {
      const files = Array.from(e.target.files ?? []);
      // Reset the input so picking the same file twice still fires.
      e.target.value = "";
      void handleFiles(files);
    },
    [handleFiles],
  );

  const updateRow = useCallback(
    (
      stagingId: string,
      patch: Partial<PhotoRow>,
    ): void => {
      setPhotoRows((prev) =>
        prev.map((row) =>
          row.staging_id === stagingId ? { ...row, ...patch } : row,
        ),
      );
    },
    [],
  );

  const submit = useCallback(async (): Promise<void> => {
    // L3: re-entry guard. Without this, a parent who spams the
    // "save all" button (or hits Enter in a feature input) can fire
    // multiple in-flight confirm-bulks against the same staging ids,
    // and the second one reliably 404s once the first commits.
    if (submitting) return;
    if (batch === null) return;
    setSubmitting(true);
    setTopError(null);
    setCollision(null);
    // Build the assignments list. Photos without a staging_id (error
    // rows from the upload) are skipped — there's nothing to commit.
    const assignments = photoRows
      .filter((row) => row.staging_id !== "")
      .filter((row) => row.label !== UNASSIGNED_TAB || row.use_existing_room_id !== null)
      .map((row) => ({
        staging_id: row.staging_id,
        room_id: row.use_existing_room_id,
        new_room_label:
          row.use_existing_room_id !== null ? null : row.label,
        features: row.features.map((name) => ({ name })),
      }));
    if (assignments.length === 0) {
      setTopError("Assign at least one photo to a room before saving.");
      setSubmitting(false);
      return;
    }
    const body: RoomConfirmBulkRequest = {
      batch_id: batch.batch_id,
      assignments,
    };
    try {
      await api.confirmRoomsBulk(body, {
        signal: aborterRef.current?.signal,
      });
      await refetchRooms();
      resetPhase();
    } catch (err) {
      if (isAbortError(err)) return;
      const collide = extractRoomNameCollisionDetail(err);
      if (collide !== null) {
        setCollision(collide);
        // L5: jump the active tab to the offending label so the
        // parent's edit lands on the photos that triggered the
        // collision. ``label`` here is the case the server saw,
        // and ``partitionByTab`` keys tabs by the photo's current
        // label string verbatim — case-insensitive match keeps us
        // robust if the parent typed "living room" while the row
        // was tabbed under "Living Room".
        const targetLabel = collide.label;
        const matching = photoRows.find(
          (r) => r.label.toLowerCase() === targetLabel.toLowerCase(),
        );
        if (matching !== undefined) {
          setActiveTab(matching.label);
        }
        return;
      }
      if (err instanceof ApiError) {
        setTopError(`save failed: ${err.status}`);
      } else if (err instanceof Error) {
        setTopError(`save failed: ${err.message}`);
      } else {
        setTopError("save failed");
      }
    } finally {
      setSubmitting(false);
    }
  }, [api, batch, photoRows, refetchRooms, resetPhase, submitting]);

  // Group photos by tab. Memoised so the keyboard navigation across
  // tabs doesn't re-shuffle on every rerender.
  const grouped = useMemo(() => partitionByTab(photoRows), [photoRows]);
  const tabs = useMemo(() => Array.from(grouped.keys()), [grouped]);

  const openRoomEdit = useCallback((room: Room): void => {
    setEditingRoomId(room.id);
    setEditRoomName(room.display_name);
    setEditRoomNotes(room.notes ?? "");
    setEditRoomType(room.room_type ?? "");
    setEditRoomError(null);
  }, []);

  const cancelRoomEdit = useCallback((): void => {
    setEditingRoomId(null);
    setEditRoomName("");
    setEditRoomNotes("");
    setEditRoomType("");
    setEditRoomError(null);
  }, []);

  // Phase X Step X1: flip a room's active flag. Mirrors the toy
  // active-toggle UX — one PATCH, then refetch so the list re-renders
  // with the new state (and dimmed styling for inactive rooms).
  const toggleRoomActive = useCallback(
    async (room: Room): Promise<void> => {
      if (togglingActiveId !== null) return;
      setTogglingActiveId(room.id);
      setRoomRowError(null);
      try {
        await api.updateRoom(
          room.id,
          { active: !room.active },
          { signal: aborterRef.current?.signal },
        );
        await refetchRooms();
      } catch (err) {
        if (isAbortError(err)) return;
        if (err instanceof Error) {
          setRoomRowError(`toggle failed: ${err.message}`);
        } else {
          setRoomRowError("toggle failed");
        }
      } finally {
        setTogglingActiveId(null);
      }
    },
    [api, refetchRooms, togglingActiveId],
  );

  const replaceRoomPicture = useCallback(
    async (roomId: string, file: File): Promise<void> => {
      setEditRoomError(null);
      setEditRoomSubmitting(true);
      try {
        await api.replaceRoomImage(roomId, file, {
          signal: aborterRef.current?.signal,
        });
        await refetchRooms();
      } catch (err) {
        if (isAbortError(err)) return;
        if (err instanceof ApiError) {
          setEditRoomError(uploadErrorMessage(err));
        } else if (err instanceof Error) {
          setEditRoomError(`change picture failed: ${err.message}`);
        } else {
          setEditRoomError("change picture failed");
        }
      } finally {
        setEditRoomSubmitting(false);
      }
    },
    [api, refetchRooms],
  );

  const deleteRoomRow = useCallback(
    async (room: Room): Promise<void> => {
      if (deletingRoomId !== null) return;
      const ok = window.confirm(
        `Delete ${room.display_name}? This can't be undone.`,
      );
      if (!ok) return;
      setDeletingRoomId(room.id);
      setRoomRowError(null);
      try {
        await api.deleteRoom(room.id, {
          signal: aborterRef.current?.signal,
        });
        await refetchRooms();
        if (editingRoomId === room.id) cancelRoomEdit();
      } catch (err) {
        if (isAbortError(err)) return;
        const inUse = extractRoomInUseDetail(err);
        if (inUse !== null) {
          setRoomRowError(
            `Can't delete "${room.display_name}" — ${inUse.feature_count} ` +
              `feature${inUse.feature_count === 1 ? "" : "s"} still ` +
              `reference this room.`,
          );
        } else if (err instanceof Error) {
          setRoomRowError(`delete failed: ${err.message}`);
        } else {
          setRoomRowError("delete failed");
        }
      } finally {
        setDeletingRoomId(null);
      }
    },
    [api, cancelRoomEdit, deletingRoomId, editingRoomId, refetchRooms],
  );

  const submitRoomEdit = useCallback(async (): Promise<void> => {
    if (editingRoomId === null) return;
    setEditRoomSubmitting(true);
    setEditRoomError(null);
    try {
      const trimmedNotes = editRoomNotes.trim();
      const trimmedType = editRoomType.trim();
      await api.updateRoom(
        editingRoomId,
        {
          display_name: editRoomName,
          notes: trimmedNotes.length === 0 ? null : trimmedNotes,
          room_type: trimmedType.length === 0 ? null : trimmedType,
        },
        { signal: aborterRef.current?.signal },
      );
      await refetchRooms();
      cancelRoomEdit();
    } catch (err) {
      if (isAbortError(err)) return;
      if (err instanceof ApiError) {
        setEditRoomError(`save failed: ${err.status}`);
      } else if (err instanceof Error) {
        setEditRoomError(`save failed: ${err.message}`);
      } else {
        setEditRoomError("save failed");
      }
    } finally {
      setEditRoomSubmitting(false);
    }
  }, [
    api,
    cancelRoomEdit,
    editRoomName,
    editRoomNotes,
    editRoomType,
    editingRoomId,
    refetchRooms,
  ]);

  return (
    <section
      data-testid="room-ingest-bulk"
      style={{
        border: "1px solid #ccc",
        borderRadius: 6,
        padding: 16,
        margin: "12px 0",
        background: "#fff",
      }}
    >
      <h2 style={{ margin: 0, fontSize: 17, marginBottom: 8 }}>Rooms</h2>

      <p style={{ color: "#666", fontSize: 12, margin: "0 0 12px" }}>
        Pick up to 50 photos of rooms in your home. Each photo is sent
        to Claude AI for naming. Once saved, the images stay on this
        device.
      </p>

      {/* Phase A — file picker. */}
      {batch === null && (
        <div data-testid="room-picker" style={{ marginBottom: 8 }}>
          <label
            htmlFor="room-files-input"
            style={{ display: "block", fontSize: 13, marginBottom: 4 }}
          >
            Pick room photos (JPEG, PNG, WebP — up to 50, 15 MB each)
          </label>
          <input
            id="room-files-input"
            data-testid="room-files-input"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            multiple
            disabled={uploading}
            onChange={onFileInputChange}
          />
          {uploading && (
            <p
              data-testid="room-uploading"
              style={{ color: "#777", fontSize: 13 }}
            >
              uploading {/* count not shown — the response is one batch */}...
            </p>
          )}
        </div>
      )}

      {topError !== null && (
        <p
          data-testid="room-top-error"
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
          {topError}
        </p>
      )}

      {collision !== null && (
        <div
          data-testid="room-collision-modal"
          role="alertdialog"
          aria-labelledby="room-collision-title"
          style={{
            background: "#fff8e1",
            border: "1px solid #ffe082",
            padding: 12,
            borderRadius: 4,
            margin: "8px 0",
          }}
        >
          <h3
            id="room-collision-title"
            style={{ margin: 0, fontSize: 15, marginBottom: 6 }}
          >
            Room already exists
          </h3>
          <p style={{ margin: 0, fontSize: 13 }}>
            <strong data-testid="collision-room-name">
              {collision.existing_room.display_name}
            </strong>{" "}
            is already in your rooms list. Rename the new room or assign
            these photos to the existing one.
          </p>
          <button
            type="button"
            data-testid="dismiss-collision-button"
            onClick={() => setCollision(null)}
            style={{ marginTop: 8 }}
          >
            edit assignments
          </button>
        </div>
      )}

      {batch !== null && tabs.length > 0 && (
        <div data-testid="room-review">
          {/* Tabs */}
          <div
            role="tablist"
            data-testid="room-tablist"
            style={{
              display: "flex",
              gap: 4,
              borderBottom: "1px solid #ddd",
              marginBottom: 8,
              flexWrap: "wrap",
            }}
          >
            {tabs.map((tab) => {
              const count = grouped.get(tab)?.length ?? 0;
              const active = activeTab === tab;
              return (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  data-testid={`room-tab-${tab.replace(/\s+/g, "-").toLowerCase()}`}
                  aria-selected={active}
                  onClick={() => setActiveTab(tab)}
                  style={{
                    padding: "4px 8px",
                    fontSize: 13,
                    background: active ? "#e3f2fd" : "transparent",
                    border: "1px solid #ddd",
                    borderBottom: active ? "2px solid #1976d2" : "none",
                    borderRadius: "4px 4px 0 0",
                    cursor: "pointer",
                  }}
                >
                  {tab} ({count})
                </button>
              );
            })}
          </div>

          {activeTab !== null && grouped.get(activeTab) !== undefined && (
            <div
              role="tabpanel"
              data-testid="room-tabpanel"
              data-active-tab={activeTab}
            >
              {(grouped.get(activeTab) ?? []).map((row) => (
                <div
                  key={row.staging_id || row.filename}
                  data-testid="photo-card"
                  data-staging-id={row.staging_id}
                  style={{
                    border: "1px solid #eee",
                    borderRadius: 4,
                    padding: 8,
                    margin: "6px 0",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      gap: 8,
                    }}
                  >
                    <strong style={{ fontSize: 13 }}>{row.filename}</strong>
                    {row.error !== null && (
                      <span
                        data-testid="photo-error"
                        style={{ color: "#b71c1c", fontSize: 12 }}
                      >
                        {row.error}
                      </span>
                    )}
                    {row.error === null && row.vision_error !== null && (
                      <span
                        data-testid="photo-vision-error"
                        style={{ color: "#b85c00", fontSize: 12 }}
                      >
                        vision: {row.vision_error}
                      </span>
                    )}
                  </div>
                  {row.staging_id !== "" && (
                    <div style={{ marginTop: 6 }}>
                      <label
                        style={{
                          display: "block",
                          fontSize: 12,
                          color: "#666",
                        }}
                      >
                        Room
                      </label>
                      <select
                        data-testid="photo-room-select"
                        value={
                          row.use_existing_room_id !== null
                            ? `existing:${row.use_existing_room_id}`
                            : `new:${row.label}`
                        }
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value.startsWith("existing:")) {
                            const id = value.slice("existing:".length);
                            const room = rooms.find((r) => r.id === id);
                            const nextLabel = room?.display_name ?? row.label;
                            updateRow(row.staging_id, {
                              use_existing_room_id: id,
                              label: nextLabel,
                              assigned_tab: nextLabel,
                            });
                            // Follow the row to its new tab so the
                            // parent doesn't lose visual context after
                            // assigning to an existing room.
                            setActiveTab(nextLabel);
                          } else {
                            updateRow(row.staging_id, {
                              use_existing_room_id: null,
                            });
                          }
                        }}
                        style={{ width: "100%", padding: 4, fontSize: 13 }}
                      >
                        <option value={`new:${row.label}`}>
                          {row.label === UNASSIGNED_TAB
                            ? "Create new room…"
                            : `Create new: ${row.label}`}
                        </option>
                        {rooms.map((r) => (
                          <option
                            key={r.id}
                            value={`existing:${r.id}`}
                          >
                            Existing: {r.display_name}
                          </option>
                        ))}
                      </select>
                      {row.use_existing_room_id === null && (
                        <input
                          data-testid="photo-new-label"
                          type="text"
                          value={row.label === UNASSIGNED_TAB ? "" : row.label}
                          placeholder="Room name (e.g. Living Room)"
                          maxLength={40}
                          onChange={(e) =>
                            updateRow(row.staging_id, {
                              label:
                                e.target.value.length > 0
                                  ? e.target.value
                                  : UNASSIGNED_TAB,
                            })
                          }
                          style={{
                            width: "100%",
                            padding: 4,
                            marginTop: 4,
                            fontSize: 13,
                          }}
                        />
                      )}
                      <label
                        style={{
                          display: "block",
                          fontSize: 12,
                          color: "#666",
                          marginTop: 6,
                        }}
                      >
                        Features (comma-separated)
                      </label>
                      <input
                        data-testid="photo-features"
                        type="text"
                        value={row.features.join(", ")}
                        onChange={(e) =>
                          updateRow(row.staging_id, {
                            features: e.target.value
                              .split(",")
                              .map((s) => s.trim())
                              .filter((s) => s.length > 0),
                          })
                        }
                        style={{ width: "100%", padding: 4, fontSize: 13 }}
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
            <button
              type="button"
              data-testid="save-rooms-button"
              onClick={() => void submit()}
              disabled={submitting}
            >
              {submitting ? "saving..." : "save all"}
            </button>
            <button
              type="button"
              data-testid="cancel-rooms-button"
              onClick={resetPhase}
              disabled={submitting}
            >
              cancel
            </button>
          </div>
        </div>
      )}

      <hr style={{ margin: "16px 0 8px", border: "none", borderTop: "1px solid #eee" }} />

      {roomsLoading && (
        <p data-testid="rooms-loading" style={{ color: "#777", fontSize: 13 }}>
          loading rooms...
        </p>
      )}
      {roomsError !== null && (
        <p
          data-testid="rooms-list-error"
          role="alert"
          style={{ color: "#b71c1c", fontSize: 13 }}
        >
          {roomsError}
        </p>
      )}
      {!roomsLoading && rooms.length === 0 && (
        <p data-testid="rooms-empty" style={{ color: "#777", fontSize: 13 }}>
          No rooms yet. Add some above.
        </p>
      )}
      {roomRowError !== null && (
        <p
          data-testid="room-row-error"
          role="alert"
          style={{
            background: "#fdecea",
            border: "1px solid #f5c2c0",
            padding: 8,
            borderRadius: 4,
            fontSize: 13,
            margin: "4px 0",
          }}
        >
          {roomRowError}
        </p>
      )}
      {rooms.length > 0 && (
        <ul
          data-testid="rooms-list"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {rooms.map((r) => {
            const isEditing = editingRoomId === r.id;
            const thumb = imageUrl(r.image_path, r.image_hash);
            return (
              <li
                key={r.id}
                data-testid="room-row"
                data-room-id={r.id}
                data-room-active={r.active ? "true" : "false"}
                style={{
                  padding: "8px 0",
                  borderBottom: "1px solid #eee",
                  fontSize: 14,
                  opacity: r.active ? 1 : 0.5,
                }}
              >
                {!isEditing && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 8,
                    }}
                  >
                    {thumb !== null && (
                      <img
                        data-testid="room-thumb"
                        src={thumb}
                        alt=""
                        style={{
                          width: 36,
                          height: 36,
                          objectFit: "cover",
                          borderRadius: 4,
                          border: "1px solid #eee",
                          flexShrink: 0,
                        }}
                      />
                    )}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <strong>{r.display_name}</strong>
                      {r.room_type !== null && r.room_type.length > 0 && (
                        <span
                          data-testid="room-type-chip"
                          style={{
                            marginLeft: 6,
                            fontSize: 11,
                            color: "#1e40af",
                            background: "#dbeafe",
                            borderRadius: 10,
                            padding: "1px 8px",
                          }}
                        >
                          {r.room_type}
                        </span>
                      )}
                      {r.notes !== null && r.notes.length > 0 && (
                        <div
                          style={{
                            fontSize: 12,
                            color: "#666",
                            marginTop: 2,
                          }}
                        >
                          {r.notes}
                        </div>
                      )}
                    </div>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button
                        type="button"
                        data-testid="toggle-room-active-button"
                        aria-pressed={r.active}
                        disabled={togglingActiveId === r.id}
                        onClick={() => {
                          void toggleRoomActive(r);
                        }}
                        title={
                          r.active
                            ? "Deactivate this room (exclude from play-time selection)"
                            : "Activate this room"
                        }
                      >
                        {togglingActiveId === r.id
                          ? "..."
                          : r.active
                            ? "active"
                            : "inactive"}
                      </button>
                      <button
                        type="button"
                        data-testid="edit-room-button"
                        onClick={() => openRoomEdit(r)}
                      >
                        edit
                      </button>
                      <button
                        type="button"
                        data-testid="delete-room-button"
                        disabled={deletingRoomId === r.id}
                        onClick={() => {
                          void deleteRoomRow(r);
                        }}
                      >
                        {deletingRoomId === r.id ? "deleting..." : "delete"}
                      </button>
                    </div>
                  </div>
                )}
                {isEditing && (
                  <form
                    data-testid="room-edit-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void submitRoomEdit();
                    }}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr",
                      gap: 6,
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 8,
                      }}
                    >
                      {thumb !== null && (
                        <img
                          data-testid="room-edit-thumb"
                          src={thumb}
                          alt=""
                          style={{
                            width: 64,
                            height: 64,
                            objectFit: "cover",
                            borderRadius: 4,
                            border: "1px solid #eee",
                            flexShrink: 0,
                          }}
                        />
                      )}
                      <div>
                        <label
                          style={{
                            display: "block",
                            fontSize: 12,
                            color: "#666",
                          }}
                        >
                          Change picture
                        </label>
                        <input
                          data-testid="room-edit-picture-input"
                          type="file"
                          accept="image/jpeg,image/png,image/webp"
                          disabled={editRoomSubmitting}
                          onChange={(e) => {
                            const f = e.target.files?.[0] ?? null;
                            e.target.value = "";
                            if (f !== null) {
                              void replaceRoomPicture(r.id, f);
                            }
                          }}
                          style={{ fontSize: 12 }}
                        />
                      </div>
                    </div>
                    <div>
                      <label style={{ display: "block", fontSize: 12 }}>
                        Name
                      </label>
                      <input
                        data-testid="edit-room-name"
                        type="text"
                        required
                        maxLength={40}
                        value={editRoomName}
                        onChange={(e) => setEditRoomName(e.target.value)}
                        style={{ width: "100%", padding: 4 }}
                      />
                    </div>
                    <div>
                      <label style={{ display: "block", fontSize: 12 }}>
                        Type (category)
                      </label>
                      <input
                        data-testid="edit-room-type"
                        type="text"
                        maxLength={40}
                        placeholder="e.g. bedroom, kitchen"
                        value={editRoomType}
                        onChange={(e) => setEditRoomType(e.target.value)}
                        style={{ width: "100%", padding: 4 }}
                      />
                    </div>
                    <div>
                      <label style={{ display: "block", fontSize: 12 }}>
                        Notes
                      </label>
                      <textarea
                        data-testid="edit-room-notes"
                        rows={2}
                        maxLength={2000}
                        value={editRoomNotes}
                        onChange={(e) => setEditRoomNotes(e.target.value)}
                        style={{ width: "100%", padding: 4 }}
                      />
                    </div>
                    {editRoomError !== null && (
                      <p
                        data-testid="room-edit-error"
                        role="alert"
                        style={{
                          background: "#fdecea",
                          border: "1px solid #f5c2c0",
                          padding: 6,
                          borderRadius: 4,
                          fontSize: 12,
                          margin: 0,
                        }}
                      >
                        {editRoomError}
                      </p>
                    )}
                    <div style={{ display: "flex", gap: 6 }}>
                      <button
                        type="submit"
                        data-testid="save-room-edit-button"
                        disabled={editRoomSubmitting}
                      >
                        {editRoomSubmitting ? "saving..." : "save"}
                      </button>
                      <button
                        type="button"
                        data-testid="cancel-room-edit-button"
                        onClick={cancelRoomEdit}
                        disabled={editRoomSubmitting}
                      >
                        cancel
                      </button>
                    </div>
                  </form>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
