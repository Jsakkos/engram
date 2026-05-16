import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { sv } from "./tokens";

/* ═══════════════════════════════════════════════════════════════════════════
   SvRipAnimation — "C3 · Bitstream · Falling Code" (ambient layer)

   A decorative full-viewport background layer of falling hex characters in
   the Engram terminal aesthetic, shown behind all content while a rip is in
   progress. Recreated from the design handoff at
   docs/design_handoff_rip_animation/ and adapted from a bordered card into a
   fixed ambient layer that fills the dashboard's negative space.

   Rendered to a <canvas> rather than DOM nodes: a viewport-sized field is
   ~8k cells, far too many to re-render as React spans at 20fps. The canvas
   redraws the whole field each tick with plain fillText calls.

   Fully procedural — head positions, active columns, and characters are
   derived from a wall-clock timer and a hash function. No rip data is
   plumbed in.
   ═══════════════════════════════════════════════════════════════════════════ */

const FPS = 20; // redraw rate
const COL_PX = 20; // column spacing
const ROW_PX = 22; // row spacing
const FONT_PX = 14;
const HEX = "0123456789ABCDEF";
const LAYER_OPACITY = 0.5; // ambient — quiet enough not to fight content

/** Stable deterministic hash so the per-column "fill pattern" doesn't shimmer. */
function hash(n: number): number {
  let x = (n * 374761393) ^ ((n >> 11) * 668265263);
  x = (x ^ (x >> 16)) >>> 0;
  return x / 0xffffffff;
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
 * Draw one frame of the falling-code field. `t` is elapsed seconds; columns
 * descend at per-column deterministic speeds with a bright head, a five-row
 * fading trail, and a barely-visible background field.
 */
function drawField(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
) {
  ctx.clearRect(0, 0, w, h);
  ctx.font = `${FONT_PX}px ${sv.mono}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  const cols = Math.ceil(w / COL_PX);
  const rows = Math.ceil(h / ROW_PX) + 1;
  const tick = Math.floor(t * 2); // chars refresh ~2 Hz

  for (let c = 0; c < cols; c++) {
    const colSeed = c * 13.71;
    const speed = 1.2 + hash(c + 99) * 1.6; // 1.2 – 2.8 rows/sec
    const phase = hash(c + 5) * 4; // 0 – 4 sec offset
    const head = ((t * speed + phase) % (rows + 4)) - 2; // -2 .. rows+2
    const isActive = hash(c + 71) > 0.7; // ~30% magenta columns
    const x = c * COL_PX + COL_PX / 2;

    for (let r = 0; r < rows; r++) {
      const d = head - r; // rows below the head
      const onHead = d > -0.4 && d < 0.6;
      const inTrail = d > 0 && d < 5;

      if (onHead) {
        ctx.fillStyle = isActive ? sv.magentaHi : sv.cyanHi;
        ctx.shadowColor = isActive ? sv.magenta : sv.cyan;
        ctx.shadowBlur = 6;
      } else if (inTrail) {
        const fade = Math.max(0, 1 - d / 5);
        ctx.fillStyle = isActive
          ? `rgba(255,61,127,${fade * 0.65})`
          : `rgba(94,234,212,${fade * 0.55})`;
        ctx.shadowBlur = 0;
      } else {
        ctx.fillStyle = "rgba(74,83,105,0.18)";
        ctx.shadowBlur = 0;
      }

      const ch =
        HEX[
          Math.floor(Math.abs(Math.sin(colSeed + r * 1.7 + tick)) * 16) % 16
        ];
      ctx.fillText(ch, x, r * ROW_PX + ROW_PX / 2);
    }
  }
  ctx.shadowBlur = 0;
}

/**
 * Ambient falling-code background. Render gated on a ripping job; wrap the
 * call site in `<AnimatePresence>` for the fade-out when ripping ends.
 */
export function SvRipAnimation() {
  const reducedMotion = usePrefersReducedMotion();
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let lastDraw = 0;
    const start = performance.now();

    // Size the backing store to the device pixel ratio for crisp glyphs.
    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = window.innerWidth;
      const h = window.innerHeight;
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (reducedMotion) drawField(ctx, w, h, 0);
    };
    resize();
    window.addEventListener("resize", resize);

    if (reducedMotion) {
      // Static single frame — no animation loop.
      return () => window.removeEventListener("resize", resize);
    }

    const frame = (now: number) => {
      if (now - lastDraw >= 1000 / FPS) {
        lastDraw = now;
        drawField(ctx, window.innerWidth, window.innerHeight, (now - start) / 1000);
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, [reducedMotion]);

  return (
    <motion.div
      data-testid="sv-rip-animation"
      aria-hidden="true"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 0,
        pointerEvents: "none",
      }}
      initial={{ opacity: 0 }}
      animate={{ opacity: LAYER_OPACITY }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.6, ease: "easeOut" }}
    >
      <canvas ref={canvasRef} style={{ display: "block" }} />
    </motion.div>
  );
}
