// Child kiosk REST client. Mirrors the parent `api.ts` shape: injects
// X-Toybox-Token, serializes If-Match-Version, normalizes 409 into a
// typed VersionConflictError, and exposes withConflictHandler so the
// next-step button can refetch + toast without blind retry.
//
// Phase A note: the kiosk authenticates with a parent-scope token
// (POST /api/auth/parent). A dedicated child/kiosk pairing flow
// arrives in Phase D Step 20. The endpoint surface used here
// (advance/getActivity/issueParentToken) is the minimal set the kiosk
// needs to drive an approved activity to completion.
//
// Shapes mirror src/toybox/api/activities.py and core/version_check.py.

export type ActivityState =
  | "proposed"
  | "approved"
  | "running"
  | "completed"
  | "ended"
  | "dismissed"
  | "didnt_work";

// Phase G G4: runtime activity-step choice option. The kiosk receives
// one of these per branch when the current step is a choice point;
// derived by the API serializer from ``activity_steps.choices_json``
// (a JSON array of pre-rendered label strings) — array index becomes
// ``choice_index``. The shared template-time ``Choice`` shape (with a
// ``next`` field) lives in ``shared/types.ts``; the kiosk does NOT
// consume that one — it only reads pre-rendered labels + indices.
//
// Defined locally here (vs. regenerated into ``shared/types.ts``)
// because: (a) G3 is the authoritative source for the runtime
// activity-step response shape and may evolve the field; (b) the
// kiosk's ``ActivityStep`` already lives in this file, so colocating
// the choice option keeps the kiosk's runtime types in one place;
// (c) ``shared/types.ts`` documents on lines 36-40 that runtime
// shapes belong in ``frontend/src/child/api.ts``. When G3's codegen
// lands the runtime ``ActivityStep`` upstream, this interface and
// the ``choices`` field can be replaced by an import — no other
// kiosk file needs to change.
export interface ChoiceOption {
  label: string;
  choice_index: number;
}

