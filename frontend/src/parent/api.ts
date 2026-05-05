// Parent UI REST client. Injects X-Toybox-Token, serializes
// If-Match-Version, and normalizes 409 (version_conflict) into a typed
// error. All routes hit /api/* (vite dev proxy forwards to :8000).
// Shapes mirror src/toybox/api/activities.py and core/version_check.py.

export type ActivityState =
  | "proposed"
  | "approved"
  | "running"
  | "paused"
  | "completed"
  | "ended"
  | "dismissed"
  | "didnt_work";

export interface ActivityStep {
  seq: number;
  body: string;
  sfx: string | null;
  expected_action: string | null;
  current: boolean;
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
  // Step 23: "why this?" telemetry surfaced on the suggestion card.
  // ``trigger_phrase`` is the literal substring of the transcript that
  // fired the trigger; null when manually proposed. ``persona_reasoning``
  // is a short rationale for the chosen persona — the backend
  // synthesises a default when the propose call didn't supply one, so
  // the field is null only on pre-step-23 activities.
  trigger_phrase: string | null;
  persona_reasoning: string | null;
}

export interface VersionConflictBody {
  code: "version_conflict";
  current_version: number;
  current_state: string;
}

export interface HealthResponse {
  ok: boolean;
  capability_reason: string | null;
}

export interface ParentTokenResponse {
  token: string;
  expires_at: number;
  subject: { kind: "parent" };
}

// Step 21: PIN-gate wire shapes. Mirror the Pydantic models in
// src/toybox/api/auth.py.

// GET /api/auth/parent/status — pre-token bootstrap probe. Used to
// decide between the first-run PinSetup screen and the recurring
// PinLogin screen, plus to surface an active lockout countdown
// without first issuing a (failing) login attempt.
export interface ParentAuthStatus {
  pin_set: boolean;
  locked: boolean;
  // Integer seconds remaining on a lock; 0 when not locked.
  seconds_until_unlock: number;
}

// Body for POST /api/auth/parent (login). Digits-only PIN, 4-12 chars.
export interface ParentLoginRequest {
  pin: string;
}

// Body for POST /api/auth/parent/setup (first-run only).
export interface ParentSetupRequest {
  pin: string;
  confirm: string;
}

// Detail body for the 401 returned when a wrong PIN is submitted.
export interface PinInvalidDetail {
  code: "pin_invalid";
  attempts_remaining: number;
}

// Detail body for the 423 (Locked) returned during lockout. The
// ``Retry-After`` header carries the same integer.
export interface PinLockedDetail {
  code: "pin_locked";
  seconds_until_unlock: number;
}

export interface ProposePayload {
  intent: string;
  slot?: string | null;
  hour: number;
  seed: number;
  persona_id?: string | null;
  session_id?: string | null;
  context?: Record<string, unknown> | null;
  // Step 23: optional "why this?" telemetry (see Activity).
  trigger_phrase?: string | null;
  persona_reasoning?: string | null;
}

// Step 18: child-profile editor wire shapes. Mirror the Pydantic
// models in src/toybox/api/children.py.
export type ReadingLevel = "pre-reader" | "early-reader" | "fluent";

export interface ChildProfile {
  id: string;
  display_name: string;
  birthdate: string | null;
  pronouns: string | null;
  reading_level: ReadingLevel | null;
  interests: string | null;
  comfort: string | null;
  banned_themes: string | null;
  notes: string | null;
}

// POST body. display_name is required; everything else is optional.
// Send `null` to leave a column unset on creation.
export interface ChildProfileCreate {
  display_name: string;
  birthdate?: string | null;
  pronouns?: string | null;
  reading_level?: ReadingLevel | null;
  interests?: string | null;
  comfort?: string | null;
  banned_themes?: string | null;
  notes?: string | null;
}

// PATCH body. All fields are optional; only fields present in the
// object are written. Pass `null` for an optional field to clear it.
export type ChildProfileUpdate = Partial<ChildProfileCreate>;

export interface ChildProfileListResponse {
  children: ChildProfile[];
}

// Body of the 409 returned by DELETE /api/children/{id} when an
// activity still references the profile. The frontend reads
// `referring_activity_count` to render the "can't delete — N activities
// still reference this profile" message.
export interface ChildInUseDetail {
  code: "child_in_use";
  child_id: string;
  referring_activity_count: number;
}

// Step 16: toy ingest wire shapes. Mirror the Pydantic models in
// src/toybox/api/toys.py.
export interface Toy {
  id: string;
  display_name: string;
  image_path: string;
  image_hash: string;
  tags: string[];
  persona_id: string | null;
  archived: boolean;
  created_at: string;
  last_used_at: string | null;
}

export interface ToyVisionSuggestion {
  display_name: string;
  tags: string[];
  persona_match_id: string | null;
}

