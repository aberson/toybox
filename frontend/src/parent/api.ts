// Parent UI REST client. Injects X-Toybox-Token, serializes
// If-Match-Version, and normalizes 409 (version_conflict) into a typed
// error. All routes hit /api/* (vite dev proxy forwards to :8000).
// Shapes mirror src/toybox/api/activities.py and core/version_check.py.

export type ActivityState =
  | "proposed"
  | "approved"
  | "running"
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

export interface ProposePayload {
  intent: string;
  slot?: string | null;
  hour: number;
  seed: number;
  persona_id?: string | null;
  session_id?: string | null;
  context?: Record<string, unknown> | null;
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

  async issueParentToken(
    opts: RequestOptions = {},
  ): Promise<ParentTokenResponse> {
    return this.request<ParentTokenResponse>("/api/auth/parent", {
      method: "POST",
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
