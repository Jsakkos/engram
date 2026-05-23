import { useState, useEffect, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { motion } from 'motion/react';
import { Save, Package } from 'lucide-react';
import { IcoDisc, IcoPlay, IcoRetry } from '../app/components/icons';
import type { CSSProperties, FocusEvent, ReactNode } from 'react';
import { Job, DiscTitle } from '../types';
import { formatDuration, formatSize, titleDisplayName } from './ReviewQueue/utils';
import { MATCHING_CONFIG } from '../config/constants';
import { SvActionButton, SvAtmosphere, SvBadge, SvLabel, SvNotice, SvPageHeader, SvPanel, sv } from '../app/components/synapse';
import { useSeasonRoster } from '../hooks/useSeasonRoster';
import { assignmentsByCode, buildCandidates, collidingCodes, computeCoverage, normalizeEpisodeCode, suggestGapCode } from './ReviewQueue/coverage';
import { SeasonRosterStrip } from './ReviewQueue/SeasonRosterStrip';
import { TitleList } from './ReviewQueue/TitleList';
import { Inspector } from './ReviewQueue/Inspector';

/** Uppercase mono caption styling, reused for metadata rows. */
const monoLabelStyle: CSSProperties = {
    fontFamily: sv.mono,
    fontSize: 11,
    color: sv.inkFaint,
};

/** Single-line clipping with ellipsis overflow. */
const truncateStyle: CSSProperties = {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
};

/** Shared base for bare (borderless-chrome) inputs. */
const fieldBaseStyle: CSSProperties = {
    background: sv.bg0,
    border: `1px solid ${sv.lineMid}`,
    color: sv.ink,
    fontFamily: sv.mono,
    outline: 'none',
};

/** Highlight an input's border on focus. */
function applyFieldFocus(e: FocusEvent<HTMLElement>): void {
    e.currentTarget.style.borderColor = sv.cyan;
}

/** Restore an input's border on blur. */
function applyFieldBlur(e: FocusEvent<HTMLElement>): void {
    e.currentTarget.style.borderColor = sv.lineMid;
}

/**
 * Synapse text input — used for the Edition tag field on movie titles.
 */
function SvTextInput({
    value,
    onChange,
    placeholder,
    list,
    ariaLabel,
    style,
}: {
    value: string;
    onChange: (v: string) => void;
    placeholder?: string;
    list?: string;
    ariaLabel?: string;
    style?: CSSProperties;
}) {
    return (
        <input
            type="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            list={list}
            aria-label={ariaLabel}
            style={{
                ...fieldBaseStyle,
                width: '100%',
                padding: '7px 12px',
                fontSize: 12,
                letterSpacing: '0.04em',
                transition: 'border-color 120ms, box-shadow 120ms',
                ...style,
            }}
            onFocus={(e) => {
                applyFieldFocus(e);
                e.currentTarget.style.boxShadow = `0 0 8px ${sv.cyan}33`;
            }}
            onBlur={(e) => {
                applyFieldBlur(e);
                e.currentTarget.style.boxShadow = 'none';
            }}
        />
    );
}

/**
 * Small uniform header-action button for the ReviewQueue. Inline-styled with
 * sv tokens so it matches the `SvPageHeader` chrome.
 */
function HeaderButton({
    color,
    onClick,
    disabled,
    icon,
    children,
}: {
    color: string;
    onClick: () => void;
    disabled?: boolean;
    icon?: ReactNode;
    children: ReactNode;
}) {
    const base: CSSProperties = {
        height: 32,
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        padding: '0 12px',
        background: sv.bg0,
        border: `1px solid ${color}55`,
        color,
        fontFamily: sv.mono,
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: '0.20em',
        textTransform: 'uppercase',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        boxShadow: `0 0 8px ${color}33`,
        transition: 'border-color 120ms, box-shadow 120ms',
    };
    return (
        <button
            onClick={onClick}
            disabled={disabled}
            style={base}
            onMouseEnter={(e) => {
                if (disabled) return;
                e.currentTarget.style.borderColor = color;
                e.currentTarget.style.boxShadow = `0 0 14px ${color}66`;
            }}
            onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = `${color}55`;
                e.currentTarget.style.boxShadow = `0 0 8px ${color}33`;
            }}
        >
            {icon}
            <span>{children}</span>
        </button>
    );
}

