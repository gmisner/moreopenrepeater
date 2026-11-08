import { Badge, Card } from "@mantine/core";
import { Link } from "react-router-dom";
import type { Config } from "../../config";
import type { RepeaterStatus } from "../../status";

// What each controller state means, in the words an operator would use.
const STATE_COPY: Record<string, string> = {
  idle: "Waiting for a signal.",
  receiving: "Repeating a local signal.",
  transmitting_id: "Sending the station ID.",
  courtesy_tone: "Playing the courtesy tone.",
  hang_time: "Holding the transmitter up for a reply.",
  timeout: "The time-out timer tripped. It resets when the carrier drops.",
  lockout: "Locked out after repeated time-outs.",
  announcing: "Playing an announcement.",
  patch: "Phone call on the air.",
};

const STATE_LABEL: Record<string, string> = {
  idle: "Idle",
  receiving: "Receiving",
  transmitting_id: "Sending ID",
  courtesy_tone: "Courtesy tone",
  hang_time: "Hang time",
  timeout: "Timed out",
  lockout: "Locked out",
  announcing: "Announcing",
  patch: "Phone call",
};

type Tone = "idle" | "rx" | "tx" | "alert";

function stateTone(state: string): Tone {
  if (state === "receiving") return "rx";
  if (state === "timeout" || state === "lockout") return "alert";
  if (state === "idle" || state === "unknown") return "idle";
  return "tx";
}

const num = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : null);

/** m:ss for timers. */
function clock(seconds: number) {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function hz(value: number) {
  return value.toFixed(1);
}

function StationLine({ status, config }: { status: RepeaterStatus | null; config: Config | null }) {
  const freq = num(config?.aprs_frequency_mhz);
  const offset = num(config?.aprs_offset_mhz);
  const tone = num(config?.aprs_tone_hz) ?? num(config?.require_ctcss_hz);
  const callsign = status?.callsign || "";
  return (
    <div className="mor-onair-station">
      {callsign ? (
        <span className="mor-onair-callsign">{callsign}</span>
      ) : (
        <Link to="/identification">Set a callsign</Link>
      )}
      {freq != null ? (
        <span className="mor-dash-num">
          {freq.toFixed(3)} MHz
          {offset ? ` ${offset > 0 ? "+" : "−"}${Math.abs(offset).toFixed(3)}` : ""}
        </span>
      ) : null}
      {tone != null ? <span className="mor-dash-num">Tone {hz(tone)}</span> : null}
      {status?.transmitter_enabled === false ? (
        <Badge component={Link} to="/timing" color="red" variant="light" style={{ textDecoration: "none", cursor: "pointer" }}>
          Transmitter muted
        </Badge>
      ) : null}
      {status?.net_active ? (
        <Badge component={Link} to="/net" color="amber" variant="light" style={{ textDecoration: "none", cursor: "pointer" }}>
          Net running
        </Badge>
      ) : null}
    </div>
  );
}

type TimerProps = {
  label: string;
  remaining: number | null;
  total: number | null;
  /** "fill" grows as time is used (TOT); "drain" shrinks as it runs out (hang, ID). */
  mode: "fill" | "drain";
  idleText: string;
  warnUnder?: number;
};

function Timer({ label, remaining, total, mode, idleText, warnUnder }: TimerProps) {
  const running = remaining != null;
  let fill = 0;
  if (running && total && total > 0) {
    const left = Math.min(1, Math.max(0, remaining / total));
    fill = mode === "fill" ? 1 - left : left;
  }
  const warn = running && warnUnder != null && remaining <= warnUnder && remaining > 0;
  const danger = running && mode === "fill" && remaining <= 0;
  return (
    <div
      className="mor-timer"
      data-idle={running ? undefined : ""}
      data-warn={warn ? "" : undefined}
      data-danger={danger ? "" : undefined}
    >
      <dt>{label}</dt>
      <dd>{running ? clock(remaining) : idleText}</dd>
      <div className="mor-timer-bar" aria-hidden="true">
        <span style={{ ["--fill" as string]: `${(fill * 100).toFixed(1)}%` }} />
      </div>
    </div>
  );
}

type PathStage = { name: string; value: string; lit?: "rx" | "tx" | "busy"; wire?: "rx" | "tx" };

function SignalPath({ stages }: { stages: PathStage[] }) {
  return (
    <ol className="mor-path" aria-label="Signal path">
      {stages.map((stage) => (
        <li key={stage.name} className="mor-path-node" data-lit={stage.lit} data-wire={stage.wire}>
          <span className="mor-path-dot" aria-hidden="true" />
          <span className="mor-path-name">{stage.name}</span>
          <span className="mor-path-value">{stage.value}</span>
        </li>
      ))}
    </ol>
  );
}

export function OnAir({ status, config }: { status: RepeaterStatus | null; config: Config | null }) {
  const state = status?.state ?? "unknown";
  const tone = stateTone(state);
  const cos = Boolean(status?.cos_active);
  const ptt = Boolean(status?.ptt_active);
  const ctcss = num(status?.ctcss_hz);
  const required = num(config?.require_ctcss_hz);
  const accessOk = required == null || ctcss != null;
  const repeating = state === "receiving" && cos;

  const toneValue =
    cos && ctcss != null ? `${hz(ctcss)} Hz` : required != null ? `Needs ${hz(required)}` : cos ? "None" : "Not required";

  const stages: PathStage[] = [
    { name: "Receiver", value: cos ? "Carrier" : "Quiet", lit: cos ? "rx" : undefined, wire: cos && accessOk ? "rx" : undefined },
    {
      name: "Tone",
      value: toneValue,
      lit: cos && ctcss != null ? "rx" : undefined,
      wire: repeating ? "rx" : undefined,
    },
    {
      name: "Controller",
      value: STATE_LABEL[state] ?? state.replaceAll("_", " "),
      lit: tone === "rx" ? "rx" : tone === "tx" ? "busy" : tone === "alert" ? "tx" : undefined,
      wire: ptt ? "tx" : undefined,
    },
    { name: "Transmitter", value: status?.transmitter_enabled === false ? "Muted" : ptt ? "Keyed" : "Off", lit: ptt ? "tx" : undefined },
  ];

  const tot = num(config?.tot_duration);
  const hang = num(config?.hang_time);
  const idInterval = num(config?.id_interval);

  return (
    <Card withBorder radius="md" className="mor-onair" data-tone={status?.locked_out ? "alert" : tone} component="section" aria-label="On the air">
      <StationLine status={status} config={config} />
      <div className="mor-onair-main">
        <div>
          <h2 className="mor-onair-state" data-tone={tone}>
            {STATE_LABEL[state] ?? state.replaceAll("_", " ")}
          </h2>
          <p className="mor-onair-copy" style={{ marginBottom: 0 }}>
            {status ? STATE_COPY[state] ?? "" : "Waiting for the controller."}
          </p>
        </div>
        <dl className="mor-timers" aria-label="Timers">
          <Timer
            label="Time-out timer"
            remaining={num(status?.tot_remaining)}
            total={tot}
            mode="fill"
            idleText={tot != null ? `${clock(tot)} limit` : "Not running"}
            warnUnder={tot != null ? Math.min(30, tot * 0.2) : undefined}
          />
          <Timer label="Hang time" remaining={num(status?.hang_remaining)} total={hang} mode="drain" idleText="Not running" />
          <Timer label="Next ID" remaining={num(status?.id_due_in)} total={idInterval} mode="drain" idleText="None owed" />
        </dl>
      </div>
      <SignalPath stages={stages} />
    </Card>
  );
}
