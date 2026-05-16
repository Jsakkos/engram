import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { motion } from "motion/react";
import { sv } from "./tokens";
import { SvCorners } from "./SvCorners";
import { SvLabel } from "./SvLabel";

/* ═══════════════════════════════════════════════════════════════════════════
   SvRipAnimation — "C3 · Bitstream · Falling Code"

   A live-animated panel that fills the empty space beneath the active job
   card while a rip is in progress: a wide field of falling hex characters in
   the Engram terminal aesthetic. Recreated from the design handoff at
   docs/design_handoff_rip_animation/ (README.md + source/rip-anim.jsx).

   Decorative — every value (head positions, active columns, characters, the
   readout text) is procedural, derived from a wall-clock timer and a hash
   function. No real rip data is plumbed in.

   The parent gates this on `state === 'ripping'` and unmounts it otherwise;
   the 20fps timer's lifetime is therefore naturally bounded.
   ═══════════════════════════════════════════════════════════════════════════ */

const COLS = 72; // evenly-spaced character columns (handoff spec)
const ROWS = 12; // visible hex rows per column
const FPS = 20; // animation tick rate
const MIN_HEIGHT = 168; // floor height; grows to fill the column otherwise
const HEX = "0123456789ABCDEF";

/** Stable deterministic hash so the per-column "fill pattern" doesn't shimmer. */
function hash(n: number): number {
  let x = (n * 374761393) ^ ((n >> 11) * 668265263);
  x = (x ^ (x >> 16)) >>> 0;
  return x / 0xffffffff;
}

/**
 * Elapsed-seconds clock driven by `setInterval`. Returns a frozen 0 when
 * `enabled` is false so callers can render a single static frame.
 */
function useSvTime(fps: number, enabled: boolean): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const start = performance.now();
    const id = setInterval(
      () => setNow((performance.now() - start) / 1000),
      1000 / fps,
    );
    return () => clearInterval(id);
  }, [fps, enabled]);
  return now;
}

/** Tracks the `prefers-reduced-motion` media query reactively. */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handler = () => setReduced(mq.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return reduced;
}

/**
 * Decorative falling-code panel for the ripping state. Render gated on a
 * ripping job; wrap the call site in `<AnimatePresence>` for the exit fade.
 */
export function SvRipAnimation() {
  const reducedMotion = usePrefersReducedMotion();
  const t = useSvTime(FPS, !reducedMotion);

  const shell: CSSProperties = {
    position: "relative",
    flex: 1,
    minHeight: MIN_HEIGHT,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    border: `1px solid ${sv.lineMid}`,
    background:
      "linear-gradient(180deg, rgba(18,24,39,0.62), rgba(10,14,24,0.85))",
    boxShadow: "inset 0 0 32px rgba(94,234,212,0.02)",
  };

  const header: CSSProperties = {
    flexShrink: 0,
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "8px 14px",
    borderBottom: `1px solid ${sv.line}`,
    background: "rgba(5,7,12,0.4)",
  };

  const body: CSSProperties = {
    position: "relative",
    flex: 1,
    overflow: "hidden",
  };

  const field: CSSProperties = {
    position: "absolute",
    inset: 0,
    padding: "12px 10px",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "stretch",
    overflow: "hidden",
  };

  return (
    <motion.div
      style={shell}
      data-testid="sv-rip-animation"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 8 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
    >
      <SvCorners color={sv.lineHi} />

      {/* Header strip */}
      <div style={header}>
        <SvLabel color={sv.cyan}>Bitstream · Falling Code</SvLabel>
        <SvLabel color={sv.inkFaint} noCaret>
          Decode · Live · 0x00000000 → 0xFFFFFFFF
        </SvLabel>
      </div>

      {/* Body — the falling code */}
      <div style={body}>
        <div style={field}>
          {Array.from({ length: COLS }).map((_, c) => (
            <RipColumn key={c} col={c} t={t} />
          ))}
        </div>
      </div>

      {/* Overlay readouts — positioned against the shell root */}
      <div
        style={{
          position: "absolute",
          top: 36,
          left: 14,
          padding: "4px 8px",
          background: "rgba(5,7,12,0.7)",
          border: `1px solid ${sv.line}`,
          fontFamily: sv.mono,
          fontSize: 9,
          letterSpacing: "0.18em",
          color: sv.magentaHi,
          pointerEvents: "none",
        }}
      >
        T00 · 0x4A8F · 23.0 MB/s
      </div>
      <div
        style={{
          position: "absolute",
          bottom: 8,
          right: 14,
          fontFamily: sv.mono,
          fontSize: 9,
          letterSpacing: "0.20em",
          textTransform: "uppercase",
          color: sv.inkFaint,
          pointerEvents: "none",
        }}
      >
        {COLS} Streams · Decoded
      </div>
    </motion.div>
  );
}

/**
 * A single descending column: a bright head row trailed by five fading rows,
 * over a barely-visible background field. Speed and phase are per-column and
 * deterministic so the column positions never shimmer.
 */
function RipColumn({ col, t }: { col: number; t: number }) {
  const colSeed = col * 13.71;
  const speed = 1.2 + hash(col + 99) * 1.6; // 1.2 – 2.8 rows/sec
  const phase = hash(col + 5) * 4; // 0 – 4 sec offset
  const head = ((t * speed + phase) % (ROWS + 4)) - 2; // -2 .. ROWS+2
  const isActive = hash(col + 71) > 0.7; // ~30% magenta columns

  const column: CSSProperties = {
    flexShrink: 0,
    width: `${100 / COLS}%`,
    display: "flex",
    flexDirection: "column",
    fontFamily: sv.mono,
    fontSize: 11,
    lineHeight: "1.05em",
    letterSpacing: "0.04em",
    textAlign: "center",
  };

  return (
    <div style={column}>
      {Array.from({ length: ROWS }).map((_, r) => {
        const d = head - r; // rows below the head
        const onHead = d > -0.4 && d < 0.6;
        const inTrail = d > 0 && d < 5;
        // Deterministic per-cell char — refreshes ~2 Hz in place.
        const ch =
          HEX[
            Math.floor(
              Math.abs(Math.sin(colSeed + r * 1.7 + Math.floor(t * 2))) * 16,
            ) % 16
          ];

        let color: string;
        let weight = 400;
        let glow = "none";
        if (onHead) {
          color = isActive ? sv.magentaHi : sv.cyanHi;
          weight = 700;
          glow = `0 0 6px ${isActive ? sv.magenta : sv.cyan}`;
        } else if (inTrail) {
          const fade = Math.max(0, 1 - d / 5);
          color = isActive
            ? `rgba(255,61,127,${fade * 0.65})`
            : `rgba(94,234,212,${fade * 0.55})`;
        } else {
          color = "rgba(74,83,105,0.18)";
        }

        return (
          <span key={r} style={{ color, fontWeight: weight, textShadow: glow }}>
            {ch}
          </span>
        );
      })}
    </div>
  );
}
