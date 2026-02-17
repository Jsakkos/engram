/**
 * Disc metadata display component (title, subtitle, label)
 */

import { motion } from "motion/react";

interface DiscMetadataProps {
    title: string;
    subtitle?: string;
    discLabel?: string;
}

export function DiscMetadata({ title, subtitle, discLabel }: DiscMetadataProps) {
    return (
        <div className="flex-1 min-w-0">
            <h3
                className="text-xl font-bold text-cyan-400 truncate mb-1 tracking-wide uppercase"
                style={{ textShadow: "0 0 10px rgba(6, 182, 212, 0.5)" }}
            >
                {title}
            </h3>
            {subtitle && (
                <p className="text-sm text-slate-400 truncate font-mono">{subtitle}</p>
            )}
            {discLabel && (
                <motion.p
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    className="text-xs text-magenta-400 font-mono mt-1 tracking-wider"
                >
                    &gt; {discLabel}
                </motion.p>
            )}
        </div>
    );
}
