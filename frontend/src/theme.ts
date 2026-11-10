import { createTheme, virtualColor, type MantineColorsTuple } from "@mantine/core";

// MASTER tokens from design-system/moreopenrepeater/MASTER.md.
// Mantine color tuples are 10 shades; we pin the semantic stops we use.

// MOR Blue — primary actions and brand chrome (never TX/RX status).
const accent: MantineColorsTuple = [
  "#e8f2ff",
  "#d0e5ff",
  "#a8d1ff",
  "#7abafe",
  "#3da5ff",
  "#0d6efd",
  "#0b5ed7",
  "#094bb0",
  "#073888",
  "#041f4d",
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

// Mantine dark scheme uses `colors.dark` for body/borders/defaults:
// dark-4 = default border, dark-6 = default fill, dark-7 = --mantine-color-body.
// Cards are painted to MASTER --panel in field-contrast.css (Paper uses body).
const dark: MantineColorsTuple = [
  "#e5e7eb", // 0 body text (--text)
  "#d5d9df",
  "#b9bfc8",
  "#8a919c", // 3 muted / placeholders
  "#243044", // 4 --border
  "#1a2436", // 5 --panel-2
  "#080e18", // 6 --inset / default control fill
  "#0b1220", // 7 --bg (Mantine body)
  "#10182a", // 8 --sidebar
  "#080e18", // 9 deepest
];

// One UI face everywhere. Controllers are often offline, so nothing loads from
// a CDN — brand guidelines name Montserrat/Inter, but mixed fallbacks read as
// three fonts on a Pi. Keep monospace only for tabular telemetry.
const uiSans = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

export const theme = createTheme({
  fontFamily: uiSans,
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
  black: "#0b1220",
  fontSizes: {
    xs: "0.78rem",
    sm: "0.86rem",
    md: "0.92rem",
    lg: "1rem",
    xl: "1.2rem",
  },
  headings: {
    fontFamily: uiSans,
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
    Anchor: {
      defaultProps: {
        underline: "always",
      },
    },
    Input: {
      defaultProps: {
        variant: "default",
      },
      styles: {
        input: {
          color: "var(--mantine-color-text)",
          // --mor-field-* come from field-contrast.css (scheme-aware).
          border: "1px solid var(--mor-field-bd, #2c323b)",
          backgroundColor: "var(--mor-field-bg, #0b0c0f)",
        },
      },
    },
    TextInput: {
      defaultProps: { variant: "default" },
    },
    // Numeric fields hold a short value and a unit, so they don't need the
    // full width of a form column. Pass maw to widen one.
    NumberInput: {
      defaultProps: { variant: "default", maw: 240 },
    },
    // Mantine's documented native-scroll Select recipe
    // (https://mantine.dev/core/select/#scrollable-dropdown): ScrollArea is the
    // library default, but on long pages its dropdown steals poorly — wheel
    // scrolls the document and closes the popover. Prefer the same native
    // overflow approach everywhere so every Select behaves alike.
    Select: {
      defaultProps: {
        variant: "default",
        withScrollArea: false,
        comboboxProps: { withinPortal: true, zIndex: 400 },
      },
      styles: {
        dropdown: {
          maxHeight: 220,
          overflowY: "auto",
        },
      },
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
