/**
 * Disc metadata — title (display font), subtitle (mono), disc label (caret).
 * Synapse v2 typography: Chakra Petch for the title, JetBrains Mono for meta.
 */

import { motion } from "motion/react";
import { sv } from "../synapse";

interface DiscMetadataProps {
    title: string;
    subtitle?: string;
    discLabel?: string;
    /** Outcome of the optional whole-disc backup, straight from the job row. */
    backupStatus?: string | null;
    /** Backend prose for a skipped or failed backup, rendered verbatim. */
    backupStatusReason?: string | null;
}

export function DiscMetadata({
    title,
    subtitle,
    discLabel,
    backupStatus,
    backupStatusReason,
}: DiscMetadataProps) {
    // Only the two outcomes that silently changed what happened are surfaced:
    // "skipped" and "failed" both mean the disc was ripped from the drive with
    // no copy kept. "pending" has its own BACKING UP phase rendering and
    // "completed" is the happy path, so neither earns a warning line.
    const backupWarning =
        backupStatus === 'skipped' || backupStatus === 'failed'
            ? backupStatusReason || 'the backup did not run; the disc was ripped directly'
            : null;
    // Suppress the standalone disc-label caret line when the subtitle already
    // contains it (e.g. subtitle "TV · FOR_ALL_MANKIND_S1_D1" + discLabel
    // "FOR_ALL_MANKIND_S1_D1" → render only the subtitle).
    const showDiscLabel =
        !!discLabel && !(subtitle && subtitle.toUpperCase().includes(discLabel.toUpperCase()));

    return (
        <div style={{ flex: 1, minWidth: 0 }} data-testid="sv-disc-metadata">
            <h3
                data-testid="sv-job-title"
                style={{
                    fontFamily: sv.display,
                    fontSize: 26,
                    fontWeight: 700,
                    letterSpacing: "0.04em",
                    color: sv.cyanHi,
                    textShadow: `0 0 14px ${sv.cyan}55`,
                    margin: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                }}
            >
                {title}
            </h3>
            {subtitle && (
                <p
                    style={{
                        fontFamily: sv.mono,
                        fontSize: 11,
                        letterSpacing: "0.10em",
                        color: sv.inkDim,
                        margin: "4px 0 0 0",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                    }}
                >
                    {subtitle}
                </p>
            )}
            {showDiscLabel && (
                <motion.p
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        letterSpacing: "0.20em",
                        textTransform: "uppercase",
                        color: sv.magenta,
                        margin: "6px 0 0 0",
                    }}
                >
                    <span style={{ color: sv.cyan }}>›</span> {discLabel}
                </motion.p>
            )}
            {backupWarning && (
                <p
                    data-testid="sv-backup-warning"
                    title={backupWarning}
                    style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        letterSpacing: "0.08em",
                        color: sv.amber,
                        margin: "6px 0 0 0",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                    }}
                >
                    <span aria-hidden>⚠</span> Backup{" "}
                    {backupStatus === "failed" ? "failed" : "skipped"}: {backupWarning}
                </p>
            )}
        </div>
    );
}
