import { Button } from "@mantine/core";
import { Fragment } from "react";
import { EmptyState } from "../../components/EmptyState";
import { shortTime } from "./format";
import { minutesLabel, mondayFirstDay, weekBlocks, type ScheduleLike } from "./model";
import "./allstar.css";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const FULL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

type LiveRow = { node: string; next_start?: string | null; until?: string | null };

function duration(minutes: number) {
  if (minutes <= 0) return "until disconnect";
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

function dayList(days: number[]) {
  if (days.length === 7) return "every day";
  return days.map((d) => DAYS[d]).join(", ");
}

export function ScheduleWeek({
  schedules,
  live,
  now = new Date(),
  onEdit,
}: {
  schedules: (ScheduleLike & { name: string })[];
  live: LiveRow[];
  now?: Date;
  onEdit?: () => void;
}) {
  if (!schedules.length) {
    return (
      <EmptyState
        action={
          onEdit ? (
            <Button size="xs" variant="default" onClick={onEdit}>
              Add a schedule
            </Button>
          ) : undefined
        }
      >
        No scheduled links yet. Add one to connect to a node automatically, like a weekly net.
      </EmptyState>
    );
  }

  const blocks = weekBlocks(schedules);
  const today = mondayFirstDay(now);
  const nowPct = ((now.getHours() * 60 + now.getMinutes()) / 1440) * 100;

  return (
    <>
      <div className="mor-as mor-week">
        <div style={{ gridColumn: 1 }} />
        <div className="mor-week-axis" aria-hidden="true">
          {[
            [0, "12 AM"],
            [25, "6 AM"],
            [50, "12 PM"],
            [75, "6 PM"],
            [100, "12 AM"],
          ]
            .filter(([at]) => Math.abs((at as number) - nowPct) > 9)
            .map(([at, label]) => (
              <span key={at} style={{ left: `${at}%` }}>
                {label}
              </span>
            ))}
        </div>
        {DAYS.map((label, day) => (
          <div key={label} style={{ display: "contents" }}>
            <div className="mor-week-day" data-today={day === today ? "" : undefined}>
              {label}
            </div>
            <div className="mor-week-track" data-today={day === today ? "" : undefined}>
              {blocks
                .filter((b) => b.day === day)
                .map((b) => {
                  const s = schedules[b.index];
                  const width = ((b.endMin - b.startMin) / 1440) * 100;
                  const text = s.name.trim() || s.node;
                  const inside = width > 14;
                  const endPct = (b.endMin / 1440) * 100;
                  const startPct = (b.startMin / 1440) * 100;
                  const tagRight = endPct < 80;
                  return (
                    <Fragment key={`${b.index}-${b.startMin}-${b.continued}`}>
                    <button
                      type="button"
                      className="mor-week-block"
                      style={{ left: `${(b.startMin / 1440) * 100}%`, width: `${width}%` }}
                      data-open={b.openEnded ? "" : undefined}
                      data-off={s.enabled ? undefined : ""}
                      data-continued={b.continued ? "" : undefined}
                      onClick={onEdit ?? (() => {})}
                      disabled={!onEdit}
                      aria-label={`${FULL_DAYS[b.day]} ${minutesLabel(b.startMin)} to ${b.openEnded ? "disconnect" : minutesLabel(b.endMin)}, node ${s.node}${
                        s.name.trim() ? `, ${s.name.trim()}` : ""
                      }${s.enabled ? "" : ", turned off"}. Edit schedules.`}
                      title={`${text} · ${s.time} · ${duration(s.minutes)}${s.enabled ? "" : " · off"}`}
                    >
                      {inside ? text : ""}
                    </button>
                    {!inside && !b.continued ? (
                      <span
                        className="mor-week-tag"
                        style={tagRight ? { left: `${endPct}%` } : { right: `${100 - startPct}%` }}
                        aria-hidden="true"
                      >
                        {text}
                      </span>
                    ) : null}
                    </Fragment>
                  );
                })}
            </div>
          </div>
        ))}
        <div className="mor-week-now" style={{ left: `calc(2.6rem + (100% - 2.6rem) * ${nowPct / 100})` }} aria-hidden="true" />
      </div>

      <ul className="mor-as mor-week-list" aria-label="Scheduled links">
        {schedules.map((s, i) => {
          const status = live[i];
          const state = status?.until ? `linked until ${shortTime(status.until)}` : status?.next_start ? `next ${shortTime(status.next_start)}` : "";
          return (
            <li key={`${s.node}-${s.time}-${i}`}>
              <b>{s.time || "—:—"}</b>
              <span>{dayList(s.days)}</span>
              <span>
                node {s.node || "—"}
                {s.name.trim() ? `, ${s.name.trim()}` : ""}
              </span>
              <small>
                {duration(s.minutes)}
                {s.monitor ? ", monitor only" : ""}
                {s.enabled ? "" : ", off"}
                {state ? `, ${state}` : ""}
              </small>
            </li>
          );
        })}
      </ul>
    </>
  );
}
