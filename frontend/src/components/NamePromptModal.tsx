import { useState, useRef, useEffect, KeyboardEvent } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { IcoDisc, IcoMovie, IcoTv, IcoError } from '../app/components/icons';
import type { Job } from '../types';
import { SvPanel, SvLabel, sv } from '../app/components/synapse';
import { parseIdentityPrompt } from '../app/promptSelection';

interface NamePromptModalProps {
    job: Job;
    onSubmit: (name: string, contentType: 'tv' | 'movie', season?: number) => void;
    /** Close the prompt without touching the job (Escape / backdrop click). */
    onDismiss: () => void;
    /** Explicitly cancel the underlying job — destructive, button only. */
    onCancelJob: () => void;
    initialTitle?: string;
}

export default function NamePromptModal({
    job,
    onSubmit,
    onDismiss,
    onCancelJob,
    initialTitle,
}: NamePromptModalProps) {
    const [title, setTitle] = useState(initialTitle ?? '');
    const [contentType, setContentType] = useState<'movie' | 'tv'>(
        job.content_type === 'tv' ? 'tv' : 'movie',
    );
    const [season, setSeason] = useState<string>(String(job.detected_season ?? 1));
    const titleInputRef = useRef<HTMLInputElement>(null);

    // The walk-away rip starts BEFORE this modal can open (Gate B/C rip-first),
    // so the modal never gates the rip — it records the title. `isRipping`
    // distinguishes a still-running rip (CTA-opened) from a job already parked
    // for review at rip-end, so the copy never claims the rip is blocked.
    const isRipping = job.state === 'ripping';
    // Prefer the live identity-prompt reason, then the parked review_reason; only
    // fall back to the generic unreadable-label line when neither is present
    // (e.g. a genuinely blank/garbled label). This stops the modal from telling
    // the user the label "cannot be read" when it read fine and TMDB just missed.
    const identifyReason = parseIdentityPrompt(job)?.reason || job.review_reason || '';

    useEffect(() => {
        titleInputRef.current?.focus();
    }, []);

    const handleSubmit = () => {
        if (!title.trim()) return;
        onSubmit(
            title.trim(),
            contentType,
            contentType === 'tv' ? (parseInt(season, 10) || 1) : undefined,
        );
    };

    const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === 'Enter') handleSubmit();
        if (e.key === 'Escape') onDismiss();
    };

    const inputStyle = (filled: boolean): React.CSSProperties => ({
        width: '100%',
        background: sv.bg1,
        border: `1px solid ${filled ? sv.lineHi : sv.lineMid}`,
        color: sv.cyanHi,
        fontFamily: sv.mono,
        fontSize: 13,
        padding: '10px 12px',
        outline: 'none',
        boxShadow: filled ? `0 0 12px ${sv.cyan}33, inset 0 0 8px ${sv.cyan}0d` : 'none',
        transition: 'border-color 0.18s',
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
            aria-labelledby="name-prompt-title"
            aria-describedby="name-prompt-description"
        >
            {/* Backdrop with sv-tinted scanline overlay */}
            <motion.div
                className="absolute inset-0"
                style={{ background: `${sv.bg0}d9`, backdropFilter: 'blur(4px)' }}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                onClick={onDismiss}
                data-testid="name-prompt-backdrop"
            />
            <div
                className="absolute inset-0 pointer-events-none"
                style={{
                    backgroundImage: `repeating-linear-gradient(0deg, transparent, transparent 2px, ${sv.cyan} 2px, ${sv.cyan} 4px)`,
                    opacity: 0.03,
                }}
            />

            {/* Card */}
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
                            <motion.div
                                animate={{ rotate: [0, 360] }}
                                transition={{ duration: 8, repeat: Infinity, ease: 'linear' }}
                            >
                                <IcoDisc
                                    size={22}
                                    color={sv.cyan}
                                    style={{ filter: `drop-shadow(0 0 6px ${sv.cyan}cc)` }}
                                />
                            </motion.div>
                            <div style={{ flex: 1 }}>
                                <h2
                                    id="name-prompt-title"
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
                                    Identify Disc
                                </h2>
                                <motion.div
                                    style={{
                                        height: 1,
                                        marginTop: 4,
                                        background: `linear-gradient(90deg, ${sv.cyan}cc, transparent)`,
                                    }}
                                    initial={{ scaleX: 0, originX: 0 }}
                                    animate={{ scaleX: 1 }}
                                    transition={{ delay: 0.2, duration: 0.4 }}
                                />
                            </div>
                        </div>

                        {/* Warning notice */}
                        <div
                            style={{
                                display: 'flex',
                                gap: 12,
                                alignItems: 'flex-start',
                                padding: 12,
                                border: `1px solid ${sv.yellow}4d`,
                                background: `${sv.yellow}0d`,
                                boxShadow: `inset 0 0 10px ${sv.yellow}0d`,
                            }}
                        >
                            <IcoError
                                size={16}
                                color={sv.yellow}
                                style={{ marginTop: 2, flexShrink: 0, filter: `drop-shadow(0 0 4px ${sv.yellow}99)` }}
                            />
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
                                <p
                                    id="name-prompt-description"
                                    style={{
                                        fontFamily: sv.mono,
                                        fontSize: 11,
                                        lineHeight: 1.5,
                                        color: `${sv.yellow}cc`,
                                        // A specific reason is a full sentence, so it reads as
                                        // sentence text; the generic fallback keeps the terse
                                        // uppercase telemetry look of the brand warning labels.
                                        textTransform: identifyReason ? 'none' : 'uppercase',
                                        letterSpacing: identifyReason ? '0.02em' : '0.14em',
                                        margin: 0,
                                    }}
                                >
                                    {identifyReason || 'Disc label cannot be read automatically'}
                                </p>
                                <code
                                    title={job.volume_label}
                                    style={{
                                        display: 'block',
                                        fontFamily: sv.mono,
                                        fontSize: 11,
                                        color: `${sv.yellow}b3`,
                                        background: `${sv.yellow}1a`,
                                        border: `1px solid ${sv.yellow}33`,
                                        padding: '4px 8px',
                                        whiteSpace: 'nowrap',
                                        overflow: 'hidden',
                                        textOverflow: 'ellipsis',
                                    }}
                                >
                                    {job.volume_label || 'NO_LABEL'}
                                </code>
                            </div>
                        </div>

                        <div style={{ height: 1, background: sv.line }} />

                        {/* Disc Title Input */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            <SvLabel size={10}>Disc Title</SvLabel>
                            <input
                                ref={titleInputRef}
                                type="text"
                                value={title}
                                onChange={(e) => setTitle(e.target.value)}
                                placeholder="e.g. The Italian Job"
                                aria-required="true"
                                style={inputStyle(!!title)}
                                onFocus={(e) => (e.currentTarget.style.borderColor = sv.cyan)}
                                onBlur={(e) =>
                                    (e.currentTarget.style.borderColor = title ? sv.lineHi : sv.lineMid)
                                }
                            />
                        </div>

                        {/* Media Type Toggle */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            <SvLabel size={10}>Media Type</SvLabel>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                                {(
                                    [
                                        { value: 'movie', label: 'Movie', Icon: IcoMovie },
                                        { value: 'tv', label: 'TV Show', Icon: IcoTv },
                                    ] as const
                                ).map(({ value, label, Icon }) => {
                                    const active = contentType === value;
                                    return (
                                        <motion.button
                                            key={value}
                                            type="button"
                                            onClick={() => setContentType(value)}
                                            whileHover={{ scale: 1.02 }}
                                            whileTap={{ scale: 0.98 }}
                                            style={{
                                                display: 'flex',
                                                alignItems: 'center',
                                                justifyContent: 'center',
                                                gap: 8,
                                                padding: '10px 14px',
                                                fontFamily: sv.mono,
                                                fontSize: 11,
                                                fontWeight: 700,
                                                letterSpacing: '0.18em',
                                                textTransform: 'uppercase',
                                                color: active ? sv.cyanHi : sv.inkDim,
                                                border: `1px solid ${active ? sv.cyan : sv.lineMid}`,
                                                background: active ? `${sv.cyan}14` : 'transparent',
                                                boxShadow: active
                                                    ? `0 0 12px ${sv.cyan}4d, inset 0 0 8px ${sv.cyan}0d`
                                                    : 'none',
                                                cursor: 'pointer',
                                                transition: 'all 0.18s',
                                            }}
                                        >
                                            <Icon size={14} />
                                            {label}
                                        </motion.button>
                                    );
                                })}
                            </div>
                        </div>

                        {/* Season — TV only */}
                        <AnimatePresence>
                            {contentType === 'tv' && (
                                <motion.div
                                    initial={{ opacity: 0, height: 0 }}
                                    animate={{ opacity: 1, height: 'auto' }}
                                    exit={{ opacity: 0, height: 0 }}
                                    transition={{ type: 'spring', stiffness: 400, damping: 35 }}
                                    style={{ overflow: 'hidden' }}
                                >
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 4 }}>
                                        <SvLabel size={10}>Season</SvLabel>
                                        <input
                                            type="number"
                                            min={1}
                                            max={99}
                                            value={season}
                                            onChange={(e) => setSeason(e.target.value)}
                                            style={{
                                                width: 128,
                                                background: sv.bg0,
                                                border: `1px solid ${sv.lineMid}`,
                                                color: sv.cyanHi,
                                                fontFamily: sv.mono,
                                                fontSize: 13,
                                                padding: '10px 12px',
                                                outline: 'none',
                                                boxShadow: `0 0 8px ${sv.cyan}1a`,
                                            }}
                                            onFocus={(e) => (e.currentTarget.style.borderColor = sv.cyan)}
                                            onBlur={(e) => (e.currentTarget.style.borderColor = sv.lineMid)}
                                        />
                                    </div>
                                </motion.div>
                            )}
                        </AnimatePresence>

                        <div style={{ height: 1, background: sv.line }} />

                        {/* Action Buttons */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                            {/* Neutral, recessive cancel — the primary CTA carries the
                                visual weight. Job cancellation is destructive, but that's
                                an explicit/confirmed action, not the loud default here. */}
                            <motion.button
                                type="button"
                                onClick={onCancelJob}
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.97 }}
                                onMouseEnter={(e) => {
                                    e.currentTarget.style.color = sv.ink;
                                    e.currentTarget.style.borderColor = sv.lineHi;
                                }}
                                onMouseLeave={(e) => {
                                    e.currentTarget.style.color = sv.inkDim;
                                    e.currentTarget.style.borderColor = sv.lineMid;
                                }}
                                style={{
                                    flex: 1,
                                    padding: '10px 16px',
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    letterSpacing: '0.18em',
                                    textTransform: 'uppercase',
                                    color: sv.inkDim,
                                    border: `1px solid ${sv.lineMid}`,
                                    background: 'transparent',
                                    boxShadow: 'none',
                                    cursor: 'pointer',
                                }}
                            >
                                Cancel job
                            </motion.button>

                            <motion.button
                                type="button"
                                onClick={handleSubmit}
                                disabled={!title.trim()}
                                data-testid="name-prompt-submit"
                                whileHover={title.trim() ? { scale: 1.02 } : {}}
                                whileTap={title.trim() ? { scale: 0.97 } : {}}
                                style={{
                                    flex: 1,
                                    padding: '10px 16px',
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    letterSpacing: '0.18em',
                                    textTransform: 'uppercase',
                                    color: title.trim() ? sv.cyan : `${sv.cyan}4d`,
                                    border: `1px solid ${title.trim() ? sv.cyan : `${sv.cyan}33`}`,
                                    background: title.trim() ? `${sv.cyan}1f` : 'transparent',
                                    boxShadow: title.trim()
                                        ? `0 0 16px ${sv.cyan}4d, inset 0 0 8px ${sv.cyan}0d`
                                        : 'none',
                                    cursor: title.trim() ? 'pointer' : 'not-allowed',
                                    opacity: title.trim() ? 1 : 0.3,
                                }}
                            >
                                Save title
                            </motion.button>
                        </div>
                    </div>

                    {/* Bottom status bar */}
                    <div
                        style={{
                            borderTop: `1px solid ${sv.line}`,
                            padding: '8px 24px',
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                        }}
                    >
                        <motion.div
                            animate={{ opacity: [0.3, 1, 0.3] }}
                            transition={{ duration: 1.5, repeat: Infinity }}
                            style={{
                                width: 6,
                                height: 6,
                                borderRadius: '50%',
                                background: sv.cyan,
                                filter: `drop-shadow(0 0 3px ${sv.cyan}cc)`,
                            }}
                        />
                        <span
                            style={{
                                fontFamily: sv.mono,
                                fontSize: 10,
                                letterSpacing: '0.22em',
                                textTransform: 'uppercase',
                                color: sv.inkFaint,
                            }}
                        >
                            Drive {job.drive_id} · {isRipping ? 'Ripping' : 'Awaiting Input'}
                        </span>
                    </div>
                </SvPanel>
            </motion.div>
        </motion.div>
    );
}