export interface ActivityStep {
  seq: number;
  body: string;
  sfx: string | null;
  expected_action: string | null;
  current: boolean;
  // Phase F Step F6/F7: per-step toy-action vocabulary key (one of the
  // 10 ACTION_SLOTS) or null when the step has no associated sprite.
  // F7 reads this to decide whether to render a ToyActionSprite next
  // to the step body. Optional on the wire so legacy envelopes /
  // pre-F6 fixtures continue to typecheck without an explicit null.
  action_slot?: string | null;
  // Phase G G4: when non-null and non-empty, this step is a branch
  // point — the kiosk renders one ``<ChoiceButton>`` per entry and
  // posts ``{choice_index}`` to ``/advance`` instead of the bare
  // ``<NextStepButton>``. Optional + nullable so pre-G3 wire payloads
  // (which omit the field entirely) continue to typecheck and render
  // the linear-advance path.
  choices?: ChoiceOption[] | null;
  // Phase K K3 / K12: per-step discriminator. ``"text"`` is the
  // implicit default for legacy + pre-K12 wire payloads — StepCard
  // reads this defensively via ``readStepKind`` and falls back to
  // "text" when the field is absent. K12 dispatches on this field to
  // mount SongPlayer / JokeStep instead of the default text+choices
  // path. Optional + nullable so pre-K3 wire payloads typecheck.
  // Phase L L10: ``"reward"`` is the terminal-step kind appended by
  // ``_terminal_advance`` when an activity ends — the kiosk's
  // RewardStep dispatches on ``metadata.reward_kind`` to render the
  // picture / joke / song reward.
  // Phase W Step W4: ``"adventure_beat"`` is a dynamically generated
  // adventure beat (body + choices + Next via the default text/fork path).
  // Phase W Step W5: ``"boss_fight"`` is the interactive climax beat — the
  // kiosk renders a distinct, STATIC "BOSS" banner above the default
  // body + defeat-choices path.
  kind?:
    | "text"
    | "fork"
    | "song"
    | "joke"
    | "reward"
    | "adventure_beat"
    | "boss_fight"
    | null;
  // Phase K K12: arbitrary per-step metadata. Today's known keys:
  //   * ``audio_url`` (string) — absolute or backend-relative URL for
  //     a song step's mp3. Read by SongPlayer when present.
  //   * ``song_id`` (string) — corpus id; SongPlayer falls back to
  //     ``/api/static/songs/audio/<id>.mp3`` when ``audio_url`` is
  //     absent. K13's standalone surface is expected to populate
  //     either field.
  //   * ``punchline`` (string) — the reveal beat for a joke step;
  //     JokeStep reads it 1.5s after the setup speaks.
  //   * ``interjection`` (string) — one of ``"embedded" |
  //     "ending" | "parent" | "spontaneity"`` per K14/K15; today
  //     the kiosk does NOT surface this to the kid (invisible
  //     metadata so the step feels like any other step).
  //   * ``source_id`` (string) — corpus entry id of the song/joke for
  //     learning-loop telemetry.
  // The shape is ``Record<string, unknown>`` because the wire is
  // pydantic ``dict[str, Any]`` (invariant 9 leaves ``metadata``
  // un-codegenned). Consumers read defensively per K12's
  // "render even on a malformed envelope" contract.
  //
  // Phase M Step M3: when ``element_id`` is non-null, the backend
  // serializer also denormalizes the corpus fields into ``metadata``
  // so ElementCard can render without a separate fetch:
  //   * ``element_id`` (string) — mirrored from the top-level field.
  //   * ``element_symbol`` (string) — display-case symbol (e.g. ``Au``).
  //   * ``element_name`` (string) — common name (e.g. ``Gold``).
  //   * ``element_atomic_number`` (number) — atomic number (1-118).
  //
  // Phase Z Z4/Z5: server-rendered neural TTS clip URLs (all
  // ``/api/static/tts/<voice>/<sha16>.wav``; a URL may 404 until the
  // background worker renders it — the kiosk falls back to Web Speech,
  // designed behavior). Read via the typed accessors in
  // ``clip-audio.ts`` (the one place the key literals live):
  //   * ``spoken_audio_url`` (string) — plain step body clip.
  //   * ``spoken_audio_setup_url`` / ``spoken_audio_punchline_url``
  //     (string) — joke-kind steps, incl. reward jokes.
  //   * ``spoken_choice_audio_urls`` (string[]) — aligned
  //     index-for-index with ``choices``.
  //   * ``spoken_question_audio_url`` (string) — present when the step
  //     carries R3 question text; NOT yet consumed (question text is
  //     rendered, never spoken — no kiosk surface exists today).
  metadata?: Record<string, unknown> | null;
  // Phase M Step M3: optional reference to a Periodic Table element
  // corpus entry. When non-null, the kiosk's StepCard renders an
  // ElementCard above the step body. Format ``<symbol-lower>-<atomic-
  // number>`` (e.g. ``au-79``); the backend's _validator gates the
  // shape and resolves to a real corpus entry at template load.
  // Optional + nullable so pre-M3 wire payloads typecheck.
  element_id?: string | null;
  // Phase R Step R3: optional Q&A gating fields. ``question`` is the
  // text the child is asked (shown in StepCard below the step body).
  // ``question_pending`` is true when the question exists AND the
  // parent has not yet approved or skipped it — the kiosk hides the
  // Next button and shows "Waiting for parent…". Both are absent on
  // the overwhelming majority of steps (no Q&A on most templates).
  question?: string | null;
  question_pending?: boolean;
}

/**
 * Phase K K5: one resolved cast member on an activity. The kiosk's
 * persona-avatar / sprite resolvers do NOT yet consume this shape
 * (K7 wires the parent UI cast panel; the kiosk currently keeps
 * rendering the activity-level toy_ids sprite). Declared on the
 * Activity interface for completeness so a future kiosk cast-aware
 * sprite swap can compile.
 */
export interface RoleAssignment {
  role_name: string;
  toy_id: string | null;
  generic_descriptor: string | null;
  display_name: string;
}

