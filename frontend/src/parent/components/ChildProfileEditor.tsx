import type { JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  extractChildInUseDetail,
  extractValidationErrors,
  isAbortError,
} from "../api";
import type {
  ApiClient,
  ChildProfile,
  ChildProfileCreate,
  ChildProfileUpdate,
  ReadingLevel,
  ValidationFieldError,
} from "../api";

// Shape of the editor form. Matches ``ChildProfileCreate`` but with
// strings (never null) so the controlled inputs round-trip cleanly.
// Empty strings convert to ``null`` when shipping to the API.
//
// Phase H step H5: ``banned_themes`` was removed from this form along
// with the per-child column (migration 0009). The new home is the
// global ``BannedThemesSettings`` block under Settings.
interface FormState {
  display_name: string;
  birthdate: string;
  pronouns: string;
  reading_level: ReadingLevel | "";
  interests: string;
  comfort: string;
  notes: string;
}

const EMPTY_FORM: FormState = {
  display_name: "",
  birthdate: "",
  pronouns: "",
  reading_level: "",
  interests: "",
  comfort: "",
  notes: "",
};

function profileToForm(profile: ChildProfile): FormState {
  return {
    display_name: profile.display_name,
    birthdate: profile.birthdate ?? "",
    pronouns: profile.pronouns ?? "",
    reading_level: profile.reading_level ?? "",
    interests: profile.interests ?? "",
    comfort: profile.comfort ?? "",
    notes: profile.notes ?? "",
  };
}

// Convert form state -> POST body. Empty strings become null so the
// backend stores SQL NULL rather than an empty string.
function formToCreatePayload(form: FormState): ChildProfileCreate {
  const nullify = (s: string): string | null => (s.trim() === "" ? null : s);
  return {
    display_name: form.display_name,
    birthdate: nullify(form.birthdate),
    pronouns: nullify(form.pronouns),
    reading_level: form.reading_level === "" ? null : form.reading_level,
    interests: nullify(form.interests),
    comfort: nullify(form.comfort),
    notes: nullify(form.notes),
  };
}

// PATCH bodies are sparse — only send fields that changed from the
// original profile, so we don't overwrite a field a different parent
// edited concurrently. Empty -> null clears the column.
function diffToUpdatePayload(
  original: ChildProfile,
  form: FormState,
): ChildProfileUpdate {
  const out: ChildProfileUpdate = {};
  const nullify = (s: string): string | null => (s.trim() === "" ? null : s);
  if (form.display_name !== original.display_name) {
    out.display_name = form.display_name;
  }
  const next: Record<keyof Omit<ChildProfileCreate, "display_name">, string | null> = {
    birthdate: nullify(form.birthdate),
    pronouns: nullify(form.pronouns),
    reading_level: form.reading_level === "" ? null : form.reading_level,
    interests: nullify(form.interests),
    comfort: nullify(form.comfort),
    notes: nullify(form.notes),
  };
  const original_map: Record<keyof typeof next, string | null> = {
    birthdate: original.birthdate,
    pronouns: original.pronouns,
    reading_level: original.reading_level,
    interests: original.interests,
    comfort: original.comfort,
    notes: original.notes,
  };
  for (const key of Object.keys(next) as (keyof typeof next)[]) {
    if (next[key] !== original_map[key]) {
      // Index assignment is safe: the keys overlap exactly with
      // ChildProfileUpdate's optional fields.
      (out as Record<string, string | null>)[key] = next[key];
    }
  }
  return out;
}

// Group validation errors by field name (loc[1] for body fields). The
// form renders the first error per field under the input.
function fieldErrorMap(
  errors: ValidationFieldError[] | null,
): Record<string, string> {
  if (errors === null) return {};
  const map: Record<string, string> = {};
  for (const e of errors) {
    // FastAPI body validation errors look like ["body","display_name"].
    const field = e.loc.length >= 2 ? String(e.loc[1]) : String(e.loc[0]);
    if (!(field in map)) {
      map[field] = e.msg;
    }
  }
  return map;
}

