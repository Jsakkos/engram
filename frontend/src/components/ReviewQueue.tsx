import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { motion } from 'motion/react';
import { Disc3, Play, Save, Trash2, Package, SkipForward, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import type { CSSProperties, ReactNode } from 'react';
import { Job, DiscTitle } from '../types';
import { formatDuration, formatSize, parseMatchDetails, generateEpisodeOptions, getReviewReasons } from './ReviewQueue/utils';
import { MATCHING_CONFIG, EPISODE_CONFIG, FEATURES } from '../config/constants';
import { SvActionButton, SvAtmosphere, SvBadge, SvLabel, SvNotice, SvPageHeader, SvPanel, sv } from '../app/components/synapse';

/**
 * Synapse text input — used for the Edition tag field on movie titles and
 * the manual episode-code input in the TV title row.
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
                width: '100%',
                padding: '7px 12px',
                background: sv.bg0,
                border: `1px solid ${sv.lineMid}`,
                color: sv.ink,
                fontFamily: sv.mono,
                fontSize: 12,
                letterSpacing: '0.04em',
                outline: 'none',
                transition: 'border-color 120ms, box-shadow 120ms',
                ...style,
            }}
            onFocus={(e) => {
                e.currentTarget.style.borderColor = sv.cyan;
                e.currentTarget.style.boxShadow = `0 0 8px ${sv.cyan}33`;
            }}
            onBlur={(e) => {
                e.currentTarget.style.borderColor = sv.lineMid;
                e.currentTarget.style.boxShadow = 'none';
            }}
        />
    );
}

/**
 * Section heading — colored dot + uppercase mono label + bracket count.
 * Used for the Auto-matched / Needs review / Processed groupings on the TV
 * review page.
 */
function SectionHeading({
    color,
    count,
    children,
}: {
    color: string;
    count: number;
    children: ReactNode;
}) {
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
            <span
                style={{
                    width: 8,
                    height: 8,
                    background: color,
                    boxShadow: `0 0 8px ${color}cc`,
                }}
            />
            <h2
                style={{
                    margin: 0,
                    fontFamily: sv.mono,
                    fontSize: 13,
                    fontWeight: 700,
                    letterSpacing: '0.20em',
                    textTransform: 'uppercase',
                    color,
                }}
            >
                {children}
                <span style={{ marginLeft: 8, color: `${color}aa` }}>[{count}]</span>
            </h2>
        </div>
    );
}

