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

  async getActivity(id: string, opts: RequestOptions = {}): Promise<Activity> {
    return this.request<Activity>(`/api/activities/${encodeURIComponent(id)}`, {
      method: "GET",
      signal: opts.signal,
    });
  }

  async advance(
    id: string,
    version: number,
    opts: RequestOptions = {},
  ): Promise<Activity> {
    return this.request<Activity>(
      `/api/activities/${encodeURIComponent(id)}/advance`,
      {
        method: "POST",
        ifMatchVersion: version,
        signal: opts.signal,
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