export interface Activity {
  id: string;
  state: ActivityState;
  version: number;
  title: string | null;
  summary: string | null;
  persona_id: string | null;
  intent_source: string | null;
  child_ids: string[];
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  steps: ActivityStep[];
  metadata: Record<string, unknown>;
  // Phase F Step F7: list of toy ids associated with this activity.
  // The kiosk's ToyActionSprite renders the sprite for the FIRST id
  // (deterministic) when both ``action_slot`` on the current step AND
  // a non-empty ``toy_ids`` are present. Optional because the wire
  // shape may not expose this yet for pre-F7 callers; missing/empty
  // → no sprite, same as today.
  toy_ids?: string[];
  // Phase K K5: resolved role-slot cast (keyed by lowercase role name).
  // Optional + may be {} for pre-K5 activities or templates with no
  // declared roles.
  roles?: Record<string, RoleAssignment>;
  // Phase K K5: comma-separated cast summary string. Optional + may be
  // "" when ``roles`` is empty.
  cast_summary?: string;
  // Phase Y: kiosk scene-backdrop URL, denormalized from the persisted
  // ``activities.scene_id`` (``/api/static/images/scenes/<scene_id>.png``).
  // null/absent when no scene resolved (legacy rows) — the kiosk renders no
  // backdrop (prior flat-gradient look). StepCard reads this as the
  // full-viewport backdrop ``<img>`` src.
  scene_url?: string | null;
}

export interface VersionConflictBody {
  code: "version_conflict";
  current_version: number;
  current_state: string;
}

export interface ParentTokenResponse {
  token: string;
  expires_at: number;
  subject: { kind: "parent" };
}

// Phase K step K2: parent-controlled feature flags consumed by the
// kiosk. Types + defaults live in ``../shared/feature_flags`` — both
// the parent UI and the kiosk import from there so a future ninth
// flag is a single edit (code-quality §2). The source-of-truth-lock
// test in ``tests/integration/test_phase_k_feature_flag_lists_agree.py``
// guards drift against the backend.
//
// ``KioskFeatureFlag`` / ``KioskFeatureFlags`` are re-exported aliases
// so kiosk call sites stay grep-friendly (the kiosk-leaning name
// remains in the kiosk module) while the underlying type IS the
// shared declaration — TS will fail the build at compile time if
// they ever drift.
//
// We BOTH ``import type`` (so the names resolve inside this file's
// body — the ``getFeatureFlag`` method references both
// ``KioskFeatureFlag`` and ``FeatureFlagResponse``) AND re-export.
import type {
  FeatureFlagResponse,
  PhaseKFeatureFlag,
  PhaseKFeatureFlags,
} from "../shared/feature_flags";

export type {
  FeatureFlagResponse,
  PhaseKFeatureFlag,
  PhaseKFeatureFlags,
} from "../shared/feature_flags";
export { PHASE_K_FEATURE_FLAG_DEFAULTS } from "../shared/feature_flags";

// Kiosk-facing aliases — identical type, kept for grep-friendliness
// at kiosk call sites. They resolve to the shared declarations.
export type KioskFeatureFlag = PhaseKFeatureFlag;
export type KioskFeatureFlags = PhaseKFeatureFlags;

// Backwards-compat re-export under the kiosk-leaning name so existing
// kiosk callers keep working. Resolves to the same object as the
// parent UI's ``PHASE_K_FEATURE_FLAG_DEFAULTS`` (literally the same
// reference at runtime — one source of truth).
export { PHASE_K_FEATURE_FLAG_DEFAULTS as KIOSK_FEATURE_FLAG_DEFAULTS } from "../shared/feature_flags";

// Kiosk-only routing concern: the kebab-case URL paths used by the
// kiosk's bootstrap fetcher. Not part of shared/ because the parent
// UI's ApiClient embeds these paths in per-flag setter methods rather
// than routing through a flag-keyed dict; this lookup is a kiosk-only
// pattern. Drift is guarded by the source-of-truth-lock test which
// reads paths from the backend per-setting modules.
export const KIOSK_FEATURE_FLAG_PATHS: Readonly<
  Record<KioskFeatureFlag, string>
