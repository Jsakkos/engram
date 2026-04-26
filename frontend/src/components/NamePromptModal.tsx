import { useState, useRef, useEffect, KeyboardEvent } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Disc3, Film, Tv, AlertTriangle } from 'lucide-react';
import type { Job } from '../types';
import { SvPanel, SvLabel, sv } from '../app/components/synapse';

interface NamePromptModalProps {
    job: Job;
    onSubmit: (name: string, contentType: 'tv' | 'movie', season?: number) => void;
    onCancel: () => void;
}

export default function NamePromptModal({ job, onSubmit, onCancel }: NamePromptModalProps) {
    const [title, setTitle] = useState('');
    const [contentType, setContentType] = useState<'movie' | 'tv'>('movie');
    const [season, setSeason] = useState<string>('1');
    const titleInputRef = useRef<HTMLInputElement>(null);

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
        if (e.key === 'Escape') onCancel();
    };

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
                onClick={onCancel}
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
                                <Disc3
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
                            <AlertTriangle
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
                                        color: `${sv.yellow}cc`,
                                        textTransform: 'uppercase',
                                        letterSpacing: '0.14em',
                                        margin: 0,
                                    }}
                                >
                                    Disc label cannot be read automatically
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
                                style={{
                                    width: '100%',
                                    background: sv.bg1,
                                    border: `1px solid ${title ? sv.lineHi : sv.lineMid}`,
                                    color: sv.cyanHi,
                                    fontFamily: sv.mono,
                                    fontSize: 13,
                                    padding: '10px 12px',
                                    outline: 'none',
                                    boxShadow: title ? `0 0 12px ${sv.cyan}33, inset 0 0 8px ${sv.cyan}0d` : 'none',
                                    transition: 'border-color 0.18s',
                                }}
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
                                        { value: 'movie', label: 'Movie', Icon: Film },
                                        { value: 'tv', label: 'TV Show', Icon: Tv },
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
                                                color: active ? sv.magentaHi : sv.inkDim,
                                                border: `1px solid ${active ? sv.magenta : sv.lineMid}`,
                                                background: active ? `${sv.magenta}14` : 'transparent',
                                                boxShadow: active
                                                    ? `0 0 12px ${sv.magenta}4d, inset 0 0 8px ${sv.magenta}0d`
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
                            <motion.button
                                type="button"
                                onClick={onCancel}
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.97 }}
                                style={{
                                    flex: 1,
                                    padding: '10px 16px',
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    letterSpacing: '0.18em',
                                    textTransform: 'uppercase',
                                    color: sv.red,
                                    border: `1px solid ${sv.red}80`,
                                    background: 'transparent',
                                    boxShadow: `0 0 8px ${sv.red}26`,
                                    cursor: 'pointer',
                                }}
                            >
                                Cancel
                            </motion.button>

                            <motion.button
                                type="button"
                                onClick={handleSubmit}
                                disabled={!title.trim()}
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
                                Start Ripping →
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
                            Drive {job.drive_id} · Awaiting Input
                        </span>
                    </div>
                </SvPanel>
            </motion.div>
        </motion.div>
    );
}