// Response from POST /api/toys/upload. ``suggested`` is null when
// vision failed or was skipped (offline mode). When vision returned a
// rate-limit / timeout / malformed-JSON, ``vision_error`` carries the
// short reason string so the UI can surface it. ``vision_skipped`` is
// true when Claude isn't capable (no token, breaker open, etc.) and
// no vision call was attempted at all.
export interface ToyUploadResponse {
  staging_id: string;
  image_hash: string;
  suggested: ToyVisionSuggestion | null;
  vision_error: string | null;
  vision_skipped: boolean;
  media_type: string;
  width: number;
  height: number;
}

// Body of the 409 returned by POST /api/toys/upload when the
// SHA-256 hash matches an existing non-archived toy. The frontend
// uses this to surface "this image already exists, view existing
// toy" with a link.
export interface ToyImageExistsDetail {
  code: "image_already_exists";
  existing_toy: Toy;
}

// Body for POST /api/toys (commit). ``staging_id`` comes from the
// upload response.
export interface ToyConfirmRequest {
  staging_id: string;
  display_name: string;
  tags: string[];
  persona_id?: string | null;
}

// Body for PATCH /api/toys/{id}. All fields optional; only fields
// present in the body are written.
export interface ToyUpdateRequest {
  display_name?: string;
  tags?: string[];
  persona_id?: string | null;
  archived?: boolean;
}

export interface ToyListResponse {
  toys: Toy[];
}

// Step 17: room ingest bulk wire shapes. Mirror the Pydantic models in
// src/toybox/api/rooms.py.
export interface Room {
  id: string;
  display_name: string;
  image_path: string | null;
  image_hash: string | null;
  notes: string | null;
}

export interface RoomFeature {
  id: string;
  room_id: string;
  name: string;
}

export interface FeatureSuggestion {
  name: string;
}

export interface HouseVisionSuggestion {
  suggested_room_label: string;
  features: FeatureSuggestion[];
}

// One photo's status inside a bulk-upload response. The fields are
// ordered by the parent UI's read priority: ``error`` first (validation
// / dedup rejection), then ``vision_error`` (Claude failed for this
// photo specifically — parent assigns from the Unassigned tab), then
// ``suggested`` (vision succeeded — render in the matching room tab).
export interface BulkPhoto {
  staging_id: string;
  image_hash: string;
  filename: string;
  suggested: HouseVisionSuggestion | null;
  vision_error: string | null;
  error: string | null;
  existing_room: Room | null;
}

export interface RoomBulkUploadResponse {
  batch_id: string;
  photos: BulkPhoto[];
  vision_skipped: boolean;
}

export interface RoomAssignment {
  staging_id: string;
  room_id: string | null;
  new_room_label: string | null;
  features: FeatureSuggestion[];
}

export interface RoomConfirmBulkRequest {
  batch_id: string;
  assignments: RoomAssignment[];
}

export interface RoomConfirmBulkResponse {
  rooms: Room[];
  features: RoomFeature[];
}

export interface RoomListResponse {
  rooms: Room[];
}

export interface RoomFeatureListResponse {
  features: RoomFeature[];
}

export interface RoomUpdateRequest {
  display_name?: string;
  notes?: string | null;
}

// Body of the 409 returned by POST /api/rooms/confirm-bulk when a
// new_room_label collides (case-insensitive) with an existing room. The
// parent UI uses ``existing_room`` to render a "use existing or rename?"
// modal with the existing room's photo + label.
export interface RoomNameCollisionDetail {
  code: "room_label_collision";
  label: string;
  existing_room: Room;
}

// Body of the 409 returned by DELETE /api/rooms/{id} when any
// room_features row still references the room.
export interface RoomInUseDetail {
  code: "room_in_use";
  room_id: string;
  feature_count: number;
}

// Step 13/22: transcripts wire shapes. Mirror the Pydantic models in
// src/toybox/api/transcripts.py.
export interface TranscriptRow {
  id: string;
  session_id: string;
  mic_id: string | null;
  started_at: string | null;
  ended_at: string | null;
  text: string | null;
  confidence: number | null;
  language: string;
  triggered_intent: string | null;
}

export interface TranscriptListResponse {
  items: TranscriptRow[];
}

// Body of the 200 returned by DELETE /api/transcripts/{id}.
export interface TranscriptDeleteOneResponse {
  ok: boolean;
}

// Body for DELETE /api/transcripts (wipe-all).
export interface TranscriptWipeRequest {
  pin: string;
}

// Body of the 200 returned by DELETE /api/transcripts.
export interface TranscriptWipeResponse {
  deleted: number;
}

// Body of the 404 returned by DELETE /api/transcripts/{id} when no row
// matches. The frontend reads this to render "already deleted" inline
// when the operator double-clicks delete.
export interface TranscriptNotFoundDetail {
  code: "transcript_not_found";
  id: string;
}

