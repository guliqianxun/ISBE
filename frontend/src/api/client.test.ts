import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeServer } from '../test/fakeServer';
import { api, ApiError, endSession, onUnauthorized, setCsrfToken, StaleSession, withOwner } from './client';

describe('api client', () => {
  beforeEach(() => {
    endSession();
    onUnauthorized(() => {});
  });

  it('sends the CSRF token and a JSON body on writes only', async () => {
    const { requests } = fakeServer({
      'GET /api/topics': () => ({ body: { topics: [] } }),
      'PUT /api/settings': () => ({ body: { ok: true } }),
    });
    setCsrfToken('tok');
    expect(await api.get('/api/topics')).toEqual({ topics: [] });
    expect(await api.put('/api/settings', { a: 1 })).toEqual({ ok: true });
    expect(requests[0]!.headers).toEqual({});
    expect(requests[1]).toMatchObject({
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-ISBE-CSRF': 'tok' },
      body: { a: 1 },
    });
  });

  it('raises the server error text', async () => {
    fakeServer({
      'POST /api/tasks': () => ({ status: 409, body: { error: 'A report is already running' } }),
    });
    const failure = api.post('/api/tasks', {});
    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({ status: 409, message: 'A report is already running' });
  });

  it('keeps the whole refusal, so a caller can read its code and task', async () => {
    const body = { code: 'busy', error: 'A report is already running', job: { id: 'job-1' } };
    fakeServer({ 'POST /api/tasks': () => ({ status: 409, body }) });
    await expect(api.post('/api/tasks', {})).rejects.toMatchObject({ status: 409, body });
  });

  it('answers null for a missing read when asked to', async () => {
    fakeServer({});
    expect(await api.get('/api/report?topic=x', { missingOk: true })).toBeNull();
    await expect(api.get('/api/report?topic=x')).rejects.toMatchObject({ status: 404 });
  });

  it('reports a 401 and drops answers from an ended session', async () => {
    fakeServer({ 'GET /api/overview': () => ({ status: 401, body: { error: 'Please sign in' } }) });
    const handler = vi.fn();
    onUnauthorized(handler);
    await expect(api.get('/api/overview')).rejects.toBeInstanceOf(StaleSession);
    expect(handler).toHaveBeenCalledOnce();

    let release: () => void = () => {};
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>((resolve) => (release = () => resolve(new Response('{}'))))),
    );
    const pending = api.get('/api/topics');
    endSession();
    release();
    await expect(pending).rejects.toBeInstanceOf(StaleSession);
  });

  it('a failed sign-in is not a session end', async () => {
    fakeServer({ 'POST /api/login': () => ({ status: 401, body: { error: 'Wrong password' } }) });
    const handler = vi.fn();
    onUnauthorized(handler);
    await expect(api.post('/api/login', {})).rejects.toMatchObject({ message: 'Wrong password' });
    expect(handler).not.toHaveBeenCalled();
  });

  it('adds the workspace owner', () => {
    expect(withOwner('/api/reports', null)).toBe('/api/reports');
    expect(withOwner('/api/reports', 'u 2')).toBe('/api/reports?owner=u%202');
    expect(withOwner('/api/report?topic=a', 'u2')).toBe('/api/report?topic=a&owner=u2');
  });
});