/**
 * Small uniform header-action button for the ReviewQueue. Inline-styled with
 * sv tokens so it matches the `SvPageHeader` chrome and the dashboard's
 * ActionButtons family.
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
    const [expandedTitles, setExpandedTitles] = useState<Set<number>>(new Set());

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
                        episodes[title.id] = title.matched_episode;
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

    const handleEpisodeChange = (titleId: number, episodeCode: string) => {
        setSelectedEpisodes(prev => ({ ...prev, [titleId]: episodeCode }));
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

    const toggleExpand = (titleId: number) => {
        setExpandedTitles(prev => {
            const next = new Set(prev);
            next.has(titleId) ? next.delete(titleId) : next.add(titleId);
            return next;
        });
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

    const handleSaveAll = async () => {
        setIsSaving(true);
        setError(null);
        try {
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

    // --- Categorize titles ---
    const matchedTitles = titles.filter(t =>
        t.matched_episode && t.match_confidence >= MATCHING_CONFIG.AUTO_MATCH_THRESHOLD && t.state !== 'completed' && t.state !== 'failed'
    );
    const needsReviewTitles = titles.filter(t =>
        (!t.matched_episode || t.match_confidence < MATCHING_CONFIG.AUTO_MATCH_THRESHOLD) && t.state !== 'completed' && t.state !== 'failed'
    );
    const completedTitles = titles.filter(t => t.state === 'completed' || t.state === 'failed');

    const assignedCount = Object.keys(selectedEpisodes).length;

    // --- Render ---

    if (isLoading) {
        return (
            <SvAtmosphere>
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
                    <motion.div animate={{ rotate: 360 }} transition={{ duration: 2, repeat: Infinity, ease: 'linear' }}>
                        <Disc3 size={48} color={sv.cyan} style={{ filter: `drop-shadow(0 0 10px ${sv.cyan}cc)` }} />
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
                                                        fontFamily: sv.mono,
                                                        fontSize: 13,
                                                        color: sv.cyanHi,
                                                        overflow: 'hidden',
                                                        textOverflow: 'ellipsis',
                                                        whiteSpace: 'nowrap',
                                                    }}
                                                >
                                                    {title.output_filename ? title.output_filename.split(/[/\\]/).pop() : `Title ${title.title_index}`}
                                                </span>
                                            </div>
                                            <div
                                                style={{
                                                    display: 'flex',
                                                    alignItems: 'center',
                                                    gap: 24,
                                                    fontFamily: sv.mono,
                                                    fontSize: 11,
                                                    color: sv.inkFaint,
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

    // ==================== TV REVIEW ====================
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
                            icon={<Play size={12} />}
                        >
                            Start rip
                        </HeaderButton>
                        {assignedCount > 0 && (
                            <HeaderButton
                                color={sv.yellow}
                                onClick={handleSaveAll}
                                disabled={isSaving || isProcessing}
                                icon={<Save size={12} />}
                            >
                                {isSaving ? 'Saving…' : `Save ${assignedCount}`}
                            </HeaderButton>
                        )}
                        {assignedCount > 0 && (
                            <HeaderButton
                                color={sv.green}
                                onClick={handleProcessMatched}
                                disabled={isSaving || isProcessing}
                                icon={<Package size={12} />}
                            >
                                {isProcessing ? 'Processing…' : `Process ${assignedCount}`}
                            </HeaderButton>
                        )}
                        <HeaderButton
                            color={sv.magenta}
                            onClick={handleRematchAll}
                            disabled={isSaving || isProcessing || isRematching}
                            icon={<RefreshCw size={12} className={isRematching ? 'animate-spin' : ''} />}
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
                {job.subtitle_status === 'failed' && !job.error_message?.includes('Subtitle') && (
                    <SvNotice tone="warn">
                        › SUBTITLE DOWNLOAD FAILED. MANUAL FETCH MAY BE REQUIRED.
                    </SvNotice>
                )}

                {/* Matched Section */}
                {matchedTitles.length > 0 && (
                    <div style={{ marginBottom: 32 }}>
                        <SectionHeading color={sv.green} count={matchedTitles.length}>Auto-matched</SectionHeading>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            {matchedTitles.map(title => (
                                <TVTitleRow
                                    key={title.id}
                                    title={title}
                                    job={job}
                                    selectedEpisode={selectedEpisodes[title.id] || ''}
                                    titleAction={titleActions[title.id]}
                                    isExpanded={expandedTitles.has(title.id)}
                                    onEpisodeChange={handleEpisodeChange}
                                    onTitleAction={handleTitleAction}
                                    onToggleExpand={toggleExpand}
                                    onRematch={handleRematch}
                                    variant="matched"
                                />
                            ))}
                        </div>
                    </div>
                )}

                {/* Needs Review Section */}
                {needsReviewTitles.length > 0 && (
                    <div style={{ marginBottom: 32 }}>
                        <SectionHeading color={sv.yellow} count={needsReviewTitles.length}>Needs review</SectionHeading>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            {needsReviewTitles.map(title => (
                                <TVTitleRow
                                    key={title.id}
                                    title={title}
                                    job={job}
                                    selectedEpisode={selectedEpisodes[title.id] || ''}
                                    titleAction={titleActions[title.id]}
                                    isExpanded={expandedTitles.has(title.id)}
                                    onEpisodeChange={handleEpisodeChange}
                                    onTitleAction={handleTitleAction}
                                    onToggleExpand={toggleExpand}
                                    onRematch={handleRematch}
                                    variant="review"
                                />
                            ))}
                        </div>
                    </div>
                )}

                {/* Completed Section */}
                {completedTitles.length > 0 && (
                    <div style={{ marginBottom: 32 }}>
                        <SectionHeading color={sv.inkFaint} count={completedTitles.length}>Processed</SectionHeading>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, opacity: 0.55 }}>
                            {completedTitles.map(title => (
                                <div
                                    key={title.id}
                                    style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 24,
                                        padding: '14px 16px',
                                        background: sv.bg1,
                                        border: `1px solid ${sv.line}`,
                                        fontFamily: sv.mono,
                                        fontSize: 11,
                                        color: sv.inkFaint,
                                    }}
                                >
                                    <SvBadge size="sm" tone={sv.inkFaint} dot={false}>#{title.title_index}</SvBadge>
                                    <span
                                        style={{
                                            flex: 1,
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis',
                                            whiteSpace: 'nowrap',
                                        }}
                                    >
                                        {title.output_filename?.split(/[/\\]/).pop() || `Title ${title.title_index}`}
                                    </span>
                                    <span>{formatDuration(title.duration_seconds)}</span>
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
        </SvAtmosphere>
    );
}