// Step 24: operator dashboard wire shapes. Mirror the dataclasses in
// src/toybox/metrics/__init__.py. The same shape is delivered both as
// the body of GET /api/metrics and as the payload of a ``metrics`` ws
// envelope.
export interface MetricsActivityCounts {
  // ``*_current`` fields are point-in-time counts of rows currently in
  // each state. ``running_current`` for example dropped to zero once the
  // sole running activity transitioned to ``completed``. The 24h
  // breakdown is rows whose ``created_at`` lies in the last 24h, keyed
  // by the row's CURRENT state.
  proposed_current: number;
  approved_current: number;
  running_current: number;
  completed_current: number;
  ended_current: number;
  dismissed_current: number;
  didnt_work_current: number;
  last_24h: Record<string, number>;
}

export interface MetricsTranscriptCounts {
  total: number;
  last_24h: number;
}

export interface MetricsAudioStatus {
  mic_device: string | null;
  queue_depth: number;
  // Process-lifetime counter, not a 24h window. Resets to zero on
  // restart; surfacing it lets the operator spot a mic stall.
  buffer_overruns_total: number;
}

export interface MetricsAIStatus {
  breaker_state: "closed" | "open" | "half_open";
  breaker_retry_after_iso: string | null;
  claude_capable: boolean;
  claude_capability_reason: string | null;
  listening_mode: number;
  min_interval_throttle_seconds: number;
}

// Mirrors backend ``ListeningMode`` enum (toybox/core/listening.py):
// 1=OFFLINE, 2=LOW, 3=DEFAULT, 4=HIGH, 5=INTENSE.
export type ListeningMode = 1 | 2 | 3 | 4 | 5;

export interface ListeningModeResponse {
  mode: ListeningMode;
}

export interface MetricsJudgeParentAgreement {
  overlap_count: number;
  agreement_rate: number | null;
  metric_name: string;
}

export interface MetricsActivityQuality {
  last_24h_mean_scores: Record<string, number | null>;
  judge_parent_agreement: MetricsJudgeParentAgreement;
  safety_autofails_last_24h: number;
}

export interface MetricsEvalGateStatus {
  last_run_at: string | null;
  mean_dimension_scores: Record<string, number> | null;
  regressions_detected: number;
  placeholder_baseline: boolean;
}

export interface MetricsSnapshot {
  generated_at: string;
  ws_subscribers: number;
  activities: MetricsActivityCounts;
  transcripts: MetricsTranscriptCounts;
  audio: MetricsAudioStatus;
  ai: MetricsAIStatus;
  activity_quality: MetricsActivityQuality;
  eval_gate: MetricsEvalGateStatus;
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

// Per-call options threaded through public methods. signal lets the
// caller bind a request to a component lifecycle (AbortController on
// unmount), so an in-flight mutation can't reach into a dead store.
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
  // Defensive — the type guard already verified the shape.
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

  async getHealth(opts: RequestOptions = {}): Promise<HealthResponse> {
    return this.request<HealthResponse>("/api/health", {
      method: "GET",
      signal: opts.signal,
    });
  }

  // Step 21: PIN-gated login. Body carries the entered PIN; failures
  // surface as either ApiError(401) with ``pin_invalid`` detail or
  // ApiError(423) with ``pin_locked`` detail (use
  // ``extractPinInvalidDetail`` / ``extractPinLockedDetail`` to read).
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

  // First-run PIN setup. 409 if a PIN is already set (the bootstrap
  // path checks ``getAuthStatus`` first to avoid that branch in the
  // normal flow). On success returns a parent token immediately.
  async setupPin(
    body: ParentSetupRequest,
    opts: RequestOptions = {},
  ): Promise<ParentTokenResponse> {
    return this.request<ParentTokenResponse>("/api/auth/parent/setup", {
      method: "POST",
      body: JSON.stringify(body),
      signal: opts.signal,
    });
  }

