import type { JSX, KeyboardEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { Animation } from "../../shared/types";
import "../animations/rewardAnimationsPreview.css";
import {
  ANIMATION_LABELS,
  ANIMATION_OPTIONS,
  REWARD_PREVIEW_ANIMATIONS,
} from "../animations/rewardAnimationsPreview";
import {
  ApiError,
  extractRewardImageExistsDetail,
  extractValidationErrors,
  imageUrl,
  isAbortError,
} from "../api";
import type {
  ApiClient,
  Reward,
  RewardUploadResponse,
  ValidationFieldError,
} from "../api";

// Phase L Step L7: parent-facing reward ingest UI. Cloned from
// :component:`ToyIngest` and stripped of the ``allowed_roles`` popover
// + ``ToyActionGrid`` — rewards have neither. The pipeline is two-phase
// like the toy flow:
//
// Phase A — pick a file. Parent picks an image, we POST it to
//   /api/rewards/upload. There is NO Claude vision suggestion phase
//   (rewards are simple). The response carries a ``staging_key`` we
//   hand back to /api/rewards on confirm.
//
// Phase B — review and save. The parent fills display_name, tags
//   (chip input), picks one of the six Animation enum members via a
//   segmented control, and toggles ``active``. The preview card to the
//   right of the form plays the selected animation so the parent sees
//   what the kiosk will render.
//
// Error surfaces:
//   * 415/413 on upload → top-level banner with the error code
//   * 409 on upload     → "this image is already in your library"
//                         banner with a button that closes the form
//   * 422 on confirm    → per-field errors under the inputs
//
// AbortController plumbing matches ToyIngest: a single ref spans the
// component's lifetime, every fetch carries the same signal, and
// unmount aborts whatever's in flight.

// Form caps. Plan §"Form fields" calls for max 60 chars; the backend
// caps display_name at 40 chars (see RewardConfirmRequest in
// src/toybox/api/rewards.py) so we constrain the input to the backend
// cap to avoid a guaranteed 422 round-trip. The plan's 60-char ask is
// an upper bound for the UI, NOT a hard cap below the backend's.
const DISPLAY_NAME_MAX = 40;
// Tag caps mirror the backend (_MAX_TAG_LENGTH, _MAX_TAGS_PER_REWARD).
const TAG_MAX_LENGTH = 24;
const MAX_TAGS = 10;

interface FormState {
  display_name: string;
  tags: string[];
  animation: Animation;
  active: boolean;
}

const EMPTY_FORM: FormState = {
  display_name: "",
  tags: [],
  // Default to the first enum member — matches the plan's segmented-
  // control default highlight ("clicking another changes selection").
  animation: ANIMATION_OPTIONS[0],
  active: true,
};

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

// Client-side validation. Returns the first violation message or null.
// Caps mirror the backend so an obvious mistake doesn't have to round-
// trip to surface. The backend still validates — this is UX, not a
// security boundary.
function validateForm(form: FormState): {
  field: keyof FormState | null;
  message: string;
} | null {
  const trimmedName = form.display_name.trim();
  if (trimmedName.length === 0) {
    return { field: "display_name", message: "Name is required." };
  }
  if (trimmedName.length > DISPLAY_NAME_MAX) {
    return {
      field: "display_name",
      message: `Name must be at most ${DISPLAY_NAME_MAX} characters.`,
    };
  }
  if (form.tags.length > MAX_TAGS) {
    return {
      field: "tags",
      message: `At most ${MAX_TAGS} tags per reward.`,
    };
  }
  const tooLong = form.tags.find((t) => t.length > TAG_MAX_LENGTH);
  if (tooLong !== undefined) {
    return {
      field: "tags",
      message: `Each tag must be at most ${TAG_MAX_LENGTH} characters.`,
    };
  }
  return null;
}

export interface RewardIngestProps {
  api: ApiClient;
  // Phase L Step L8 will pass a pre-selected reward from a sibling
  // list panel to seed the edit form on mount. Optional so L7 ships
  // standalone without a parent wrapper.
  initialEditingReward?: Reward | null;
}

// Chip input: a small inline component used for the ``tags`` field.
// Parent supplies a comma-delimited buffer + the committed-chip list;
// pressing comma / Enter commits the buffer as a chip, backspace on
// an empty buffer pops the last chip. Mirrors the existing pattern
// used for free-text tag entry across the parent UI.
interface ChipInputProps {
  values: string[];
  onChange: (values: string[]) => void;
  inputTestId: string;
  chipTestIdPrefix: string;
  ariaLabel: string;
  disabled?: boolean;
  // Per-chip cap (delegated client-side; backend re-enforces).
  maxChipLength?: number;
}

function ChipInput(props: ChipInputProps): JSX.Element {
  const {
    values,
    onChange,
    inputTestId,
    chipTestIdPrefix,
    ariaLabel,
    disabled,
    maxChipLength,
  } = props;
  const [buffer, setBuffer] = useState<string>("");

  // Commit any chips delimited by commas in ``raw``. The trailing
  // non-comma fragment stays in the buffer so a user mid-typing a tag
  // isn't interrupted. Lowercase + strip mirrors the backend's
  // ``_normalise_tags`` (case-fold + whitespace strip); we don't NFKC
  // here because the backend re-normalises on receive and it would
  // complicate the chip-equality check below.
  const commitFromBuffer = useCallback(
    (raw: string): void => {
      const parts = raw.split(",");
      const tail = parts.pop() ?? "";
      const fresh: string[] = [];
      for (const part of parts) {
        const cleaned = part.trim().toLowerCase();
        if (cleaned.length === 0) continue;
        if (values.includes(cleaned) || fresh.includes(cleaned)) continue;
        fresh.push(cleaned);
      }
      if (fresh.length > 0) {
        onChange([...values, ...fresh]);
      }
      setBuffer(tail);
    },
    [onChange, values],
  );

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>): void => {
      if (e.key === "Enter") {
        e.preventDefault();
        commitFromBuffer(buffer + ",");
        return;
      }
      // Backspace on an empty (or whitespace-only) buffer pops the
      // last chip. Whitespace tolerance avoids "I typed 'pirate, ' and
      // backspace did nothing" — the trailing space the comma-commit
      // path leaves in the buffer is irrelevant to the pop intent.
      if (
        e.key === "Backspace" &&
        buffer.trim() === "" &&
        values.length > 0
      ) {
        e.preventDefault();
        // Also clear any trailing whitespace from the buffer so the
        // input visibly resets to the new tail.
        setBuffer("");
        onChange(values.slice(0, -1));
      }
    },
    [buffer, commitFromBuffer, onChange, values],
  );

  const removeAt = useCallback(
    (idx: number): void => {
      const next = values.slice();
      next.splice(idx, 1);
      onChange(next);
    },
    [onChange, values],
  );

  return (
    <div
      data-testid={`${chipTestIdPrefix}-container`}
      role="group"
      aria-label={ariaLabel}
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 4,
        padding: 4,
        border: "1px solid #ccc",
        borderRadius: 4,
        background: disabled === true ? "#f4f4f4" : "#fff",
        minHeight: 30,
      }}
    >
      {values.map((tag, idx) => (
        <span
          key={`${tag}-${idx}`}
          data-testid={`${chipTestIdPrefix}-chip-${tag}`}
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
          {tag}
          <button
            type="button"
            data-testid={`${chipTestIdPrefix}-chip-remove-${tag}`}
            aria-label={`Remove ${tag}`}
            disabled={disabled === true}
            onClick={() => removeAt(idx)}
            style={{
              border: "none",
              background: "transparent",
              cursor: disabled === true ? "default" : "pointer",
              fontSize: 12,
              padding: 0,
            }}
          >
            ×
          </button>
        </span>
      ))}
      <input
        data-testid={inputTestId}
        type="text"
        value={buffer}
        disabled={disabled === true}
        onChange={(e) => {
          // Cap each comma-delimited segment at the per-chip length.
          // Surfaces the cap immediately rather than letting the user
          // type ahead and then trip a 422 on submit. ``,`` survives
          // the cap pass (it's the chip delimiter).
          let raw = e.target.value;
          if (maxChipLength !== undefined) {
            raw = raw
              .split(",")
              .map((seg) => seg.slice(0, maxChipLength))
              .join(",");
          }
          if (raw.includes(",")) {
            commitFromBuffer(raw);
          } else {
            setBuffer(raw);
          }
        }}
        onKeyDown={onKeyDown}
        onBlur={() => {
          // Flush a buffer the user finished typing but didn't comma-
          // separate; otherwise an unsubmitted tag would silently drop.
          if (buffer.trim().length > 0) {
            commitFromBuffer(buffer + ",");
          }
        }}
        style={{
          border: "none",
          outline: "none",
          flex: 1,
          minWidth: 80,
          fontSize: 13,
          padding: 2,
          background: "transparent",
        }}
      />
    </div>
  );
}

