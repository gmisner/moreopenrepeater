import { Button, Card, Stack, Text, Title } from "@mantine/core";
import { useEffect, useRef, useState } from "react";
import { BrandMark } from "../layout/BrandMark";

type PublicStatus = {
  callsign?: string;
  text?: string;
  on_air?: boolean;
  receiving?: boolean;
  net?: string | null;
  audio?: boolean;
  username?: string | null;
  listeners?: number;
  max_listeners?: number;
};

const POLL_MS = 2000;
const LEAD_SECONDS = 0.25;
const MAX_LAG_SECONDS = 1.0;

export function Listen() {
  const [status, setStatus] = useState<PublicStatus | null>(null);
  const [listening, setListening] = useState(false);
  const [listenNote, setListenNote] = useState("");
  const contextRef = useRef<AudioContext | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const nextTimeRef = useRef(0);
  const sampleRateRef = useRef(16000);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const response = await fetch("/api/public/status", { cache: "no-store" });
        if (response.status === 401) {
          location.replace("/login?next=/listen");
          return;
        }
        if (response.status === 404) {
          if (!cancelled) setStatus({ text: "This page is turned off." });
          return;
        }
        if (!cancelled) setStatus(await response.json());
      } catch {
        if (!cancelled) setStatus({ text: "Can't reach the repeater." });
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      socketRef.current?.close();
    };
  }, []);

  const stop = () => {
    socketRef.current?.close();
    socketRef.current = null;
    void contextRef.current?.suspend();
    setListening(false);
    setListenNote("");
  };

  const start = async () => {
    try {
      contextRef.current ??= new AudioContext();
      await contextRef.current.resume();
    } catch (error) {
      setListenNote(error instanceof Error ? error.message : String(error));
      return;
    }
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${location.host}/ws/public/audio`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        sampleRateRef.current = JSON.parse(event.data).sample_rate;
        return;
      }
      const ctx = contextRef.current;
      if (!ctx) return;
      const ints = new Int16Array(event.data as ArrayBuffer);
      const buffer = ctx.createBuffer(1, ints.length, sampleRateRef.current);
      const channel = buffer.getChannelData(0);
      for (let i = 0; i < ints.length; i++) channel[i] = ints[i] / 32768;
      const node = ctx.createBufferSource();
      node.buffer = buffer;
      node.connect(ctx.destination);
      const now = ctx.currentTime;
      if (nextTimeRef.current < now + 0.02 || nextTimeRef.current > now + MAX_LAG_SECONDS) {
        nextTimeRef.current = now + LEAD_SECONDS;
      }
      node.start(nextTimeRef.current);
      nextTimeRef.current += buffer.duration;
    };
    ws.onclose = () => {
      if (socketRef.current === ws) stop();
    };
    socketRef.current = ws;
    nextTimeRef.current = 0;
    setListening(true);
    setListenNote("Connecting…");
  };

  useEffect(() => {
    if (status?.callsign) document.title = `${status.callsign} · Listen live`;
  }, [status?.callsign]);

  const stateLabel = !status
    ? "…"
    : status.on_air
      ? status.receiving
        ? "Someone's talking"
        : "On the air"
      : status.text?.includes("turned off") || status.text?.includes("Can't reach")
        ? status.text
        : "Quiet";

  return (
    <Stack mih="100dvh" align="center" justify="center" p="md">
      <Card withBorder padding="xl" radius="lg" w="100%" maw={420}>
        <Stack gap="md">
          <BrandMark size={32} />
          <div>
            <Title order={1}>{status?.callsign || "Repeater"}</Title>
            {status?.text && !status.text.includes("turned off") && !status.text.includes("Can't reach") ? (
              <Text c="dimmed">{status.text}</Text>
            ) : null}
          </div>
          <Title order={2} c={status?.on_air ? "green" : "dimmed"} style={{ fontSize: "1.5rem" }}>
            {stateLabel}
          </Title>
          {status?.net ? <Text c="dimmed">{status.net} is in session.</Text> : null}
          {status?.audio ? (
            <Stack gap="xs">
              <Button
                onClick={() => (listening ? stop() : void start())}
                variant={listening ? "light" : "filled"}
                color={listening ? "accent" : "accent"}
              >
                {listening ? "Stop" : "Listen"}
              </Button>
              <Text size="sm" c="dimmed">
                {listenNote ||
                  (status.listeners != null && status.max_listeners != null && status.listeners >= status.max_listeners
                    ? "Every listening spot is taken right now. Try again in a while."
                    : "")}
              </Text>
            </Stack>
          ) : null}
          {status?.username ? (
            <Text size="sm" c="dimmed">
              Signed in as {status.username}
            </Text>
          ) : null}
        </Stack>
      </Card>
    </Stack>
  );
}
