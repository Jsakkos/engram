import { sv } from "./tokens";
import { SvMark } from "./SvMark";
import { MarkMono } from "./MarkMono";

interface Props {
  /** Side length in px. Required — there is no sensible default for an icon. */
  size: number;
  /** "dark" (default) or "light" / paper edition. */
  edition?: "dark" | "light";
  /** Render the mark's radial glow. Defaults to true. */
  glow?: boolean;
  /** Show the version stamp in the bottom-right corner. Auto at size >= 96. */
  versionStamp?: boolean;
  /**
   * Override the version label. Defaults to the Vite-injected
   * `__APP_VERSION__` global (e.g. "0.6.0") so the stamp tracks
   * `package.json` automatically.
   */
  version?: string;
}

/**
 * Resolve the version label safely. `__APP_VERSION__` is a Vite-injected
 * global available in the running app, but unit tests and any future
 * SSR consumer won't have it defined — fall back to "1" rather than
 * crashing on the ReferenceError.
 */
function resolveVersion(override?: string): string {
  if (override) return override;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as any;
  return typeof g.__APP_VERSION__ === "string" ? g.__APP_VERSION__ : "1";
}

const PAPER_BG = "#F3EEE4";
const PAPER_INK = "#15161A";

/**
 * Application icon — rounded square ("squircle") containing the mark.
 * Used for OS app icons (.icns, .ico) and any place we want a "framed"
 * version of the mark (about modal, dock area). The bare mark goes on
 * favicons.
 *
 * Dark edition: cyan ring grid + scanlines + corner ticks + v1 stamp.
 * Light edition (paper): monochrome mark on warm paper, no glow or chrome.
 *
 * Below size=24 the inner mark falls back to <MarkMono> (drops the
 * read-line/node, which would not render cleanly at that size).
 */
export function AppIcon({
  size,
  edition = "dark",
  glow = true,
  versionStamp,
  version,
}: Props) {
  const dark = edition === "dark";
  const radius = Math.round(size * 0.2237);
  const inset = size * 0.18;
  const showVersion = versionStamp ?? (dark && size >= 96);
  const useMonogram = size < 24;
  const versionLabel = resolveVersion(version);

  return (
    <div
      data-testid="sv-app-icon"
      data-edition={edition}
      style={{
        width: size,
        height: size,
        borderRadius: radius,
        overflow: "hidden",
        position: "relative",
        background: dark
          ? `radial-gradient(ellipse at 30% 20%, #102031, ${sv.bg0} 60%, #02030a)`
          : PAPER_BG,
        boxShadow: dark
          ? `0 ${size * 0.04}px ${size * 0.12}px rgba(0,0,0,0.5), inset 0 0 0 1px rgba(94,234,212,0.18)`
          : `0 ${size * 0.04}px ${size * 0.12}px rgba(0,0,0,0.18), inset 0 0 0 1px rgba(0,0,0,0.06)`,
      }}
    >
      {dark && <RingGrid />}
      {dark && <Scanlines />}
      <CornerTicks
        size={size}
        color={dark ? "rgba(94,234,212,0.42)" : "rgba(0,0,0,0.25)"}
      />

      {/* The mark itself, centered inside the inset area. */}
      <div
        style={{
          position: "absolute",
          inset,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {useMonogram ? (
          <MarkMono
            size={size - inset * 2}
            color={dark ? sv.cyan : PAPER_INK}
            glow={dark && glow}
          />
        ) : (
          <SvMark
            size={size - inset * 2}
            color={dark ? sv.cyan : PAPER_INK}
            accent={dark ? sv.magenta : PAPER_INK}
            glow={dark && glow}
            monochrome={!dark}
          />
        )}
      </div>

      {showVersion && (
        <div
          style={{
            position: "absolute",
            bottom: size * 0.06,
            right: size * 0.08,
            fontFamily: sv.mono,
            fontSize: size * 0.05,
            letterSpacing: "0.18em",
            color: "rgba(230,236,245,0.32)",
          }}
        >
          v{versionLabel}
        </div>
      )}
    </div>
  );
}

/**
 * Faint concentric ring grid behind the mark — hints at the disc tracks
 * without competing with the mark's three arcs.
 */
function RingGrid() {
  return (
    <svg
      viewBox="0 0 128 128"
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
      }}
    >
      {[50, 40, 30, 20].map((r, i) => (
        <circle
          key={r}
          cx="64"
          cy="64"
          r={r}
          fill="none"
          stroke={sv.cyan}
          strokeWidth="0.4"
          opacity={0.04 + i * 0.02}
        />
      ))}
    </svg>
  );
}

function Scanlines() {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        opacity: 0.18,
        backgroundImage:
          "repeating-linear-gradient(0deg, rgba(94,234,212,0.18) 0 1px, transparent 1px 4px)",
      }}
    />
  );
}

interface CornerTickProps {
  size: number;
  color: string;
}

function CornerTicks({ size, color }: CornerTickProps) {
  const inset = size * 0.06;
  const tick = size * 0.05;
  const base = {
    position: "absolute" as const,
    width: tick,
    height: tick,
    borderColor: color,
    borderStyle: "solid",
    pointerEvents: "none" as const,
  };
  return (
    <>
      <div style={{ ...base, top: inset, left: inset, borderWidth: "1.2px 0 0 1.2px" }} />
      <div style={{ ...base, top: inset, right: inset, borderWidth: "1.2px 1.2px 0 0" }} />
      <div style={{ ...base, bottom: inset, left: inset, borderWidth: "0 0 1.2px 1.2px" }} />
      <div style={{ ...base, bottom: inset, right: inset, borderWidth: "0 1.2px 1.2px 0" }} />
    </>
  );
}