type TitleAction = 'episode' | 'extra' | 'discard' | 'skip';

function ReviewQueue() {
    const { jobId } = useParams<{ jobId: string }>();
    const navigate = useNavigate();
    const [job, setJob] = useState<Job | null>(null);
    const [titles, setTitles] = useState<DiscTitle[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [isProcessing, setIsProcessing] = useState(false);
    const [isRematching, setIsRematching] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Per-title state
    const [selectedEpisodes, setSelectedEpisodes] = useState<Record<number, string>>({});
    const [selectedEditions, setSelectedEditions] = useState<Record<number, string>>({});
    const [titleActions, setTitleActions] = useState<Record<number, TitleAction>>({});
    const [selectedTitleId, setSelectedTitleId] = useState<number | null>(null);

    const { roster, error: rosterError, episodeName } = useSeasonRoster(jobId);

    useEffect(() => {
        fetchJobDetails();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [jobId]);

    const fetchJobDetails = async () => {
        try {
            const [jobResponse, titlesResponse] = await Promise.all([
                fetch(`/api/jobs/${jobId}`),
                fetch(`/api/jobs/${jobId}/titles`),
            ]);

            if (jobResponse.ok) {
                setJob(await jobResponse.json());
            }

            if (titlesResponse.ok) {
                const titlesData = await titlesResponse.json();
                setTitles(titlesData);

                // Pre-fill selections from existing match results
                const episodes: Record<number, string> = {};
                const actions: Record<number, TitleAction> = {};
                titlesData.forEach((title: DiscTitle) => {
                    if (title.matched_episode) {
                        // Canonicalize so unpadded matcher output (e.g. "S1E14")
                        // dedupes/collides against padded codes and the roster.
                        episodes[title.id] = normalizeEpisodeCode(title.matched_episode);
                        actions[title.id] = 'episode';
                    }
                });
                setSelectedEpisodes(episodes);
                setTitleActions(actions);
            }
        } catch (err) {
            console.error('Failed to fetch job:', err);
            setError('Failed to load job details');
        } finally {
            setIsLoading(false);
        }
    };

    // Default the inspector to the first title that needs attention.
    useEffect(() => {
        if (selectedTitleId !== null) return;
        const active = titles.filter((t) => t.state !== 'completed' && t.state !== 'failed');
        if (active.length === 0) return;
        const firstReview = active.find(
            (t) => !t.matched_episode || t.match_confidence < MATCHING_CONFIG.AUTO_MATCH_THRESHOLD,
        );
        setSelectedTitleId((firstReview ?? active[0]).id);
    }, [titles, selectedTitleId]);

    const handleEpisodeChange = (titleId: number, episodeCode: string) => {
        setSelectedEpisodes(prev => ({ ...prev, [titleId]: normalizeEpisodeCode(episodeCode) }));
        setTitleActions(prev => ({ ...prev, [titleId]: 'episode' }));
    };

    const handleEditionChange = (titleId: number, edition: string) => {
        setSelectedEditions(prev => ({ ...prev, [titleId]: edition }));
    };

    const handleTitleAction = (titleId: number, action: TitleAction) => {
        setTitleActions(prev => ({ ...prev, [titleId]: action }));
        if (action === 'extra') {
            setSelectedEpisodes(prev => ({ ...prev, [titleId]: 'extra' }));
        } else if (action === 'discard') {
            setSelectedEpisodes(prev => ({ ...prev, [titleId]: 'skip' }));
        } else if (action === 'skip') {
            // Remove from selections — leave unresolved
            setSelectedEpisodes(prev => {
                const next = { ...prev };
                delete next[titleId];
                return next;
            });
        }
    };

    // --- API Handlers ---

    const handleRematch = async (titleId: number, sourcePreference: string = 'engram') => {
        setIsRematching(true);
        setError(null);
        try {
            const response = await fetch(`/api/jobs/${jobId}/titles/${titleId}/rematch`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_preference: sourcePreference }),
            });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(`Failed to re-match title: ${text}`);
            }
            await fetchJobDetails();
        } catch (err) {
            console.error('Failed to re-match:', err);
            setError(err instanceof Error ? err.message : 'Failed to re-match');
        } finally {
            setIsRematching(false);
        }
    };

    const handleRematchAll = async () => {
        setIsRematching(true);
        setError(null);
        try {
            const response = await fetch(`/api/jobs/${jobId}/rematch`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_preference: 'engram' }),
            });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(`Failed to re-match all: ${text}`);
            }
            await fetchJobDetails();
        } catch (err) {
            console.error('Failed to re-match all:', err);
            setError(err instanceof Error ? err.message : 'Failed to re-match all');
        } finally {
            setIsRematching(false);
        }
    };

    // Deep re-match every title claiming a contested episode (denser sampling +
    // stricter votes) so a collision can resolve either way.
    const handleRematchConflict = async (episodeCode: string) => {
        setIsRematching(true);
        setError(null);
        try {
            const response = await fetch(`/api/jobs/${jobId}/rematch-conflict`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ episode_code: episodeCode }),
            });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(`Deep re-match failed: ${text}`);
            }
            await fetchJobDetails();
        } catch (err) {
            console.error('Failed to deep re-match conflict:', err);
            setError(err instanceof Error ? err.message : 'Failed to deep re-match');
        } finally {
            setIsRematching(false);
        }
    };

    // POST every pending episode selection to the review endpoint.
    // Throws on the first failure so callers can abort their follow-up step.
    const submitPendingSelections = async () => {
        for (const [titleId, episodeCode] of Object.entries(selectedEpisodes)) {
            const response = await fetch(`/api/jobs/${jobId}/review`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title_id: parseInt(titleId),
                    episode_code: episodeCode,
                }),
            });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(`Failed to save title ${titleId}: ${text}`);
            }
        }
    };

    const handleSaveAll = async () => {
        setIsSaving(true);
        setError(null);
        try {
            await submitPendingSelections();
            navigate('/');
        } catch (err) {
            console.error('Failed to save reviews:', err);
            setError(err instanceof Error ? err.message : 'Failed to save reviews');
        } finally {
            setIsSaving(false);
        }
    };

    const handleProcessMatched = async () => {
        setIsProcessing(true);
        setError(null);
        try {
            // First submit all pending selections
            await submitPendingSelections();
            // Then process matched titles
            const response = await fetch(`/api/jobs/${jobId}/process-matched`, { method: 'POST' });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(`Processing failed: ${text}`);
            }
            const result = await response.json();
            if (result.unresolved === 0) {
                navigate('/');
            } else {
                // Refresh to see updated state
                await fetchJobDetails();
            }
        } catch (err) {
            console.error('Failed to process matched:', err);
            setError(err instanceof Error ? err.message : 'Failed to process');
        } finally {
            setIsProcessing(false);
        }
    };

    const handleStartRip = async () => {
        setError(null);
        try {
            const response = await fetch(`/api/jobs/${jobId}/start`, { method: 'POST' });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(`Failed to start: ${text}`);
            }
            navigate('/');
        } catch (err) {
            console.error('Failed to start rip:', err);
            setError(err instanceof Error ? err.message : 'Failed to start ripping');
        }
    };

    const handleSaveMovie = async (titleId: number, matchAction: 'save' | 'skip') => {
        setIsSaving(true);
        setError(null);
        try {
            const response = await fetch(`/api/jobs/${jobId}/review`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title_id: titleId,
                    episode_code: matchAction === 'skip' ? 'skip' : undefined,
                    edition: matchAction === 'save' ? (selectedEditions[titleId] || null) : undefined,
                }),
            });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(`Review failed: ${response.status} ${text}`);
            }
            navigate('/');
        } catch (err) {
            console.error('Failed to save movie review:', err);
            setError(err instanceof Error ? err.message : 'Failed to save review');
        } finally {
            setIsSaving(false);
        }
    };

    // --- Derived disc-level coverage (live, from current selections) ---
    const rosterEpisodes = useMemo(() => roster?.episodes ?? [], [roster]);
    const titleIndexById = useMemo(
        () => Object.fromEntries(titles.map((t) => [t.id, t.title_index])) as Record<number, number>,
        [titles],
    );
    const coverage = useMemo(
        () => computeCoverage(selectedEpisodes, rosterEpisodes),
        [selectedEpisodes, rosterEpisodes],
    );
    const holders = useMemo(() => assignmentsByCode(selectedEpisodes), [selectedEpisodes]);
    const collisions = useMemo(() => collidingCodes(selectedEpisodes), [selectedEpisodes]);
    const hasConflicts = collisions.size > 0;

    const activeTitles = titles.filter((t) => t.state !== 'completed' && t.state !== 'failed');
    const completedTitles = titles.filter((t) => t.state === 'completed' || t.state === 'failed');
    const selectedTitle =
        activeTitles.find((t) => t.id === selectedTitleId) ?? activeTitles[0] ?? null;

    const candidates = selectedTitle ? buildCandidates(selectedTitle, episodeName) : [];

    // Suggest the disc's remaining gap for the selected title when it is
    // unassigned or its current pick collides with another title. Memoized so
    // the extra computeCoverage pass inside suggestGapCode only runs when the
    // inputs actually change, not on every render.
    const { inspectorSuggestion, suggestedForSelected } = useMemo<{
        inspectorSuggestion: { code: string; name: string } | null;
        suggestedForSelected: string | null;
    }>(() => {
        if (!selectedTitle) return { inspectorSuggestion: null, suggestedForSelected: null };
        const currentSel = selectedEpisodes[selectedTitle.id];
        const needsHelp = !currentSel || collisions.has(currentSel);
        if (!needsHelp) return { inspectorSuggestion: null, suggestedForSelected: null };
        const others = { ...selectedEpisodes };
        delete others[selectedTitle.id];
        const gap = suggestGapCode(others, rosterEpisodes);
        if (!gap) return { inspectorSuggestion: null, suggestedForSelected: null };
        return { inspectorSuggestion: { code: gap, name: episodeName(gap) }, suggestedForSelected: gap };
    }, [selectedTitle, selectedEpisodes, collisions, rosterEpisodes, episodeName]);

    const assignedCount = Object.keys(selectedEpisodes).length;

    // --- Render ---

    if (isLoading) {
        return (
            <SvAtmosphere>
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
                    <motion.div animate={{ rotate: 360 }} transition={{ duration: 2, repeat: Infinity, ease: 'linear' }}>
                        <IcoDisc size={48} color={sv.cyan} style={{ filter: `drop-shadow(0 0 10px ${sv.cyan}cc)` }} />
                    </motion.div>
                    <span style={{ fontFamily: sv.mono, fontSize: 12, letterSpacing: '0.20em', textTransform: 'uppercase', color: sv.cyan }}>
                        › LOADING JOB DATA…
                    </span>
                </div>
            </SvAtmosphere>
        );
    }

    if (!job) {
        return (
            <SvAtmosphere>
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 24 }}>
                    <h2 style={{ fontFamily: sv.display, fontSize: 28, fontWeight: 700, letterSpacing: '0.10em', color: sv.red, textTransform: 'uppercase', textShadow: `0 0 12px ${sv.red}55`, margin: 0 }}>
                        JOB NOT FOUND
                    </h2>
                    <button
                        onClick={() => navigate('/')}
                        style={{
                            padding: '10px 18px',
                            background: 'transparent',
                            border: `1px solid ${sv.cyan}88`,
                            color: sv.cyan,
                            fontFamily: sv.mono,
                            fontSize: 11,
                            fontWeight: 700,
                            letterSpacing: '0.20em',
                            textTransform: 'uppercase',
                            cursor: 'pointer',
                        }}
                    >
                        RETURN TO DASHBOARD
                    </button>
                </div>
            </SvAtmosphere>
        );
    }

    // ==================== MOVIE REVIEW ====================
    if (job.content_type === 'movie') {
        return (
            <SvAtmosphere>
                <SvPageHeader
                    title="Select movie version"
                    subtitle={`› ${job.detected_title || job.volume_label}`}
                    onBack={() => navigate('/')}
                    maxWidth={1280}
                />

                {/* Content */}
                <div className="max-w-[1280px] mx-auto px-6 py-8 relative z-0">
                    {error && <SvNotice tone="error">› ERROR: {error}</SvNotice>}
                    <SvNotice tone="warn">
                        › MULTIPLE FEATURE-LENGTH TITLES DETECTED. SELECT THE CORRECT VERSION TO KEEP.
                    </SvNotice>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                        {titles.map(title => (
                            <motion.div
                                key={title.id}
                                initial={{ opacity: 0, y: 10 }}
                                animate={{ opacity: 1, y: 0 }}
                            >
                                <SvPanel pad={20}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
                                        {/* Title info */}
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                                                <SvBadge size="sm" tone={sv.inkDim} dot={false}>
                                                    #{title.title_index}
                                                </SvBadge>
                                                <span
                                                    style={{
                                                        ...truncateStyle,
                                                        fontFamily: sv.mono,
                                                        fontSize: 13,
                                                        color: sv.cyanHi,
                                                    }}
                                                >
                                                    {titleDisplayName(title)}
                                                </span>
                                            </div>
                                            <div
                                                style={{
                                                    ...monoLabelStyle,
                                                    display: 'flex',
                                                    alignItems: 'center',
                                                    gap: 24,
                                                }}
                                            >
                                                <span>{formatDuration(title.duration_seconds)}</span>
                                                <span>{formatSize(title.file_size_bytes)}</span>
                                                <SvBadge size="sm" tone={sv.cyan} dot={false}>
                                                    {title.video_resolution || 'Unknown'}
                                                </SvBadge>
                                                <span>{title.chapter_count} chapters</span>
                                            </div>
                                        </div>

                                        {/* Edition input */}
                                        <div style={{ width: 192 }}>
                                            <SvTextInput
                                                value={selectedEditions[title.id] || ''}
                                                onChange={(v) => handleEditionChange(title.id, v)}
                                                placeholder="Edition tag…"
                                                list="edition-suggestions"
                                                ariaLabel={`Edition tag for title ${title.title_index}`}
                                            />
                                        </div>

                                        {/* Actions */}
                                        <div style={{ display: 'flex', gap: 8 }}>
                                            <SvActionButton
                                                tone="green"
                                                onClick={() => handleSaveMovie(title.id, 'save')}
                                                disabled={isSaving}
                                            >
                                                Select
                                            </SvActionButton>
                                            <SvActionButton
                                                tone="red"
                                                onClick={() => handleSaveMovie(title.id, 'skip')}
                                                disabled={isSaving}
                                            >
                                                Discard
                                            </SvActionButton>
                                        </div>
                                    </div>
                                </SvPanel>
                            </motion.div>
                        ))}
                    </div>

                    <datalist id="edition-suggestions">
                        <option value="Theatrical" />
                        <option value="Extended" />
                        <option value="Director's Cut" />
                        <option value="Unrated" />
                        <option value="IMAX" />
                    </datalist>
                </div>
            </SvAtmosphere>
        );
    }

    // ==================== TV REVIEW (inspector layout) ====================
    const subtitleText = `› ${job.detected_title || job.volume_label}${job.detected_season ? ` / SEASON ${job.detected_season}` : ''}`;
    return (
        <SvAtmosphere>
            <SvPageHeader
                title="Review titles"
                subtitle={subtitleText}
                onBack={() => navigate('/')}
                maxWidth={1280}
                right={
                    <>
                        <HeaderButton
                            color={sv.cyan}
                            onClick={handleStartRip}
                            disabled={isSaving || isProcessing}
                            icon={<IcoPlay size={12} />}
                        >
                            Start rip
                        </HeaderButton>
                        {assignedCount > 0 && (
                            <HeaderButton
                                color={sv.yellow}
                                onClick={handleSaveAll}
                                disabled={isSaving || isProcessing || hasConflicts}
                                icon={<Save size={12} />}
                            >
                                {isSaving ? 'Saving…' : `Save ${assignedCount}`}
                            </HeaderButton>
                        )}
                        {assignedCount > 0 && (
                            <HeaderButton
                                color={sv.green}
                                onClick={handleProcessMatched}
                                disabled={isSaving || isProcessing || hasConflicts}
                                icon={<Package size={12} />}
                            >
                                {isProcessing ? 'Processing…' : `Process ${assignedCount}`}
                            </HeaderButton>
                        )}
                        <HeaderButton
                            color={sv.magenta}
                            onClick={handleRematchAll}
                            disabled={isSaving || isProcessing || isRematching}
                            icon={<IcoRetry size={12} className={isRematching ? 'animate-spin' : ''} />}
                        >
                            {isRematching ? 'Re-matching…' : 'Re-match all'}
                        </HeaderButton>
                    </>
                }
            />

            {/* Content */}
            <div className="max-w-[1280px] mx-auto px-6 py-8 relative z-0 pb-24">
                {error && <SvNotice tone="error">› ERROR: {error}</SvNotice>}
                {job.error_message && <SvNotice tone="warn">› {job.error_message}</SvNotice>}
                {hasConflicts && (
                    <SvNotice tone="warn">
                        › {collisions.size} EPISODE CONFLICT{collisions.size > 1 ? 'S' : ''} — TWO TITLES SHARE AN EPISODE. RESOLVE BEFORE SAVING.
                    </SvNotice>
                )}
                {job.subtitle_status === 'failed' && !job.error_message?.includes('Subtitle') && (
                    <SvNotice tone="warn">
                        › SUBTITLE DOWNLOAD FAILED. MANUAL FETCH MAY BE REQUIRED.
                    </SvNotice>
                )}
                {rosterError && (
                    <SvNotice tone="warn">
                        › SEASON ROSTER UNAVAILABLE — RELOAD TO RETRY.
                    </SvNotice>
                )}

                {/* Season roster */}
                {roster?.available && rosterEpisodes.length > 0 && (
                    <div style={{ marginBottom: 24 }}>
                        <div style={{ marginBottom: 12 }}>
                            <SvLabel>Season roster — coverage across this disc</SvLabel>
                        </div>
                        <SvPanel pad={14}>
                            <SeasonRosterStrip
                                episodes={rosterEpisodes}
                                coverage={coverage}
                                suggestedCode={suggestedForSelected}
                                titleIndexById={titleIndexById}
                            />
                        </SvPanel>
                    </div>
                )}

                {/* List + inspector */}
                <div
                    style={{
                        display: 'grid',
                        gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.05fr)',
                        gap: 18,
                        alignItems: 'start',
                    }}
                >
                    {/* Left: title list */}
                    <div>
                        <div style={{ marginBottom: 12 }}>
                            <SvLabel>Titles [{activeTitles.length}] — select one to inspect</SvLabel>
                        </div>
                        <TitleList
                            titles={activeTitles}
                            selectedTitleId={selectedTitle?.id ?? null}
                            selections={selectedEpisodes}
                            collisions={collisions}
                            episodeName={episodeName}
                            onSelect={setSelectedTitleId}
                        />

                        {completedTitles.length > 0 && (
                            <div style={{ marginTop: 24 }}>
                                <div style={{ marginBottom: 12 }}>
                                    <SvLabel>Processed [{completedTitles.length}]</SvLabel>
                                </div>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, opacity: 0.55 }}>
                                    {completedTitles.map(title => (
                                        <div
                                            key={title.id}
                                            style={{
                                                ...monoLabelStyle,
                                                display: 'flex',
                                                alignItems: 'center',
                                                gap: 16,
                                                padding: '12px 14px',
                                                background: sv.bg1,
                                                border: `1px solid ${sv.line}`,
                                            }}
                                        >
                                            <SvBadge size="sm" tone={sv.inkFaint} dot={false}>#{title.title_index}</SvBadge>
                                            <span style={{ ...truncateStyle, flex: 1 }}>
                                                {titleDisplayName(title)}
                                            </span>
                                            <span>{title.matched_episode || '—'}</span>
                                            <SvBadge
                                                size="sm"
                                                state={title.state === 'completed' ? 'complete' : 'error'}
                                                dot={false}
                                            >
                                                {title.state.toUpperCase()}
                                            </SvBadge>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Right: inspector */}
                    <div>
                        <div style={{ marginBottom: 12 }}>
                            <SvLabel>Inspector{selectedTitle ? ` — title #${selectedTitle.title_index}` : ''}</SvLabel>
                        </div>
                        {selectedTitle ? (
                            <Inspector
                                title={selectedTitle}
                                job={job}
                                candidates={candidates}
                                suggestion={inspectorSuggestion}
                                selection={selectedEpisodes[selectedTitle.id]}
                                action={titleActions[selectedTitle.id]}
                                episodes={rosterEpisodes}
                                coverage={coverage}
                                holders={holders}
                                titleIndexById={titleIndexById}
                                isRematching={isRematching}
                                onAssign={(code) => handleEpisodeChange(selectedTitle.id, code)}
                                onAction={(a) => handleTitleAction(selectedTitle.id, a)}
                                onRematch={handleRematch}
                                onDeepRematch={handleRematchConflict}
                            />
                        ) : (
                            <SvPanel pad={24}>
                                <div style={{ ...monoLabelStyle, textAlign: 'center' }}>
                                    No titles awaiting review.
                                </div>
                            </SvPanel>
                        )}
                    </div>
                </div>
            </div>
        </SvAtmosphere>
    );
}

export default ReviewQueue;
