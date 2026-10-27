// Server-sent events for long runs. Each event carries an `id` (its sequence
// number) and a JSON `data` body. The browser resends the last id as
// `Last-Event-ID` when it reconnects by itself; after `close()` a new
// subscription can resume with `after` (sent as `?after=<id>`).
//
// A server event named `error` and the browser's own connection error both
// reach `onerror` and the `error` listeners: the server's carries `data`, the
// browser's does not, and each goes only where it belongs.

export interface StreamEvent<T = unknown> {
  id: string;
  type: string;
  data: T;
}

export interface Subscription {
  close(): void;
  /** The id of the last event received, or the `after` it started from. */
  lastId(): string;
}

export interface SubscribeOptions<T> {
  /** Named event types to listen for besides the default `message`. */
  types?: string[];
  after?: string;
  onEvent: (event: StreamEvent<T>) => void;
  /** Connection state: true when open, false while the browser reconnects. */
  onStatus?: (open: boolean) => void;
  /** A `data` body that is not JSON. */
  onInvalid?: (raw: string) => void;
}

/** The part of `EventSource` used here. */
export interface Source {
  addEventListener(type: string, listener: (event: Event) => void): void;
  close(): void;
  onopen: ((event: Event) => unknown) | null;
  onerror: ((event: Event) => unknown) | null;
}

// Tests replace this with a fake.
export let createSource = (url: string): Source => new EventSource(url, { withCredentials: true });
export function setSourceFactory(factory: (url: string) => Source): void {
  createSource = factory;
}

export function subscribe<T>(url: string, options: SubscribeOptions<T>): Subscription {
  let last = options.after ?? '';
  const target = last ? `${url}${url.includes('?') ? '&' : '?'}after=${encodeURIComponent(last)}` : url;
  const source = createSource(target);
  const listen = (type: string) =>
    source.addEventListener(type, (raw: Event) => {
      if (!fromServer(raw)) return;
      const message = raw as MessageEvent<string>;
      let data: T;
      try {
        data = JSON.parse(message.data) as T;
      } catch {
        options.onInvalid?.(message.data);
        return;
      }
      if (message.lastEventId) last = message.lastEventId;
      options.onEvent({ id: message.lastEventId, type, data });
    });
  for (const type of new Set(['message', ...(options.types ?? [])])) listen(type);
  source.onopen = () => options.onStatus?.(true);
  source.onerror = (event) => {
    if (!fromServer(event)) options.onStatus?.(false);
  };
  return { close: () => source.close(), lastId: () => last };
}

/** An event the server sent (it has a `data` body), not a connection error. */
function fromServer(event: Event): boolean {
  return typeof (event as Partial<MessageEvent>).data === 'string';
}
