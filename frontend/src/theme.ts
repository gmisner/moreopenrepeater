import { createTheme, virtualColor, type MantineColorsTuple } from "@mantine/core";

// MASTER tokens from design-system/moreopenrepeater/MASTER.md.
// Mantine color tuples are 10 shades; we pin the semantic stops we use.

const accent: MantineColorsTuple = [
  "#e8f2ff",
  "#d0e4ff",
  "#a0c9ff",
  "#70adff",
  "#5aa5ff",
  "#1a5cc8",
  "#1649a0",
  "#113778",
  "#0c2550",
  "#08111f",
];

const green: MantineColorsTuple = [
  "#e6f9ef",
  "#c6f0d8",
  "#8ae0b0",
  "#55d48f",
  "#35d07f",
  "#0a6b3b",
  "#085830",
  "#064524",
  "#043218",
  "#021f0c",
];

const red: MantineColorsTuple = [
  "#ffe8e8",
  "#ffcfcf",
  "#ffa0a0",
  "#ff8585",
  "#ff7070",
  "#b8292a",
  "#962122",
  "#74191a",
  "#521212",
  "#1a0505",
];

const amber: MantineColorsTuple = [
  "#fff6e5",
  "#ffe8bf",
  "#ffd080",
  "#ffc040",
  "#ffb020",
  "#8a5200",
  "#6e4200",
  "#523100",
  "#362100",
  "#1a1000",
];

const gray: MantineColorsTuple = [
  "#f3f4f6",
  "#eef0f3",
  "#e6e8eb",
  "#d5d9df",
  "#b9bfc8",
  "#8a919c",
  "#5a6270",
  "#2c323b",
  "#1b1f25",
  "#111317",
];

// Mantine's dark scheme (inputs, cards, default borders) uses `colors.dark`,
// not `gray`. Pin it to MASTER so fields keep a visible edge on panels.
const dark: MantineColorsTuple = [
  "#e6e8eb", // 0 body text (--text)
  "#d5d9df",
  "#b9bfc8",
  "#8a919c", // 3 muted / placeholders
  "#4a515c", // 4 borders — a step above MASTER --border so inputs read on --panel
  "#22272e", // 5 --panel-2 / filled controls
  "#0b0c0f", // 6 --inset / default input fill (darker than panel)
  "#1b1f25", // 7 --panel
  "#16191e", // 8 --sidebar
  "#111317", // 9 --bg
];

export const theme = createTheme({
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  fontFamilyMonospace: "ui-monospace, SFMono-Regular, Menlo, monospace",
  primaryColor: "accent",
  defaultRadius: "md",
  cursorType: "pointer",
  colors: {
    accent,
    green,
    red,
    amber,
    gray,
    dark,
    // Virtual colors flip between light/dark semantic stops.
    brand: virtualColor({ name: "brand", light: "accent", dark: "accent" }),
  },
  primaryShade: { light: 5, dark: 4 },
  white: "#ffffff",
  black: "#111317",
  fontSizes: {
    xs: "0.78rem",
    sm: "0.86rem",
    md: "0.92rem",
    lg: "1rem",
    xl: "1.2rem",
  },
  headings: {
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    sizes: {
      h1: { fontSize: "1.5rem", fontWeight: "700", lineHeight: "1.3" },
      h2: {
        fontSize: "0.72rem",
        fontWeight: "600",
        lineHeight: "1.4",
      },
    },
  },
  spacing: {
    xs: "0.5rem",
    sm: "0.75rem",
    md: "1rem",
    lg: "1.25rem",
    xl: "1.5rem",
  },
  breakpoints: {
    xs: "36em",
    sm: "48em",
    md: "53.75em",
    lg: "75em",
    xl: "88em",
  },
  components: {
    Input: {
      defaultProps: {
        variant: "default",
      },
      styles: {
        input: {
          color: "var(--mantine-color-text)",
          // --mor-field-* come from field-contrast.css (scheme-aware).
          border: "2px solid var(--mor-field-bd, #8a919c)",
          backgroundColor: "var(--mor-field-bg, #0b0c0f)",
        },
      },
    },
    TextInput: {
      defaultProps: { variant: "default" },
    },
    NumberInput: {
      defaultProps: { variant: "default" },
    },
    Select: {
      defaultProps: { variant: "default" },
    },
    Textarea: {
      defaultProps: { variant: "default" },
    },
    PasswordInput: {
      defaultProps: { variant: "default" },
    },
  },
  other: {
    status: {
      tx: "var(--mantine-color-red-4)",
      rx: "var(--mantine-color-green-4)",
      warn: "var(--mantine-color-amber-4)",
      idle: "var(--mantine-color-gray-5)",
    },
  },
});