export interface ChildProfileEditorProps {
  api: ApiClient;
}

export function ChildProfileEditor(
  props: ChildProfileEditorProps,
): JSX.Element {
  const { api } = props;
  const [children, setChildren] = useState<ChildProfile[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [listError, setListError] = useState<string | null>(null);

  // Editing mode: null = list view; "new" = create form; profile = edit form.
  const [editing, setEditing] = useState<ChildProfile | "new" | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  // AbortController spanning one mount of the editor. Recreated on each
  // mount inside the useEffect below — under React 18 StrictMode the
  // mount→cleanup→remount cycle would otherwise leave us with a
  // permanently-aborted signal and every fetch would silently reject
  // with AbortError. Callbacks read `aborterRef.current?.signal` at
  // call time so they always see the live controller, not one captured
  // from a stale render.
  const aborterRef = useRef<AbortController | null>(null);

  const refetch = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const resp = await api.listChildren({
        signal: aborterRef.current?.signal,
      });
      setChildren(resp.children);
      setListError(null);
    } catch (err) {
      if (isAbortError(err)) return;
      const message = err instanceof Error ? err.message : "load failed";
      setListError(message);
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    const aborter = new AbortController();
    aborterRef.current = aborter;
    void refetch();
    return () => {
      aborter.abort();
      if (aborterRef.current === aborter) {
        aborterRef.current = null;
      }
    };
  }, [refetch]);

  const openNew = useCallback((): void => {
    setEditing("new");
    setForm(EMPTY_FORM);
    setFormError(null);
    setFieldErrors({});
  }, []);

  const openEdit = useCallback((profile: ChildProfile): void => {
    setEditing(profile);
    setForm(profileToForm(profile));
    setFormError(null);
    setFieldErrors({});
  }, []);

  const cancel = useCallback((): void => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setFieldErrors({});
  }, []);

  const submit = useCallback(async (): Promise<void> => {
    if (editing === null) return;
    setSubmitting(true);
    setFormError(null);
    setFieldErrors({});
    try {
      const signal = aborterRef.current?.signal;
      if (editing === "new") {
        await api.createChild(formToCreatePayload(form), { signal });
      } else {
        const payload = diffToUpdatePayload(editing, form);
        if (Object.keys(payload).length === 0) {
          // Nothing changed — close the form without an API call.
          cancel();
          return;
        }
        await api.updateChild(editing.id, payload, { signal });
      }
      await refetch();
      cancel();
    } catch (err) {
      // Unmount-during-flight: drop silently — the component is gone, no
      // setState targets remain.
      if (isAbortError(err)) return;
      const validation = extractValidationErrors(err);
      if (validation !== null) {
        setFieldErrors(fieldErrorMap(validation));
        setFormError("Please fix the errors below.");
      } else if (err instanceof ApiError) {
        setFormError(`save failed: ${err.status}`);
      } else if (err instanceof Error) {
        setFormError(`save failed: ${err.message}`);
      } else {
        setFormError("save failed");
      }
    } finally {
      setSubmitting(false);
    }
  }, [api, cancel, editing, form, refetch]);

  const deleteRow = useCallback(
    async (profile: ChildProfile): Promise<void> => {
      if (deletingId !== null) return;
      const ok = window.confirm(
        `Delete profile for ${profile.display_name}? This can't be undone.`,
      );
      if (!ok) return;
      setDeletingId(profile.id);
      setRowError(null);
      try {
        await api.deleteChild(profile.id, {
          signal: aborterRef.current?.signal,
        });
        await refetch();
      } catch (err) {
        if (isAbortError(err)) return;
        const inUse = extractChildInUseDetail(err);
        if (inUse !== null) {
          setRowError(
            `Can't delete — ${inUse.referring_activity_count} ` +
              `activit${inUse.referring_activity_count === 1 ? "y" : "ies"} ` +
              `still reference this profile.`,
          );
        } else if (err instanceof Error) {
          setRowError(`delete failed: ${err.message}`);
        } else {
          setRowError("delete failed");
        }
      } finally {
        setDeletingId(null);
      }
    },
    [api, deletingId, refetch],
  );

  const updateField = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]): void => {
      setForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  return (
    <section
      data-testid="child-profile-editor"
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
          marginBottom: 8,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 17 }}>Child profiles</h2>
        {editing === null && (
          <button
            type="button"
            data-testid="new-child-button"
            onClick={openNew}
          >
            new child
          </button>
        )}
      </div>
      {loading && (
        <p data-testid="children-loading" style={{ color: "#777", fontSize: 13 }}>
          loading...
        </p>
      )}
      {listError !== null && (
        <p
          data-testid="children-list-error"
          role="alert"
          style={{ color: "#b71c1c", fontSize: 13 }}
        >
          {listError}
        </p>
      )}
      {rowError !== null && (
        <p
          data-testid="child-row-error"
          role="alert"
          style={{
            background: "#fdecea",
            border: "1px solid #f5c2c0",
            padding: 8,
            borderRadius: 4,
            fontSize: 13,
          }}
        >
          {rowError}
        </p>
      )}
      {editing === null && !loading && children.length === 0 && (
        <p data-testid="children-empty" style={{ color: "#777", fontSize: 13 }}>
          No child profiles yet. Click "new child" to add one.
        </p>
      )}
      {editing === null && children.length > 0 && (
        <ul
          data-testid="children-list"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {children.map((c) => (
            <li
              key={c.id}
              data-testid="child-row"
              data-child-id={c.id}
              style={{
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "space-between",
                gap: 8,
                padding: "10px 0",
                borderBottom: "1px solid #eee",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div>
                  <button
                    type="button"
                    data-testid="edit-child-button"
                    onClick={() => openEdit(c)}
                    style={{
                      background: "none",
                      border: "none",
                      padding: 0,
                      color: "#1769aa",
                      cursor: "pointer",
                      fontSize: 14,
                      fontWeight: 600,
                    }}
                  >
                    {c.display_name}
                  </button>
                  {c.pronouns !== null && (
                    <span style={{ marginLeft: 8, color: "#777", fontSize: 12 }}>
                      ({c.pronouns})
                    </span>
                  )}
                  {c.birthdate !== null && (
                    <span style={{ marginLeft: 8, color: "#777", fontSize: 12 }}>
                      • {c.birthdate}
                    </span>
                  )}
                  {c.reading_level !== null && (
                    <span style={{ marginLeft: 8, color: "#777", fontSize: 12 }}>
                      • {c.reading_level}
                    </span>
                  )}
                </div>
                {c.interests !== null && (
                  <div
                    data-testid="child-row-interests"
                    style={{ fontSize: 12, color: "#555", marginTop: 4 }}
                  >
                    <span style={{ color: "#888" }}>interests:</span>{" "}
                    {c.interests}
                  </div>
                )}
                {c.comfort !== null && (
                  <div
                    data-testid="child-row-comfort"
                    style={{ fontSize: 12, color: "#555", marginTop: 2 }}
                  >
                    <span style={{ color: "#888" }}>comfort:</span> {c.comfort}
                  </div>
                )}
                {c.notes !== null && (
                  <div
                    data-testid="child-row-notes"
                    style={{ fontSize: 12, color: "#555", marginTop: 2 }}
                  >
                    <span style={{ color: "#888" }}>notes:</span> {c.notes}
                  </div>
                )}
              </div>
              <button
                type="button"
                data-testid="delete-child-button"
                disabled={deletingId === c.id}
                onClick={() => {
                  void deleteRow(c);
                }}
              >
                {deletingId === c.id ? "deleting..." : "delete"}
              </button>
            </li>
          ))}
        </ul>
      )}
      {editing !== null && (
        <form
          data-testid="child-form"
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
          <div>
            <label
              htmlFor="child-display-name"
              style={{ display: "block", fontSize: 13 }}
            >
              Name
            </label>
            <input
              id="child-display-name"
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
            <label
              htmlFor="child-birthdate"
              style={{ display: "block", fontSize: 13 }}
            >
              Birthdate
            </label>
            <input
              id="child-birthdate"
              data-testid="field-birthdate"
              type="date"
              value={form.birthdate}
              onChange={(e) => updateField("birthdate", e.target.value)}
              style={{ padding: 6 }}
            />
            {fieldErrors["birthdate"] !== undefined && (
              <p
                data-testid="error-birthdate"
                role="alert"
                style={{ color: "#b71c1c", fontSize: 12, margin: "2px 0 0" }}
              >
                {fieldErrors["birthdate"]}
              </p>
            )}
          </div>
          <div>
            <label
              htmlFor="child-pronouns"
              style={{ display: "block", fontSize: 13 }}
            >
              Pronouns
            </label>
            <input
              id="child-pronouns"
              data-testid="field-pronouns"
              type="text"
              maxLength={40}
              value={form.pronouns}
              onChange={(e) => updateField("pronouns", e.target.value)}
              style={{ width: "100%", padding: 6 }}
            />
          </div>
          <div>
            <label
              htmlFor="child-reading-level"
              style={{ display: "block", fontSize: 13 }}
            >
              Reading level
            </label>
            <select
              id="child-reading-level"
              data-testid="field-reading-level"
              value={form.reading_level}
              onChange={(e) =>
                updateField(
                  "reading_level",
                  e.target.value as ReadingLevel | "",
                )
              }
              style={{ padding: 6 }}
            >
              <option value="">(none)</option>
              <option value="pre-reader">pre-reader</option>
              <option value="early-reader">early-reader</option>
              <option value="fluent">fluent</option>
            </select>
          </div>
          <div>
            <label
              htmlFor="child-interests"
              style={{ display: "block", fontSize: 13 }}
            >
              Interests
            </label>
            <p
              id="child-interests-help"
              style={{
                margin: "0 0 4px",
                color: "#777",
                fontSize: 12,
              }}
            >
              Things they're into — e.g. dinosaurs, princesses, building,
              music.
            </p>
            <textarea
              id="child-interests"
              data-testid="field-interests"
              aria-describedby="child-interests-help"
              maxLength={1000}
              rows={2}
              value={form.interests}
              onChange={(e) => updateField("interests", e.target.value)}
              style={{ width: "100%", padding: 6 }}
            />
          </div>
          <div>
            <label
              htmlFor="child-comfort"
              style={{ display: "block", fontSize: 13 }}
            >
              Comfort
            </label>
            <p
              id="child-comfort-help"
              style={{
                margin: "0 0 4px",
                color: "#777",
                fontSize: 12,
              }}
            >
              How they do with loud/intense play — e.g. loud_ok,
              prefers_quiet, mixed, or your own notes.
            </p>
            <textarea
              id="child-comfort"
              data-testid="field-comfort"
              aria-describedby="child-comfort-help"
              maxLength={1000}
              rows={2}
              value={form.comfort}
              onChange={(e) => updateField("comfort", e.target.value)}
              style={{ width: "100%", padding: 6 }}
            />
          </div>
          <div>
            <label htmlFor="child-notes" style={{ display: "block", fontSize: 13 }}>
              Notes
            </label>
            <textarea
              id="child-notes"
              data-testid="field-notes"
              maxLength={2000}
              rows={3}
              value={form.notes}
              onChange={(e) => updateField("notes", e.target.value)}
              style={{ width: "100%", padding: 6 }}
            />
          </div>
          {formError !== null && (
            <p
              data-testid="form-error"
              role="alert"
              style={{
                background: "#fdecea",
                border: "1px solid #f5c2c0",
                padding: 8,
                borderRadius: 4,
                fontSize: 13,
              }}
            >
              {formError}
            </p>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="submit"
              data-testid="save-child-button"
              disabled={submitting}
            >
              {submitting ? "saving..." : "save"}
            </button>
            <button
              type="button"
              data-testid="cancel-child-button"
              onClick={cancel}
              disabled={submitting}
            >
              cancel
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
