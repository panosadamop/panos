/**
 * `/ws/events` with first-message auth, reconnect with backoff, and replay/resync (M7-04).
 *
 * The stream remembers the server's epoch and the last `seq` it saw. On reconnect the server
 * replays missed events, or answers `resync` when they are gone (or the node restarted);
 * listeners then reload their state.
 */
import type { Connection } from "./connection";
import type { EventType, PanosEvent, ServerMessage } from "./events";

type Listener = (event: PanosEvent) => void;
export type StreamState = "connecting" | "open" | "closed";

export interface SocketLike {
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  send(data: string): void;
  close(): void;
}

export type SocketFactory = (url: string) => SocketLike;

export class EventStream {
  private socket: SocketLike | null = null;
  private listeners = new Map<EventType | "*", Set<Listener>>();
  private resyncListeners = new Set<() => void>();
  private stateListeners = new Set<(s: StreamState) => void>();
  private epoch: string | null = null;
  private lastSeq = 0;
  private attempts = 0;
  private stopped = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  state: StreamState = "closed";

  constructor(
    private readonly conn: Connection,
    private readonly factory: SocketFactory = (url) => new WebSocket(url) as unknown as SocketLike,
    private readonly backoffMs = (attempt: number) => Math.min(30_000, 500 * 2 ** attempt),
  ) {}

  start(): void {
    this.stopped = false;
    this.open();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.socket?.close();
    this.setState("closed");
  }

  on<T extends EventType>(type: T | "*", listener: (event: PanosEvent<T>) => void): () => void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener as Listener);
    this.listeners.set(type, set);
    return () => set.delete(listener as Listener);
  }

  onResync(listener: () => void): () => void {
    this.resyncListeners.add(listener);
    return () => this.resyncListeners.delete(listener);
  }

  onState(listener: (s: StreamState) => void): () => void {
    this.stateListeners.add(listener);
    return () => this.stateListeners.delete(listener);
  }

  private setState(state: StreamState): void {
    this.state = state;
    this.stateListeners.forEach((l) => l(state));
  }

  private open(): void {
    this.setState("connecting");
    const url = `${this.conn.baseUrl.replace(/^http/, "ws")}/api/v1/ws/events`;
    const socket = this.factory(url);
    this.socket = socket;
    socket.onopen = () => {
      const auth: Record<string, unknown> = { type: "auth", token: this.conn.token };
      if (this.epoch !== null) Object.assign(auth, { epoch: this.epoch, since: this.lastSeq });
      socket.send(JSON.stringify(auth));
    };
    socket.onmessage = (ev) => this.handle(JSON.parse(String(ev.data)) as ServerMessage);
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.setState("closed");
      if (!this.stopped) {
        const delay = this.backoffMs(this.attempts++);
        this.timer = setTimeout(() => this.open(), delay);
      }
    };
    socket.onerror = () => socket.close();
  }

  private handle(message: ServerMessage): void {
    if (message.type === "auth.ok") {
      const restarted = this.epoch !== null && this.epoch !== message.epoch;
      this.epoch = message.epoch;
      if (restarted) {
        this.lastSeq = message.seq;
        this.resyncListeners.forEach((l) => l());
      } else if (this.lastSeq === 0) {
        this.lastSeq = message.seq;
      }
      this.attempts = 0;
      this.setState("open");
      return;
    }
    if (message.type === "resync") {
      this.lastSeq = message.seq;
      this.resyncListeners.forEach((l) => l());
      return;
    }
    if (message.seq <= this.lastSeq) return; // duplicate after replay
    this.lastSeq = message.seq;
    this.listeners.get(message.type)?.forEach((l) => l(message));
    this.listeners.get("*")?.forEach((l) => l(message));
  }
}
