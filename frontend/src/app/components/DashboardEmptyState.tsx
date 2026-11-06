import { sv } from "./synapse";

export type EmptyFilter = "all" | "active" | "completed";

/** Empty-state copy keyed by the active dashboard filter. */
const emptyHeading: Record<EmptyFilter, string> = {
  active: "› No active operations",
  completed: "› No completed archives",
  all: "› No discs detected",
};

const emptyBody: Record<EmptyFilter, string> = {
  active: "All operations complete. Insert a disc to start a new job.",
  completed: "No archived media yet. Completed jobs will appear here.",
  all: "Insert a disc into your optical drive to begin archiving.",
};

const FIRST_RUN_HEADING = "› Welcome to Engram";
const FIRST_RUN_BODY = "Insert a disc, or import rips and disc backups you already have.";
const GETTING_STARTED_URL = "https://jsakkos.github.io/engram/getting-started/installation/";

interface Props {
  filter: EmptyFilter;
  /** True when the install has never run a job (not merely an empty filter). */
  firstRun: boolean;
  onImport: () => void;
}

/**
 * Heading + copy (+ first-run actions) shown under the dashboard radar when
 * the job list is empty. The first-run variant welcomes a new user and points
 * at the other way in (Import); once any job exists the filter copy returns.
 */
export function DashboardEmptyState({ filter, firstRun, onImport }: Props) {
  const actionStyle: React.CSSProperties = {
    fontFamily: sv.mono,
    fontSize: 11,
    letterSpacing: "0.20em",
    textTransform: "uppercase",
    cursor: "pointer",
  };
  return (
    <>
      <h2
        data-testid="sv-empty-heading"
        style={{
          fontFamily: sv.display,
          fontWeight: 700,
          fontSize: 22,
          letterSpacing: "0.2em",
          textTransform: "uppercase",
          color: sv.cyanHi,
          textShadow: `0 0 12px ${sv.cyan}99`,
          marginBottom: 10,
        }}
      >
        {firstRun ? FIRST_RUN_HEADING : emptyHeading[filter]}
      </h2>
      <p
        data-testid="sv-empty-body"
        style={{
          fontFamily: sv.mono,
          fontSize: 11,
          letterSpacing: "0.18em",
          textTransform: "uppercase",
          color: sv.inkDim,
          maxWidth: 480,
          lineHeight: 1.6,
        }}
      >
        {firstRun ? FIRST_RUN_BODY : emptyBody[filter]}
      </p>
      {firstRun && (
        <div style={{ display: "flex", gap: 20, alignItems: "center", marginTop: 20 }}>
          <button
            type="button"
            onClick={onImport}
            data-testid="sv-empty-import"
            style={{
              ...actionStyle,
              padding: "8px 18px",
              color: sv.cyanHi,
              background: "transparent",
              border: `1px solid ${sv.cyan}`,
            }}
          >
            Import
          </button>
          <a
            href={GETTING_STARTED_URL}
            target="_blank"
            rel="noopener noreferrer"
            style={{ ...actionStyle, color: sv.inkDim, textDecoration: "underline" }}
          >
            Getting started
          </a>
        </div>
      )}
    </>
  );
}