  // Pre-token probe: report whether a PIN is set and whether the
  // login gate is currently locked out. The bootstrap flow polls this
  // first to choose between PinSetup and PinLogin.
  async getAuthStatus(opts: RequestOptions = {}): Promise<ParentAuthStatus> {
    return this.request<ParentAuthStatus>("/api/auth/parent/status", {
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

  async propose(
    payload: ProposePayload,
    opts: RequestOptions = {},
  ): Promise<Activity> {
    return this.request<Activity>("/api/activities/propose", {
      method: "POST",
      body: JSON.stringify(payload),
      signal: opts.signal,
    });
  }

  async approve(
    id: string,
    version: number,
    childIds?: string[],
    opts: RequestOptions = {},
  ): Promise<Activity> {
    return this.request<Activity>(`/api/activities/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      body: JSON.stringify({ child_ids: childIds ?? null }),
      ifMatchVersion: version,
      signal: opts.signal,
    });
  }

  async dismiss(
    id: string,
    version: number,
    opts: RequestOptions = {},
  ): Promise<Activity> {
    return this.request<Activity>(`/api/activities/${encodeURIComponent(id)}/dismiss`, {
      method: "POST",
      ifMatchVersion: version,
      signal: opts.signal,
    });
  }

  async regenerate(
    id: string,
    version: number,
    opts: RequestOptions = {},
  ): Promise<Activity> {
    return this.request<Activity>(`/api/activities/${encodeURIComponent(id)}/regenerate`, {
      method: "POST",
      body: JSON.stringify({}),
      ifMatchVersion: version,
      signal: opts.signal,
    });
  }

  async end(
    id: string,
    version: number,
    opts: RequestOptions = {},
  ): Promise<Activity> {
    return this.request<Activity>(`/api/activities/${encodeURIComponent(id)}/end`, {
      method: "POST",
      ifMatchVersion: version,
      signal: opts.signal,
    });
  }

  // Step 23: pause/resume the live activity. Both are idempotent on
  // the backend — pausing an already-paused activity returns 200 with
  // the same version, no envelope emit. The version supplied here MUST
  // match the activity's current version, even on the no-op branch,
  // because every other mutation uses optimistic concurrency.
  async pause(
    id: string,
    version: number,
    opts: RequestOptions = {},
  ): Promise<Activity> {
    return this.request<Activity>(`/api/activities/${encodeURIComponent(id)}/pause`, {
      method: "POST",
      ifMatchVersion: version,
      signal: opts.signal,
    });
  }

  async resume(
    id: string,
    version: number,
    opts: RequestOptions = {},
  ): Promise<Activity> {
    return this.request<Activity>(`/api/activities/${encodeURIComponent(id)}/resume`, {
      method: "POST",
      ifMatchVersion: version,
      signal: opts.signal,
    });
  }

  async didntWork(
    id: string,
    version: number,
    reason?: string,
    opts: RequestOptions = {},
  ): Promise<Activity> {
    return this.request<Activity>(`/api/activities/${encodeURIComponent(id)}/didnt-work`, {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? null }),
      ifMatchVersion: version,
      signal: opts.signal,
    });
  }

  // Step 15: parent thumbs-up writes parent_signal=+1 to the
  // matching labeled_events row. No state transition, no
  // If-Match-Version (the activity itself isn't modified). Backend
  // returns the unchanged activity so the UI can confirm the click.
  async thumbsUp(id: string, opts: RequestOptions = {}): Promise<Activity> {
    return this.request<Activity>(`/api/activities/${encodeURIComponent(id)}/thumbs-up`, {
      method: "POST",
      signal: opts.signal,
    });
  }

  // Step 18: child-profile CRUD. The 409 child_in_use surfaces as the
  // generic ApiError (status 409, body has detail.code === "child_in_use");
  // the editor reads referring_activity_count off body.detail.
  async listChildren(
    opts: RequestOptions = {},
  ): Promise<ChildProfileListResponse> {
    return this.request<ChildProfileListResponse>("/api/children", {
      method: "GET",
      signal: opts.signal,
    });
  }

  async getChild(id: string, opts: RequestOptions = {}): Promise<ChildProfile> {
    return this.request<ChildProfile>(`/api/children/${encodeURIComponent(id)}`, {
      method: "GET",
      signal: opts.signal,
    });
  }

  async createChild(
    body: ChildProfileCreate,
    opts: RequestOptions = {},
  ): Promise<ChildProfile> {
    return this.request<ChildProfile>("/api/children", {
      method: "POST",
      body: JSON.stringify(body),
      signal: opts.signal,
    });
  }

  async updateChild(
    id: string,
    body: ChildProfileUpdate,
    opts: RequestOptions = {},
  ): Promise<ChildProfile> {
    return this.request<ChildProfile>(`/api/children/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
      signal: opts.signal,
    });
  }

  async deleteChild(id: string, opts: RequestOptions = {}): Promise<void> {
    await this.request<unknown>(`/api/children/${encodeURIComponent(id)}`, {
      method: "DELETE",
      signal: opts.signal,
    });
  }

  // Step 16: toy ingest. The upload endpoint takes multipart form
  // data, so we don't go through ``request<T>`` (which JSON-encodes).
  // Auth + signal are still threaded the same way.
  async uploadToyPhoto(
    file: File,
    opts: RequestOptions = {},
  ): Promise<ToyUploadResponse> {
    const headers = new Headers();
    const token = this.getToken();
    if (token !== null) {
      headers.set("X-Toybox-Token", token);
    }
    // ``Content-Type: multipart/form-data; boundary=...`` is set by
    // the browser when ``body`` is a ``FormData``; setting it manually
    // would clobber the boundary token.
    const form = new FormData();
    form.append("file", file);
    const resp = await this.fetchImpl(this.baseUrl + "/api/toys/upload", {
      method: "POST",
      headers,
      body: form,
      signal: opts.signal,
    });
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
      throw new ApiError(resp.status, body);
    }
    return body as ToyUploadResponse;
  }

  async confirmToy(
    body: ToyConfirmRequest,
    opts: RequestOptions = {},
  ): Promise<Toy> {
    return this.request<Toy>("/api/toys", {
      method: "POST",
      body: JSON.stringify(body),
      signal: opts.signal,
    });
  }

  async listToys(opts: RequestOptions = {}): Promise<ToyListResponse> {
    return this.request<ToyListResponse>("/api/toys", {
      method: "GET",
      signal: opts.signal,
    });
  }

  async getToy(id: string, opts: RequestOptions = {}): Promise<Toy> {
    return this.request<Toy>(`/api/toys/${encodeURIComponent(id)}`, {
      method: "GET",
      signal: opts.signal,
    });
  }

  async updateToy(
    id: string,
    body: ToyUpdateRequest,
    opts: RequestOptions = {},
  ): Promise<Toy> {
    return this.request<Toy>(`/api/toys/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
      signal: opts.signal,
    });
  }

  // Replace an existing toy's image with a freshly picked file.
  // Server-side dedup excludes the toy being edited so re-uploading
  // the current image is a no-op success. A 409 here means the new
  // image collides with a *different* toy; the body's
  // ``existing_toy`` is the colliding row (extracted via
  // ``extractToyImageExistsDetail``).
  async replaceToyImage(
    id: string,
    file: File,
    opts: RequestOptions = {},
  ): Promise<Toy> {
    const headers = new Headers();
    const token = this.getToken();
    if (token !== null) {
      headers.set("X-Toybox-Token", token);
    }
    const form = new FormData();
    form.append("file", file);
    const resp = await this.fetchImpl(
      this.baseUrl + `/api/toys/${encodeURIComponent(id)}/image`,
      { method: "POST", headers, body: form, signal: opts.signal },
    );
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
      throw new ApiError(resp.status, body);
    }
    return body as Toy;
  }

  // Soft delete on the backend (sets ``archived = 1``); we name the
  // method ``archiveToy`` so the call site reads honestly.
  async archiveToy(id: string, opts: RequestOptions = {}): Promise<void> {
    await this.request<unknown>(`/api/toys/${encodeURIComponent(id)}`, {
      method: "DELETE",
      signal: opts.signal,
    });
  }

  // Step 17: bulk room ingest. The upload endpoint takes multipart
  // form data (one ``files`` part per photo, ≤50). The browser sets
  // the Content-Type boundary; we don't pass it through ``request<T>``.
  async uploadRoomsBulk(
    files: File[],
    opts: RequestOptions = {},
  ): Promise<RoomBulkUploadResponse> {
    const headers = new Headers();
    const token = this.getToken();
    if (token !== null) {
      headers.set("X-Toybox-Token", token);
    }
    const form = new FormData();
    for (const file of files) {
      form.append("files", file);
    }
    const resp = await this.fetchImpl(
      this.baseUrl + "/api/rooms/upload-bulk",
      {
        method: "POST",
        headers,
        body: form,
        signal: opts.signal,
      },
    );
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
      throw new ApiError(resp.status, body);
    }
    return body as RoomBulkUploadResponse;
  }

  async confirmRoomsBulk(
    body: RoomConfirmBulkRequest,
    opts: RequestOptions = {},
  ): Promise<RoomConfirmBulkResponse> {
    return this.request<RoomConfirmBulkResponse>("/api/rooms/confirm-bulk", {
      method: "POST",
      body: JSON.stringify(body),
      signal: opts.signal,
    });
  }

  async listRooms(opts: RequestOptions = {}): Promise<RoomListResponse> {
    return this.request<RoomListResponse>("/api/rooms", {
      method: "GET",
      signal: opts.signal,
    });
  }

  async getRoom(id: string, opts: RequestOptions = {}): Promise<Room> {
    return this.request<Room>(`/api/rooms/${encodeURIComponent(id)}`, {
      method: "GET",
      signal: opts.signal,
    });
  }

  async getRoomFeatures(
    id: string,
    opts: RequestOptions = {},
  ): Promise<RoomFeatureListResponse> {
    return this.request<RoomFeatureListResponse>(
      `/api/rooms/${encodeURIComponent(id)}/features`,
      { method: "GET", signal: opts.signal },
    );
  }

  async updateRoom(
    id: string,
    body: RoomUpdateRequest,
    opts: RequestOptions = {},
  ): Promise<Room> {
    return this.request<Room>(`/api/rooms/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
      signal: opts.signal,
    });
  }

