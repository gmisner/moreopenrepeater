import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  StatusProvider,
  useStatus,
  useStatusCallsign,
  useStatusConnected,
  useStatusExtras,
  useStatusLive,
} from "./status";

type Handler = ((event?: { data?: string }) => void) | null;

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  url: string;
  onopen: Handler = null;
  onmessage: Handler = null;
  onclose: Handler = null;
  readyState = MockWebSocket.CONNECTING;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  open() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  push(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }

  pushRaw(data: string) {
    this.onmessage?.({ data });
  }
}

function StatusProbe() {
  const { status, connected } = useStatus();
  return (
    <div>
      <span data-testid="connected">{String(connected)}</span>
      <span data-testid="callsign">{status?.callsign ?? ""}</span>
      <span data-testid="ptt">{String(Boolean(status?.ptt_active))}</span>
      <span data-testid="cos">{String(status?.cos_active ?? "")}</span>
      <span data-testid="raw">{JSON.stringify(status)}</span>
    </div>
  );
}

let narrowRenderCount = 0;

function NarrowConnectedProbe() {
  narrowRenderCount += 1;
  const connected = useStatusConnected();
  return <span data-testid="narrow-connected">{String(connected)}</span>;
}

function NarrowSlicesProbe() {
  const callsign = useStatusCallsign();
  const live = useStatusLive();
  const extras = useStatusExtras();
  return (
    <div>
      <span data-testid="slice-callsign">{callsign ?? ""}</span>
      <span data-testid="slice-ptt">{String(Boolean(live?.ptt_active))}</span>
      <span data-testid="slice-gpio">{String(Boolean((extras.gpio as { available?: boolean } | null)?.available))}</span>
    </div>
  );
}

