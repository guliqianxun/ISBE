// The console's HTTP API. Every write sends the process CSRF token in
// `X-ISBE-CSRF` with a JSON body (the server refuses anything else); every
// failure arrives as `{"error": "..."}`. A 401 outside sign-in means the
// session ended: the session layer is told and answers go nowhere.

export class ApiError extends Error {
  readonly status: number;
  /** The decoded JSON answer, when there was one (some refusals carry a `code` or a `job`). */
  readonly body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

/** Thrown for answers to requests made before the session ended. */
export class StaleSession extends ApiError {
  constructor() {
    super(401, 'session ended');
    this.name = 'StaleSession';
  }
}

let csrfToken = '';
let epoch = 0;
let unauthorized: () => void = () => {};

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

export function onUnauthorized(handler: () => void): void {
  unauthorized = handler;
}

/** Drop answers to every request in flight; the next session starts clean. */
export function endSession(): void {
  epoch += 1;
  csrfToken = '';
}

async function failure(response: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    // Not JSON.
  }
  const text =
    body && typeof body === 'object' && typeof (body as { error?: unknown }).error === 'string'
      ? (body as { error: string }).error
      : `${response.status} ${response.statusText}`.trim();
  return new ApiError(response.status, text, body);
}

export type Method = 'GET' | 'POST' | 'PUT' | 'DELETE';

/** One API call; the decoded JSON answer, `null` for a 404 on a read when `missingOk`. */
export async function request<T>(
  method: Method,
  url: string,
  body?: unknown,
  options: { missingOk?: boolean; signal?: AbortSignal } = {},
): Promise<T> {
  const started = epoch;
  const init: RequestInit = { method, cache: 'no-store', credentials: 'same-origin', signal: options.signal };
  if (method !== 'GET') {
    init.headers = { 'Content-Type': 'application/json', 'X-ISBE-CSRF': csrfToken };
    init.body = JSON.stringify(body ?? {});
  }
  const response = await fetch(url, init);
  if (started !== epoch) throw new StaleSession();
  if (response.status === 401 && url !== '/api/login') {
    unauthorized();
    throw new StaleSession();
  }
  if (response.status === 404 && options.missingOk && method === 'GET') return null as T;
  if (!response.ok) throw await failure(response);
  if (response.status === 204) return null as T;
  const data = (await response.json()) as T;
  if (started !== epoch) throw new StaleSession();
  return data;
}

export const api = {
  get: <T>(url: string, options?: { missingOk?: boolean; signal?: AbortSignal }) =>
    request<T>('GET', url, undefined, options),
  post: <T>(url: string, body?: unknown) => request<T>('POST', url, body),
  put: <T>(url: string, body?: unknown) => request<T>('PUT', url, body),
  delete: <T>(url: string, body?: unknown) => request<T>('DELETE', url, body),
};

/** `url` with `?owner=` when an administrator reads another account's workspace. */
export function withOwner(url: string, owner: string | null | undefined): string {
  if (!owner) return url;
  return `${url}${url.includes('?') ? '&' : '?'}owner=${encodeURIComponent(owner)}`;
}
