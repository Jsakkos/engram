import { useState, useEffect, useRef, KeyboardEvent } from 'react';
import { motion } from 'motion/react';
import { IcoTv, IcoError } from '../app/components/icons';
import type { Job } from '../types';
import { SvPanel, SvLabel, sv } from '../app/components/synapse';
import { EPISODE_CONFIG } from '../config/constants';

interface SeasonPromptModalProps {
    job: Job;
    /** Called with the picked season, or undefined for "match across all seasons". */
    onSubmit: (season?: number) => void;
    /** Close the prompt without touching the job (Escape / backdrop click). */
    onDismiss: () => void;
    /** Explicitly cancel the underlying job — destructive, button only. */
    onCancelJob: () => void;
}

/**
 * Insert-time season prompt (#370): a disc labeled by disc number only
 * ("Eureka D3") identifies the show but not the season. Asking up front stems
 * the downstream mess — N-season subtitle downloads, cross-season ASR, and a
 * review dropdown locked to S01. "All seasons" is the automation escape hatch.
 */
export default function SeasonPromptModal({
    job,
    onSubmit,
    onDismiss,
    onCancelJob,
}: SeasonPromptModalProps) {
    const [season, setSeason] = useState<string>('1');
    const [seasonCount, setSeasonCount] = useState<number | null>(null);
    const selectRef = useRef<HTMLSelectElement>(null);

    // Focus the season select on mount so Escape (and other keyboard shortcuts)
    // are handled immediately without the user needing to click inside first.
    useEffect(() => {
        selectRef.current?.focus();
    }, []);

    // season_count comes from the roster endpoint, which reports it whenever
    // the job's season is unknown — exactly this modal's trigger state.
    useEffect(() => {
        let cancelled = false;
        fetch(`/api/jobs/${job.id}/season-roster`)
            .then((r) => (r.ok ? r.json() : null))
            .then((data) => {
                if (!cancelled && data && typeof data.season_count === 'number') {
                    setSeasonCount(data.season_count);
                }
            })
            .catch(() => {
                /* fall back to the generic option range */
            });
        return () => {
            cancelled = true;
        };
    }, [job.id]);

    const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === 'Enter') onSubmit(parseInt(season, 10) || 1);
        if (e.key === 'Escape') onDismiss();
    };

    const buttonStyle = (color: string, filled: boolean): React.CSSProperties => ({
        flex: 1,
        padding: '10px 16px',
        fontFamily: sv.mono,
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: '0.18em',
        textTransform: 'uppercase',
        color,
        border: `1px solid ${color}${filled ? '' : '80'}`,
        background: filled ? `${color}1f` : 'transparent',
        boxShadow: filled ? `0 0 16px ${color}4d, inset 0 0 8px ${color}0d` : `0 0 8px ${color}26`,
        cursor: 'pointer',
    });

    return (
        <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onKeyDown={handleKeyDown}
            role="dialog"
            aria-modal="true"
            aria-labelledby="season-prompt-title"
            aria-describedby="season-prompt-description"
        >
            <motion.div
                className="absolute inset-0"
                style={{ background: `${sv.bg0}d9`, backdropFilter: 'blur(4px)' }}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                onClick={onDismiss}
                data-testid="season-prompt-backdrop"
            />
            <motion.div
                className="relative w-full max-w-md"
                initial={{ opacity: 0, scale: 0.92, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.92, y: 20 }}
                transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            >
                <SvPanel
                    glow
                    pad={0}
                    style={{
                        background: `linear-gradient(180deg, ${sv.bg2}, ${sv.bg1})`,
                        boxShadow: `0 0 40px ${sv.cyan}33, 0 0 80px ${sv.cyan}11, inset 0 0 30px ${sv.cyan}0d`,
                    }}
                >
                    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
                        {/* Header */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <IcoTv
                                size={22}
                                color={sv.cyan}
                                style={{ filter: `drop-shadow(0 0 6px ${sv.cyan}cc)` }}
                            />
                            <h2
                                id="season-prompt-title"
                                style={{
                                    fontFamily: sv.display,
                                    fontWeight: 700,
                                    fontSize: 18,
                                    letterSpacing: '0.2em',
                                    textTransform: 'uppercase',
                                    color: sv.cyanHi,
                                    textShadow: `0 0 10px ${sv.cyan}99`,
                                    margin: 0,
                                }}
                            >
                                Select Season
                            </h2>
                        </div>

                        {/* Notice */}
                        <div
                            style={{
                                display: 'flex',
                                gap: 12,
                                alignItems: 'flex-start',
                                padding: 12,
                                border: `1px solid ${sv.yellow}4d`,
                                background: `${sv.yellow}0d`,
                            }}
                        >
                            <IcoError size={16} color={sv.yellow} style={{ marginTop: 2, flexShrink: 0 }} />
                            <p
                                id="season-prompt-description"
                                style={{
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    color: `${sv.yellow}cc`,
                                    textTransform: 'uppercase',
                                    letterSpacing: '0.14em',
                                    margin: 0,
                                    lineHeight: 1.6,
                                }}
                            >
                                Identified as &ldquo;{job.detected_title}&rdquo; but the disc label (
                                {job.volume_label || 'NO_LABEL'}) does not reveal the season.
                            </p>
                        </div>

                        {/* Season select */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            <SvLabel size={10}>Season</SvLabel>
                            <select
                                ref={selectRef}
                                value={season}
                                onChange={(e) => setSeason(e.target.value)}
                                aria-label="Season"
                                style={{
                                    width: 220,
                                    background: sv.bg0,
                                    border: `1px solid ${sv.lineMid}`,
                                    color: sv.cyanHi,
                                    fontFamily: sv.mono,
                                    fontSize: 13,
                                    padding: '10px 12px',
                                    outline: 'none',
                                    cursor: 'pointer',
                                }}
                            >
                                {Array.from(
                                    { length: seasonCount ?? EPISODE_CONFIG.FALLBACK_SEASON_COUNT },
                                    (_, i) => i + 1,
                                ).map((s) => (
                                    <option key={s} value={s}>
                                        {`Season ${String(s).padStart(2, '0')}`}
                                    </option>
                                ))}
                            </select>
                        </div>

                        <div style={{ height: 1, background: sv.line }} />

                        {/* Actions */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <motion.button
                                type="button"
                                onClick={onCancelJob}
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.97 }}
                                style={buttonStyle(sv.red, false)}
                            >
                                Cancel job
                            </motion.button>
                            <motion.button
                                type="button"
                                onClick={() => onSubmit(undefined)}
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.97 }}
                                title="Slower: matches every season's references"
                                style={buttonStyle(sv.magenta, false)}
                            >
                                All Seasons
                            </motion.button>
                            <motion.button
                                type="button"
                                onClick={() => onSubmit(parseInt(season, 10) || 1)}
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.97 }}
                                style={buttonStyle(sv.cyan, true)}
                            >
                                Continue &rarr;
                            </motion.button>
                        </div>
                    </div>
                </SvPanel>
            </motion.div>
        </motion.div>
    );
}