describe("StatusProvider", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("location", { protocol: "http:", host: "pi.local" });
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("connects to /ws/status and updates subscribers on frames", async () => {
    render(
      <StatusProvider>
        <StatusProbe />
      </StatusProvider>,
    );

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url).toBe("ws://pi.local/ws/status");
    expect(screen.getByTestId("connected")).toHaveTextContent("false");

    await act(async () => {
      MockWebSocket.instances[0].open();
    });
    expect(screen.getByTestId("connected")).toHaveTextContent("true");

    await act(async () => {
      MockWebSocket.instances[0].push({ callsign: "W1AW", ptt_active: true });
    });
    expect(screen.getByTestId("callsign")).toHaveTextContent("W1AW");
    expect(screen.getByTestId("ptt")).toHaveTextContent("true");
  });

  it("ignores malformed frames", async () => {
    render(
      <StatusProvider>
        <StatusProbe />
      </StatusProvider>,
    );

    await act(async () => {
      MockWebSocket.instances[0].open();
      MockWebSocket.instances[0].push({ callsign: "N0CALL" });
      MockWebSocket.instances[0].pushRaw("not-json");
    });

    expect(screen.getByTestId("callsign")).toHaveTextContent("N0CALL");
  });

  it("marks disconnected and retries after close", async () => {
    render(
      <StatusProvider>
        <StatusProbe />
      </StatusProvider>,
    );

    await act(async () => {
      MockWebSocket.instances[0].open();
    });
    expect(screen.getByTestId("connected")).toHaveTextContent("true");

    await act(async () => {
      MockWebSocket.instances[0].push({ callsign: "W1AW", ptt_active: true });
      MockWebSocket.instances[0].close();
    });
    expect(screen.getByTestId("connected")).toHaveTextContent("false");
    expect(screen.getByTestId("callsign")).toHaveTextContent("");
    expect(screen.getByTestId("ptt")).toHaveTextContent("false");
    expect(MockWebSocket.instances).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2));
    expect(MockWebSocket.instances[1].url).toBe("ws://pi.local/ws/status");
  });

  it("discards frames and open events from a closed socket", async () => {
    render(
      <StatusProvider>
        <StatusProbe />
      </StatusProvider>,
    );

    const socket = MockWebSocket.instances[0];
    await act(async () => {
      socket.open();
      socket.push({ callsign: "W1AW" });
      socket.close();
      socket.push({ callsign: "STALE" });
      socket.open();
    });

    expect(screen.getByTestId("connected")).toHaveTextContent("false");
    expect(screen.getByTestId("raw")).toHaveTextContent("null");
  });

  it("clears status and reconnects when updates stop arriving", async () => {
    render(
      <StatusProvider>
        <StatusProbe />
      </StatusProvider>,
    );

    await act(async () => {
      MockWebSocket.instances[0].open();
      MockWebSocket.instances[0].push({ callsign: "W1AW", ptt_active: true });
      await vi.advanceTimersByTimeAsync(5000);
      MockWebSocket.instances[0].push(null);
      await vi.advanceTimersByTimeAsync(8000);
    });

    expect(screen.getByTestId("connected")).toHaveTextContent("false");
    expect(screen.getByTestId("raw")).toHaveTextContent("null");
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  it("uses wss when the page is https", () => {
    vi.stubGlobal("location", { protocol: "https:", host: "example.ts.net" });
    render(
      <StatusProvider>
        <StatusProbe />
      </StatusProvider>,
    );
    expect(MockWebSocket.instances[0].url).toBe("wss://example.ts.net/ws/status");
  });

  it("cancels reconnect when unmounted before the retry fires", async () => {
    const view = render(
      <StatusProvider>
        <StatusProbe />
      </StatusProvider>,
    );

    await act(async () => {
      MockWebSocket.instances[0].open();
      MockWebSocket.instances[0].close();
    });
    expect(MockWebSocket.instances).toHaveLength(1);

    view.unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("replaces status wholesale on each frame", async () => {
    render(
      <StatusProvider>
        <StatusProbe />
      </StatusProvider>,
    );

    await act(async () => {
      MockWebSocket.instances[0].open();
      MockWebSocket.instances[0].push({ callsign: "N0CALL", ptt_active: true, cos_active: true });
    });
    expect(screen.getByTestId("callsign")).toHaveTextContent("N0CALL");
    expect(screen.getByTestId("ptt")).toHaveTextContent("true");
    expect(screen.getByTestId("cos")).toHaveTextContent("true");

    await act(async () => {
      MockWebSocket.instances[0].push({ callsign: "W1AW", ptt_active: false });
    });
    expect(screen.getByTestId("callsign")).toHaveTextContent("W1AW");
    expect(screen.getByTestId("ptt")).toHaveTextContent("false");
    // Second frame omitted cos_active — must not keep the old value.
    expect(screen.getByTestId("cos")).toHaveTextContent("");
    expect(screen.getByTestId("raw")).toHaveTextContent(
      JSON.stringify({ callsign: "W1AW", ptt_active: false }),
    );
  });

  it("accepts frames on the socket opened after reconnect", async () => {
    render(
      <StatusProvider>
        <StatusProbe />
      </StatusProvider>,
    );

    await act(async () => {
      MockWebSocket.instances[0].open();
      MockWebSocket.instances[0].close();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2));

    await act(async () => {
      MockWebSocket.instances[1].open();
      MockWebSocket.instances[1].push({ callsign: "K1ABC", ptt_active: true });
    });

    expect(screen.getByTestId("connected")).toHaveTextContent("true");
    expect(screen.getByTestId("callsign")).toHaveTextContent("K1ABC");
    expect(screen.getByTestId("ptt")).toHaveTextContent("true");
  });

  it("keeps connected-only subscribers quiet when only live fields change", async () => {
    narrowRenderCount = 0;
    render(
      <StatusProvider>
        <NarrowConnectedProbe />
        <NarrowSlicesProbe />
      </StatusProvider>,
    );

    await act(async () => {
      MockWebSocket.instances[0].open();
      MockWebSocket.instances[0].push({
        callsign: "W1AW",
        ptt_active: false,
        gpio: { available: true, pins: [], error: null },
      });
    });
    const afterOpen = narrowRenderCount;

    await act(async () => {
      MockWebSocket.instances[0].push({
        callsign: "W1AW",
        ptt_active: true,
        gpio: { available: true, pins: [], error: null },
      });
    });

    expect(screen.getByTestId("slice-ptt")).toHaveTextContent("true");
    expect(screen.getByTestId("slice-callsign")).toHaveTextContent("W1AW");
    expect(screen.getByTestId("slice-gpio")).toHaveTextContent("true");
    // Connected flag unchanged → connected-only probe must not re-render.
    expect(narrowRenderCount).toBe(afterOpen);
  });

  it("defers React updates while the tab is hidden, then applies the pending frame", async () => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    render(
      <StatusProvider>
        <StatusProbe />
      </StatusProvider>,
    );

    await act(async () => {
      MockWebSocket.instances[0].open();
      MockWebSocket.instances[0].push({ callsign: "HIDDEN", ptt_active: true });
    });
    expect(screen.getByTestId("callsign")).toHaveTextContent("");
    expect(screen.getByTestId("ptt")).toHaveTextContent("false");

    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(screen.getByTestId("callsign")).toHaveTextContent("HIDDEN");
    expect(screen.getByTestId("ptt")).toHaveTextContent("true");
  });

  it("updates extras only when discrete gpio / link_radio fields change", async () => {
    render(
      <StatusProvider>
        <NarrowSlicesProbe />
      </StatusProvider>,
    );

    await act(async () => {
      MockWebSocket.instances[0].open();
      MockWebSocket.instances[0].push({
        callsign: "W1AW",
        gpio: { available: true, pins: [{ pin: 1, mode: "output", on: false }], error: null },
        link_radio: { enabled: true, running: true, receiving: false, transmitting: false, timed_out: false },
      });
    });
    expect(screen.getByTestId("slice-gpio")).toHaveTextContent("true");

    await act(async () => {
      MockWebSocket.instances[0].push({
        callsign: "W1AW",
        ptt_active: true,
        gpio: { available: true, pins: [{ pin: 1, mode: "output", on: false }], error: null },
        link_radio: { enabled: true, running: true, receiving: false, transmitting: false, timed_out: false },
      });
    });
    expect(screen.getByTestId("slice-ptt")).toHaveTextContent("true");

    await act(async () => {
      MockWebSocket.instances[0].push({
        callsign: "W1AW",
        ptt_active: true,
        gpio: { available: true, pins: [{ pin: 1, mode: "output", on: true }], error: null },
        link_radio: { enabled: true, running: true, receiving: false, transmitting: false, timed_out: false },
      });
    });
    expect(screen.getByTestId("slice-gpio")).toHaveTextContent("true");
  });
});
