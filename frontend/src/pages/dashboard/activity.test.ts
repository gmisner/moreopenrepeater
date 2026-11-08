import { describe, expect, it } from "vitest";
import { formatAirtime, groupByDay } from "./activity";

describe("dashboard activity helpers", () => {
  it("groups transmissions by day, newest first, and flags kerchunks and timeouts", () => {
    const now = new Date(2026, 10, 8, 12, 0, 0);
    const groups = groupByDay(
      [
        { started_at: new Date(2026, 10, 8, 11, 0).toISOString(), duration: 12, timed_out: false },
        { started_at: new Date(2026, 10, 8, 10, 0).toISOString(), duration: 0.8, timed_out: false },
        { started_at: new Date(2026, 10, 7, 9, 0).toISOString(), duration: 180, timed_out: true },
      ],
      now,
    );
    expect(groups.map((g) => g.label)).toEqual(["Today", "Yesterday"]);
    expect(groups[0].rows.map((r) => r.kind)).toEqual(["normal", "kerchunk"]);
    expect(groups[1].rows[0].kind).toBe("timeout");
  });

  it("formats airtime", () => {
    expect(formatAirtime(4)).toBe("4s");
    expect(formatAirtime(95)).toBe("1m 35s");
  });
});