> = {
  jokes_enabled: "/api/settings/jokes-enabled",
  songs_enabled: "/api/settings/songs-enabled",
  play_standalone_enabled: "/api/settings/play-standalone-enabled",
  clickable_words_enabled: "/api/settings/clickable-words-enabled",
  read_me_button_enabled: "/api/settings/read-me-button-enabled",
  neural_voice_enabled: "/api/settings/neural-voice-enabled",
};

// Phase R Step R2: spoken text character limit wire shape. The kiosk
// fetches ``GET /api/settings/spoken-text-limit`` on boot (unauthenticated
// household read) and passes the value down to StepCard → ReadMeButton.
//
// ``SpokenTextLimit`` is a literal union of the five canonical presets so
// the compiler catches any future mis-wiring of a raw ``number`` against a
// prop that expects one of the valid values (0=off, others are char limits).
export type SpokenTextLimit = 0 | 50 | 100 | 150 | 250;

export interface SpokenTextLimitResponse {
  value: SpokenTextLimit;
}

// Image-gen mode (mirrors backend ``ImageGenMode``). The kiosk only
// cares whether it's ``claude_svg`` (→ prefer the .svg sprite); the
// other values render the .png as before.
export type ImageGenMode = "cartoon" | "composite" | "claude_svg";

export interface ImageGenModeResponse {
  mode: ImageGenMode;
}

// Step 21: ``POST /api/auth/parent`` is now PIN-gated. The kiosk does
// not own the PIN and will get its token from the parent UI via the
// kiosk pairing flow (``POST /api/auth/pair``). Until that landing
// step ships, the kiosk has no usable bootstrap token; the
// ``issueParentToken`` call below is kept for type-safety but expects
// the caller to provide a PIN (the test path mocks the response).
export interface ParentLoginRequest {
  pin: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `api error ${status}`);
    this.status = status;
    this.body = body;
  }
}

export class VersionConflictError extends ApiError {
  readonly conflict: VersionConflictBody;
  constructor(conflict: VersionConflictBody) {
    super(409, conflict, "version_conflict");
    this.conflict = conflict;
  }
}

export interface FetchLike {
  (input: string, init?: RequestInit): Promise<Response>;
}

export interface ApiClientOptions {
  baseUrl?: string;
  fetchImpl?: FetchLike;
  getToken?: () => string | null;
}

export interface RequestOptions {
  signal?: AbortSignal;
}

function isVersionConflict(body: unknown): body is VersionConflictBody {
  if (typeof body !== "object" || body === null) return false;
  const rec = body as Record<string, unknown>;
  // FastAPI HTTPException wraps the body under `detail`. Both shapes
  // appear in the wild depending on whether middleware unwraps.
  const candidate = "detail" in rec ? rec["detail"] : rec;
  if (typeof candidate !== "object" || candidate === null) return false;
  const c = candidate as Record<string, unknown>;
  return (
    c["code"] === "version_conflict" &&
    typeof c["current_version"] === "number" &&
    typeof c["current_state"] === "string"
  );
}

