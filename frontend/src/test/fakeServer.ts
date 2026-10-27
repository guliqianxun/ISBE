// A fake console API for component tests: routes answered from a table, and
// every request recorded with its method, headers and JSON body.
import { vi } from 'vitest';

export interface Recorded {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

type Answer = { status?: number; body: unknown };
type Handler = (req: Recorded) => Answer | Promise<Answer>;

export function fakeServer(routes: Record<string, Handler>) {
  const requests: Recorded[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method ?? 'GET';
    const headers = Object.fromEntries(Object.entries((init.headers ?? {}) as Record<string, string>));
    const req: Recorded = {
      method,
      url,
      headers,
      body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined,
    };
    requests.push(req);
    const handler = routes[`${method} ${url}`];
    const { status = 200, body } = handler
      ? await handler(req)
      : { status: 404, body: { error: 'not found' } };
    // A 204 has no body at all.
    if (status === 204) return new Response(null, { status });
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  return { requests, fetchMock };
}

export const ADMIN = { id: 'u1', username: 'ada', role: 'admin', active: true };
export const MEMBER = { id: 'u2', username: 'bo', role: 'member', active: true };