  async deleteRoom(id: string, opts: RequestOptions = {}): Promise<void> {
    await this.request<unknown>(`/api/rooms/${encodeURIComponent(id)}`, {
      method: "DELETE",
      signal: opts.signal,
    });
  }

  // Replace an existing room's primary image. Same dedup-against-
  // others semantics as ``replaceToyImage`` — a 409 means the new
  // image already belongs to a different room (body
  // ``existing_room``).
  async replaceRoomImage(
    id: string,
    file: File,
    opts: RequestOptions = {},
  ): Promise<Room> {
    const headers = new Headers();
    const token = this.getToken();
    if (token !== null) {
      headers.set("X-Toybox-Token", token);
    }
    const form = new FormData();
    form.append("file", file);
    const resp = await this.fetchImpl(
      this.baseUrl + `/api/rooms/${encodeURIComponent(id)}/image`,
      { method: "POST", headers, body: form, signal: opts.signal },
    );
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
      throw new ApiError(resp.status, body);
    }
    return body as Room;
  }

  // Step 24: operator metrics dashboard. Fetches the same snapshot
  // shape that the ``metrics`` ws topic publishes every 30s — used by
  // OperatorTab as the first-render value before the ws snapshot
  // arrives, and as the fallback when the ws connection is down.
  async getMetrics(opts: RequestOptions = {}): Promise<MetricsSnapshot> {
    return this.request<MetricsSnapshot>("/api/metrics", {
      method: "GET",
      signal: opts.signal,
    });
  }

  // Listening-mode write path. The current value is read from
  // ``snapshot.ai.listening_mode`` (already in the metrics envelope),
  // so the operator tab does not need a paired GET — it patches the
  // local snapshot with the response from this PUT and lets the next
  // metrics envelope reconcile.
  async setListeningMode(
    mode: ListeningMode,
    opts: RequestOptions = {},
  ): Promise<ListeningModeResponse> {
    return this.request<ListeningModeResponse>("/api/listening/mode", {
      method: "PUT",
      body: JSON.stringify({ mode }),
      signal: opts.signal,
    });
  }

  // Step 13: list transcripts. ``before`` is an ISO timestamp cursor;
  // rows with ``ended_at < before`` are returned, most-recent first.
  // Step 22 reuses this for the management UI's pagination.
  async listTranscripts(
    params: { limit?: number; before?: string | null } = {},
    opts: RequestOptions = {},
  ): Promise<TranscriptListResponse> {
    const search = new URLSearchParams();
    if (params.limit !== undefined) search.set("limit", String(params.limit));
    if (params.before !== undefined && params.before !== null) {
      search.set("before", params.before);
    }
    const qs = search.toString();
    return this.request<TranscriptListResponse>(
      `/api/transcripts${qs.length > 0 ? `?${qs}` : ""}`,
      { method: "GET", signal: opts.signal },
    );
  }

  // Step 13: case-insensitive substring search over transcript text.
  // ``q`` is sent verbatim; the backend rejects empty/whitespace-only
  // queries.
  async searchTranscripts(
    query: string,
    params: { limit?: number } = {},
    opts: RequestOptions = {},
  ): Promise<TranscriptListResponse> {
    const search = new URLSearchParams();
    search.set("q", query);
    if (params.limit !== undefined) search.set("limit", String(params.limit));
    return this.request<TranscriptListResponse>(
      `/api/transcripts/search?${search.toString()}`,
      { method: "GET", signal: opts.signal },
    );
  }

  // Step 22: delete one transcript by id. 404 surfaces as ApiError(404)
  // with detail.code === "transcript_not_found"; the management UI
  // treats that as "already deleted" rather than a hard error.
  async deleteTranscript(
    id: string,
    opts: RequestOptions = {},
  ): Promise<TranscriptDeleteOneResponse> {
    return this.request<TranscriptDeleteOneResponse>(
      `/api/transcripts/${encodeURIComponent(id)}`,
      { method: "DELETE", signal: opts.signal },
    );
  }

  // Step 22: wipe all transcripts. PIN re-confirm body is required on
  // top of the parent token; the rate limiter is shared with
  // ``POST /api/auth/parent`` so a wrong PIN here counts toward the
  // global lock just like a wrong login.
  async wipeTranscripts(
    body: TranscriptWipeRequest,
    opts: RequestOptions = {},
  ): Promise<TranscriptWipeResponse> {
    return this.request<TranscriptWipeResponse>("/api/transcripts", {
      method: "DELETE",
      body: JSON.stringify(body),
      signal: opts.signal,
    });
  }
}

