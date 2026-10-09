import { describe, expect, it, vi } from "vitest";

import { EventStream } from "../api/eventStream";
import { CONN, event, FakeSocket } from "./fakes";

function stream() {
  FakeSocket.all = [];
  const s = new EventStream(CONN, (url) => new FakeSocket(url), () => 5);
  s.start();
  return { s, sock: () => FakeSocket.all[FakeSocket.all.length - 1] };
}

describe("[M7-04] event stream", () => {
  it("authenticates with the first message and delivers typed events", () => {
    const { s, sock } = stream();
    expect(sock().url).toBe("ws://127.0.0.1:48123/api/v1/ws/events");
    sock().open();
    expect(sock().sent[0]).toEqual({ type: "auth", token: "t0ken" });
    const seen: string[] = [];
    s.on("job.status", (e) => seen.push(e.payload.status));
    sock().receive({ type: "auth.ok", v: 1, epoch: "E1", seq: 10 });
    expect(s.state).toBe("open");
    sock().receive({ ...event("job.status", { job_id: "j", status: "RUNNING", reason: "", project_id: null, kind: "coding" }), seq: 11 });
    expect(seen).toEqual(["RUNNING"]);
  });

  it("reconnects with since/epoch, drops duplicates and resyncs after a restart", async () => {
    vi.useFakeTimers();
    const { s, sock } = stream();
    sock().open();
    sock().receive({ type: "auth.ok", v: 1, epoch: "E1", seq: 3 });
    const got: number[] = [];
    s.on("*", (e) => got.push(e.seq));
    sock().receive({ ...event("voice.spoken", { seconds: 1 }), seq: 4 });
    sock().close(); // connection lost
    expect(s.state).toBe("closed");
    await vi.advanceTimersByTimeAsync(10);
    const second = sock();
    second.open();
    expect(second.sent[0]).toEqual({ type: "auth", token: "t0ken", epoch: "E1", since: 4 });
    second.receive({ type: "auth.ok", v: 1, epoch: "E1", seq: 6 });
    second.receive({ ...event("voice.spoken", { seconds: 1 }), seq: 4 }); // duplicate: ignored
    second.receive({ ...event("voice.spoken", { seconds: 1 }), seq: 5 });
    expect(got).toEqual([4, 5]);

    const resyncs = vi.fn();
    s.onResync(resyncs);
    second.receive({ type: "resync", seq: 40 });
    expect(resyncs).toHaveBeenCalledTimes(1);
    second.close();
    await vi.advanceTimersByTimeAsync(10);
    sock().open();
    sock().receive({ type: "auth.ok", v: 1, epoch: "E2", seq: 1 }); // node restarted
    expect(resyncs).toHaveBeenCalledTimes(2);
    s.stop();
    vi.useRealTimers();
  });
});
