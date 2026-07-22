import { motion } from "motion/react";
import { IcoDisc, IcoCancel } from "./icons";
import { sv } from "./synapse";

/** User-asserted identity locked to a drive, awaiting the next disc insert. */
export interface ArmedIdentity {
  title: string;
  content_type: string;
  season: number | null;
  tmdb_id: number | null;
  disc_number: number | null;
}

interface ArmedDriveCardProps {
  driveId: string;
  identity: ArmedIdentity;
  onDisarm: (driveId: string) => void;
}

/**
 * Placeholder card for a drive armed with a manual identity (see
 * ArmDiscModal) but not yet holding a disc. Deliberately reads as "not a
 * real job yet": dashed border, no glow, subdued title — distinct from the
 * DiscCard treatment used once a disc actually starts ripping.
 */
export default function ArmedDriveCard({ driveId, identity, onDisarm }: ArmedDriveCardProps) {
  const isTv = identity.content_type === "tv";
  const metaParts = [isTv ? "TV SERIES" : "MOVIE"];
  if (isTv && identity.season != null) metaParts.push(`SEASON ${identity.season}`);
  if (identity.tmdb_id != null) metaParts.push(`TMDB ${identity.tmdb_id}`);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      data-testid="armed-drive-card"
    >
      <div
        style={{
          position: "relative",
          border: `1px dashed ${sv.lineMid}`,
          background: "transparent",
          padding: 20,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <IcoDisc size={18} color={sv.inkFaint} />
            <div>
              <div
                style={{
                  fontFamily: sv.display,
                  fontSize: 15,
                  fontWeight: 700,
                  letterSpacing: "0.04em",
                  color: sv.inkDim,
                }}
              >
                {identity.title}
              </div>
              <div
                style={{
                  fontFamily: sv.mono,
                  fontSize: 10,
                  letterSpacing: "0.16em",
                  textTransform: "uppercase",
                  color: sv.inkFaint,
                  marginTop: 2,
                }}
              >
                {metaParts.join(" · ")}
              </div>
            </div>
          </div>

          <span
            style={{
              fontFamily: sv.mono,
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              color: sv.magentaDim,
              border: `1px solid ${sv.magenta}4d`,
              padding: "3px 8px",
              whiteSpace: "nowrap",
            }}
          >
            Awaiting Disc
          </span>
        </div>

        <div
          style={{
            fontFamily: sv.mono,
            fontSize: 10,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            color: sv.inkFaint,
          }}
        >
          Drive {driveId} · Identity Locked By User
        </div>

        <p
          style={{
            fontFamily: sv.mono,
            fontSize: 11,
            color: sv.inkFaint,
            margin: 0,
            lineHeight: 1.5,
          }}
        >
          Insert a disc here. It will scan, adopt this identity, and rip without stopping to ask.
        </p>

        <div>
          <button
            type="button"
            onClick={() => onDisarm(driveId)}
            aria-label={`Disarm drive ${driveId}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              background: "transparent",
              border: `1px solid ${sv.lineMid}`,
              color: sv.inkDim,
              fontFamily: sv.mono,
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              padding: "6px 10px",
              cursor: "pointer",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = sv.ink;
              e.currentTarget.style.borderColor = sv.lineHi;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = sv.inkDim;
              e.currentTarget.style.borderColor = sv.lineMid;
            }}
          >
            <IcoCancel size={12} />
            Disarm
          </button>
        </div>
      </div>
    </motion.div>
  );
}
