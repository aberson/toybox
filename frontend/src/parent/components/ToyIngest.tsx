import type { JSX } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ROLE_DISPLAY_NAMES,
  type RoleName,
} from "../../shared/types";
import {
  ApiError,
  extractToyImageExistsDetail,
  extractValidationErrors,
  imageUrl,
  isAbortError,
} from "../api";
import type {
  ApiClient,
  Toy,
  ToyActionsCapability,
  ToyUploadResponse,
  ValidationFieldError,
} from "../api";
import { useParentStore } from "../store";
import { ToyActionGrid } from "./ToyActionGrid";

// Sorted RoleName list driven by ROLE_DISPLAY_NAMES — the single
// source of truth for the role taxonomy lives in
// ``src/toybox/activities/roles.py`` (per code-quality.md §2).
// Sorting at module load keeps the popover order stable.
const ROLE_NAMES_SORTED: RoleName[] = (
  Object.keys(ROLE_DISPLAY_NAMES) as RoleName[]
).sort();

// Step 16: parent-facing toy ingest UI. The component runs in two
// phases.
//
// Phase A — pick-a-file:
//   The parent drops or browses to one image. We POST it to
//   /api/toys/upload, which validates + dedups + (when capable) calls
//   Claude to suggest fields. The response carries a ``staging_id``
//   we hand back to the backend on confirm.
//
// Phase B — review-and-save:
//   We render the suggested ``display_name`` + ``tags`` as editable
//   inputs, plus a banner when vision failed/skipped (so the parent
//   knows to fill in the fields manually). On Save we POST
//   /api/toys with the staging_id + edited fields.
//
// Errors are surfaced inline:
//   * 415/413 on upload → top-level banner (with the error code)
//   * 409 on upload     → "this image is already in your library"
//                         banner with a button that closes the form
//   * 422 on confirm    → per-field errors under the inputs (mirrors
//                         ChildProfileEditor)
//
// AbortController plumbing matches ChildProfileEditor: a single ref
// spans the component's lifetime, every fetch carries the same signal,
// and unmount aborts whatever's in flight.

interface FormState {
  display_name: string;
  tags: string;
  // Comma-separated raw text — the only TextField shape that
  // round-trips cleanly. Submission splits on commas + trims.
  allowed_roles: string[];
  // Per-toy role restriction (post-K). Empty array = "unrestricted"
  // (default). Items must be ``RoleName`` values — typed as
  // ``string[]`` here so the popover can write/read freely without
  // re-narrowing in every handler; the validator on the backend
  // rejects unknown role names with HTTP 422.
}

const EMPTY_FORM: FormState = {
  display_name: "",
  tags: "",
  allowed_roles: [],
};

function suggestionToForm(suggestion: ToyUploadResponse["suggested"]): FormState {
  if (suggestion === null) return EMPTY_FORM;
  return {
    display_name: suggestion.display_name,
    tags: suggestion.tags.join(", "),
    allowed_roles: [],
  };
}

function parseTags(raw: string): string[] {
  return raw
    .split(",")
    .map((t) => t.trim())
    .filter((t) => t.length > 0);
}

function fieldErrorMap(
  errors: ValidationFieldError[] | null,
): Record<string, string> {
  if (errors === null) return {};
  const map: Record<string, string> = {};
  for (const e of errors) {
    const field = e.loc.length >= 2 ? String(e.loc[1]) : String(e.loc[0]);
    if (!(field in map)) {
      map[field] = e.msg;
    }
  }
  return map;
}

