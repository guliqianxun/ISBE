import { describe, expect, it, vi } from 'vitest';
import { setSourceFactory, subscribe, type StreamEvent } from './sse';

class FakeSource {
  url: string;
  closed = false;
  listeners = new Map<string, (e: Event) => void>();
  onopen: ((e: Event) => unknown) | null = null;
  onerror: ((e: Event) => unknown) | null = null;
  constructor(url: string) {
    this.url = url;
  }
  addEventListener(type: string, listener: (e: Event) => void) {
    this.listeners.set(type, listener);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: string, id = '') {
    this.listeners.get(type)?.(new MessageEvent(type, { data, lastEventId: id }));
  }
}

describe('event stream', () => {
  it('decodes events, remembers the last id and resumes after it', () => {
    const sources: FakeSource[] = [];
    setSourceFactory((url) => {
      const source = new FakeSource(url);
      sources.push(source);
      return source;
    });
    const events: StreamEvent<{ n: number }>[] = [];
    const status = vi.fn();
    const invalid = vi.fn();
    const sub = subscribe<{ n: number }>('/api/question/runs/1/events', {
      types: ['star'],
      onEvent: (e) => events.push(e),
      onStatus: status,
      onInvalid: invalid,
    });
    const source = sources[0]!;
    expect(source.url).toBe('/api/question/runs/1/events');
    source.onopen?.(new Event('open'));
    source.emit('message', '{"n":1}', '1');
    source.emit('star', '{"n":2}', '2');
    source.emit('star', 'not json', '3');
    source.onerror?.(new Event('error'));
    expect(events).toEqual([
      { id: '1', type: 'message', data: { n: 1 } },
      { id: '2', type: 'star', data: { n: 2 } },
    ]);
    expect(invalid).toHaveBeenCalledWith('not json');
    expect(status.mock.calls).toEqual([[true], [false]]);
    expect(sub.lastId()).toBe('2');
    sub.close();
    expect(source.closed).toBe(true);

    subscribe('/api/question/runs/1/events?x=1', { after: sub.lastId(), onEvent: () => {} });
    expect(sources[1]!.url).toBe('/api/question/runs/1/events?x=1&after=2');
  });

  it('keeps a server event named error apart from a connection error', () => {
    let source!: FakeSource;
    setSourceFactory((url) => (source = new FakeSource(url)));
    const events: StreamEvent<{ n: number }>[] = [];
    const status = vi.fn();
    const invalid = vi.fn();
    subscribe<{ n: number }>('/x', {
      types: ['error'],
      onEvent: (e) => events.push(e),
      onStatus: status,
      onInvalid: invalid,
    });
    const problem = new MessageEvent('error', { data: '{"n":1}', lastEventId: '4' });
    // A browser hands a server `error` event to the listeners and to onerror alike.
    source.listeners.get('error')!(problem);
    source.onerror?.(problem);
    expect(events).toEqual([{ id: '4', type: 'error', data: { n: 1 } }]);
    expect(status).not.toHaveBeenCalled();
    // Its own connection error has no data: a status change, not an event.
    const dropped = new Event('error');
    source.listeners.get('error')!(dropped);
    source.onerror?.(dropped);
    expect(events).toHaveLength(1);
    expect(invalid).not.toHaveBeenCalled();
    expect(status.mock.calls).toEqual([[false]]);
  });
});
