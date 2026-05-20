import type { CSSProperties, ReactNode } from "react";
import { sv } from "./tokens";
import { MarkAnimated } from "./MarkAnimated";
import { Wordmark } from "./Wordmark";

interface Props {
  /** Override the "INITIALIZING..." label (e.g. "RECONNECTING..."). */
  label?: string;
  /** Bottom-left caption. Defaults to "ENGRAM · MEDIA ARCHIVE". */
  captionLeft?: ReactNode;
  /** Bottom-right caption — usually `v{version} · BUILD {date}`. */
  captionRight?: ReactNode;
  /** Render the atmospheric background. Defaults to true. */
  atmosphere?: boolean;
}

/**
 * Full-viewport splash — atmospheric background, animated mark, wordmark,
 * and a blinking "INITIALIZING..." label. Used during boot and during
 * WebSocket reconnect (label changes to "RECONNECTING...").
 *
 * Per the handoff: do not over-decorate. The atmosphere stack is enough;
 * resist adding more chrome.
 */
export function Splash({
  label = "INITIALIZING",
  captionLeft = "ENGRAM · MEDIA ARCHIVE",
  captionRight,
  atmosphere = true,
}: Props) {
  const root: CSSProperties = {
    position: "fixed",
    inset: 0,
    zIndex: 100,
    background: atmosphere ? sv.bg0 : "transparent",
    overflow: "hidden",
  };

  return (
    <div data-testid="sv-splash" style={root}>
      {atmosphere && <AtmosphereLayers />}

      <div
        style={{
          position: "relative",
          zIndex: 2,
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 28,
        }}
      >
        <MarkAnimated size={180} />
        <Wordmark size={56} letterSpacing="0.22em" />
        <div
          style={{
            fontFamily: sv.mono,
            fontSize: 11,
            color: sv.cyan,
            letterSpacing: "0.32em",
            marginTop: 4,
            textTransform: "uppercase",
          }}
          data-testid="sv-splash-label"
        >
          {label}
          <span style={{ animation: "engBlink 1s infinite" }}>...</span>
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          bottom: 30,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "space-between",
          padding: "0 40px",
          fontFamily: sv.mono,
          fontSize: 10,
          color: sv.inkFaint,
          letterSpacing: "0.18em",
          zIndex: 2,
        }}
      >
        <span>{captionLeft}</span>
        <span>{captionRight}</span>
      </div>
    </div>
  );
}

/**
 * Atmosphere stack used inside the splash. Borrows the same gradients +
 * scanlines + vignette as SvAtmosphere but rendered inline so the splash
 * can be standalone (used before App.tsx mounts the global atmosphere).
 */
function AtmosphereLayers() {
  return (
    <>
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          backgroundImage:
            "radial-gradient(ellipse at 15% 20%, rgba(94,234,212,0.10), transparent 55%)," +
            "radial-gradient(ellipse at 85% 85%, rgba(255,61,127,0.07), transparent 50%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          zIndex: 3,
          opacity: 0.3,
          backgroundImage:
            "repeating-linear-gradient(0deg, rgba(94,234,212,0.05) 0 1px, transparent 1px 3px)",
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          zIndex: 5,
          background:
            "radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.5) 100%)",
        }}
      />
    </>
  );
}
