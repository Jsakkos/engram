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
}

export function DiscMetadata({ title, subtitle, discLabel }: DiscMetadataProps) {
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
            {discLabel && (
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
        </div>
    );
}
