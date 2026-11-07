import { describe, expect, it } from "vitest";
import { theme } from "./theme";

describe("Select theme defaults", () => {
  it("uses Mantine's documented native-scroll dropdown recipe", () => {
    const select = theme.components?.Select as {
      defaultProps?: { withScrollArea?: boolean; comboboxProps?: { withinPortal?: boolean; zIndex?: number } };
      styles?: { dropdown?: { maxHeight?: number; overflowY?: string } };
    };
    expect(select.defaultProps?.withScrollArea).toBe(false);
    expect(select.defaultProps?.comboboxProps).toEqual({ withinPortal: true, zIndex: 400 });
    expect(select.styles?.dropdown).toEqual({ maxHeight: 220, overflowY: "auto" });
  });
});
