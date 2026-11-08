import { describe, expect, it } from "vitest";
import { curvePath, isReady, labelSide, mondayFirstDay, recentNodes, ringPositions, setupSteps, weekBlocks, type ScheduleLike } from "./model";

const sched = (over: Partial<ScheduleLike>): ScheduleLike => ({
  node: "1",
  name: "",
  days: [1],
  time: "20:00",
  minutes: 60,
  monitor: false,
  enabled: true,
  ...over,
});

describe("ringPositions", () => {
  it("returns nothing for no links and puts one link to the right", () => {
    expect(ringPositions(0)).toEqual([]);
    expect(ringPositions(1)).toHaveLength(1);
    expect(ringPositions(1)[0].x).toBeGreaterThan(50);
  });
  it("keeps every node inside the box", () => {
    for (const p of ringPositions(12)) {
      expect(p.x).toBeGreaterThan(5);
      expect(p.x).toBeLessThan(95);
      expect(p.y).toBeGreaterThan(5);
      expect(p.y).toBeLessThan(95);
    }
  });
});

describe("weekBlocks", () => {
  it("places a simple block", () => {
    expect(weekBlocks([sched({})])).toEqual([
      { index: 0, day: 1, startMin: 1200, endMin: 1260, openEnded: false, continued: false },
    ]);
  });
  it("spills past midnight into the next day", () => {
    const blocks = weekBlocks([sched({ days: [6], time: "23:00", minutes: 120 })]);
    expect(blocks).toHaveLength(2);
    expect(blocks[1]).toMatchObject({ day: 0, startMin: 0, endMin: 60, continued: true });
  });
  it("draws open-ended links to the end of the day", () => {
    expect(weekBlocks([sched({ minutes: 0 })])[0]).toMatchObject({ endMin: 1440, openEnded: true });
  });
  it("skips schedules with no usable time", () => {
    expect(weekBlocks([sched({ time: "" }), sched({ time: "25:00" })])).toEqual([]);
  });
});

describe("mondayFirstDay", () => {
  it("maps Sunday to 6 and Monday to 0", () => {
    expect(mondayFirstDay(new Date("2026-11-08T12:00:00"))).toBe(6);
    expect(mondayFirstDay(new Date("2026-11-09T12:00:00"))).toBe(0);
  });
});

describe("setup", () => {
  const base = { available: true, manual: false, restart_needed: false, error: null, node: null };
  it("is not ready until a node is chosen", () => {
    expect(isReady(null)).toBe(false);
    expect(isReady({ ...base, available: false })).toBe(false);
    expect(isReady(base)).toBe(false);
    expect(isReady({ ...base, node: "12345" })).toBe(true);
    expect(isReady({ ...base, node: "12345", restart_needed: true })).toBe(false);
  });
  it("marks steps done as they happen", () => {
    expect(setupSteps({ ...base, available: false }, 0).map((s) => s.done)).toEqual([false, false, false]);
    expect(setupSteps({ ...base, node: "12345", identity: { has_password: true } }, 1).every((s) => s.done)).toBe(true);
  });
  it("asks for the password when a real node has none", () => {
    const steps = setupSteps({ ...base, node: "12345", identity: { has_password: false } }, 0);
    expect(steps[1].done).toBe(false);
  });
  it("adds a restart step only when needed", () => {
    expect(setupSteps({ ...base, node: "1999", restart_needed: true }, 0).some((s) => s.id === "restart")).toBe(true);
    expect(setupSteps(base, 0).some((s) => s.id === "restart")).toBe(false);
  });
});

describe("recentNodes", () => {
  const ev = (node: string, at: string, kind: "connected" | "disconnected" = "connected") => ({ at, kind, node, label: `n${node}` });
  it("newest first, no duplicates, skips favorites and linked", () => {
    const out = recentNodes(
      [ev("1", "2026-01-01"), ev("2", "2026-01-03"), ev("2", "2026-01-02"), ev("3", "2026-01-04"), ev("4", "2026-01-05"), ev("5", "2026-01-06", "disconnected")],
      ["3"],
      ["4"],
    );
    expect(out.map((r) => r.node)).toEqual(["2", "1"]);
  });
});

describe("curvePath", () => {
  it("starts at the hub and ends at the node", () => {
    expect(curvePath(80, 30)).toMatch(/^M 50 50 Q [\d.]+ [\d.]+ 80\.00 30\.00$/);
  });
  it("bows every wire the same way", () => {
    const control = (d: string) => d.split(" ").slice(4, 6).map(Number);
    const [ax, ay] = control(curvePath(80, 50));
    const [bx, by] = control(curvePath(20, 50));
    // straight-line midpoints are (65,50) and (35,50); offsets are opposite in y
    expect(ay - 50).toBeGreaterThan(0);
    expect(by - 50).toBeLessThan(0);
    expect(ax).toBeCloseTo(65);
    expect(bx).toBeCloseTo(35);
  });
});

describe("labelSide", () => {
  it("puts labels above nodes in the top half and below the rest", () => {
    expect(labelSide(20)).toBe("up");
    expect(labelSide(50)).toBe("down");
    expect(labelSide(80)).toBe("down");
  });
});
