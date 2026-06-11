import { motion } from "motion/react";
import { Disc3 } from "lucide-react";
import { sv } from "./synapse";
import type { ParkedDisc } from "../../types";

interface ParkedDiscBannerProps {
  discs: ParkedDisc[];
  onFinishSetup: () => void;
}

/**
 * Banner for discs the backend parked behind the first-run setup gate (P12):
 * a disc was inserted before the setup wizard finished, so the pipeline did
 * NOT auto-start into unconfirmed paths. Completing setup releases the disc
 * automatically (no eject/reinsert), the backend broadcasts an empty parked
 * list, and this banner disappears — so there is no dismiss button.
 */
export function ParkedDiscBanner({ discs, onFinishSetup }: ParkedDiscBannerProps) {
  if (discs.length === 0) return null;

  const labels = discs
    .map((d) => d.volume_label)
    .filter(Boolean)
    .join(", ");

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className="max-w-[1600px] mx-auto px-4 sm:px-6 mt-4"
      data-testid="parked-disc-banner"
    >
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: 12,
          padding: "12px 16px",
          background: `${sv.cyan}10`,
          border: `1px solid ${sv.cyan}55`,
          boxShadow: `0 0 12px ${sv.cyan}22`,
        }}
      >
        <Disc3 size={18} color={sv.cyan} style={{ flexShrink: 0, marginTop: 1 }} />
        <div
          style={{
            flex: 1,
            fontFamily: sv.mono,
            fontSize: 12,
            letterSpacing: "0.06em",
            color: sv.cyanHi,
            lineHeight: 1.45,
          }}
        >
          <span>
            Disc detected{labels ? ` (${labels})` : ""} — finish setup to start ripping.{" "}
          </span>
          <button
            onClick={onFinishSetup}
            style={{
              fontFamily: "inherit",
              fontSize: "inherit",
              color: sv.cyan,
              textDecoration: "underline",
              textUnderlineOffset: 2,
              background: "none",
              border: 0,
              padding: 0,
              cursor: "pointer",
            }}
          >
            Finish setup
          </button>
        </div>
      </div>
    </motion.div>
  );
}