function unwrapConflict(body: unknown): VersionConflictBody {
  if (typeof body === "object" && body !== null) {
    const rec = body as Record<string, unknown>;
    const candidate = "detail" in rec ? rec["detail"] : rec;
    return candidate as VersionConflictBody;
  }
  throw new Error("not a conflict body");
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: FetchLike;
  private readonly getToken: () => string | null;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? "";
    this.fetchImpl = options.fetchImpl ?? ((input, init) => fetch(input, init));
    this.getToken = options.getToken ?? (() => null);
  }

  private async request<T>(
    path: string,
    init: RequestInit & {
      ifMatchVersion?: number;
      signal?: AbortSignal;
    } = {},
  ): Promise<T> {
    const headers = new Headers(init.headers ?? {});
    const token = this.getToken();
    if (token !== null && !headers.has("X-Toybox-Token")) {
      headers.set("X-Toybox-Token", token);
    }
    if (init.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (init.ifMatchVersion !== undefined) {
      headers.set("If-Match-Version", String(init.ifMatchVersion));
    }
    const resp = await this.fetchImpl(this.baseUrl + path, {
      ...init,
      headers,
      signal: init.signal,
    });
    if (resp.status === 204) {
      return undefined as T;
    }
    let body: unknown = null;
    const text = await resp.text();
    if (text.length > 0) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    if (!resp.ok) {
      if (resp.status === 409 && isVersionConflict(body)) {
        throw new VersionConflictError(unwrapConflict(body));
      }
      throw new ApiError(resp.status, body);
    }
    return body as T;
  }

  async issueParentToken(
    body: ParentLoginRequest,
    opts: RequestOptions = {},
  ): Promise<ParentTokenResponse> {
    return this.request<ParentTokenResponse>("/api/auth/parent", {
      method: "POST",
      body: JSON.stringify(body),
      signal: opts.signal,
    });
  }

  // Phase K step K2: kiosk bootstrap fetcher for one of the eight
  // feature flags. Wire shape is identical for all eight, so a single
  // method indexed by ``KioskFeatureFlag`` keeps the kiosk's
  // ApiClient narrow (the parent UI's ApiClient exposes a per-flag
  // method per the per-setting-module convention; the kiosk has no
  // writers, only reads). One source of truth: the path comes from
  // ``KIOSK_FEATURE_FLAG_PATHS`` — no per-call-site string literal.
  async getFeatureFlag(
    flag: KioskFeatureFlag,
    opts: RequestOptions = {},
  ): Promise<FeatureFlagResponse> {
    return this.request<FeatureFlagResponse>(
      KIOSK_FEATURE_FLAG_PATHS[flag],
      { method: "GET", signal: opts.signal },
    );
  }

  // Phase R Step R2: kiosk bootstrap fetcher for the spoken text limit.
  // Unauthenticated household read (matches getFeatureFlag). The value
  // is passed down to StepCard → ReadMeButton so TTS is truncated at
  // the parent-configured limit.
  async getSpokenTextLimit(
    opts: RequestOptions = {},
  ): Promise<SpokenTextLimitResponse> {
    return this.request<SpokenTextLimitResponse>(
      "/api/settings/spoken-text-limit",
      { method: "GET", signal: opts.signal },
    );
  }

  // Image-gen mode (unauthenticated household read). The kiosk reads it
  // so it can prefer the Claude-authored ``.svg`` sprite over the PNG
  // when the operator picked the "claude_svg" mode — threaded to
  // StepCard → ToyActionSprite (`preferSvg`).
  async getImageGenMode(
    opts: RequestOptions = {},
  ): Promise<ImageGenModeResponse> {
    return this.request<ImageGenModeResponse>("/api/settings/image-gen-mode", {
      method: "GET",
      signal: opts.signal,
    });
  }

  async getActivity(id: string, opts: RequestOptions = {}): Promise<Activity> {
    return this.request<Activity>(`/api/activities/${encodeURIComponent(id)}`, {
      method: "GET",
      signal: opts.signal,
    });
  }

  // Phase G G4: ``choiceIndex`` is the optional choice the kid picked
  // when the current step has ``choices``. Posted in the body as
  // ``{choice_index}`` so the backend's edge resolver can pick the
  // right successor (G3). Linear advance steps (no choices) call this
  // without ``choiceIndex`` and the body is omitted, matching the
  // pre-G3 wire shape.
  async advance(
    id: string,
    version: number,
    opts: RequestOptions & { choiceIndex?: number } = {},
  ): Promise<Activity> {
    const body =
      opts.choiceIndex !== undefined
        ? JSON.stringify({ choice_index: opts.choiceIndex })
        : undefined;
    return this.request<Activity>(
      `/api/activities/${encodeURIComponent(id)}/advance`,
      {
        method: "POST",
        ifMatchVersion: version,
        signal: opts.signal,
        body,
      },
    );
  }
}