function visionErrorMessage(reason: string | null, skipped: boolean): string | null {
  if (skipped) {
    return (
      "Couldn't auto-fill — Claude isn't reachable right now. " +
      "Please fill the fields manually."
    );
  }
  if (reason === null) return null;
  switch (reason) {
    case "rate_limited":
      return "Couldn't auto-fill — Claude is rate-limited. Please fill manually.";
    case "timeout":
      return "Couldn't auto-fill — vision call timed out. Please fill manually.";
    case "malformed":
      return (
        "Couldn't auto-fill — vision returned an unreadable response. " +
        "Please fill manually."
      );
    default:
      return "Couldn't auto-fill — please fill manually.";
  }
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
        if (code === "upload_too_large") {
          return "Image is too large (max 15 MB).";
        }
        if (code === "upload_too_large_dimensions") {
          return "Image dimensions are too large (max 8000 × 8000).";
        }
        if (code === "upload_bad_mime") {
          return "Unsupported image format. Please use JPEG, PNG, or WebP.";
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

export interface ToyIngestProps {
  api: ApiClient;
}

export function ToyIngest(props: ToyIngestProps): JSX.Element {
  const { api } = props;

  // Toy list (so the parent sees what's already in the library).
  const [toys, setToys] = useState<Toy[]>([]);
  const [listLoading, setListLoading] = useState<boolean>(true);
  const [listError, setListError] = useState<string | null>(null);

  // Phase B state — populated after a successful upload.
  const [upload, setUpload] = useState<ToyUploadResponse | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [uploading, setUploading] = useState<boolean>(false);
  const [topError, setTopError] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState<Toy | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // Inline edit state for an existing toy in the list. Null = no row
  // is being edited; otherwise the row id whose form is open.
  const [editingToyId, setEditingToyId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<FormState>(EMPTY_FORM);
  const [editSubmitting, setEditSubmitting] = useState<boolean>(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [editFieldErrors, setEditFieldErrors] = useState<
    Record<string, string>
  >({});
  const [archivingId, setArchivingId] = useState<string | null>(null);
  // Migration 0018: per-toy active/inactive toggle. Tracks the id of
  // the toy whose PATCH is in flight so we can disable the control +
  // surface a brief "..." label without locking out the rest of the
  // list. Null = no toggle currently in flight.
  const [togglingActiveId, setTogglingActiveId] = useState<string | null>(
    null,
  );

  // Sort mode for the toy list. "active" puts active toys first
  // (alpha within each group); "name" is pure alpha A→Z. Default is
  // "active" so the list the parent operates on most is at the top.
  // Auto-resort on toggle is free — toggleToyActive calls refetchToys
  // which replaces `toys`, and the useMemo below recomputes.
  const [sortMode, setSortMode] = useState<"active" | "name">("active");

  const sortedToys = useMemo<Toy[]>(() => {
    const copy = [...toys];
    copy.sort((a, b) => {
      if (sortMode === "active" && a.active !== b.active) {
        return a.active ? -1 : 1;
      }
      return a.display_name.localeCompare(b.display_name);
    });
    return copy;
  }, [toys, sortMode]);

  // Per-toy allowed-roles popover. Closed by default; toggled by the
  // "Allowed roles" button on the edit form. The popover renders the
  // 10 ``RoleName`` checkboxes (sorted) — selected items also render
  // as removable chips below the button so the parent always sees
  // their selection without re-opening the popover.
  const [editRolePopoverOpen, setEditRolePopoverOpen] =
    useState<boolean>(false);

  // Phase F Step F8: track the id of the toy whose action grid should
  // render below the form. Set to the freshly-committed toy id after
  // a successful save; mirrors what the toy-edit flow surfaces when
  // ``editingToyId`` is non-null. Cleared on resetPhase so picking a
  // new file gets a clean form.
  const [justCommittedToyId, setJustCommittedToyId] = useState<string | null>(
    null,
  );
  // Capability snapshot keyed by toy_id — ``ToyActionsResponse``
  // bundles the capability gate state. We cache it locally rather
  // than redoing a /api/health probe so the disabled-banner reason
  // matches what the actions endpoint returned.
  const [toyCapabilities, setToyCapabilities] = useState<
    Record<string, ToyActionsCapability>
  >({});
  // F.5-3a: per-toy ``mode`` field from the actions endpoint.
  // ``"composite_only"`` → render the Tier C banner on the grid.
  const [toyModes, setToyModes] = useState<Record<string, string | null>>({});

  // Selector: pull the per-toy slot map out of the zustand store so
  // grid cells re-render as ws envelopes arrive. ``shallow``-style
  // equality isn't needed — zustand defaults to ===, and we replace
  // the inner object on each merge.
  const toyActions = useParentStore((s) => s.toyActions);

  // AbortController spanning one mount of the editor. Recreated on each
  // mount inside the useEffect below — under React 18 StrictMode the
  // mount→cleanup→remount cycle would otherwise leave us with a
  // permanently-aborted signal and every fetch would silently reject
  // with AbortError. Callbacks read `aborterRef.current?.signal` at
  // call time so they always see the live controller.
  const aborterRef = useRef<AbortController | null>(null);

  // Track the preview URL so we can revoke it on phase-reset / unmount
  // (URL.createObjectURL leaks until revoked).
  const previewRef = useRef<string | null>(null);

  const refetchToys = useCallback(async (): Promise<void> => {
    setListLoading(true);
    try {
      const resp = await api.listToys({ signal: aborterRef.current?.signal });
      setToys(resp.toys);
      setListError(null);
    } catch (err) {
      if (isAbortError(err)) return;
      const message = err instanceof Error ? err.message : "load failed";
      setListError(message);
    } finally {
      setListLoading(false);
    }
  }, [api]);

  useEffect(() => {
    const aborter = new AbortController();
    aborterRef.current = aborter;
    void refetchToys();
    return () => {
      aborter.abort();
      if (aborterRef.current === aborter) {
        aborterRef.current = null;
      }
      if (previewRef.current !== null) {
        URL.revokeObjectURL(previewRef.current);
        previewRef.current = null;
      }
    };
  }, [refetchToys]);

  const resetPhase = useCallback((): void => {
    setUpload(null);
    setForm(EMPTY_FORM);
    setTopError(null);
    setDuplicate(null);
    setFieldErrors({});
    setJustCommittedToyId(null);
    if (previewRef.current !== null) {
      URL.revokeObjectURL(previewRef.current);
      previewRef.current = null;
    }
    setPreviewUrl(null);
  }, []);

  // Phase F Step F8: pull the action grid + capability snapshot for
  // a single toy and seed the store. Best-effort — a 4xx/5xx is
  // swallowed (logged via the editError surface only when the caller
  // is the edit flow); the grid renders with whatever ws envelopes
  // populate the slot map, so a transient failure here just means
  // the operator may see an empty grid until the next ws push.
  const loadToyActions = useCallback(
    async (toyId: string): Promise<void> => {
      try {
        const resp = await api.listToyActions(toyId, {
          signal: aborterRef.current?.signal,
        });
        useParentStore.getState().setToyActions(toyId, resp.actions);
        setToyCapabilities((prev) => ({
          ...prev,
          [toyId]: resp.capability,
        }));
        setToyModes((prev) => ({
          ...prev,
          [toyId]: resp.mode ?? null,
        }));
      } catch (err) {
        if (isAbortError(err)) return;
        // Non-fatal — the grid still renders from whatever ws push
        // has arrived (or as 10 ``not_started`` placeholders).
      }
    },
    [api],
  );

  const handleRegenerateAll = useCallback(
    async (toyId: string): Promise<void> => {
      try {
        const resp = await api.regenerateAllActions(toyId, {
          signal: aborterRef.current?.signal,
        });
        setToyModes((prev) => ({
          ...prev,
          [toyId]: resp.mode ?? null,
        }));
      } catch (err) {
        if (isAbortError(err)) return;
        const message =
          err instanceof Error ? err.message : "regenerate failed";
        useParentStore
          .getState()
          .pushToast("error", `regenerate all: ${message}`);
      }
    },
    [api],
  );

  const handleRegenerateSlot = useCallback(
    async (toyId: string, slot: string): Promise<void> => {
      try {
        const resp = await api.regenerateActionSlot(toyId, slot, {
          signal: aborterRef.current?.signal,
        });
        setToyModes((prev) => ({
          ...prev,
          [toyId]: resp.mode ?? null,
        }));
      } catch (err) {
        if (isAbortError(err)) return;
        const message =
          err instanceof Error ? err.message : "regenerate failed";
        useParentStore
          .getState()
          .pushToast("error", `regenerate ${slot}: ${message}`);
      }
    },
    [api],
  );

  const handleFile = useCallback(
    async (file: File): Promise<void> => {
      resetPhase();
      setUploading(true);
      // Build a preview URL from the picked file — we don't have a
      // server URL until the parent confirms. ``URL.createObjectURL``
      // leaks until ``revokeObjectURL`` is called, so we track the
      // current URL in ``previewRef`` and revoke it on every error
      // branch (and on phase reset / unmount).
      const objectUrl = URL.createObjectURL(file);
      previewRef.current = objectUrl;
      setPreviewUrl(objectUrl);
      let kept = false;
      try {
        const resp = await api.uploadToyPhoto(file, {
          signal: aborterRef.current?.signal,
        });
        setUpload(resp);
        setForm(suggestionToForm(resp.suggested));
        // Successful upload — phase B owns the preview now; don't
        // revoke it in the finally below.
        kept = true;
      } catch (err) {
        if (isAbortError(err)) {
          // Aborts get cleaned up by the unmount effect; skip the
          // finally-revoke so we don't double-revoke.
          kept = true;
          return;
        }
        const dup = extractToyImageExistsDetail(err);
        if (dup !== null) {
          setDuplicate(dup.existing_toy);
          // Fall through to the finally block, which revokes + clears
          // the preview state so the duplicate banner reads cleanly.
          return;
        }
        setTopError(uploadErrorMessage(err));
      } finally {
        if (!kept && previewRef.current !== null) {
          URL.revokeObjectURL(previewRef.current);
          previewRef.current = null;
          setPreviewUrl(null);
        }
        setUploading(false);
      }
    },
    [api, resetPhase],
  );

  const onFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>): void => {
      const file = e.target.files?.[0] ?? null;
      // Reset the input so picking the same file twice still fires.
      e.target.value = "";
      if (file !== null) {
        void handleFile(file);
      }
    },
    [handleFile],
  );

  const submit = useCallback(async (): Promise<void> => {
    if (upload === null) return;
    setSubmitting(true);
    setTopError(null);
    setFieldErrors({});
    try {
      const committed = await api.confirmToy(
        {
          staging_id: upload.staging_id,
          display_name: form.display_name,
          tags: parseTags(form.tags),
          persona_id: null,
        },
        { signal: aborterRef.current?.signal },
      );
      await refetchToys();
      // Phase F Step F8: keep enough of phase B alive to render the
      // action grid below the form. We clear ``upload``/preview/etc.
      // — the form is hidden — but ``justCommittedToyId`` keeps the
      // grid mounted with the freshly-committed toy id. The image-
      // gen worker has already enqueued 10 jobs (see
      // toys.py:_maybe_enqueue_action_jobs_for_toy), so the grid
      // populates as ws envelopes arrive.
      setUpload(null);
      setForm(EMPTY_FORM);
      setTopError(null);
      setDuplicate(null);
      setFieldErrors({});
      if (previewRef.current !== null) {
        URL.revokeObjectURL(previewRef.current);
        previewRef.current = null;
      }
      setPreviewUrl(null);
      setJustCommittedToyId(committed.id);
      void loadToyActions(committed.id);
      return;
    } catch (err) {
      if (isAbortError(err)) return;
      const validation = extractValidationErrors(err);
      if (validation !== null) {
        setFieldErrors(fieldErrorMap(validation));
        setTopError("Please fix the errors below.");
      } else if (err instanceof ApiError && err.status === 404) {
        setTopError(
          "Upload expired (it may have been more than an hour). " +
            "Please pick the photo again.",
        );
        resetPhase();
      } else if (err instanceof ApiError) {
        setTopError(`save failed: ${err.status}`);
      } else if (err instanceof Error) {
        setTopError(`save failed: ${err.message}`);
      } else {
        setTopError("save failed");
      }
    } finally {
      setSubmitting(false);
    }
  }, [api, form, loadToyActions, refetchToys, resetPhase, upload]);

  const updateField = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]): void => {
      setForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const openToyEdit = useCallback(
    (toy: Toy): void => {
      setEditingToyId(toy.id);
      setEditForm({
        display_name: toy.display_name,
        tags: toy.tags.join(", "),
        allowed_roles: toy.allowed_roles ?? [],
      });
      setEditError(null);
      setEditFieldErrors({});
      setEditRolePopoverOpen(false);
      // Phase F Step F8: seed the action grid for the toy under edit.
      // Archived toys (toy.archived === true) hide the grid entirely
      // per plan §F8 — the existing toy list already filters
      // archived rows server-side, but we defensively skip the load
      // here too in case a future flag exposes archived toys to the
      // edit flow.
      if (!toy.archived) {
        void loadToyActions(toy.id);
      }
    },
    [loadToyActions],
  );

  const cancelToyEdit = useCallback((): void => {
    setEditingToyId(null);
    setEditForm(EMPTY_FORM);
    setEditError(null);
    setEditFieldErrors({});
    setEditRolePopoverOpen(false);
  }, []);

  const toggleAllowedRole = useCallback((role: RoleName): void => {
    setEditForm((prev) => {
      const present = prev.allowed_roles.includes(role);
      const next = present
        ? prev.allowed_roles.filter((r) => r !== role)
        : [...prev.allowed_roles, role];
      return { ...prev, allowed_roles: next };
    });
  }, []);

  const removeAllowedRole = useCallback((role: string): void => {
    setEditForm((prev) => ({
      ...prev,
      allowed_roles: prev.allowed_roles.filter((r) => r !== role),
    }));
  }, []);

  const submitToyEdit = useCallback(async (): Promise<void> => {
    if (editingToyId === null) return;
    setEditSubmitting(true);
    setEditError(null);
    setEditFieldErrors({});
    try {
      await api.updateToy(
        editingToyId,
        {
          display_name: editForm.display_name,
          tags: parseTags(editForm.tags),
          // Always submit allowed_roles (empty array clears any
          // previous restriction). Omitting it would leave the
          // existing value untouched — that's NOT what "save the
          // form" should mean here.
          allowed_roles: editForm.allowed_roles,
        },
        { signal: aborterRef.current?.signal },
      );
      await refetchToys();
      cancelToyEdit();
    } catch (err) {
      if (isAbortError(err)) return;
      const validation = extractValidationErrors(err);
      if (validation !== null) {
        setEditFieldErrors(fieldErrorMap(validation));
        setEditError("Please fix the errors below.");
      } else if (err instanceof ApiError) {
        setEditError(`save failed: ${err.status}`);
      } else if (err instanceof Error) {
        setEditError(`save failed: ${err.message}`);
      } else {
        setEditError("save failed");
      }
    } finally {
      setEditSubmitting(false);
    }
  }, [api, cancelToyEdit, editForm, editingToyId, refetchToys]);

  const deleteToy = useCallback(
    async (toy: Toy): Promise<void> => {
      // ``archivingId`` keeps its name because the backend still does a
      // soft archive (sets ``archived=1``, file kept on disk). The UI
      // surfaces it as "delete" because from the parent's perspective
      // the toy is gone — the soft-vs-hard distinction is internal.
      if (archivingId !== null) return;
      const ok = window.confirm(
        `Delete ${toy.display_name}? It will no longer appear in the toy list.`,
      );
      if (!ok) return;
      setArchivingId(toy.id);
      try {
        await api.archiveToy(toy.id, { signal: aborterRef.current?.signal });
        await refetchToys();
        if (editingToyId === toy.id) cancelToyEdit();
      } catch (err) {
        if (isAbortError(err)) return;
        const message = err instanceof Error ? err.message : "delete failed";
        setListError(message);
      } finally {
        setArchivingId(null);
      }
    },
    [api, archivingId, cancelToyEdit, editingToyId, refetchToys],
  );

  const toggleToyActive = useCallback(
    async (toy: Toy): Promise<void> => {
      if (togglingActiveId !== null) return;
      setTogglingActiveId(toy.id);
      try {
        await api.updateToy(
          toy.id,
          { active: !toy.active },
          { signal: aborterRef.current?.signal },
        );
        await refetchToys();
      } catch (err) {
        if (isAbortError(err)) return;
        const message =
          err instanceof Error ? err.message : "toggle failed";
        setListError(message);
      } finally {
        setTogglingActiveId(null);
      }
    },
    [api, refetchToys, togglingActiveId],
  );

  const updateEditField = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]): void => {
      setEditForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const replaceToyPicture = useCallback(
    async (toyId: string, file: File): Promise<void> => {
      setEditError(null);
      setEditSubmitting(true);
      try {
        await api.replaceToyImage(toyId, file, {
          signal: aborterRef.current?.signal,
        });
        await refetchToys();
      } catch (err) {
        if (isAbortError(err)) return;
        const dup = extractToyImageExistsDetail(err);
        if (dup !== null) {
          setEditError(
            `That image is already in your library as "${dup.existing_toy.display_name}". Pick a different photo.`,
          );
        } else if (err instanceof ApiError) {
          setEditError(uploadErrorMessage(err));
        } else if (err instanceof Error) {
          setEditError(`change picture failed: ${err.message}`);
        } else {
          setEditError("change picture failed");
        }
      } finally {
        setEditSubmitting(false);
      }
    },
    [api, refetchToys],
  );

  return (
    <section
      data-testid="toy-ingest"
      style={{
        border: "1px solid #ccc",
        borderRadius: 6,
        padding: 16,
        margin: "12px 0",
        background: "#fff",
      }}
    >
      <h2 style={{ margin: 0, fontSize: 17, marginBottom: 8 }}>Toys</h2>

      <p style={{ color: "#666", fontSize: 12, margin: "0 0 12px" }}>
        Toy photos are sent to Claude AI for naming. Once saved, the image
        stays on this device.
      </p>

      {/* Phase A — file picker. Hidden during phase B. */}
      {upload === null && duplicate === null && (
        <div data-testid="toy-picker" style={{ marginBottom: 8 }}>
          <label
            htmlFor="toy-file-input"
            style={{ display: "block", fontSize: 13, marginBottom: 4 }}
          >
            Pick a toy photo (JPEG, PNG, or WebP, max 15 MB)
          </label>
          <input
            id="toy-file-input"
            data-testid="toy-file-input"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            disabled={uploading}
            onChange={onFileInputChange}
          />
          {uploading && (
            <p
              data-testid="toy-uploading"
              style={{ color: "#777", fontSize: 13 }}
            >
              uploading...
            </p>
          )}
        </div>
      )}

      {duplicate !== null && (
        <div
          data-testid="toy-duplicate-banner"
          role="alert"
          style={{
            background: "#fff8e1",
            border: "1px solid #ffe082",
            padding: 8,
            borderRadius: 4,
            fontSize: 13,
            margin: "8px 0",
          }}
        >
          <p style={{ margin: 0 }}>
            This image is already in your library as{" "}
            <strong data-testid="duplicate-toy-name">
              {duplicate.display_name}
            </strong>
            .
          </p>
          <button
            type="button"
            data-testid="dismiss-duplicate-button"
            onClick={resetPhase}
            style={{ marginTop: 6 }}
          >
            pick a different photo
          </button>
        </div>
      )}

      {topError !== null && (
        <p
          data-testid="toy-top-error"
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

      {upload !== null && (
        <form
          data-testid="toy-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
          style={{
            display: "grid",
            gridTemplateColumns: "1fr",
            gap: 8,
            marginTop: 8,
          }}
        >
          {previewUrl !== null && (
            <img
              data-testid="toy-preview"
              src={previewUrl}
              alt="toy preview"
              style={{
                maxWidth: 240,
                maxHeight: 240,
                borderRadius: 4,
                border: "1px solid #eee",
              }}
            />
          )}
          {visionErrorMessage(upload.vision_error, upload.vision_skipped) !==
            null && (
            <p
              data-testid="toy-vision-banner"
              role="alert"
              style={{
                background: "#fff8e1",
                border: "1px solid #ffe082",
                padding: 8,
                borderRadius: 4,
                fontSize: 13,
                margin: 0,
              }}
            >
              {visionErrorMessage(upload.vision_error, upload.vision_skipped)}
            </p>
          )}
          <div>
            <label
              htmlFor="toy-display-name"
              style={{ display: "block", fontSize: 13 }}
            >
              Name
            </label>
            <input
              id="toy-display-name"
              data-testid="field-display-name"
              type="text"
              required
              maxLength={40}
              value={form.display_name}
              onChange={(e) => updateField("display_name", e.target.value)}
              style={{ width: "100%", padding: 6 }}
            />
            {fieldErrors["display_name"] !== undefined && (
              <p
                data-testid="error-display-name"
                role="alert"
                style={{ color: "#b71c1c", fontSize: 12, margin: "2px 0 0" }}
              >
                {fieldErrors["display_name"]}
              </p>
            )}
          </div>
          <div>
            <label htmlFor="toy-tags" style={{ display: "block", fontSize: 13 }}>
              Tags (comma-separated)
            </label>
            <input
              id="toy-tags"
              data-testid="field-tags"
              type="text"
              value={form.tags}
              onChange={(e) => updateField("tags", e.target.value)}
              style={{ width: "100%", padding: 6 }}
            />
            {fieldErrors["tags"] !== undefined && (
              <p
                data-testid="error-tags"
                role="alert"
                style={{ color: "#b71c1c", fontSize: 12, margin: "2px 0 0" }}
              >
                {fieldErrors["tags"]}
              </p>
            )}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="submit"
              data-testid="save-toy-button"
              disabled={submitting}
            >
              {submitting ? "saving..." : "save toy"}
            </button>
            <button
              type="button"
              data-testid="cancel-toy-button"
              onClick={resetPhase}
              disabled={submitting}
            >
              cancel
            </button>
          </div>
        </form>
      )}

      {/* Phase F Step F8: post-commit action grid. The form has just */}
      {/* cleared; the grid renders below to show the parent the */}
      {/* worker's progress on the 10 sprites it queued for this toy. */}
      {justCommittedToyId !== null && upload === null && (
        <div data-testid="toy-post-commit-grid" style={{ marginTop: 8 }}>
          <p style={{ color: "#2e7d32", fontSize: 13, margin: "0 0 8px" }}>
            Saved. Generating action sprites — this can take a few minutes.
          </p>
          <ToyActionGrid
            toyId={justCommittedToyId}
            actions={Object.values(toyActions[justCommittedToyId] ?? {})}
            toyDisplayName={
              toys.find((t) => t.id === justCommittedToyId)?.display_name
            }
            onRegenerateAll={() => handleRegenerateAll(justCommittedToyId)}
            onRegenerateSlot={(slot) =>
              handleRegenerateSlot(justCommittedToyId, slot)
            }
            compositeOnlyMode={
              toyModes[justCommittedToyId] === "composite_only"
            }
            disabledReason={
              toyCapabilities[justCommittedToyId] !== undefined &&
              !toyCapabilities[justCommittedToyId]!.capable &&
              toyModes[justCommittedToyId] !== "composite_only"
                ? toyCapabilities[justCommittedToyId]!.reason
                : undefined
            }
          />
          <button
            type="button"
            data-testid="dismiss-post-commit-grid"
            onClick={() => setJustCommittedToyId(null)}
            style={{ marginTop: 8, fontSize: 12 }}
          >
            done
          </button>
        </div>
      )}

      <hr style={{ margin: "16px 0 8px", border: "none", borderTop: "1px solid #eee" }} />

      {listLoading && (
        <p data-testid="toys-loading" style={{ color: "#777", fontSize: 13 }}>
          loading toys...
        </p>
      )}
      {listError !== null && (
        <p
          data-testid="toys-list-error"
          role="alert"
          style={{ color: "#b71c1c", fontSize: 13 }}
        >
          {listError}
        </p>
      )}
      {!listLoading && toys.length === 0 && (
        <p data-testid="toys-empty" style={{ color: "#777", fontSize: 13 }}>
          No toys yet. Add one above.
        </p>
      )}
      {toys.length > 0 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            margin: "4px 0 8px",
          }}
        >
          <span style={{ fontSize: 12, color: "#666" }}>Sort:</span>
          <button
            type="button"
            data-testid="toys-sort-toggle"
            data-sort-mode={sortMode}
            onClick={() =>
              setSortMode((prev) => (prev === "active" ? "name" : "active"))
            }
            title="Click to switch sort"
            style={{ fontSize: 12, padding: "2px 8px" }}
          >
            {sortMode === "active" ? "Active first" : "Name (A→Z)"}
          </button>
        </div>
      )}
      {toys.length > 0 && (
        <ul
          data-testid="toys-list"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {sortedToys.map((t) => {
            const isEditing = editingToyId === t.id;
            const thumb = imageUrl(t.image_path, t.image_hash);
            return (
              <li
                key={t.id}
                data-testid="toy-row"
                data-toy-id={t.id}
                data-toy-active={t.active ? "true" : "false"}
                style={{
                  padding: "8px 0",
                  borderBottom: "1px solid #eee",
                  fontSize: 14,
                  opacity: t.active ? 1 : 0.5,
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
                        data-testid="toy-thumb"
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
                      <strong>{t.display_name}</strong>
                      {t.tags.length > 0 && (
                        <span
                          style={{
                            marginLeft: 8,
                            color: "#777",
                            fontSize: 12,
                          }}
                        >
                          {t.tags.join(", ")}
                        </span>
                      )}
                    </div>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button
                        type="button"
                        data-testid="toggle-toy-active-button"
                        aria-pressed={t.active}
                        disabled={togglingActiveId === t.id}
                        onClick={() => {
                          void toggleToyActive(t);
                        }}
                        title={
                          t.active
                            ? "Deactivate this toy (exclude from suggestions and play)"
                            : "Activate this toy"
                        }
                      >
                        {togglingActiveId === t.id
                          ? "..."
                          : t.active
                            ? "active"
                            : "inactive"}
                      </button>
                      <button
                        type="button"
                        data-testid="edit-toy-button"
                        onClick={() => openToyEdit(t)}
                      >
                        edit
                      </button>
                      <button
                        type="button"
                        data-testid="delete-toy-button"
                        disabled={archivingId === t.id}
                        onClick={() => {
                          void deleteToy(t);
                        }}
                      >
                        {archivingId === t.id ? "deleting..." : "delete"}
                      </button>
                    </div>
                  </div>
                )}
                {isEditing && (
                  <form
                    data-testid="toy-edit-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void submitToyEdit();
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
                          data-testid="toy-edit-thumb"
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
                          data-testid="toy-edit-picture-input"
                          type="file"
                          accept="image/jpeg,image/png,image/webp"
                          disabled={editSubmitting}
                          onChange={(e) => {
                            const f = e.target.files?.[0] ?? null;
                            e.target.value = "";
                            if (f !== null) {
                              void replaceToyPicture(t.id, f);
                            }
                          }}
                          style={{ fontSize: 12 }}
                        />
                      </div>
                    </div>
                    <div>
                      <label
                        style={{ display: "block", fontSize: 12 }}
                      >
                        Name
                      </label>
                      <input
                        data-testid="edit-field-display-name"
                        type="text"
                        required
                        maxLength={40}
                        value={editForm.display_name}
                        onChange={(e) =>
                          updateEditField("display_name", e.target.value)
                        }
                        style={{ width: "100%", padding: 4 }}
                      />
                      {editFieldErrors["display_name"] !== undefined && (
                        <p
                          role="alert"
                          style={{
                            color: "#b71c1c",
                            fontSize: 12,
                            margin: "2px 0 0",
                          }}
                        >
                          {editFieldErrors["display_name"]}
                        </p>
                      )}
                    </div>
                    <div>
                      <label
                        style={{ display: "block", fontSize: 12 }}
                      >
                        Tags (comma-separated)
                      </label>
                      <input
                        data-testid="edit-field-tags"
                        type="text"
                        value={editForm.tags}
                        onChange={(e) => updateEditField("tags", e.target.value)}
                        style={{ width: "100%", padding: 4 }}
                      />
                      {editFieldErrors["tags"] !== undefined && (
                        <p
                          role="alert"
                          style={{
                            color: "#b71c1c",
                            fontSize: 12,
                            margin: "2px 0 0",
                          }}
                        >
                          {editFieldErrors["tags"]}
                        </p>
                      )}
                    </div>
                    <div>
                      <button
                        type="button"
                        data-testid="edit-field-allowed-roles"
                        onClick={() =>
                          setEditRolePopoverOpen((prev) => !prev)
                        }
                        aria-expanded={editRolePopoverOpen}
                        style={{
                          display: "block",
                          fontSize: 12,
                          padding: "4px 8px",
                        }}
                      >
                        Allowed roles
                        {editForm.allowed_roles.length > 0 && (
                          <span style={{ marginLeft: 6, color: "#666" }}>
                            ({editForm.allowed_roles.length})
                          </span>
                        )}
                      </button>
                      {editRolePopoverOpen && (
                        <div
                          data-testid="allowed-roles-popover"
                          role="group"
                          aria-label="Allowed roles"
                          style={{
                            border: "1px solid #ccc",
                            borderRadius: 4,
                            padding: 8,
                            marginTop: 4,
                            background: "#fafafa",
                            display: "flex",
                            flexDirection: "column",
                            gap: 4,
                          }}
                        >
                          {ROLE_NAMES_SORTED.map((role) => {
                            const checked =
                              editForm.allowed_roles.includes(role);
                            return (
                              <label
                                key={role}
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: 6,
                                  fontSize: 12,
                                }}
                              >
                                <input
                                  type="checkbox"
                                  data-testid={`allowed-role-checkbox-${role}`}
                                  checked={checked}
                                  onChange={() => toggleAllowedRole(role)}
                                />
                                {ROLE_DISPLAY_NAMES[role]}
                              </label>
                            );
                          })}
                        </div>
                      )}
                      {editForm.allowed_roles.length > 0 && (
                        <div
                          data-testid="allowed-roles-chips"
                          style={{
                            display: "flex",
                            flexWrap: "wrap",
                            gap: 4,
                            marginTop: 6,
                          }}
                        >
                          {editForm.allowed_roles.map((role) => (
                            <span
                              key={role}
                              data-testid={`allowed-role-chip-${role}`}
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: 4,
                                padding: "2px 6px",
                                fontSize: 12,
                                background: "#e3f2fd",
                                border: "1px solid #90caf9",
                                borderRadius: 12,
                              }}
                            >
                              {ROLE_DISPLAY_NAMES[role as RoleName] ?? role}
                              <button
                                type="button"
                                data-testid={`allowed-role-chip-remove-${role}`}
                                aria-label={`Remove ${role}`}
                                onClick={() => removeAllowedRole(role)}
                                style={{
                                  border: "none",
                                  background: "transparent",
                                  cursor: "pointer",
                                  fontSize: 12,
                                  padding: 0,
                                }}
                              >
                                ×
                              </button>
                            </span>
                          ))}
                        </div>
                      )}
                      <p
                        style={{
                          color: "#666",
                          fontSize: 11,
                          margin: "4px 0 0",
                        }}
                      >
                        Leave empty to allow any role.
                      </p>
                    </div>
                    {editError !== null && (
                      <p
                        data-testid="toy-edit-error"
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
                        {editError}
                      </p>
                    )}
                    <div style={{ display: "flex", gap: 6 }}>
                      <button
                        type="submit"
                        data-testid="save-toy-edit-button"
                        disabled={editSubmitting}
                      >
                        {editSubmitting ? "saving..." : "save"}
                      </button>
                      <button
                        type="button"
                        data-testid="cancel-toy-edit-button"
                        onClick={cancelToyEdit}
                        disabled={editSubmitting}
                      >
                        cancel
                      </button>
                    </div>
                    {/* Phase F Step F8: action grid in the edit flow. */}
                    {/* Hidden on archived toys per plan (the list is */}
                    {/* already filtered server-side, defensive guard */}
                    {/* keeps it that way if a future flag exposes them). */}
                    {!t.archived && (
                      <ToyActionGrid
                        toyId={t.id}
                        actions={Object.values(toyActions[t.id] ?? {})}
                        toyDisplayName={t.display_name}
                        onRegenerateAll={() => handleRegenerateAll(t.id)}
                        onRegenerateSlot={(slot) =>
                          handleRegenerateSlot(t.id, slot)
                        }
                        compositeOnlyMode={
                          toyModes[t.id] === "composite_only"
                        }
                        disabledReason={
                          toyCapabilities[t.id] !== undefined &&
                          !toyCapabilities[t.id]!.capable &&
                          toyModes[t.id] !== "composite_only"
                            ? toyCapabilities[t.id]!.reason
                            : undefined
                        }
                      />
                    )}
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
