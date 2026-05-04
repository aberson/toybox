import type { JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  extractToyImageExistsDetail,
  extractValidationErrors,
  isAbortError,
} from "../api";
import type {
  ApiClient,
  Toy,
  ToyUploadResponse,
  ValidationFieldError,
} from "../api";

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
}

const EMPTY_FORM: FormState = { display_name: "", tags: "" };

function suggestionToForm(suggestion: ToyUploadResponse["suggested"]): FormState {
  if (suggestion === null) return EMPTY_FORM;
  return {
    display_name: suggestion.display_name,
    tags: suggestion.tags.join(", "),
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

  // Single AbortController spanning the editor's lifetime. Mirrors the
  // pattern in ``ChildProfileEditor``.
  const aborterRef = useRef<AbortController | null>(null);
  if (aborterRef.current === null) {
    aborterRef.current = new AbortController();
  }
  const aborter = aborterRef.current;

  // Track the preview URL so we can revoke it on phase-reset / unmount
  // (URL.createObjectURL leaks until revoked).
  const previewRef = useRef<string | null>(null);

  const refetchToys = useCallback(async (): Promise<void> => {
    setListLoading(true);
    try {
      const resp = await api.listToys({ signal: aborter.signal });
      setToys(resp.toys);
      setListError(null);
    } catch (err) {
      if (isAbortError(err)) return;
      const message = err instanceof Error ? err.message : "load failed";
      setListError(message);
    } finally {
      setListLoading(false);
    }
  }, [api, aborter]);

  useEffect(() => {
    void refetchToys();
    return () => {
      aborter.abort();
      if (previewRef.current !== null) {
        URL.revokeObjectURL(previewRef.current);
        previewRef.current = null;
      }
    };
  }, [aborter, refetchToys]);

  const resetPhase = useCallback((): void => {
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
  }, []);

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
        const resp = await api.uploadToyPhoto(file, { signal: aborter.signal });
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
    [aborter, api, resetPhase],
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
      await api.confirmToy(
        {
          staging_id: upload.staging_id,
          display_name: form.display_name,
          tags: parseTags(form.tags),
          persona_id: null,
        },
        { signal: aborter.signal },
      );
      await refetchToys();
      resetPhase();
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
  }, [aborter, api, form, refetchToys, resetPhase, upload]);

  const updateField = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]): void => {
      setForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
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
        <ul
          data-testid="toys-list"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {toys.map((t) => (
            <li
              key={t.id}
              data-testid="toy-row"
              data-toy-id={t.id}
              style={{
                padding: "6px 0",
                borderBottom: "1px solid #eee",
                fontSize: 14,
              }}
            >
              <strong>{t.display_name}</strong>
              {t.tags.length > 0 && (
                <span style={{ marginLeft: 8, color: "#777", fontSize: 12 }}>
                  {t.tags.join(", ")}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
