// Realtime WebSocket client for the status.py monitoring backend.
//
// Monitoring stack (per design): React frontend + WebSocket API + Python backend.
// status.py serves HTTP /api/* AND a RFC 6455 WebSocket at ws(s)://<host>/ws on
// the same port. This client prefers the WS push channel for instant status and
// log streaming, and falls back to HTTP polling automatically whenever the
// socket is unavailable (older backend, proxy blocking Upgrade, network drop).

export type WsTransport = 'websocket' | 'polling' | 'connecting';

export interface WsStatusUpdate {
  connected: boolean;
  transport: WsTransport;
  latencyMs?: number;
  error?: string;
}

export interface VpsSocketHandlers {
  /** Receive a full /api/status-shaped snapshot pushed by the backend. */
  onSnapshot: (payload: any) => void;
  /** Receive incremental engine log lines pushed by the backend. */
  onLogLines: (lines: string[]) => void;
  /** Connection lifecycle changes (transport, latency, errors). */
  onStatus: (update: WsStatusUpdate) => void;
}

const MAX_BACKOFF_MS = 30_000;
const PING_INTERVAL_MS = 5_000;

/**
 * VpsSocket — owns one WebSocket to `<base>/ws` with:
 *  - exponential backoff reconnect (1s → 30s)
 *  - app-level ping/pong round-trip latency measurement
 *  - immediate, bounded snapshots (connect + reconnect + manual refresh)
 *  - connect()/disconnect() safe to call repeatedly
 * Consumers keep HTTP polling as fallback via onStatus({ transport }).
 */
export class VpsSocket {
  private ws: WebSocket | null = null;
  private base = '';
  private handlers: VpsSocketHandlers;
  private reconnectTimer: number | null = null;
  private pingTimer: number | null = null;
  private attempt = 0;
  private desired = false;
  private lastConnectAt = 0;

  constructor(handlers: VpsSocketHandlers) {
    this.handlers = handlers;
  }

  /** Open (or move) the realtime channel to the given endpoint base. */
  connect(base: string): void {
    const normalized = (base || (typeof window !== 'undefined' ? window.location.origin : '')).replace(/\/+$/, '');
    if (this.desired && normalized === this.base && this.ws) return; // already connected/connecting there
    this.disconnect();
    this.desired = true;
    this.base = normalized;
    this.attempt = 0;
    this.open();
  }

  /** Stop the channel and all timers (idempotent). */
  disconnect(): void {
    this.desired = false;
    this.clearTimers();
    if (this.ws) {
      const sock = this.ws;
      this.ws = null;
      // Prevent onclose from scheduling a reconnect after an intentional stop.
      sock.onclose = null;
      sock.onmessage = null;
      sock.onerror = null;
      sock.onopen = null;
      try { sock.close(); } catch { /* already closed */ }
    }
    this.handlers.onStatus({ connected: false, transport: 'connecting' });
  }

  /** Manual refresh: ask the backend for an immediate snapshot. */
  refresh(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.sendSafe({ type: 'refresh' });
    }
  }

  /** Round-trip latency probe (also doubles as an app-level keepalive). */
  private measureLatency(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.sendSafe({ type: 'ping', ts: Date.now() });
    }
  }

  private sendSafe(obj: Record<string, unknown>): void {
    try {
      this.ws?.send(JSON.stringify(obj));
    } catch { /* racing close — next ping/reconnect handles it */ }
  }

  private open(): void {
    if (!this.desired || !this.base) return;
    const wsBase = this.base.replace(/^http/, 'ws');
    let sock: WebSocket;
    try {
      sock = new WebSocket(`${wsBase}/ws`);
    } catch (err: any) {
      this.scheduleReconnect(err?.message || 'WebSocket constructor failed');
      return;
    }
    this.ws = sock;
    this.lastConnectAt = Date.now();

    sock.onopen = () => {
      this.attempt = 0;
      // Initial paint + steady-state latency probe.
      this.sendSafe({ type: 'refresh' });
      this.measureLatency();
      this.pingTimer = window.setInterval(() => this.measureLatency(), PING_INTERVAL_MS);
      this.handlers.onStatus({ connected: true, transport: 'websocket' });
    };

    sock.onmessage = (ev: MessageEvent) => {
      try {
        const msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
        if (msg.type === 'pong' && typeof msg.echo_ts === 'number') {
          this.handlers.onStatus({
            connected: true,
            transport: 'websocket',
            latencyMs: Math.max(0, Date.now() - msg.echo_ts),
          });
          return;
        }
        if (msg.type === 'log' && Array.isArray(msg.lines)) {
          this.handlers.onLogLines(msg.lines);
          return;
        }
        // hello / status snapshots / anything payload-shaped
        if (msg.timestamp || msg.type === 'status' || msg.type === 'hello') {
          this.handlers.onSnapshot(msg);
        }
      } catch { /* ignore malformed frames */ }
    };

    sock.onerror = () => {
      // onclose always follows onerror — report through onclose only.
    };

    sock.onclose = () => {
      this.clearTimers();
      this.ws = null;
      if (!this.desired) return;
      const fastFail = Date.now() - this.lastConnectAt < 5_000;
      this.scheduleReconnect(fastFail ? 'WebSocket unavailable — using HTTP polling fallback' : 'WebSocket closed');
    };
  }

  private scheduleReconnect(reason: string): void {
    this.handlers.onStatus({ connected: false, transport: 'polling', error: reason });
    const delay = Math.min(1000 * 2 ** this.attempt, MAX_BACKOFF_MS);
    this.attempt += 1;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      if (this.desired) this.open();
    }, delay);
  }

  private clearTimers(): void {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }
}
