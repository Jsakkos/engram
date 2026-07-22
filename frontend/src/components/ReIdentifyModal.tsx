import { useState, useMemo, type KeyboardEvent } from 'react';
import { motion } from 'motion/react';
import { IcoDisc, IcoTv, IcoRetry } from '../app/components/icons';
import type { Job } from '../types';
import { SvPanel, SvLabel, sv } from '../app/components/synapse';
import IdentityFields, { type IdentityValue } from './IdentityFields';

/** A same-name TMDB candidate persisted on the job at identify time. */
interface Candidate {
    tmdb_id: number;
    name: string;
    year?: string;
    popularity?: number;
}

interface ReIdentifyModalProps {
    job: Job;
    onSubmit: (title: string, contentType: 'tv' | 'movie', season?: number, tmdbId?: number) => void;
    onCancel: () => void;
}

export default function ReIdentifyModal({ job, onSubmit, onCancel }: ReIdentifyModalProps) {
    const [identity, setIdentity] = useState<IdentityValue>({
        title: job.detected_title || '',
        contentType: job.content_type === 'tv' ? 'tv' : 'movie',
        season: String(job.detected_season || 1),
        tmdbId: undefined,
    });
    // Mirrors IdentityFields' internal search-query state so the Enter-to-submit
    // guard below can still avoid submitting while the user is mid-search (the
    // search box itself is owned by IdentityFields now).
    const [hasSearchQuery, setHasSearchQuery] = useState(false);

    // Same-name twins recorded at identify time (e.g. Frasier 1993 + 2023). When
    // present, they drive a one-click "Did you mean?" picker so the user skips the
    // re-search. The API ships this as a raw JSON string, so parse defensively.
    const candidates = useMemo<Candidate[]>(() => {
        if (!job.candidates_json) return [];
        try {
            const parsed = JSON.parse(job.candidates_json);
            if (!Array.isArray(parsed)) return [];
            return parsed.filter(
                (c): c is Candidate =>
                    !!c && typeof c.tmdb_id === 'number' && typeof c.name === 'string',
            );
        } catch {
            return [];
        }
    }, [job.candidates_json]);

    const candidateLabel = (c: Candidate) =>
        `${c.name}${c.year ? ` (${c.year})` : ''} · #${c.tmdb_id}`;

    const selectCandidate = (c: Candidate) => {
        // Reuse the disc's detected content type (collisions are TV today, but
        // don't hardcode it — a future movie collision must not be forced to TV)
        // and detected season so the user doesn't re-enter them. The `?? 1`
        // mirrors the manual form's `|| 1`: a null season serializes to null,
        // which the backend skips, silently disabling subtitle re-download.
        const type = job.content_type === 'tv' ? 'tv' : 'movie';
        onSubmit(c.name, type, job.detected_season ?? 1, c.tmdb_id);
    };

    const handleSubmit = () => {
        if (!identity.title.trim()) return;
        onSubmit(
            identity.title.trim(),
            identity.contentType,
            identity.contentType === 'tv' ? (parseInt(identity.season, 10) || 1) : undefined,
            identity.tmdbId,
        );
    };

    const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === 'Enter' && !hasSearchQuery) handleSubmit();
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
            aria-labelledby="re-identify-title"
        >
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
                                <IcoRetry
                                    size={22}
                                    color={sv.cyan}
                                    style={{ filter: `drop-shadow(0 0 6px ${sv.cyan}cc)` }}
                                />
                            </motion.div>
                            <div style={{ flex: 1 }}>
                                <h2
                                    id="re-identify-title"
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
                                    Re-Identify Disc
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
                            <IcoDisc
                                size={16}
                                color={sv.yellow}
                                style={{ marginTop: 2, flexShrink: 0, filter: `drop-shadow(0 0 4px ${sv.yellow}99)` }}
                            />
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                                <p
                                    style={{
                                        fontFamily: sv.mono,
                                        fontSize: 11,
                                        color: `${sv.yellow}cc`,
                                        textTransform: 'uppercase',
                                        letterSpacing: '0.14em',
                                        margin: 0,
                                    }}
                                >
                                    Wrong identification? Correct it below.
                                </p>
                                {job.review_reason && (
                                    <p
                                        style={{
                                            fontFamily: sv.mono,
                                            fontSize: 11,
                                            color: `${sv.yellow}99`,
                                            margin: 0,
                                        }}
                                    >
                                        {job.review_reason}
                                    </p>
                                )}
                                {/* What the disc is identified as right now — only when
                                    a TMDB id is committed (an ambiguous disc has none)
                                    and the user hasn't yet picked a replacement, so they
                                    can compare wrong-vs-right before re-identifying. */}
                                {job.tmdb_id != null && identity.tmdbId == null && (
                                    <p
                                        style={{
                                            fontFamily: sv.mono,
                                            fontSize: 11,
                                            color: `${sv.yellow}99`,
                                            margin: 0,
                                        }}
                                    >
                                        Currently: {job.tmdb_name || job.detected_title}
                                        {job.tmdb_year ? ` (${job.tmdb_year})` : ''} · TMDB #{job.tmdb_id}
                                    </p>
                                )}
                            </div>
                        </div>

                        {/* Same-name quick-pick — one click resolves the collision */}
                        {candidates.length >= 2 && (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                                <SvLabel size={10}>Did you mean?</SvLabel>
                                {/* Cap height + scroll so an unexpectedly long candidate
                                    list never pushes the search/action buttons off-screen
                                    (matches the TMDB search-results container below). */}
                                <div
                                    style={{
                                        display: 'flex',
                                        flexDirection: 'column',
                                        gap: 8,
                                        maxHeight: 192,
                                        overflowY: 'auto',
                                    }}
                                >
                                    {candidates.map((cand) => (
                                        // Plain button (not motion.button): the color hover is
                                        // driven imperatively here, so a competing whileHover
                                        // would be a second style owner. Matches the search rows.
                                        <button
                                            key={cand.tmdb_id}
                                            type="button"
                                            onClick={() => selectCandidate(cand)}
                                            style={{
                                                display: 'flex',
                                                alignItems: 'center',
                                                gap: 10,
                                                padding: '10px 12px',
                                                border: `1px solid ${sv.cyan}4d`,
                                                background: `${sv.cyan}0d`,
                                                cursor: 'pointer',
                                                textAlign: 'left',
                                                transition: 'background 0.18s, border-color 0.18s',
                                            }}
                                            onMouseEnter={(e) => {
                                                e.currentTarget.style.background = `${sv.cyan}1f`;
                                                e.currentTarget.style.borderColor = sv.cyan;
                                            }}
                                            onMouseLeave={(e) => {
                                                e.currentTarget.style.background = `${sv.cyan}0d`;
                                                e.currentTarget.style.borderColor = `${sv.cyan}4d`;
                                            }}
                                        >
                                            <IcoTv size={14} color={sv.cyan} style={{ flexShrink: 0 }} />
                                            <span
                                                style={{
                                                    fontFamily: sv.mono,
                                                    fontSize: 13,
                                                    color: sv.cyanHi,
                                                    flex: 1,
                                                    minWidth: 0,
                                                    whiteSpace: 'nowrap',
                                                    overflow: 'hidden',
                                                    textOverflow: 'ellipsis',
                                                }}
                                            >
                                                {candidateLabel(cand)}
                                            </span>
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        <div style={{ height: 1, background: sv.line }} />

                        <IdentityFields
                            value={identity}
                            onChange={setIdentity}
                            onSearchQueryChange={(query) => setHasSearchQuery(!!query)}
                            autoFocus
                        />

                        <div style={{ height: 1, background: sv.line }} />

                        {/* Action Buttons */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                            {/* Neutral, recessive cancel — the primary CTA carries the
                                visual weight. Dismissal here just closes the modal. */}
                            <motion.button
                                type="button"
                                onClick={onCancel}
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
                                Cancel
                            </motion.button>

                            <motion.button
                                type="button"
                                onClick={handleSubmit}
                                disabled={!identity.title.trim()}
                                data-testid="reidentify-submit"
                                whileHover={identity.title.trim() ? { scale: 1.02 } : {}}
                                whileTap={identity.title.trim() ? { scale: 0.97 } : {}}
                                style={{
                                    flex: 1,
                                    padding: '10px 16px',
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    letterSpacing: '0.18em',
                                    textTransform: 'uppercase',
                                    color: identity.title.trim() ? sv.cyan : `${sv.cyan}4d`,
                                    border: `1px solid ${identity.title.trim() ? sv.cyan : `${sv.cyan}33`}`,
                                    background: identity.title.trim() ? `${sv.cyan}1f` : 'transparent',
                                    boxShadow: identity.title.trim()
                                        ? `0 0 16px ${sv.cyan}4d, inset 0 0 8px ${sv.cyan}0d`
                                        : 'none',
                                    cursor: identity.title.trim() ? 'pointer' : 'not-allowed',
                                    opacity: identity.title.trim() ? 1 : 0.3,
                                }}
                            >
                                Re-Identify
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
                            {job.volume_label || 'Unknown'} · Correcting Identification
                        </span>
                    </div>
                </SvPanel>
            </motion.div>
        </motion.div>
    );
}