export function isAbortError(err: unknown): boolean {
  if (err === null || typeof err !== "object") return false;
  const name = (err as { name?: unknown }).name;
  return name === "AbortError";
}

// True for transient failures that warrant a retry-with-backoff during
// bootstrap: network glitches (TypeError from fetch with no response)
// and 5xx server errors (e.g. the iter-1 SQLite cross-thread regression
// in /api/auth/parent). 4xx and AbortError are NOT retryable: 4xx is
// the server actively rejecting and AbortError means the caller asked
// us to stop.
export function isTransientError(err: unknown): boolean {
  if (isAbortError(err)) return false;
  if (err instanceof ApiError) {
    return err.status >= 500 && err.status < 600;
  }
  // Native fetch raises TypeError on network errors (DNS, conn refused,
  // CORS preflight failure). Treat any non-Api Error as a network blip
  // so we retry once during bootstrap rather than freezing the kiosk.
  return err instanceof Error;
}

export interface RetryOptions {
  attempts?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  // Test seam: deterministic sleep + jitter. Defaults to setTimeout +
  // Math.random when omitted.
  sleep?: (ms: number) => Promise<void>;
  jitter?: () => number;
  // Test seam: classifier for retryable errors (defaults to
  // isTransientError). Lets unit tests force the retry / no-retry path
  // without monkey-patching the module-level function.
  shouldRetry?: (err: unknown) => boolean;
}

const DEFAULT_RETRY_ATTEMPTS = 3;
const DEFAULT_RETRY_BASE_MS = 1_000;
const DEFAULT_RETRY_MAX_MS = 8_000;

// Run ``op`` with exponential backoff on transient errors (5xx +
// network). Re-throws on non-transient errors immediately and on the
// final attempt. Used by App.tsx bootstrap so an intermittent
// /api/auth/parent failure (the iter-1 SQLite cross-thread bug) doesn't
// freeze the kiosk into the idle screen forever.
export async function retryWithBackoff<T>(
  op: () => Promise<T>,
  opts: RetryOptions = {},
): Promise<T> {
  const attempts = opts.attempts ?? DEFAULT_RETRY_ATTEMPTS;
  const baseDelayMs = opts.baseDelayMs ?? DEFAULT_RETRY_BASE_MS;
  const maxDelayMs = opts.maxDelayMs ?? DEFAULT_RETRY_MAX_MS;
  const sleep =
    opts.sleep ??
    ((ms: number) => new Promise<void>((res) => setTimeout(res, ms)));
  const jitter = opts.jitter ?? (() => Math.random());
  const shouldRetry = opts.shouldRetry ?? isTransientError;

  let lastErr: unknown = null;
  for (let i = 0; i < attempts; i += 1) {
    try {
      return await op();
    } catch (err) {
      lastErr = err;
      if (!shouldRetry(err) || i === attempts - 1) throw err;
      const exp = baseDelayMs * 2 ** i;
      const capped = Math.min(exp, maxDelayMs);
      // jitter in [0.5, 1.5) so adjacent kiosks don't lock-step their
      // retries against the same backend.
      const jittered = capped * (0.5 + jitter());
      await sleep(jittered);
    }
  }
  // Defensive: the loop returns or throws, but TS wants an explicit
  // throw at the bottom.
  throw lastErr;
}

// 409 handler for the kiosk advance flow: wrap the mutation; on
// version_conflict, refetch the activity, fire a toast, and return
// null without retrying.
export interface ConflictHandlerArgs<T> {
  mutation: () => Promise<T>;
  onConflict: (conflict: VersionConflictBody, fresh: Activity | null) => void;
  refetch: () => Promise<Activity | null>;
}

export async function withConflictHandler<T>(
  args: ConflictHandlerArgs<T>,
): Promise<T | null> {
  try {
    return await args.mutation();
  } catch (err) {
    if (err instanceof VersionConflictError) {
      let fresh: Activity | null = null;
      try {
        fresh = await args.refetch();
      } catch {
        fresh = null;
      }
      args.onConflict(err.conflict, fresh);
      return null;
    }
    throw err;
  }
}