// Convert a stored ``image_path`` (DB shape: ``data/images/<sub>/<f>``)
// to the URL the parent UI's <img> tags use. The backend mounts
// committed images under ``/api/static/images`` so we just swap the
// ``data/`` prefix; falsy/empty/non-matching inputs return null so
// callers can render a placeholder. Cache-buster (``?v=hash``) lets
// "change picture" updates render immediately without service-worker
// or CDN reuse.
export function imageUrl(
  imagePath: string | null,
  cacheKey?: string | null,
): string | null {
  if (imagePath === null) return null;
  const trimmed = imagePath.trim();
  if (trimmed.length === 0) return null;
  const normalized = trimmed.replace(/\\/g, "/");
  const prefix = "data/images/";
  if (!normalized.startsWith(prefix)) return null;
  const url = "/api/static/" + normalized.slice("data/".length);
  if (cacheKey === undefined || cacheKey === null || cacheKey.length === 0) {
    return url;
  }
  return `${url}?v=${encodeURIComponent(cacheKey)}`;
}

// Step 18: a single FastAPI validation error. The wire shape comes
// from pydantic; we only need ``loc`` + ``msg`` for surfacing under the
// offending field. The full detail array lives at ``body.detail``.
export interface ValidationFieldError {
  loc: (string | number)[];
  msg: string;
  type?: string;
}

