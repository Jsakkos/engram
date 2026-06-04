import { useState, useEffect, useMemo, useRef } from 'react';
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
import { useWebSocket } from '../hooks/useWebSocket';
import { assignmentsByCode, buildCandidates, collidingCodes, computeCoverage, normalizeEpisodeCode, suggestGapCode } from './ReviewQueue/coverage';
import { SeasonRosterStrip } from './ReviewQueue/SeasonRosterStrip';
import { OrderingSelector } from './ReviewQueue/OrderingSelector';
import { TitleList } from './ReviewQueue/TitleList';
import { Inspector } from './ReviewQueue/Inspector';
import { runLLMMatch, reassignEpisode, setShowOrdering, submitReviewBatch, rematchTitle } from '../api/client';

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
    const [rematchNotice, setRematchNotice] = useState<string | null>(null);
    const [orderingError, setOrderingError] = useState<string | null>(null);
    const [aiEpisodeMatchingEnabled, setAiEpisodeMatchingEnabled] = useState(false);

    // Bulk multiselect — ids checked for bulk actions (independent of the
    // single inspected title). `lastBulkClickRef` anchors shift-click ranges.
    const [bulkSelectedIds, setBulkSelectedIds] = useState<Set<number>>(new Set());
    const lastBulkClickRef = useRef<number | null>(null);

    const { roster, error: rosterError, episodeName, reload: reloadRoster } = useSeasonRoster(jobId);

    // Persist a per-show ordering choice (#200), then refetch the roster so the
    // projection/divergence reflect it. Ordering is a show property, so it is
    // stored by tmdb_id rather than threaded through the review-batch decision.
    const handleOrderingChange = async (ordering: string) => {
        if (!roster?.show_id) return;
        setOrderingError(null);
        try {
            await setShowOrdering(roster.show_id, ordering);
            reloadRoster();
        } catch (e) {
            console.error('Failed to set show ordering', e);
            setOrderingError(
                'Could not save the ordering preference — the selection was not applied. Please try again.',
            );
        }
    };

    useEffect(() => {
        fetchJobDetails();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [jobId]);

    // Live title updates. The page holds a local `titles` snapshot fetched on
    // mount, so without this a background (advisory) re-match's progress never
    // reaches the UI: the in-flight indicator would stick on 'matching' forever
    // and a file-exists conflict raised during finalize would stay invisible.
    // Merge title_update messages for THIS job into `titles` — state/match fields
    // only, never the user's in-progress episode selections.
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
    const { addMessageListener } = useWebSocket(wsUrl);

    useEffect(() => {
        const numericJobId = jobId ? parseInt(jobId) : null;
        if (numericJobId === null || Number.isNaN(numericJobId)) return;
        return addMessageListener((msg) => {
            if (msg.type !== 'title_update' || msg.job_id !== numericJobId) return;
            setTitles((prev) =>
                prev.map((t) =>
                    t.id === msg.title_id
                        ? {
                              ...t,
                              state: msg.state,
                              matched_episode: msg.matched_episode ?? t.matched_episode,
                              match_confidence: msg.match_confidence ?? t.match_confidence,
                              match_stage: msg.match_stage ?? t.match_stage,
                              match_progress: msg.match_progress ?? t.match_progress,
                              match_details: msg.match_details ?? t.match_details,
                              error_message: msg.error ?? t.error_message,
                          }
                        : t,
                ),
            );
        });
    }, [jobId, addMessageListener]);

    // Fetch config once on mount to know whether AI matching is enabled.
    useEffect(() => {
        fetch('/api/config')
            .then((r) => r.ok ? r.json() : null)
            .then((data) => {
                if (data?.ai_episode_matching_enabled) {
                    setAiEpisodeMatchingEnabled(true);
                }
            })
            .catch(() => {/* non-critical */});
    }, []);

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

    const handleRematch = async (
        titleId: number,
        sourcePreference: string = 'engram',
        deep: boolean = false,
    ) => {
        if (!jobId) return;
        setIsRematching(true);
        setError(null);
        try {
            await rematchTitle(parseInt(jobId), titleId, sourcePreference, deep);
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
            // The job moves to MATCHING; the dashboard's live view shows progress.
            navigate('/');
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
        setRematchNotice(null);
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
            const data = await response.json();
            const skipped: Array<{ title_id: number }> = data.skipped || [];
            if (skipped.length > 0) {
                const labels = skipped
                    .map((s) => {
                        const t = titles.find((x) => x.id === s.title_id);
                        return `#${t ? t.title_index : s.title_id}`;
                    })
                    .join(', ');
                setRematchNotice(
                    `Re-matched ${data.title_ids?.length ?? 0} title(s); skipped ${labels} (already organized or file not in staging).`,
                );
            }
            await fetchJobDetails();
        } catch (err) {
            console.error('Failed to deep re-match conflict:', err);
            setError(err instanceof Error ? err.message : 'Failed to deep re-match');
        } finally {
            setIsRematching(false);
        }
    };

    // Run the LLM matcher for a single title, then refresh so the persisted
    // llm_suggestion in match_details surfaces in the Inspector.
    const handleTryLLMMatch = async (titleId: number) => {
        if (!jobId) return;
        setError(null);
        try {
            await runLLMMatch(parseInt(jobId), titleId);
            await fetchJobDetails();
        } catch (err) {
            console.error('LLM match failed', err);
            setError(err instanceof Error ? err.message : 'AI match failed');
        }
    };

    // Accept an LLM suggestion: reassign the episode tagged as 'ai_llm' source,
    // then refresh the title list so the Inspector reflects the new assignment.
    const handleAcceptLLMSuggestion = async (titleId: number, episodeNumber: number) => {
        if (!jobId) return;
        const seasonNum = job?.detected_season ?? 1;
        const seasonStr = String(seasonNum).padStart(2, '0');
        const epStr = String(episodeNumber).padStart(2, '0');
        const code = `S${seasonStr}E${epStr}`;
        setError(null);
        try {
            await reassignEpisode(parseInt(jobId), titleId, code, undefined, 'ai_llm');
            handleEpisodeChange(titleId, code);
            await fetchJobDetails();
        } catch (err) {
            console.error('Accept LLM suggestion failed', err);
            setError(err instanceof Error ? err.message : 'Failed to accept AI suggestion');
        }
    };

    // Commit every pending episode selection in one atomic batch request.
    // Throws on failure so callers can abort their follow-up step. A single
    // finalize pass keeps many extras from colliding on FILE_EXISTS.
    const submitPendingSelections = async () => {
        if (!jobId) return;
        const decisions = Object.entries(selectedEpisodes).map(([titleId, episodeCode]) => {
            const id = parseInt(titleId);
            return {
                title_id: id,
                episode_code: episodeCode,
                ...(selectedEditions[id] ? { edition: selectedEditions[id] } : {}),
            };
        });
        if (decisions.length === 0) return;
        await submitReviewBatch(parseInt(jobId), decisions);
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

    // Re-fetch the job to inspect the state a preceding /review may have left it in.
    const fetchCurrentJob = async (): Promise<Job | null> => {
        try {
            const res = await fetch(`/api/jobs/${jobId}`);
            if (!res.ok) return null;
            return (await res.json()) as Job;
        } catch (e) {
            console.warn('[handleProcessMatched] job re-fetch failed:', e);
            return null;
        }
    };

    const handleProcessMatched = async () => {
        setIsProcessing(true);
        setError(null);
        try {
            // Submit pending selections first. Resolving the last unresolved title
            // makes the backend finalize the job inline, so it may already be
            // organizing/completed/failed by the time process-matched runs.
            await submitPendingSelections();
            // Then process any remaining matched titles.
            const response = await fetch(`/api/jobs/${jobId}/process-matched`, { method: 'POST' });
            if (!response.ok) {
                const text = await response.text();
                // A preceding /review may have already finalized the job (older
                // backends returned 400 here). Decide based on the job's real state.
                if (response.status === 400) {
                    const current = await fetchCurrentJob();
                    if (current?.state === 'completed' || current?.state === 'organizing') {
                        navigate('/');
                        return;
                    }
                    if (current?.state === 'failed') {
                        // The inline finalize organized but the move failed — surface
                        // why instead of the cryptic "not awaiting review" text.
                        setError(
                            current.error_message
                                ? `Job failed during organization: ${current.error_message}`
                                : 'Job failed during organization.',
                        );
                        return;
                    }
                }
                throw new Error(`Processing failed: ${text}`);
            }
            const result = await response.json();
            // Navigate home once nothing remains to resolve. Every success response
            // (processed / already_finalized / organizing) reports unresolved: 0 when
            // the job has left review, so this single check covers them all — and if a
            // response ever reports unresolved > 0, we stay and refresh rather than
            // silently skipping the remaining titles.
            if (result.unresolved === 0) {
                navigate('/');
            } else {
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

    // --- Bulk multiselect helpers ---
    const bulkCount = bulkSelectedIds.size;
    const allActiveSelected =
        activeTitles.length > 0 && activeTitles.every((t) => bulkSelectedIds.has(t.id));

    const clearBulkSelection = () => {
        setBulkSelectedIds(new Set());
        lastBulkClickRef.current = null;
    };

    // Toggle one row; shift-click selects the contiguous range from the last
    // clicked row (matching common list-selection UIs).
    const handleBulkToggle = (titleId: number, shiftKey: boolean) => {
        const ids = activeTitles.map((t) => t.id);
        if (shiftKey && lastBulkClickRef.current !== null) {
            const a = ids.indexOf(lastBulkClickRef.current);
            const b = ids.indexOf(titleId);
            if (a !== -1 && b !== -1) {
                const [lo, hi] = a < b ? [a, b] : [b, a];
                setBulkSelectedIds((prev) => {
                    const next = new Set(prev);
                    for (const id of ids.slice(lo, hi + 1)) next.add(id);
                    return next;
                });
                lastBulkClickRef.current = titleId;
                return;
            }
        }
        setBulkSelectedIds((prev) => {
            const next = new Set(prev);
            if (next.has(titleId)) next.delete(titleId);
            else next.add(titleId);
            return next;
        });
        lastBulkClickRef.current = titleId;
    };

    const toggleSelectAll = () => {
        if (allActiveSelected) clearBulkSelection();
        else setBulkSelectedIds(new Set(activeTitles.map((t) => t.id)));
    };

    // Stage one decision for every checked row, reusing the single-title path
    // so coverage/collision indicators update live, then clear the selection.
    const applyBulkAction = (action: TitleAction) => {
        bulkSelectedIds.forEach((id) => handleTitleAction(id, action));
        clearBulkSelection();
    };

    // Re-match only the checked titles, looping the existing per-title endpoint
    // and refreshing once at the end.
    const handleBulkRematch = async () => {
        if (!jobId) return;
        const ids = Array.from(bulkSelectedIds);
        if (ids.length === 0) return;
        setIsRematching(true);
        setError(null);
        try {
            for (const id of ids) {
                await rematchTitle(parseInt(jobId), id, 'engram', false);
            }
            clearBulkSelection();
            await fetchJobDetails();
        } catch (err) {
            console.error('Failed to bulk re-match:', err);
            setError(err instanceof Error ? err.message : 'Failed to re-match selected');
        } finally {
            setIsRematching(false);
        }
    };

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
                {/* Subtitle failure and any other job error are independent now that
                    subtitle detail lives on its own field — both can surface. */}
                {job.subtitle_status === 'failed' && (
                    <SvNotice tone="error">
                        › {job.subtitle_error_message ||
                            "NO REFERENCE SUBTITLES FOUND — EPISODE MATCHING CAN'T RUN. ASSIGN EPISODES MANUALLY BELOW."}
                    </SvNotice>
                )}
                {job.error_message && <SvNotice tone="warn">› {job.error_message}</SvNotice>}
                {hasConflicts && (
                    <SvNotice tone="warn">
                        › {collisions.size} EPISODE CONFLICT{collisions.size > 1 ? 'S' : ''} — TWO TITLES SHARE AN EPISODE. RESOLVE BEFORE SAVING.
                    </SvNotice>
                )}
                {rosterError && (
                    <SvNotice tone="warn">
                        › SEASON ROSTER UNAVAILABLE — RELOAD TO RETRY.
                    </SvNotice>
                )}
                {rematchNotice && <SvNotice tone="warn">› {rematchNotice}</SvNotice>}
                {orderingError && <SvNotice tone="warn">› {orderingError}</SvNotice>}

                {/* Episode ordering (#200) — only when a divergent ordering exists. */}
                {roster?.ordering_available && roster?.ordering_diverges && roster.ordering_options && (
                    <div style={{ marginBottom: 24 }}>
                        <div style={{ marginBottom: 12 }}>
                            <SvLabel>Episode ordering — this show is numbered differently across releases</SvLabel>
                        </div>
                        <OrderingSelector
                            options={roster.ordering_options}
                            current={roster.current_ordering ?? 'aired'}
                            onChange={handleOrderingChange}
                        />
                    </div>
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
                        <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
                            <input
                                type="checkbox"
                                checked={allActiveSelected}
                                ref={(el) => {
                                    if (el) el.indeterminate = bulkCount > 0 && !allActiveSelected;
                                }}
                                onChange={toggleSelectAll}
                                disabled={activeTitles.length === 0}
                                style={{ width: 15, height: 15, accentColor: sv.cyan, cursor: 'pointer' }}
                                aria-label="Select all titles"
                                title="Select all titles"
                            />
                            <SvLabel>Titles [{activeTitles.length}] — check rows for bulk actions</SvLabel>
                        </div>

                        {/* Inline bulk-action bar — appears above the list while a
                            selection is active. Staged actions update coverage live;
                            commit with the header Save. */}
                        {bulkCount > 0 && (
                            <div
                                style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    flexWrap: 'wrap',
                                    gap: 8,
                                    padding: '10px 12px',
                                    marginBottom: 10,
                                    border: `1px solid ${sv.lineHi}`,
                                    background: 'rgba(94,234,212,0.06)',
                                    boxShadow: `0 0 18px ${sv.cyan}1a`,
                                }}
                            >
                                <span
                                    style={{
                                        fontFamily: sv.mono,
                                        fontSize: 11,
                                        letterSpacing: '0.1em',
                                        textTransform: 'uppercase',
                                        color: sv.cyanHi,
                                    }}
                                >
                                    {bulkCount} selected
                                </span>
                                <span style={{ width: 1, height: 18, background: sv.line }} />
                                <SvActionButton tone="cyan" size="sm" onClick={() => applyBulkAction('extra')} title="Mark all selected as extras">
                                    Mark as Extra
                                </SvActionButton>
                                <SvActionButton tone="red" size="sm" onClick={() => applyBulkAction('discard')} title="Discard all selected">
                                    Discard
                                </SvActionButton>
                                <SvActionButton tone="neutral" size="sm" onClick={() => applyBulkAction('skip')} title="Clear decisions — leave selected unresolved">
                                    Skip
                                </SvActionButton>
                                <SvActionButton
                                    tone="magenta"
                                    size="sm"
                                    onClick={handleBulkRematch}
                                    disabled={isRematching}
                                    title="Re-run matching for selected titles"
                                >
                                    {isRematching ? 'Re-matching…' : 'Re-Match'}
                                </SvActionButton>
                                <span style={{ width: 1, height: 18, background: sv.line }} />
                                <SvActionButton tone="neutral" size="sm" onClick={clearBulkSelection} title="Clear selection">
                                    Clear
                                </SvActionButton>
                            </div>
                        )}

                        <TitleList
                            titles={activeTitles}
                            selectedTitleId={selectedTitle?.id ?? null}
                            selections={selectedEpisodes}
                            collisions={collisions}
                            episodeName={episodeName}
                            onSelect={setSelectedTitleId}
                            selectedIds={bulkSelectedIds}
                            onToggleSelect={handleBulkToggle}
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
                                aiEpisodeMatchingEnabled={aiEpisodeMatchingEnabled}
                                onAssign={(code) => handleEpisodeChange(selectedTitle.id, code)}
                                onAction={(a) => handleTitleAction(selectedTitle.id, a)}
                                onRematch={handleRematch}
                                onDeepRematch={handleRematchConflict}
                                onTryLLMMatch={handleTryLLMMatch}
                                onAcceptLLMSuggestion={handleAcceptLLMSuggestion}
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