export function RewardIngest(props: RewardIngestProps): JSX.Element {
  const { api, initialEditingReward = null } = props;

  // Reward list (so the parent sees what's already in the library).
  const [rewards, setRewards] = useState<Reward[]>([]);
  const [listLoading, setListLoading] = useState<boolean>(true);
  const [listError, setListError] = useState<string | null>(null);

  // Phase B (new-reward) state.
  const [upload, setUpload] = useState<RewardUploadResponse | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [uploading, setUploading] = useState<boolean>(false);
  const [topError, setTopError] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState<Reward | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [clientError, setClientError] = useState<string | null>(null);

  // Inline edit state for an existing reward in the list. Null = no
  // row is being edited; otherwise the row id whose form is open.
  const [editingRewardId, setEditingRewardId] = useState<string | null>(
    initialEditingReward?.id ?? null,
  );
  const [editForm, setEditForm] = useState<FormState>(
    initialEditingReward !== null
      ? {
          display_name: initialEditingReward.display_name,
          tags: initialEditingReward.tags,
          animation: initialEditingReward.animation,
          active: initialEditingReward.active,
        }
      : EMPTY_FORM,
  );
  const [editSubmitting, setEditSubmitting] = useState<boolean>(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [editFieldErrors, setEditFieldErrors] = useState<
    Record<string, string>
  >({});
  const [archivingId, setArchivingId] = useState<string | null>(null);
  // L follow-up Change B: mirror ToyIngest's per-row active/inactive
  // toggle. ``togglingActiveId`` tracks the id of the reward whose
  // PATCH is in flight so we can disable that single control + show a
  // brief "..." label without locking out the rest of the list.
  // ``null`` = no toggle currently in flight.
  const [togglingActiveId, setTogglingActiveId] = useState<string | null>(
    null,
  );

  // Sort mode for the reward list. "active" puts active rewards first
  // (alpha within each group); "name" is pure alpha A→Z. Mirrors the
  // toys-sortable pattern shipped in d89b6d1.
  const [sortMode, setSortMode] = useState<"active" | "name">("active");

  const sortedRewards = useMemo<Reward[]>(() => {
    const copy = [...rewards];
    copy.sort((a, b) => {
      if (sortMode === "active" && a.active !== b.active) {
        return a.active ? -1 : 1;
      }
      return a.display_name.localeCompare(b.display_name);
    });
    return copy;
  }, [rewards, sortMode]);

  // AbortController spanning one mount of the editor. Recreated on
  // each mount inside the useEffect below — under React 18 StrictMode
  // the mount→cleanup→remount cycle would otherwise leave us with a
  // permanently-aborted signal and every fetch would silently reject.
  const aborterRef = useRef<AbortController | null>(null);

  // Track the preview URL so we can revoke it on phase-reset / unmount
  // (URL.createObjectURL leaks until revoked).
  const previewRef = useRef<string | null>(null);

  const refetchRewards = useCallback(async (): Promise<void> => {
    setListLoading(true);
    try {
      const resp = await api.listRewards({
        signal: aborterRef.current?.signal,
      });
      setRewards(resp.rewards);
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
    void refetchRewards();
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
  }, [refetchRewards]);

  const resetPhase = useCallback((): void => {
    setUpload(null);
    setForm(EMPTY_FORM);
    setTopError(null);
    setDuplicate(null);
    setFieldErrors({});
    setClientError(null);
    if (previewRef.current !== null) {
      URL.revokeObjectURL(previewRef.current);
      previewRef.current = null;
    }
    setPreviewUrl(null);
  }, []);

  const handleFile = useCallback(
    async (file: File): Promise<void> => {
      resetPhase();
      setUploading(true);
      const objectUrl = URL.createObjectURL(file);
      previewRef.current = objectUrl;
      setPreviewUrl(objectUrl);
      let kept = false;
      try {
        const resp = await api.uploadReward(file, {
          signal: aborterRef.current?.signal,
        });
        setUpload(resp);
        setForm(EMPTY_FORM);
        kept = true;
      } catch (err) {
        if (isAbortError(err)) {
          kept = true;
          return;
        }
        const dup = extractRewardImageExistsDetail(err);
        if (dup !== null) {
          setDuplicate(dup.existing_reward);
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
      e.target.value = "";
      if (file !== null) {
        void handleFile(file);
      }
    },
    [handleFile],
  );

  const submit = useCallback(async (): Promise<void> => {
    if (upload === null) return;
    const violation = validateForm(form);
    if (violation !== null) {
      setClientError(violation.message);
      if (violation.field !== null) {
        setFieldErrors({ [violation.field]: violation.message });
      }
      return;
    }
    setSubmitting(true);
    setTopError(null);
    setClientError(null);
    setFieldErrors({});
    try {
      await api.confirmReward(
        {
          staging_key: upload.staging_key,
          display_name: form.display_name.trim(),
          tags: form.tags,
          animation: form.animation,
          active: form.active,
        },
        { signal: aborterRef.current?.signal },
      );
      await refetchRewards();
      resetPhase();
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
  }, [api, form, refetchRewards, resetPhase, upload]);

  const updateField = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]): void => {
      setForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const openRewardEdit = useCallback((reward: Reward): void => {
    setEditingRewardId(reward.id);
    setEditForm({
      display_name: reward.display_name,
      tags: reward.tags,
      animation: reward.animation,
      active: reward.active,
    });
    setEditError(null);
    setEditFieldErrors({});
  }, []);

  const cancelRewardEdit = useCallback((): void => {
    setEditingRewardId(null);
    setEditForm(EMPTY_FORM);
    setEditError(null);
    setEditFieldErrors({});
  }, []);

  const submitRewardEdit = useCallback(async (): Promise<void> => {
    if (editingRewardId === null) return;
    const violation = validateForm(editForm);
    if (violation !== null) {
      setEditError(violation.message);
      if (violation.field !== null) {
        setEditFieldErrors({ [violation.field]: violation.message });
      }
      return;
    }
    setEditSubmitting(true);
    setEditError(null);
    setEditFieldErrors({});
    try {
      await api.updateReward(
        editingRewardId,
        {
          display_name: editForm.display_name.trim(),
          tags: editForm.tags,
          animation: editForm.animation,
          active: editForm.active,
        },
        { signal: aborterRef.current?.signal },
      );
      await refetchRewards();
      cancelRewardEdit();
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
  }, [api, cancelRewardEdit, editForm, editingRewardId, refetchRewards]);

  // L follow-up Change B: replaces the archive button with a delete
  // button (semantics + label match the toy ingest UX). The backend
  // operation is still ``archived=true`` — soft delete; the file
  // stays on disk and we filter ``archived = 0`` on read — but the
  // parent-facing label is "delete" because from the parent's
  // perspective the reward is gone. ``archivingId`` keeps its name to
  // avoid churning the in-flight tracker.
  const deleteReward = useCallback(
    async (reward: Reward): Promise<void> => {
      if (archivingId !== null) return;
      const ok = window.confirm(
        `Delete ${reward.display_name}? It will no longer appear in the rewards list.`,
      );
      if (!ok) return;
      setArchivingId(reward.id);
      try {
        await api.updateReward(
          reward.id,
          { archived: true },
          { signal: aborterRef.current?.signal },
        );
        await refetchRewards();
        if (editingRewardId === reward.id) cancelRewardEdit();
      } catch (err) {
        if (isAbortError(err)) return;
        const message = err instanceof Error ? err.message : "delete failed";
        setListError(message);
      } finally {
        setArchivingId(null);
      }
    },
    [api, archivingId, cancelRewardEdit, editingRewardId, refetchRewards],
  );

  // L follow-up Change B: per-reward active/inactive toggle. Mirrors
  // ToyIngest's ``toggleToyActive`` exactly — single global in-flight
  // tracker so a rapid double-click can't fire two PATCHes for the
  // same row, but other rows stay responsive.
  const toggleRewardActive = useCallback(
    async (reward: Reward): Promise<void> => {
      if (togglingActiveId !== null) return;
      setTogglingActiveId(reward.id);
      try {
        await api.updateReward(
          reward.id,
          { active: !reward.active },
          { signal: aborterRef.current?.signal },
        );
        await refetchRewards();
      } catch (err) {
        if (isAbortError(err)) return;
        const message = err instanceof Error ? err.message : "toggle failed";
        setListError(message);
      } finally {
        setTogglingActiveId(null);
      }
    },
    [api, refetchRewards, togglingActiveId],
  );

  const updateEditField = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]): void => {
      setEditForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const submitDisabled =
    submitting || form.display_name.trim().length === 0;

  return (
    <section
      data-testid="reward-ingest"
      style={{
        border: "1px solid #ccc",
        borderRadius: 6,
        padding: 16,
        margin: "12px 0",
        background: "#fff",
      }}
    >
      <h2 style={{ margin: 0, fontSize: 17, marginBottom: 8 }}>Rewards</h2>

      <p style={{ color: "#666", fontSize: 12, margin: "0 0 12px" }}>
        Picture rewards play with an animation when an activity ends.
      </p>

      {/* Phase A — file picker. Hidden during phase B. */}
      {upload === null && duplicate === null && (
        <div data-testid="reward-picker" style={{ marginBottom: 8 }}>
          <label
            htmlFor="reward-file-input"
            style={{ display: "block", fontSize: 13, marginBottom: 4 }}
          >
            Pick a reward image (JPEG, PNG, or WebP, max 15 MB)
          </label>
          <input
            id="reward-file-input"
            data-testid="reward-file-input"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            disabled={uploading}
            onChange={onFileInputChange}
          />
          {uploading && (
            <p
              data-testid="reward-uploading"
              style={{ color: "#777", fontSize: 13 }}
            >
              uploading...
            </p>
          )}
        </div>
      )}

      {duplicate !== null && (
        <div
          data-testid="reward-duplicate-banner"
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
            <strong data-testid="duplicate-reward-name">
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
          data-testid="reward-top-error"
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
          data-testid="reward-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 200px",
            gap: 16,
            alignItems: "start",
            marginTop: 8,
          }}
        >
          <div style={{ display: "grid", gap: 8 }}>
            <div>
              <label
                htmlFor="reward-display-name"
                style={{ display: "block", fontSize: 13 }}
              >
                Name
              </label>
              <input
                id="reward-display-name"
                data-testid="field-display-name"
                type="text"
                required
                maxLength={DISPLAY_NAME_MAX}
                value={form.display_name}
                onChange={(e) =>
                  updateField("display_name", e.target.value)
                }
                style={{ width: "100%", padding: 6 }}
              />
              {fieldErrors["display_name"] !== undefined && (
                <p
                  data-testid="error-display-name"
                  role="alert"
                  style={{
                    color: "#b71c1c",
                    fontSize: 12,
                    margin: "2px 0 0",
                  }}
                >
                  {fieldErrors["display_name"]}
                </p>
              )}
            </div>
            <div>
              <label style={{ display: "block", fontSize: 13 }}>
                Tags (press Enter or comma to add)
              </label>
              <ChipInput
                values={form.tags}
                onChange={(v) => updateField("tags", v)}
                inputTestId="field-tags-input"
                chipTestIdPrefix="reward-tag"
                ariaLabel="Reward tags"
                maxChipLength={TAG_MAX_LENGTH}
              />
              {fieldErrors["tags"] !== undefined && (
                <p
                  data-testid="error-tags"
                  role="alert"
                  style={{
                    color: "#b71c1c",
                    fontSize: 12,
                    margin: "2px 0 0",
                  }}
                >
                  {fieldErrors["tags"]}
                </p>
              )}
            </div>
            <div>
              <label style={{ display: "block", fontSize: 13 }}>
                Animation
              </label>
              <div
                role="radiogroup"
                aria-label="Reward animation"
                data-testid="reward-animation-group"
                style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
              >
                {ANIMATION_OPTIONS.map((option) => {
                  const active = form.animation === option;
                  return (
                    <button
                      key={option}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      data-testid={`reward-animation-${option}`}
                      data-selected={active ? "true" : "false"}
                      onClick={() => updateField("animation", option)}
                      style={{
                        padding: "4px 10px",
                        fontSize: 12,
                        border: active
                          ? "1px solid #1976d2"
                          : "1px solid #ccc",
                        background: active ? "#e3f2fd" : "#fff",
                        borderRadius: 4,
                        cursor: "pointer",
                      }}
                    >
                      {ANIMATION_LABELS[option]}
                    </button>
                  );
                })}
              </div>
            </div>
            <div>
              <label
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: 13,
                }}
              >
                <input
                  data-testid="field-active"
                  type="checkbox"
                  checked={form.active}
                  onChange={(e) =>
                    updateField("active", e.target.checked)
                  }
                />
                Active
              </label>
            </div>
            {clientError !== null && (
              <p
                data-testid="reward-client-error"
                role="alert"
                style={{ color: "#b71c1c", fontSize: 12, margin: 0 }}
              >
                {clientError}
              </p>
            )}
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="submit"
                data-testid="save-reward-button"
                disabled={submitDisabled}
              >
                {submitting ? "saving..." : "save reward"}
              </button>
              <button
                type="button"
                data-testid="cancel-reward-button"
                onClick={resetPhase}
                disabled={submitting}
              >
                cancel
              </button>
            </div>
          </div>
          <div>
            {previewUrl !== null && (
              <div
                data-testid="reward-preview-card"
                style={{
                  border: "1px solid #eee",
                  borderRadius: 6,
                  padding: 12,
                  background: "#fafafa",
                  display: "flex",
                  justifyContent: "center",
                  alignItems: "center",
                  height: 200,
                  overflow: "hidden",
                }}
              >
                <img
                  data-testid="reward-preview-image"
                  data-animation={form.animation}
                  src={previewUrl}
                  alt="reward preview"
                  style={{
                    maxWidth: 160,
                    maxHeight: 160,
                    borderRadius: 4,
                    ...REWARD_PREVIEW_ANIMATIONS[form.animation],
                  }}
                />
              </div>
            )}
          </div>
        </form>
      )}

      <hr
        style={{
          margin: "16px 0 8px",
          border: "none",
          borderTop: "1px solid #eee",
        }}
      />

      {listLoading && (
        <p
          data-testid="rewards-loading"
          style={{ color: "#777", fontSize: 13 }}
        >
          loading rewards...
        </p>
      )}
      {listError !== null && (
        <p
          data-testid="rewards-list-error"
          role="alert"
          style={{ color: "#b71c1c", fontSize: 13 }}
        >
          {listError}
        </p>
      )}
      {!listLoading && rewards.length === 0 && (
        <p
          data-testid="rewards-empty"
          style={{ color: "#777", fontSize: 13 }}
        >
          No rewards yet. Add one above.
        </p>
      )}
      {rewards.length > 0 && (
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
            data-testid="rewards-sort-toggle"
            data-sort-mode={sortMode}
            onClick={() =>
              setSortMode((prev) =>
                prev === "active" ? "name" : "active",
              )
            }
            title="Click to switch sort"
            style={{ fontSize: 12, padding: "2px 8px" }}
          >
            {sortMode === "active" ? "Active first" : "Name (A→Z)"}
          </button>
        </div>
      )}
      {rewards.length > 0 && (
        <ul
          data-testid="rewards-list"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {sortedRewards.map((r) => {
            const isEditing = editingRewardId === r.id;
            const thumb = imageUrl(r.image_path, r.image_hash);
            return (
              <li
                key={r.id}
                data-testid="reward-row"
                data-reward-id={r.id}
                data-reward-active={r.active ? "true" : "false"}
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
                        data-testid="reward-thumb"
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
                      <span
                        style={{
                          marginLeft: 8,
                          color: "#777",
                          fontSize: 12,
                        }}
                        data-testid="reward-row-animation"
                      >
                        {ANIMATION_LABELS[r.animation]}
                      </span>
                      {r.tags.length > 0 && (
                        <span
                          style={{
                            marginLeft: 8,
                            color: "#777",
                            fontSize: 12,
                          }}
                        >
                          {r.tags.join(", ")}
                        </span>
                      )}
                    </div>
                    <div style={{ display: "flex", gap: 6 }}>
                      {/* L follow-up Change B: active/inactive toggle
                          mirroring ToyIngest. ``aria-pressed`` reflects
                          the current active state. Disabled briefly
                          while the PATCH is in flight (single-row
                          guard via ``togglingActiveId``). */}
                      <button
                        type="button"
                        data-testid="toggle-reward-active-button"
                        aria-pressed={r.active}
                        disabled={togglingActiveId === r.id}
                        onClick={() => {
                          void toggleRewardActive(r);
                        }}
                        title={
                          r.active
                            ? "Deactivate this reward (exclude from picks)"
                            : "Activate this reward"
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
                        data-testid="edit-reward-button"
                        onClick={() => openRewardEdit(r)}
                      >
                        edit
                      </button>
                      {/* L follow-up Change B: archive button → delete
                          label. Wire shape unchanged (still PATCHes
                          ``archived=true``); operator-facing label
                          matches the toy ingest UX so the two surfaces
                          read consistently. ``data-testid`` renamed to
                          ``delete-reward-button`` accordingly; the
                          old ``archive-reward-button`` testid is
                          retired (RewardIngest.test.tsx updated in
                          step). */}
                      <button
                        type="button"
                        data-testid="delete-reward-button"
                        disabled={archivingId === r.id}
                        onClick={() => {
                          void deleteReward(r);
                        }}
                      >
                        {archivingId === r.id ? "deleting..." : "delete"}
                      </button>
                    </div>
                  </div>
                )}
                {isEditing && (
                  <form
                    data-testid="reward-edit-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void submitRewardEdit();
                    }}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 160px",
                      gap: 12,
                      alignItems: "start",
                    }}
                  >
                    <div style={{ display: "grid", gap: 6 }}>
                      <div>
                        <label style={{ display: "block", fontSize: 12 }}>
                          Name
                        </label>
                        <input
                          data-testid="edit-field-display-name"
                          type="text"
                          required
                          maxLength={DISPLAY_NAME_MAX}
                          value={editForm.display_name}
                          onChange={(e) =>
                            updateEditField("display_name", e.target.value)
                          }
                          style={{ width: "100%", padding: 4 }}
                        />
                        {editFieldErrors["display_name"] !== undefined && (
                          <p
                            role="alert"
                            data-testid="edit-error-display-name"
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
                        <label style={{ display: "block", fontSize: 12 }}>
                          Tags (press Enter or comma to add)
                        </label>
                        <ChipInput
                          values={editForm.tags}
                          onChange={(v) => updateEditField("tags", v)}
                          inputTestId="edit-field-tags-input"
                          chipTestIdPrefix="reward-edit-tag"
                          ariaLabel="Reward tags"
                          maxChipLength={TAG_MAX_LENGTH}
                        />
                        {editFieldErrors["tags"] !== undefined && (
                          <p
                            role="alert"
                            data-testid="edit-error-tags"
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
                        <label style={{ display: "block", fontSize: 12 }}>
                          Animation
                        </label>
                        <div
                          role="radiogroup"
                          aria-label="Reward animation"
                          data-testid="reward-edit-animation-group"
                          style={{
                            display: "flex",
                            flexWrap: "wrap",
                            gap: 4,
                          }}
                        >
                          {ANIMATION_OPTIONS.map((option) => {
                            const active = editForm.animation === option;
                            return (
                              <button
                                key={option}
                                type="button"
                                role="radio"
                                aria-checked={active}
                                data-testid={`reward-edit-animation-${option}`}
                                onClick={() =>
                                  updateEditField("animation", option)
                                }
                                style={{
                                  padding: "4px 10px",
                                  fontSize: 12,
                                  border: active
                                    ? "1px solid #1976d2"
                                    : "1px solid #ccc",
                                  background: active
                                    ? "#e3f2fd"
                                    : "#fff",
                                  borderRadius: 4,
                                  cursor: "pointer",
                                }}
                              >
                                {ANIMATION_LABELS[option]}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                      <div>
                        <label
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 6,
                            fontSize: 12,
                          }}
                        >
                          <input
                            data-testid="edit-field-active"
                            type="checkbox"
                            checked={editForm.active}
                            onChange={(e) =>
                              updateEditField("active", e.target.checked)
                            }
                          />
                          Active
                        </label>
                      </div>
                      {editError !== null && (
                        <p
                          data-testid="reward-edit-error"
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
                          data-testid="save-reward-edit-button"
                          disabled={editSubmitting}
                        >
                          {editSubmitting ? "saving..." : "save"}
                        </button>
                        <button
                          type="button"
                          data-testid="cancel-reward-edit-button"
                          onClick={cancelRewardEdit}
                          disabled={editSubmitting}
                        >
                          cancel
                        </button>
                      </div>
                    </div>
                    {thumb !== null && (
                      <div
                        data-testid="reward-edit-preview-card"
                        style={{
                          border: "1px solid #eee",
                          borderRadius: 6,
                          padding: 12,
                          background: "#fafafa",
                          display: "flex",
                          justifyContent: "center",
                          alignItems: "center",
                          height: 160,
                          overflow: "hidden",
                        }}
                      >
                        <img
                          data-testid="reward-edit-preview-image"
                          data-animation={editForm.animation}
                          src={thumb}
                          alt=""
                          style={{
                            maxWidth: 120,
                            maxHeight: 120,
                            borderRadius: 4,
                            ...REWARD_PREVIEW_ANIMATIONS[editForm.animation],
                          }}
                        />
                      </div>
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