// Pull pydantic's ``detail`` array off a 422 ApiError. Returns ``null``
// when the error isn't a validation error or the body is unrecognised.
//
// Two shapes are recognised:
//
//   1. The Pydantic auto-validation array: ``detail: [{loc, msg, type}]``.
//      This is what FastAPI emits when a Pydantic field validator
//      rejects the body.
//   2. The sibling-route ``{code: ..., field?: ...}`` dict shape used by
//      explicit ``raise HTTPException(422, detail={...})`` sites
//      (children/toys/rooms/metrics/auth-setup). The dict is mapped
//      back to a single-entry validation array so callers can treat
//      both 422 origins uniformly.
export function extractValidationErrors(
  err: unknown,
): ValidationFieldError[] | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  const body = err.body;
  if (typeof body !== "object" || body === null) return null;
  const rec = body as Record<string, unknown>;
  const detail = rec["detail"];
  if (Array.isArray(detail)) {
    const out: ValidationFieldError[] = [];
    for (const entry of detail) {
      if (typeof entry !== "object" || entry === null) continue;
      const e = entry as Record<string, unknown>;
      if (Array.isArray(e["loc"]) && typeof e["msg"] === "string") {
        out.push({
          loc: e["loc"] as (string | number)[],
          msg: e["msg"],
          type: typeof e["type"] === "string" ? e["type"] : undefined,
        });
      }
    }
    return out.length > 0 ? out : null;
  }
  if (typeof detail === "object" && detail !== null) {
    const d = detail as Record<string, unknown>;
    const code = d["code"];
    if (typeof code !== "string") return null;
    const field = typeof d["field"] === "string" ? d["field"] : undefined;
    // Map ``{code, field?}`` → single ValidationFieldError so the
    // PinSetup-style consumer can route on ``loc[1]`` exactly as it
    // does for the array shape. ``msg`` is derived from ``code`` so
    // the field-level UI surface still has something to render.
    return [
      {
        loc: field !== undefined ? ["body", field] : ["body"],
        msg: code,
        type: code,
      },
    ];
  }
  return null;
}

// Step 18: pull the child_in_use detail off a 409 ApiError, or null if
// the error isn't that shape. The editor renders "can't delete — N
// activities still reference this profile" using the count.
export function extractChildInUseDetail(
  err: unknown,
): ChildInUseDetail | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body;
  if (typeof body !== "object" || body === null) return null;
  const rec = body as Record<string, unknown>;
  const candidate = "detail" in rec ? rec["detail"] : rec;
  if (typeof candidate !== "object" || candidate === null) return null;
  const c = candidate as Record<string, unknown>;
  if (
    c["code"] === "child_in_use" &&
    typeof c["child_id"] === "string" &&
    typeof c["referring_activity_count"] === "number"
  ) {
    return {
      code: "child_in_use",
      child_id: c["child_id"],
      referring_activity_count: c["referring_activity_count"],
    };
  }
  return null;
}

// Step 16: pull the image_already_exists detail off a 409 ApiError, or
// null if the error isn't that shape. The toy ingest UI uses this to
// render "this image already exists" with a link to the existing toy.
export function extractToyImageExistsDetail(
  err: unknown,
): ToyImageExistsDetail | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body;
  if (typeof body !== "object" || body === null) return null;
  const rec = body as Record<string, unknown>;
  const candidate = "detail" in rec ? rec["detail"] : rec;
  if (typeof candidate !== "object" || candidate === null) return null;
  const c = candidate as Record<string, unknown>;
  if (
    c["code"] === "image_already_exists" &&
    typeof c["existing_toy"] === "object" &&
    c["existing_toy"] !== null
  ) {
    return {
      code: "image_already_exists",
      existing_toy: c["existing_toy"] as Toy,
    };
  }
  return null;
}

// Step 17: pull the room_label_collision detail off a 409 ApiError, or
// null if the error isn't that shape. The bulk ingest UI uses this to
// surface a "Living Room already exists. Use existing or rename?"
// modal with the existing room info.
export function extractRoomNameCollisionDetail(
  err: unknown,
): RoomNameCollisionDetail | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body;
  if (typeof body !== "object" || body === null) return null;
  const rec = body as Record<string, unknown>;
  const candidate = "detail" in rec ? rec["detail"] : rec;
  if (typeof candidate !== "object" || candidate === null) return null;
  const c = candidate as Record<string, unknown>;
  if (
    c["code"] === "room_label_collision" &&
    typeof c["label"] === "string" &&
    typeof c["existing_room"] === "object" &&
    c["existing_room"] !== null
  ) {
    return {
      code: "room_label_collision",
      label: c["label"],
      existing_room: c["existing_room"] as Room,
    };
  }
  return null;
}

