/** Reception desk WebSocket with polling fallback helpers. */
export type ReceptionEvent = {
  type: string;
  conversation_id?: string;
  [key: string]: unknown;
};

type Options = {
  onEvent?: (event: ReceptionEvent) => void;
  onStatus?: (connected: boolean) => void;
};

function wsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/admin/reception/ws`;
}

/** Connect to reception events. Returns a disposer. Reconnects with backoff. */
export function connectReceptionSocket(options: Options = {}): () => void {
  let closed = false;
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;

  const clearTimer = () => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const open = () => {
    if (closed) return;
    clearTimer();
    try {
      socket = new WebSocket(wsUrl());
    } catch {
      options.onStatus?.(false);
      schedule();
      return;
    }
    socket.onopen = () => {
      attempt = 0;
      options.onStatus?.(true);
    };
    socket.onmessage = (ev) => {
      try {
        const data = JSON.parse(String(ev.data)) as ReceptionEvent;
        if (data?.type && data.type !== "ping" && data.type !== "pong" && data.type !== "connected") {
          options.onEvent?.(data);
        }
      } catch {
        /* ignore malformed */
      }
    };
    socket.onerror = () => {
      options.onStatus?.(false);
    };
    socket.onclose = () => {
      options.onStatus?.(false);
      socket = null;
      if (!closed) schedule();
    };
  };

  const schedule = () => {
    clearTimer();
    const delay = Math.min(30000, 1000 * Math.pow(2, attempt++));
    timer = setTimeout(open, delay);
  };

  open();
  return () => {
    closed = true;
    clearTimer();
    try {
      socket?.close();
    } catch {
      /* ignore */
    }
    socket = null;
  };
}

/** Polling interval: longer when WS is live, original cadence when not. */
export function pollMs(connected: boolean, live: number, fallback: number) {
  return connected ? live : fallback;
}
