/**
 * Synapse v2 design tokens — TypeScript mirror of the CSS custom properties
 * declared in src/styles/theme.css. Used for inline-style cases (SVG
 * fills, animated gradients, dynamic glow color) where Tailwind utilities
 * don't reach.
 *
 * Source of truth: docs/design_handoff_synapse/synapse-v2/core.jsx (`sv` object).
 * Keep these in lockstep with the CSS variables.
 */

export const sv = {
  // Surfaces
  bg0: "#05070c",
  bg1: "#0a0e18",
  bg2: "#121827",
  bg3: "#1a2234",

  // Ink
  ink: "#e6ecf5",
  inkDim: "#8893a8",
  inkFaint: "#4a5369",
  inkGhost: "#2a3147",

  // Accents
  cyan: "#5eead4",
  cyanHi: "#9ff8e8",
  cyanDim: "#2dd4bf",
  magenta: "#ff3d7f",
  magentaHi: "#ff7aa5",
  magentaDim: "#d63171",

  // Functional
  yellow: "#fde047",
  amber: "#fcd34d",
  green: "#86efac",
  greenDim: "#4ade80",
  red: "#ff5555",
  purple: "#a78bfa",

  // Lines
  line: "rgba(94, 234, 212, 0.14)",
  lineMid: "rgba(94, 234, 212, 0.24)",
  lineHi: "rgba(94, 234, 212, 0.42)",

  // Typography stacks
  mono: '"JetBrains Mono", ui-monospace, monospace',
  sans: '"Chakra Petch", "Space Grotesk", sans-serif',
  display: '"Chakra Petch", sans-serif',

  // Layout — single source of truth so Dashboard / Review / History
  // Page chrome (max-width, content padding) all stay in sync.
  layoutMaxWidth: 1600,
  layoutPadX: 24,
} as const;

export type SvAccent = "cyan" | "magenta" | "yellow" | "amber" | "green" | "red";

/** Accent palette indexed by name — used by SvBadge / SvBar / SvJobCard. */
export const accentColor: Record<SvAccent, string> = {
  cyan: sv.cyan,
  magenta: sv.magenta,
  yellow: sv.yellow,
  amber: sv.amber,
  green: sv.green,
  red: sv.red,
};

/** Hi (brighter) variants for hover / glow. */
export const accentHi: Record<SvAccent, string> = {
  cyan: sv.cyanHi,
  magenta: sv.magentaHi,
  yellow: sv.yellow,
  amber: sv.amber,
  green: sv.green,
  red: sv.red,
};