// Step 17: pull the room_in_use detail off a 409 ApiError. Used when
// DELETE /api/rooms/{id} refuses because room_features rows reference
// the room — the room editor renders the count.
export function extractRoomInUseDetail(
  err: unknown,
): RoomInUseDetail | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body;
  if (typeof body !== "object" || body === null) return null;
  const rec = body as Record<string, unknown>;
  const candidate = "detail" in rec ? rec["detail"] : rec;
  if (typeof candidate !== "object" || candidate === null) return null;
  const c = candidate as Record<string, unknown>;
  if (
    c["code"] === "room_in_use" &&
    typeof c["room_id"] === "string" &&
    typeof c["feature_count"] === "number"
  ) {
    return {
      code: "room_in_use",
      room_id: c["room_id"],
      feature_count: c["feature_count"],
    };
  }
  return null;
}

// Step 22: pull the ``transcript_not_found`` detail off a 404 ApiError.
// The management UI uses this to distinguish "already deleted" (which
// is benign — refetch + show inline notice) from other 404s.
export function extractTranscriptNotFoundDetail(
  err: unknown,
): TranscriptNotFoundDetail | null {
  if (!(err instanceof ApiError) || err.status !== 404) return null;
  const body = err.body;
  if (typeof body !== "object" || body === null) return null;
  const rec = body as Record<string, unknown>;
  const candidate = "detail" in rec ? rec["detail"] : rec;
  if (typeof candidate !== "object" || candidate === null) return null;
  const c = candidate as Record<string, unknown>;
  if (
    c["code"] === "transcript_not_found" &&
    typeof c["id"] === "string"
  ) {
    return {
      code: "transcript_not_found",
      id: c["id"],
    };
  }
  return null;
}

// Step 21: pull the ``pin_invalid`` detail off a 401 ApiError, or null
// if the error isn't that shape. The login screen uses this to render
// "Wrong PIN. N attempts remaining" without leaking the PIN.
export function extractPinInvalidDetail(
  err: unknown,
): PinInvalidDetail | null {
  if (!(err instanceof ApiError) || err.status !== 401) return null;
  const body = err.body;
  if (typeof body !== "object" || body === null) return null;
  const rec = body as Record<string, unknown>;
  const candidate = "detail" in rec ? rec["detail"] : rec;
  if (typeof candidate !== "object" || candidate === null) return null;
  const c = candidate as Record<string, unknown>;
  if (
    c["code"] === "pin_invalid" &&
    typeof c["attempts_remaining"] === "number"
  ) {
    return {
      code: "pin_invalid",
      attempts_remaining: c["attempts_remaining"],
    };
  }
  return null;
}

// Step 21: pull the ``pin_locked`` detail off a 423 ApiError, or null
// if the error isn't that shape. The login screen uses this to switch
// to the locked-state UI with countdown.
export function extractPinLockedDetail(
  err: unknown,
): PinLockedDetail | null {
  if (!(err instanceof ApiError) || err.status !== 423) return null;
  const body = err.body;
  if (typeof body !== "object" || body === null) return null;
  const rec = body as Record<string, unknown>;
  const candidate = "detail" in rec ? rec["detail"] : rec;
  if (typeof candidate !== "object" || candidate === null) return null;
  const c = candidate as Record<string, unknown>;
  if (
    c["code"] === "pin_locked" &&
    typeof c["seconds_until_unlock"] === "number"
  ) {
    return {
      code: "pin_locked",
      seconds_until_unlock: c["seconds_until_unlock"],
    };
  }
  return null;
}

// Returns true for AbortError thrown by fetch when a signal aborts.
// fetch in browsers throws DOMException with name === "AbortError"; in
// node 18+/undici the same. Being lenient for test fakes too.
export function isAbortError(err: unknown): boolean {
  if (err === null || typeof err !== "object") return false;
  const name = (err as { name?: unknown }).name;
  return name === "AbortError";
}

// True for transient failures that warrant a retry-with-backoff during
// bootstrap: network glitches and 5xx server errors. 4xx and
// AbortError are NOT retryable.
export function isTransientError(err: unknown): boolean {
  if (isAbortError(err)) return false;
  if (err instanceof ApiError) {
    return err.status >= 500 && err.status < 600;
  }
  return err instanceof Error;
}

export interface RetryOptions {
  attempts?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  sleep?: (ms: number) => Promise<void>;
  jitter?: () => number;
  shouldRetry?: (err: unknown) => boolean;
}

const DEFAULT_RETRY_ATTEMPTS = 3;
const DEFAULT_RETRY_BASE_MS = 1_000;
const DEFAULT_RETRY_MAX_MS = 8_000;

// Run ``op`` with exponential backoff on transient errors (5xx +
// network). Mirrors the child kiosk helper so a flapping
// /api/auth/parent doesn't freeze the parent UI either.
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
      const jittered = capped * (0.5 + jitter());
      await sleep(jittered);
    }
  }
  throw lastErr;
}

// 409 handler: wrap a mutation; on version_conflict, refetch the
// activity, fire a toast, and return null without retrying. Callers
// that need a value back use the activity refetched from `onRefetch`.
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
