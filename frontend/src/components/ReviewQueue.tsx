import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { motion } from 'motion/react';
import { ArrowLeft, Disc3, Play, Save, Trash2, Package, SkipForward, ChevronDown, ChevronRight } from 'lucide-react';
import { Job, DiscTitle } from '../types';
import { formatDuration, formatSize, parseMatchDetails, generateEpisodeOptions, getReviewReasons } from './ReviewQueue/utils';
import { MATCHING_CONFIG, EPISODE_CONFIG } from '../config/constants';

type TitleAction = 'episode' | 'extra' | 'discard' | 'skip';

function ReviewQueue() {
    const { jobId } = useParams<{ jobId: string }>();
    const navigate = useNavigate();
    const [job, setJob] = useState<Job | null>(null);
    const [titles, setTitles] = useState<DiscTitle[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [isProcessing, setIsProcessing] = useState(false);
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
            <div className="min-h-screen bg-black flex flex-col items-center justify-center gap-4">
                <motion.div animate={{ rotate: 360 }} transition={{ duration: 2, repeat: Infinity, ease: 'linear' }}>
                    <Disc3 className="w-12 h-12 text-cyan-400" style={{ filter: 'drop-shadow(0 0 10px rgba(6, 182, 212, 0.8))' }} />
                </motion.div>
                <span className="text-cyan-400 font-mono uppercase tracking-wider text-sm">&gt; LOADING JOB DATA...</span>
            </div>
        );
    }

    if (!job) {
        return (
            <div className="min-h-screen bg-black flex flex-col items-center justify-center gap-6">
                <h2 className="text-2xl font-bold text-red-400 font-mono uppercase tracking-wider">JOB NOT FOUND</h2>
                <button
                    onClick={() => navigate('/')}
                    className="px-4 py-2 font-mono font-bold text-sm uppercase tracking-wider border-2 bg-black text-cyan-400 border-cyan-500/50 hover:border-cyan-400 transition-all"
                >
                    RETURN TO DASHBOARD
                </button>
            </div>
        );
    }

    // ==================== MOVIE REVIEW ====================
    if (job.content_type === 'movie') {
        return (
            <div className="min-h-screen bg-black relative overflow-hidden">
                {/* Grid background */}
                <div className="fixed inset-0 opacity-10 pointer-events-none">
                    <div className="h-full w-full" style={{
                        backgroundImage: 'linear-gradient(rgba(6, 182, 212, 0.3) 1px, transparent 1px), linear-gradient(90deg, rgba(6, 182, 212, 0.3) 1px, transparent 1px)',
                        backgroundSize: '50px 50px',
                    }} />
                </div>

                {/* Header */}
                <div className="border-b-2 border-cyan-500/30 backdrop-blur-xl bg-black/80 sticky top-0 z-10" style={{ boxShadow: '0 0 20px rgba(6, 182, 212, 0.2)' }}>
                    <div className="max-w-5xl mx-auto px-6 py-5">
                        <div className="flex items-center gap-4">
                            <button
                                onClick={() => navigate('/')}
                                className="px-3 py-2 font-mono font-bold text-sm uppercase tracking-wider border-2 bg-black text-slate-400 border-slate-700 hover:border-cyan-500/50 hover:text-cyan-400 transition-all"
                            >
                                <ArrowLeft className="w-4 h-4" />
                            </button>
                            <div>
                                <h1 className="text-xl font-bold text-cyan-400 font-mono uppercase tracking-wider" style={{ textShadow: '0 0 15px rgba(6, 182, 212, 0.6)' }}>
                                    SELECT MOVIE VERSION
                                </h1>
                                <p className="text-sm text-slate-400 font-mono">
                                    &gt; {job.detected_title || job.volume_label}
                                </p>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Content */}
                <div className="max-w-5xl mx-auto px-6 py-8 relative z-0">
                    {error && (
                        <div className="mb-6 px-4 py-3 border-2 border-red-500/50 bg-red-500/10 text-red-400 font-mono text-sm">
                            &gt; ERROR: {error}
                        </div>
                    )}

                    <div className="mb-6 px-4 py-3 border-2 border-yellow-500/30 bg-yellow-500/5 text-yellow-400 font-mono text-sm">
                        &gt; MULTIPLE FEATURE-LENGTH TITLES DETECTED. SELECT THE CORRECT VERSION TO KEEP.
                    </div>

                    <div className="space-y-4">
                        {titles.map(title => (
                            <motion.div
                                key={title.id}
                                initial={{ opacity: 0, y: 10 }}
                                animate={{ opacity: 1, y: 0 }}
                                className="border-2 border-cyan-500/20 bg-black/80 overflow-hidden"
                                style={{ boxShadow: '0 0 10px rgba(6, 182, 212, 0.1)' }}
                            >
                                <div className="p-5 flex items-center gap-6">
                                    {/* Title info */}
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-3 mb-2">
                                            <span className="text-xs font-mono text-slate-500 bg-slate-800 px-2 py-0.5">
                                                #{title.title_index}
                                            </span>
                                            <span className="text-sm font-mono text-cyan-300 truncate">
                                                {title.output_filename ? title.output_filename.split(/[/\\]/).pop() : `Title ${title.title_index}`}
                                            </span>
                                        </div>
                                        <div className="flex items-center gap-6 text-xs font-mono text-slate-500">
                                            <span>{formatDuration(title.duration_seconds)}</span>
                                            <span>{formatSize(title.file_size_bytes)}</span>
                                            <span className="px-2 py-0.5 border border-slate-700 text-slate-400">
                                                {title.video_resolution || 'Unknown'}
                                            </span>
                                            <span>{title.chapter_count} chapters</span>
                                        </div>
                                    </div>

                                    {/* Edition input */}
                                    <div className="w-48">
                                        <input
                                            type="text"
                                            placeholder="Edition tag..."
                                            list="edition-suggestions"
                                            value={selectedEditions[title.id] || ''}
                                            onChange={(e) => handleEditionChange(title.id, e.target.value)}
                                            className="w-full px-3 py-1.5 text-sm font-mono bg-black border border-slate-700 text-slate-300 placeholder-slate-600 focus:border-cyan-500/50 focus:outline-none"
                                        />
                                    </div>

                                    {/* Actions */}
                                    <div className="flex gap-2">
                                        <button
                                            onClick={() => handleSaveMovie(title.id, 'save')}
                                            disabled={isSaving}
                                            className="px-4 py-2 font-mono font-bold text-xs uppercase tracking-wider border-2 bg-black text-green-400 border-green-500/50 hover:border-green-400 hover:shadow-[0_0_15px_rgba(34,197,94,0.3)] transition-all disabled:opacity-50"
                                        >
                                            SELECT
                                        </button>
                                        <button
                                            onClick={() => handleSaveMovie(title.id, 'skip')}
                                            disabled={isSaving}
                                            className="px-4 py-2 font-mono font-bold text-xs uppercase tracking-wider border-2 bg-black text-red-400 border-red-500/50 hover:border-red-400 transition-all disabled:opacity-50"
                                        >
                                            DISCARD
                                        </button>
                                    </div>
                                </div>
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
            </div>
        );
    }

    // ==================== TV REVIEW ====================
    return (
        <div className="min-h-screen bg-black relative overflow-hidden">
            {/* Grid background */}
            <div className="fixed inset-0 opacity-10 pointer-events-none">
                <div className="h-full w-full" style={{
                    backgroundImage: 'linear-gradient(rgba(6, 182, 212, 0.3) 1px, transparent 1px), linear-gradient(90deg, rgba(6, 182, 212, 0.3) 1px, transparent 1px)',
                    backgroundSize: '50px 50px',
                }} />
            </div>

            {/* Header */}
            <div className="border-b-2 border-cyan-500/30 backdrop-blur-xl bg-black/80 sticky top-0 z-10" style={{ boxShadow: '0 0 20px rgba(6, 182, 212, 0.2)' }}>
                <div className="max-w-6xl mx-auto px-6 py-5">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-4">
                            <button
                                onClick={() => navigate('/')}
                                className="px-3 py-2 font-mono font-bold text-sm uppercase tracking-wider border-2 bg-black text-slate-400 border-slate-700 hover:border-cyan-500/50 hover:text-cyan-400 transition-all"
                            >
                                <ArrowLeft className="w-4 h-4" />
                            </button>
                            <div>
                                <h1 className="text-xl font-bold text-cyan-400 font-mono uppercase tracking-wider" style={{ textShadow: '0 0 15px rgba(6, 182, 212, 0.6)' }}>
                                    REVIEW TITLES
                                </h1>
                                <p className="text-sm text-slate-400 font-mono">
                                    &gt; {job.detected_title || job.volume_label}
                                    {job.detected_season && ` / SEASON ${job.detected_season}`}
                                </p>
                            </div>
                        </div>

                        {/* Action buttons */}
                        <div className="flex items-center gap-3">
                            <button
                                onClick={handleStartRip}
                                disabled={isSaving || isProcessing}
                                className="px-4 py-2 font-mono font-bold text-xs uppercase tracking-wider border-2 bg-black text-cyan-400 border-cyan-500/50 hover:border-cyan-400 hover:shadow-[0_0_15px_rgba(6,182,212,0.3)] transition-all flex items-center gap-2 disabled:opacity-50"
                            >
                                <Play className="w-3 h-3" />
                                START RIP
                            </button>
                            {assignedCount > 0 && (
                                <button
                                    onClick={handleSaveAll}
                                    disabled={isSaving || isProcessing}
                                    className="px-4 py-2 font-mono font-bold text-xs uppercase tracking-wider border-2 bg-black text-yellow-400 border-yellow-500/50 hover:border-yellow-400 hover:shadow-[0_0_15px_rgba(250,204,21,0.3)] transition-all flex items-center gap-2 disabled:opacity-50"
                                >
                                    <Save className="w-3 h-3" />
                                    {isSaving ? 'SAVING...' : `SAVE ${assignedCount} ASSIGNMENTS`}
                                </button>
                            )}
                            {assignedCount > 0 && (
                                <button
                                    onClick={handleProcessMatched}
                                    disabled={isSaving || isProcessing}
                                    className="px-4 py-2 font-mono font-bold text-xs uppercase tracking-wider border-2 bg-black text-green-400 border-green-500/50 hover:border-green-400 hover:shadow-[0_0_15px_rgba(34,197,94,0.3)] transition-all flex items-center gap-2 disabled:opacity-50"
                                >
                                    <Package className="w-3 h-3" />
                                    {isProcessing ? 'PROCESSING...' : `PROCESS ${assignedCount} MATCHED`}
                                </button>
                            )}
                        </div>
                    </div>
                </div>
            </div>

            {/* Content */}
            <div className="max-w-6xl mx-auto px-6 py-8 relative z-0 pb-24">
                {error && (
                    <div className="mb-6 px-4 py-3 border-2 border-red-500/50 bg-red-500/10 text-red-400 font-mono text-sm">
                        &gt; ERROR: {error}
                    </div>
                )}

                {job.error_message && (
                    <div className="mb-6 px-4 py-3 border-2 border-yellow-500/30 bg-yellow-500/5 text-yellow-400 font-mono text-sm">
                        &gt; {job.error_message}
                    </div>
                )}

                {job.subtitle_status === 'failed' && !job.error_message?.includes('Subtitle') && (
                    <div className="mb-6 px-4 py-3 border-2 border-yellow-500/30 bg-yellow-500/5 text-yellow-400 font-mono text-sm">
                        &gt; SUBTITLE DOWNLOAD FAILED. MANUAL FETCH MAY BE REQUIRED.
                    </div>
                )}

                {/* Matched Section */}
                {matchedTitles.length > 0 && (
                    <div className="mb-8">
                        <div className="flex items-center gap-3 mb-4">
                            <div className="w-2 h-2 bg-green-400" style={{ boxShadow: '0 0 8px rgba(34, 197, 94, 0.8)' }} />
                            <h2 className="text-sm font-bold text-green-400 font-mono uppercase tracking-wider">
                                AUTO-MATCHED [{matchedTitles.length}]
                            </h2>
                        </div>
                        <div className="space-y-2">
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
                                    variant="matched"
                                />
                            ))}
                        </div>
                    </div>
                )}

                {/* Needs Review Section */}
                {needsReviewTitles.length > 0 && (
                    <div className="mb-8">
                        <div className="flex items-center gap-3 mb-4">
                            <div className="w-2 h-2 bg-yellow-400" style={{ boxShadow: '0 0 8px rgba(250, 204, 21, 0.8)' }} />
                            <h2 className="text-sm font-bold text-yellow-400 font-mono uppercase tracking-wider">
                                NEEDS REVIEW [{needsReviewTitles.length}]
                            </h2>
                        </div>
                        <div className="space-y-2">
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
                                    variant="review"
                                />
                            ))}
                        </div>
                    </div>
                )}

                {/* Completed Section */}
                {completedTitles.length > 0 && (
                    <div className="mb-8">
                        <div className="flex items-center gap-3 mb-4">
                            <div className="w-2 h-2 bg-slate-500" />
                            <h2 className="text-sm font-bold text-slate-500 font-mono uppercase tracking-wider">
                                PROCESSED [{completedTitles.length}]
                            </h2>
                        </div>
                        <div className="space-y-2 opacity-50">
                            {completedTitles.map(title => (
                                <div key={title.id} className="border border-slate-800 bg-black/50 p-4 flex items-center gap-6 font-mono text-xs text-slate-600">
                                    <span className="bg-slate-900 px-2 py-0.5">#{title.title_index}</span>
                                    <span className="flex-1 truncate">{title.output_filename?.split(/[/\\]/).pop() || `Title ${title.title_index}`}</span>
                                    <span>{formatDuration(title.duration_seconds)}</span>
                                    <span>{title.matched_episode || '—'}</span>
                                    <span className={title.state === 'completed' ? 'text-green-600' : 'text-red-600'}>{title.state.toUpperCase()}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
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
    variant,
}: TVTitleRowProps) {
    const details = parseMatchDetails(title);
    const isConflict = details.error === 'file_exists';
    const reasons = getReviewReasons(title);
    const alternatives = details.runner_ups || [];

    const borderColor = isConflict
        ? 'border-yellow-500/40'
        : variant === 'matched'
        ? 'border-green-500/20'
        : 'border-cyan-500/20';

    return (
        <motion.div
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            className={`border-2 ${borderColor} bg-black/80 overflow-hidden`}
        >
            <div className="p-4">
                <div className="flex items-center gap-4">
                    {/* Expand button */}
                    <button
                        onClick={() => onToggleExpand(title.id)}
                        className="text-slate-600 hover:text-cyan-400 transition-colors"
                    >
                        {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                    </button>

                    {/* Title index */}
                    <span className="text-xs font-mono text-slate-500 bg-slate-800 px-2 py-0.5 flex-shrink-0">
                        #{title.title_index}
                    </span>

                    {/* Title name + conflict indicator */}
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-3">
                            <span className="text-sm font-mono text-cyan-300 truncate">
                                {title.output_filename ? title.output_filename.split(/[/\\]/).pop() : `Title ${title.title_index}`}
                            </span>
                            {isConflict && (
                                <span className="text-xs font-mono text-yellow-400 bg-yellow-500/10 px-2 py-0.5 border border-yellow-500/30 flex-shrink-0">
                                    FILE EXISTS
                                </span>
                            )}
                        </div>
                        <div className="flex items-center gap-4 mt-1 text-xs font-mono text-slate-600">
                            <span>{formatDuration(title.duration_seconds)}</span>
                            <span>{formatSize(title.file_size_bytes)}</span>
                        </div>
                    </div>

                    {/* Confidence badge */}
                    <div className="flex-shrink-0">
                        {title.match_confidence > 0 ? (
                            <span className={`text-xs font-mono font-bold px-2 py-1 ${
                                title.match_confidence >= MATCHING_CONFIG.AUTO_MATCH_THRESHOLD
                                    ? 'text-green-400 bg-green-500/10 border border-green-500/30'
                                    : title.match_confidence >= MATCHING_CONFIG.MIN_CONFIDENCE
                                    ? 'text-yellow-400 bg-yellow-500/10 border border-yellow-500/30'
                                    : 'text-red-400 bg-red-500/10 border border-red-500/30'
                            }`}>
                                {Math.round(title.match_confidence * 100)}%
                            </span>
                        ) : (
                            <span className="text-xs font-mono text-slate-600 bg-slate-800 px-2 py-1">&mdash;</span>
                        )}
                    </div>

                    {/* Review reasons */}
                    {reasons.length > 0 && (
                        <div className="flex gap-1 flex-shrink-0">
                            {reasons.slice(0, 2).map((r, i) => (
                                <span key={i} className="text-[10px] font-mono text-orange-400 bg-orange-500/10 px-1.5 py-0.5 border border-orange-500/20">
                                    {r}
                                </span>
                            ))}
                        </div>
                    )}

                    {/* Episode selector */}
                    <div className="w-44 flex-shrink-0">
                        <select
                            value={selectedEpisode}
                            onChange={(e) => onEpisodeChange(title.id, e.target.value)}
                            className="w-full px-2 py-1.5 text-xs font-mono bg-black border border-slate-700 text-slate-300 focus:border-cyan-500/50 focus:outline-none"
                        >
                            <option value="">Select episode...</option>
                            {title.matched_episode && (
                                <option value={title.matched_episode}>
                                    {title.matched_episode} - Best ({Math.round(title.match_confidence * 100)}%)
                                </option>
                            )}
                            {alternatives.map((alt, idx) => (
                                <option key={`alt-${idx}`} value={alt.episode}>
                                    {alt.episode} - Alt ({Math.round(alt.confidence * 100)}%)
                                </option>
                            ))}
                            {(title.matched_episode || alternatives.length > 0) && (
                                <option disabled>{'─'.repeat(20)}</option>
                            )}
                            {generateEpisodeOptions(job.detected_season || 1, EPISODE_CONFIG.DEFAULT_EPISODES_PER_SEASON).map(ep => (
                                <option key={ep} value={ep}>{ep}</option>
                            ))}
                        </select>
                    </div>

                    {/* Action buttons */}
                    <div className="flex gap-1 flex-shrink-0">
                        <button
                            onClick={() => onTitleAction(title.id, 'extra')}
                            className={`px-2 py-1.5 text-[10px] font-mono font-bold uppercase tracking-wider border transition-all ${
                                titleAction === 'extra'
                                    ? 'text-cyan-400 border-cyan-500 bg-cyan-500/10'
                                    : 'text-slate-500 border-slate-700 hover:text-cyan-400 hover:border-cyan-500/50'
                            }`}
                            title="Keep as extra content"
                        >
                            EXTRA
                        </button>
                        <button
                            onClick={() => onTitleAction(title.id, 'discard')}
                            className={`px-2 py-1.5 text-[10px] font-mono font-bold uppercase tracking-wider border transition-all ${
                                titleAction === 'discard'
                                    ? 'text-red-400 border-red-500 bg-red-500/10'
                                    : 'text-slate-500 border-slate-700 hover:text-red-400 hover:border-red-500/50'
                            }`}
                            title="Discard this title"
                        >
                            <Trash2 className="w-3 h-3" />
                        </button>
                        <button
                            onClick={() => onTitleAction(title.id, 'skip')}
                            className={`px-2 py-1.5 text-[10px] font-mono font-bold uppercase tracking-wider border transition-all ${
                                titleAction === 'skip'
                                    ? 'text-slate-300 border-slate-500 bg-slate-500/10'
                                    : 'text-slate-500 border-slate-700 hover:text-slate-300 hover:border-slate-500'
                            }`}
                            title="Skip for now"
                        >
                            <SkipForward className="w-3 h-3" />
                        </button>
                    </div>
                </div>
            </div>

            {/* Expanded details */}
            {isExpanded && (
                <div className="border-t border-slate-800 bg-slate-900/50 px-4 py-3">
                    <h4 className="text-xs font-mono text-slate-500 uppercase tracking-wider mb-3">&gt; COMPETING MATCHES</h4>

                    {/* Match stats */}
                    {details.vote_count !== undefined && (
                        <div className="flex gap-6 mb-3 text-xs font-mono">
                            <span className="text-slate-500">Votes: <span className="text-slate-300">{details.vote_count}</span></span>
                            <span className="text-slate-500">Coverage: <span className="text-slate-300">{Math.round((details.file_cov || 0) * 100)}%</span></span>
                            <span className="text-slate-500">Gap: <span className="text-slate-300">{details.score_gap !== undefined ? `+${Math.round(details.score_gap * 100)}%` : '—'}</span></span>
                        </div>
                    )}

                    <table className="w-full text-xs font-mono">
                        <thead>
                            <tr className="text-slate-600 border-b border-slate-800">
                                <th className="text-left py-1.5 pr-4">RANK</th>
                                <th className="text-left py-1.5 pr-4">EPISODE</th>
                                <th className="text-left py-1.5 pr-4">SCORE</th>
                                <th className="text-left py-1.5 pr-4">VOTES</th>
                                <th className="text-left py-1.5">ASSESSMENT</th>
                            </tr>
                        </thead>
                        <tbody>
                            {title.matched_episode && (
                                <tr className="text-green-400/80 border-b border-slate-800/50">
                                    <td className="py-1.5 pr-4">1st</td>
                                    <td className="py-1.5 pr-4 font-bold">{title.matched_episode}</td>
                                    <td className="py-1.5 pr-4">{Math.round(title.match_confidence * 100)}%</td>
                                    <td className="py-1.5 pr-4">{details.vote_count || '?'}</td>
                                    <td className="py-1.5">
                                        <span className="text-green-400 bg-green-500/10 px-1.5 py-0.5 border border-green-500/20">BEST</span>
                                    </td>
                                </tr>
                            )}
                            {alternatives.map((alt, idx) => (
                                <tr key={idx} className="text-slate-500 border-b border-slate-800/30">
                                    <td className="py-1.5 pr-4">{idx + 2}{idx === 0 ? 'nd' : idx === 1 ? 'rd' : 'th'}</td>
                                    <td className="py-1.5 pr-4">{alt.episode}</td>
                                    <td className="py-1.5 pr-4">{Math.round(alt.confidence * 100)}%</td>
                                    <td className="py-1.5 pr-4">{alt.vote_count || '?'}</td>
                                    <td className="py-1.5">
                                        <span className="text-slate-500 bg-slate-800 px-1.5 py-0.5 border border-slate-700">
                                            {alt.confidence > MATCHING_CONFIG.MIN_CONFIDENCE ? 'POSSIBLE' : 'UNLIKELY'}
                                        </span>
                                    </td>
                                </tr>
                            ))}
                            {!title.matched_episode && alternatives.length === 0 && (
                                <tr>
                                    <td colSpan={5} className="py-3 text-slate-600 text-center">NO MATCH DATA AVAILABLE</td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            )}
        </motion.div>
    );
}

export default ReviewQueue;