// ==================== TV Title Row Component ====================

interface TVTitleRowProps {
    title: DiscTitle;
    job: Job;
    selectedEpisode: string;
    titleAction?: TitleAction;
    isExpanded: boolean;
    onEpisodeChange: (titleId: number, episodeCode: string) => void;
    onTitleAction: (titleId: number, action: TitleAction) => void;
    onToggleExpand: (titleId: number) => void;
    onRematch: (titleId: number, sourcePreference?: string) => void;
    variant: 'matched' | 'review';
}

function TVTitleRow({
    title,
    job,
    selectedEpisode,
    titleAction,
    isExpanded,
    onEpisodeChange,
    onTitleAction,
    onToggleExpand,
    onRematch,
    variant,
}: TVTitleRowProps) {
    const [season, setSeason] = useState(job?.detected_season || 1);
    const details = parseMatchDetails(title);
    const isConflict = details.error === 'file_exists';
    const reasons = getReviewReasons(title);
    const alternatives = details.runner_ups || [];

    const borderColor = isConflict
        ? `${sv.yellow}66`
        : variant === 'matched'
        ? `${sv.green}33`
        : `${sv.cyan}33`;

    const confColor =
        title.match_confidence >= MATCHING_CONFIG.AUTO_MATCH_THRESHOLD ? sv.green :
        title.match_confidence >= MATCHING_CONFIG.MIN_CONFIDENCE ? sv.yellow : sv.red;

    const sourceTone =
        title.match_source === 'discdb' ? '#60a5fa' :
        title.match_source === 'user' ? sv.green : sv.purple;
    const sourceLabel =
        title.match_source === 'discdb' ? 'DISCDB' :
        title.match_source === 'user' ? 'MANUAL' : 'ENGRAM';

    return (
        <motion.div initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }}>
            <SvPanel pad={16} accent={borderColor}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                    {/* Expand button */}
                    <button
                        type="button"
                        onClick={() => onToggleExpand(title.id)}
                        aria-expanded={isExpanded}
                        aria-label={isExpanded ? `Collapse details for title ${title.title_index}` : `Expand details for title ${title.title_index}`}
                        style={{
                            background: 'transparent',
                            border: 0,
                            color: sv.inkFaint,
                            cursor: 'pointer',
                            display: 'inline-flex',
                            alignItems: 'center',
                            transition: 'color 120ms',
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.color = sv.cyan; }}
                        onMouseLeave={(e) => { e.currentTarget.style.color = sv.inkFaint; }}
                    >
                        {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </button>

                    {/* Title index */}
                    <SvBadge size="sm" tone={sv.inkDim} dot={false}>
                        #{title.title_index}
                    </SvBadge>

                    {/* Title name + conflict indicator */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <span
                                style={{
                                    fontFamily: sv.mono,
                                    fontSize: 13,
                                    color: sv.cyanHi,
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap',
                                }}
                            >
                                {title.output_filename ? title.output_filename.split(/[/\\]/).pop() : `Title ${title.title_index}`}
                            </span>
                            {isConflict && (
                                <SvBadge size="sm" state="warn" dot={false}>
                                    File exists
                                </SvBadge>
                            )}
                        </div>
                        <div
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 16,
                                marginTop: 4,
                                fontFamily: sv.mono,
                                fontSize: 11,
                                color: sv.inkFaint,
                            }}
                        >
                            <span>{formatDuration(title.duration_seconds)}</span>
                            <span>{formatSize(title.file_size_bytes)}</span>
                        </div>
                    </div>

                    {/* Confidence + source */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                        {title.match_confidence > 0 ? (
                            <SvBadge size="sm" tone={confColor} dot={false}>
                                {Math.round(title.match_confidence * 100)}%
                            </SvBadge>
                        ) : (
                            <SvBadge size="sm" tone={sv.inkFaint} dot={false}>—</SvBadge>
                        )}
                        {FEATURES.DISCDB && title.match_source && (
                            <SvBadge size="sm" tone={sourceTone}>{sourceLabel}</SvBadge>
                        )}
                    </div>

                    {/* Review reasons */}
                    {reasons.length > 0 && (
                        <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                            {reasons.slice(0, 2).map((r, i) => (
                                <SvBadge key={i} size="sm" tone="#fb923c">{r}</SvBadge>
                            ))}
                        </div>
                    )}

                    {/* Episode selector with season input */}
                    <div style={{ width: 208, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span
                            style={{
                                fontFamily: sv.mono,
                                fontSize: 11,
                                color: sv.inkDim,
                                letterSpacing: '0.10em',
                            }}
                            title="Season"
                        >
                            S
                        </span>
                        <input
                            type="number"
                            min={1}
                            max={20}
                            value={season}
                            onChange={(e) => setSeason(Math.max(1, Math.min(20, parseInt(e.target.value) || 1)))}
                            title="Season number"
                            aria-label="Season number"
                            style={{
                                width: 40,
                                padding: '4px 6px',
                                background: sv.bg0,
                                border: `1px solid ${sv.lineMid}`,
                                color: sv.ink,
                                fontFamily: sv.mono,
                                fontSize: 11,
                                textAlign: 'center',
                                outline: 'none',
                            }}
                            onFocus={(e) => { e.currentTarget.style.borderColor = sv.cyan; }}
                            onBlur={(e) => { e.currentTarget.style.borderColor = sv.lineMid; }}
                        />
                        <select
                            value={selectedEpisode}
                            onChange={(e) => onEpisodeChange(title.id, e.target.value)}
                            aria-label={`Episode assignment for title ${title.title_index}`}
                            style={{
                                flex: 1,
                                padding: '6px 8px',
                                background: sv.bg0,
                                border: `1px solid ${sv.lineMid}`,
                                color: sv.ink,
                                fontFamily: sv.mono,
                                fontSize: 11,
                                outline: 'none',
                                cursor: 'pointer',
                            }}
                            onFocus={(e) => { e.currentTarget.style.borderColor = sv.cyan; }}
                            onBlur={(e) => { e.currentTarget.style.borderColor = sv.lineMid; }}
                        >
                            <option value="">Select episode…</option>
                            {title.matched_episode && (
                                <option value={title.matched_episode}>
                                    {title.matched_episode} — Best ({Math.round(title.match_confidence * 100)}%)
                                </option>
                            )}
                            {alternatives.map((alt, idx) => (
                                <option key={`alt-${idx}`} value={alt.episode}>
                                    {alt.episode} — Alt ({Math.round(alt.confidence * 100)}%)
                                </option>
                            ))}
                            {(title.matched_episode || alternatives.length > 0) && (
                                <option disabled>{'─'.repeat(20)}</option>
                            )}
                            {generateEpisodeOptions(season, EPISODE_CONFIG.DEFAULT_EPISODES_PER_SEASON).map(ep => (
                                <option key={ep} value={ep}>{ep}</option>
                            ))}
                        </select>
                    </div>

                    {/* Action buttons */}
                    <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                        <SvActionButton
                            tone={titleAction === 'extra' ? 'cyan' : 'neutral'}
                            size="sm"
                            onClick={() => onTitleAction(title.id, 'extra')}
                            title="Keep as extra content"
                        >
                            Extra
                        </SvActionButton>
                        <SvActionButton
                            tone={titleAction === 'discard' ? 'red' : 'neutral'}
                            size="sm"
                            onClick={() => onTitleAction(title.id, 'discard')}
                            title="Discard this title"
                            ariaLabel="Discard"
                        >
                            <Trash2 size={11} />
                        </SvActionButton>
                        <SvActionButton
                            tone={titleAction === 'skip' ? 'neutral' : 'neutral'}
                            size="sm"
                            onClick={() => onTitleAction(title.id, 'skip')}
                            title="Skip for now"
                            ariaLabel="Skip"
                        >
                            <SkipForward size={11} />
                        </SvActionButton>
                        {/* Source toggle — switch between DiscDB and Engram when both exist */}
                        {FEATURES.DISCDB && title.discdb_match_details && title.match_details && (
                            <SvActionButton
                                tone="magenta"
                                size="sm"
                                onClick={() => onRematch(title.id, title.match_source === 'discdb' ? 'engram' : 'discdb')}
                                title={`Switch to ${title.match_source === 'discdb' ? 'Engram' : 'DiscDB'} match`}
                                ariaLabel="Toggle match source"
                            >
                                <RefreshCw size={11} />
                            </SvActionButton>
                        )}
                        {/* Re-match button — only DiscDB source, no Engram data yet */}
                        {FEATURES.DISCDB && title.match_source === 'discdb' && !title.match_details && (
                            <SvActionButton
                                tone="magenta"
                                size="sm"
                                onClick={() => onRematch(title.id, 'engram')}
                                title="Re-match with Engram audio matching"
                                ariaLabel="Re-match"
                            >
                                <RefreshCw size={11} />
                            </SvActionButton>
                        )}
                    </div>
                </div>

                {/* Expanded details */}
                {isExpanded && (
                    <div
                        style={{
                            marginTop: 16,
                            paddingTop: 12,
                            borderTop: `1px solid ${sv.line}`,
                        }}
                    >
                        {isConflict && details.message && (
                            <div style={{ marginBottom: 12 }}>
                                <SvNotice tone="warn">{details.message}</SvNotice>
                            </div>
                        )}
                        <div style={{ marginBottom: 12 }}>
                            <SvLabel>Competing matches</SvLabel>
                        </div>

                        {/* Match stats */}
                        {details.vote_count !== undefined && (
                            <div
                                style={{
                                    display: 'flex',
                                    gap: 24,
                                    marginBottom: 12,
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    color: sv.inkFaint,
                                }}
                            >
                                <span>Votes: <span style={{ color: sv.ink }}>{details.vote_count}</span></span>
                                <span>Coverage: <span style={{ color: sv.ink }}>{Math.round((details.file_cov || 0) * 100)}%</span></span>
                                <span>Gap: <span style={{ color: sv.ink }}>{details.score_gap !== undefined ? `+${Math.round(details.score_gap * 100)}%` : '—'}</span></span>
                            </div>
                        )}

                        <table style={{ width: '100%', fontFamily: sv.mono, fontSize: 11, borderCollapse: 'collapse' }}>
                            <thead>
                                <tr style={{ borderBottom: `1px solid ${sv.line}` }}>
                                    {(['Rank', 'Episode', 'Score', 'Votes', 'Assessment'] as const).map((h) => (
                                        <th
                                            key={h}
                                            style={{
                                                textAlign: 'left',
                                                padding: '6px 16px 6px 0',
                                                color: sv.inkFaint,
                                                letterSpacing: '0.18em',
                                                textTransform: 'uppercase',
                                                fontSize: 9,
                                                fontWeight: 700,
                                            }}
                                        >
                                            {h}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {title.matched_episode && (
                                    <tr style={{ borderBottom: `1px solid ${sv.line}` }}>
                                        <td style={{ padding: '6px 16px 6px 0', color: sv.green }}>1st</td>
                                        <td style={{ padding: '6px 16px 6px 0', color: sv.green, fontWeight: 700 }}>
                                            {title.matched_episode}
                                        </td>
                                        <td style={{ padding: '6px 16px 6px 0', color: sv.green }}>
                                            {Math.round(title.match_confidence * 100)}%
                                        </td>
                                        <td style={{ padding: '6px 16px 6px 0', color: sv.green }}>{details.vote_count || '?'}</td>
                                        <td style={{ padding: '6px 0' }}>
                                            <SvBadge size="sm" state="complete" dot={false}>BEST</SvBadge>
                                        </td>
                                    </tr>
                                )}
                                {alternatives.map((alt, idx) => (
                                    <tr key={idx} style={{ borderBottom: `1px solid ${sv.line}`, color: sv.inkDim }}>
                                        <td style={{ padding: '6px 16px 6px 0' }}>
                                            {idx + 2}{idx === 0 ? 'nd' : idx === 1 ? 'rd' : 'th'}
                                        </td>
                                        <td style={{ padding: '6px 16px 6px 0' }}>{alt.episode}</td>
                                        <td style={{ padding: '6px 16px 6px 0' }}>{Math.round(alt.confidence * 100)}%</td>
                                        <td style={{ padding: '6px 16px 6px 0' }}>{alt.vote_count || '?'}</td>
                                        <td style={{ padding: '6px 0' }}>
                                            <SvBadge size="sm" tone={sv.inkDim} dot={false}>
                                                {alt.confidence > MATCHING_CONFIG.MIN_CONFIDENCE ? 'POSSIBLE' : 'UNLIKELY'}
                                            </SvBadge>
                                        </td>
                                    </tr>
                                ))}
                                {!title.matched_episode && alternatives.length === 0 && (
                                    <tr>
                                        <td
                                            colSpan={5}
                                            style={{ padding: '14px 0', textAlign: 'center', color: sv.inkFaint }}
                                        >
                                            No match data available
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                )}
            </SvPanel>
        </motion.div>
    );
}

export default ReviewQueue;
