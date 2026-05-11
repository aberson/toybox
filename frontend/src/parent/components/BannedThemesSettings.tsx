// Phase H step H5: household-global banned-themes editor. Lives inside
// the Settings sub-tab; replaces the per-child banned-themes textarea
// that the pre-H5 ChildProfileEditor carried.
//
// UX: a single textarea pre-populated from ``GET /api/settings/banned-themes``
// + the preset-bundle picker (unchanged from the per-child version) +
// an explicit Save button (NOT debounced). Save is disabled when the
// textarea matches the last-saved value; a dirty indicator appears when
// the value drifts. On save success the last-saved value snaps to the
// server-returned string (which may differ from the typed value because
// the backend strips and clears on empty).
//
// Rationale for explicit Save (per the plan's decision log): the
// surrounding SettingsPanel toggles are boolean/enum (immediate-write
// makes sense — one click, one persistent decision); a free-text
// textarea with debounced per-keystroke writes would be noisy and
// surprising for a setting the operator is likely tuning carefully.

import type { JSX } from "react";
import { useCallback, useEffect, useState } from "react";

import { isAbortError } from "../api";
import type { ApiClient } from "../api";
import {
  BANNED_THEME_PRESETS,
  findPreset,
  mergeBannedThemes,
} from "./bannedThemePresets";

export interface BannedThemesSettingsProps {
  api: Pick<ApiClient, "getBannedThemesGlobal" | "setBannedThemesGlobal">;
}

// Normalize ``null`` to ``""`` so the controlled textarea always has a
// concrete string value. The empty string is the "no global list" UX
// state and round-trips to ``null`` on the wire.
function fromWire(themes: string | null): string {
  return themes ?? "";
}

export function BannedThemesSettings(
  props: BannedThemesSettingsProps,
): JSX.Element {
  const { api } = props;
  // ``value`` is the textarea's live content. ``lastSaved`` is the
  // server's canonical state — Save is disabled when these match.
  const [value, setValue] = useState<string>("");
  const [lastSaved, setLastSaved] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState<boolean>(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [selectedPresetId, setSelectedPresetId] = useState<string>("");

  // Initial GET. AbortController cancels the in-flight fetch on
  // unmount so a remount doesn't fire a stale setState.
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api
      .getBannedThemesGlobal({ signal: controller.signal })
      .then((resp) => {
        const initial = fromWire(resp.themes);
        setValue(initial);
        setLastSaved(initial);
        setLoadError(null);
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        const message =
          err instanceof Error ? err.message : "load banned themes failed";
        setLoadError(message);
      })
      .finally(() => {
        setLoading(false);
      });
    return () => {
      controller.abort();
    };
  }, [api]);

  const dirty = value !== lastSaved;

  const handleSave = useCallback((): void => {
    if (!dirty || saving) return;
    setSaving(true);
    setSaveError(null);
    // Empty-after-strip becomes ``null`` on the wire — the backend
    // deletes the row on null/empty and a subsequent GET returns null.
    const payload = value.trim() === "" ? null : value;
    api
      .setBannedThemesGlobal(payload)
      .then((resp) => {
        const persisted = fromWire(resp.themes);
        setLastSaved(persisted);
        // Reconcile the textarea with whatever the server actually
        // stored — typically the same string, but e.g. an empty input
        // round-trips to "" via the null branch.
        setValue(persisted);
        setSaving(false);
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) {
          setSaving(false);
          return;
        }
        const message =
          err instanceof Error ? err.message : "save banned themes failed";
        setSaveError(message);
        setSaving(false);
      });
  }, [api, dirty, saving, value]);

  const appendSelectedPreset = useCallback((): void => {
    const preset = findPreset(selectedPresetId);
    if (preset === null) return;
    setValue((prev) => mergeBannedThemes(prev, preset.themes));
    setSelectedPresetId("");
  }, [selectedPresetId]);

  const selectedPreset = findPreset(selectedPresetId);

  return (
    <section
      data-testid="banned-themes-settings"
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: 6,
        padding: 12,
        background: "#fff",
      }}
    >
      <h3 style={{ fontSize: 14, margin: "0 0 4px 0", color: "#374151" }}>
        Banned themes (household-wide)
      </h3>
      <p
        style={{
          fontSize: 12,
          color: "#6b7280",
          margin: "0 0 8px 0",
          lineHeight: 1.4,
        }}
      >
        Comma-separated list of themes the AI should avoid. Applies to
        every child in the household. The escalation pipeline already
        unioned per-child lists; this just makes the single list explicit.
      </p>
      {loading && (
        <p
          data-testid="banned-themes-loading"
          style={{ color: "#6b7280", fontSize: 12 }}
        >
          loading…
        </p>
      )}
      {loadError !== null && (
        <p
          data-testid="banned-themes-load-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 12 }}
        >
          {loadError}
        </p>
      )}
      <div
        data-testid="banned-themes-preset-picker"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          margin: "4px 0",
          flexWrap: "wrap",
        }}
      >
        <label
          htmlFor="banned-themes-preset"
          style={{ fontSize: 12, color: "#374151" }}
        >
          Add preset bundle:
        </label>
        <select
          id="banned-themes-preset"
          data-testid="banned-themes-preset-select"
          value={selectedPresetId}
          onChange={(e) => setSelectedPresetId(e.target.value)}
          style={{ padding: 4, fontSize: 12 }}
        >
          <option value="">(choose a bundle…)</option>
          {BANNED_THEME_PRESETS.map((p) => (
            <option
              key={p.id}
              value={p.id}
              data-testid={`banned-themes-preset-${p.id}`}
            >
              {p.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          data-testid="banned-themes-preset-append"
          onClick={appendSelectedPreset}
          disabled={selectedPreset === null}
          style={{ fontSize: 12 }}
        >
          append to list
        </button>
      </div>
      {selectedPreset !== null && (
        <div
          data-testid="banned-themes-preset-preview"
          style={{
            background: "#e5e7eb",
            border: "1px solid #d1d5db",
            borderRadius: 4,
            padding: 8,
            margin: "0 0 6px",
            fontSize: 12,
            color: "#374151",
          }}
        >
          <div style={{ marginBottom: 4 }}>{selectedPreset.description}</div>
          <div>
            <span style={{ color: "#6b7280" }}>themes:</span>{" "}
            {selectedPreset.themes.join(", ")}
          </div>
        </div>
      )}
      <textarea
        id="banned-themes-textarea"
        data-testid="banned-themes-textarea"
        aria-label="banned themes (comma-separated)"
        maxLength={2000}
        rows={4}
        value={value}
        disabled={loading}
        onChange={(e) => setValue(e.target.value)}
        style={{ width: "100%", padding: 6, fontSize: 12, fontFamily: "monospace" }}
      />
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginTop: 6,
          flexWrap: "wrap",
        }}
      >
        <button
          type="button"
          data-testid="banned-themes-save-button"
          onClick={handleSave}
          disabled={!dirty || saving}
          style={{ fontSize: 12, padding: "4px 10px" }}
        >
          {saving ? "saving…" : "Save"}
        </button>
        {dirty && !saving && (
          <span
            data-testid="banned-themes-dirty-indicator"
            style={{ fontSize: 11, color: "#b45309" }}
          >
            unsaved changes
          </span>
        )}
      </div>
      {saveError !== null && (
        <p
          data-testid="banned-themes-save-error"
          role="alert"
          style={{ color: "#b91c1c", fontSize: 12, marginTop: 6 }}
        >
          {saveError}
        </p>
      )}
    </section>
  );
}
