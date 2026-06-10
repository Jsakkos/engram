/**
 * Job management hook with WebSocket integration
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import { useWebSocket } from '../../hooks/useWebSocket';
import { apiFetch, apiFetchVoid } from '../../api/client';
import type { Job, DiscTitle, WebSocketMessage, UpdateStatus, UpdateStatusMessage } from '../../types';

// Trailing-debounce window for refetches triggered by a burst of unknown
// `job_update` messages, so we issue one fetch instead of N.
const UNKNOWN_JOB_REFETCH_DEBOUNCE_MS = 400;

// Title state ordering used when merging REST snapshots with WebSocket-derived
// state. WebSocket state (e.g. "ripping") is more current than a stale REST
// snapshot still reporting "pending".
const STATE_PRIORITY: Record<string, number> = {
    pending: 0,
    ripping: 1,
    matching: 2,
    matched: 3,
    review: 3,
    completed: 4,
    failed: 4,
};

// Title states that indicate a title has finished processing.
const TERMINAL_TITLE_STATES = ['matched', 'completed', 'review', 'failed'];

/**
 * Normalize an update-status payload into the UpdateStatus snapshot stored in
 * state. Shared by BOTH the WebSocket `update_status` handler and the REST seed
 * so the two channels can never disagree on a field again — the original bug was
 * that the WS push dropped `is_frozen`, so it silently defaulted to `false` and
 * hid the "Restart now" button on frozen builds.
 */
function toUpdateStatus(raw: Omit<UpdateStatusMessage, 'type'>): UpdateStatus {
    return {
        state: raw.state,
        current_version: raw.current_version,
        latest_version: raw.latest_version ?? null,
        release_notes: raw.release_notes ?? null,
        release_url: raw.release_url ?? null,
        download_progress: raw.download_progress ?? null,
        error: raw.error ?? null,
        last_update_error: raw.last_update_error ?? null,
        last_update_success_version: raw.last_update_success_version ?? null,
        is_frozen: raw.is_frozen ?? false,
    };
}

export function useJobManagement(devMode: boolean = false) {
    const [jobs, setJobs] = useState<Job[]>([]);
    const [titlesMap, setTitlesMap] = useState<Record<number, DiscTitle[]>>({});
    const [updateStatus, setUpdateStatus] = useState<import('../../types').UpdateStatus | null>(null);
    const [disclosure, setDisclosure] = useState<import('../../types').FingerprintDisclosureRequiredMessage | null>(null);

    // Use WebSocket URL that works with Vite proxy
    // When running on localhost:5173, connects to ws://localhost:5173/ws (proxied to backend)
    // In production, uses the same host as the frontend
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;

    // Stable ref to fetchJobsAndTitles so the listener/onOpen closures don't go stale
    const fetchRef = useRef<() => Promise<void>>();
    // Trailing-debounce timer for unknown-job refetches.
    const debouncedRefetchRef = useRef<number | null>(null);
    // Guards against the very first onOpen (initial connect) double-fetching,
    // since the mount effect already performs the initial load.
    const initialConnectRef = useRef(true);

    // Merge a job's REST titles snapshot with any newer WebSocket-derived state.
    const mergeTitles = useCallback((jobId: number, titlesData: DiscTitle[]) => {
        setTitlesMap(prev => {
            const existing = prev[jobId];
            if (!existing) {
                return { ...prev, [jobId]: titlesData };
            }
            // Merge: for each title, keep the more-recent state.
            const merged = titlesData.map(restTitle => {
                const wsTitle = existing.find(t => t.id === restTitle.id);
                if (!wsTitle) return restTitle;
                const restPriority = STATE_PRIORITY[restTitle.state] ?? 0;
                const wsPriority = STATE_PRIORITY[wsTitle.state] ?? 0;
                // Keep whichever has the more advanced state
                if (wsPriority > restPriority) {
                    return { ...restTitle, ...wsTitle };
                }
                return restTitle;
            });
            return { ...prev, [jobId]: merged };
        });
    }, []);

    const fetchJobsAndTitles = useCallback(async () => {
        try {
            const jobsData = await apiFetch<Job[]>('/api/jobs');
            setJobs(jobsData);

            // Fetch titles for all jobs in parallel; merge each as it resolves.
            const results = await Promise.allSettled(
                jobsData.map(async (job) => {
                    const titlesData = await apiFetch<DiscTitle[]>(`/api/jobs/${job.id}/titles`);
                    return { jobId: job.id, titlesData };
                }),
            );

            let failures = 0;
            for (const result of results) {
                if (result.status === 'fulfilled') {
                    mergeTitles(result.value.jobId, result.value.titlesData);
                } else {
                    failures += 1;
                    console.error('Failed to fetch job titles:', result.reason);
                }
            }
            if (failures > 0) {
                toast.error(
                    `Couldn't load tracks for ${failures} job${failures === 1 ? '' : 's'}. Some details may be missing.`,
                );
            }
        } catch (error) {
            // Top-level failure (job list itself) — surface it; leave state intact.
            console.error('Failed to fetch jobs:', error);
            toast.error('Failed to load jobs from the server. Retrying on the next update.');
        }
    }, [mergeTitles]);

    fetchRef.current = fetchJobsAndTitles;

    const scheduleUnknownJobRefetch = useCallback(() => {
        if (debouncedRefetchRef.current !== null) {
            window.clearTimeout(debouncedRefetchRef.current);
        }
        debouncedRefetchRef.current = window.setTimeout(() => {
            debouncedRefetchRef.current = null;
            fetchRef.current?.();
        }, UNKNOWN_JOB_REFETCH_DEBOUNCE_MS);
    }, []);

    // Seed the update banner from the authoritative REST status. The one-shot
    // startup check broadcasts over WebSocket, so a tab that connects *after* it
    // would otherwise never see the banner; this also pulls `is_frozen` from the
    // source of truth. A reconnect may follow an update+restart, so if the running
    // build no longer matches THIS bundle, hard-reload to pull the new UI (fires
    // once: `index.html` is `no-cache`, so the reloaded bundle's __APP_VERSION__
    // then matches).
    const syncUpdateStatus = useCallback(async () => {
        try {
            const status = await apiFetch<Omit<UpdateStatusMessage, 'type'>>('/api/updates/status');
            if (status.current_version && status.current_version !== __APP_VERSION__) {
                window.location.reload();
                return;
            }
            setUpdateStatus(toUpdateStatus(status));
        } catch {
            // Transient blip (e.g. during reconnect) — retry on the next sync.
        }
    }, []);

    // Resync on (re)connect so the UI recovers from any drift while disconnected.
    const handleSocketOpen = useCallback(() => {
        if (initialConnectRef.current) {
            // The mount effect already performs the first load; skip it here.
            initialConnectRef.current = false;
            return;
        }
        if (import.meta.env.DEV) {
            console.log('🔌 WebSocket reconnected — resyncing jobs');
        }
        void syncUpdateStatus();
        fetchRef.current?.();
    }, [syncUpdateStatus]);

    const { isConnected, addMessageListener } = useWebSocket(wsUrl, { onOpen: handleSocketOpen });

    // Clean up the debounce timer on unmount.
    useEffect(() => () => {
        if (debouncedRefetchRef.current !== null) {
            window.clearTimeout(debouncedRefetchRef.current);
        }
    }, []);

    // Initial data fetch
    useEffect(() => {
        if (!devMode) {
            fetchJobsAndTitles();
            void syncUpdateStatus();
        }
    }, [devMode, fetchJobsAndTitles, syncUpdateStatus]);

    async function cancelJob(jobId: string) {
        try {
            await apiFetchVoid(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
            // Job will update via WebSocket
        } catch (error) {
            console.error('Failed to cancel job:', error);
            toast.error('Failed to cancel the job. Please try again.');
        }
    }

    async function advanceJob(jobId: string) {
        try {
            await apiFetchVoid(`/api/jobs/${jobId}/advance`, { method: 'POST' });
            toast.success('Forcing the job to its next step.');
            // Job will update via WebSocket
        } catch (error) {
            console.error('Failed to advance job:', error);
            toast.error('Failed to advance the job. Please try again.');
        }
    }

    async function clearCompleted() {
        try {
            const completedJobs = jobs.filter(j => j.state === 'completed');
            await Promise.all(
                completedJobs.map(job =>
                    apiFetchVoid(`/api/jobs/${job.id}`, { method: 'DELETE' }),
                ),
            );
            // Refresh jobs
            await fetchJobsAndTitles();
        } catch (error) {
            console.error('Failed to clear completed jobs:', error);
            toast.error('Failed to clear completed jobs. Please try again.');
        }
    }

    async function setJobName(
        jobId: number,
        name: string,
        contentType: string,
        season?: number,
    ) {
        try {
            await apiFetchVoid(`/api/jobs/${jobId}/set-name`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, content_type: contentType, season: season ?? null }),
            });
            // Job will update via WebSocket
        } catch (error) {
            console.error('Failed to set job name:', error);
            toast.error('Failed to save the disc name. Please try again.');
        }
    }

    async function reIdentifyJob(
        jobId: number,
        title: string,
        contentType: string,
        season?: number,
        tmdbId?: number,
    ) {
        try {
            await apiFetchVoid(`/api/jobs/${jobId}/re-identify`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title,
                    content_type: contentType,
                    season: season ?? null,
                    tmdb_id: tmdbId ?? null,
                }),
            });
            // Job will update via WebSocket
        } catch (error) {
            console.error('Failed to re-identify job:', error);
            toast.error('Failed to re-identify the disc. Please try again.');
        }
    }

    // Handle WebSocket messages via callback — processes EVERY message, no batching loss
    useEffect(() => {
        if (devMode) return;

        const unsubscribe = addMessageListener((message: WebSocketMessage) => {
            switch (message.type) {
                case 'job_update':
                    setJobs(prev => {
                        const exists = prev.some(j => j.id === message.job_id);
                        if (exists) {
                            return prev.map(job =>
                                job.id === message.job_id ? { ...job, ...message } : job
                            );
                        }
                        // Unknown job — trigger a (debounced) fetch so a burst
                        // of unknown updates collapses into a single refetch.
                        scheduleUnknownJobRefetch();
                        return prev;
                    });
                    break;

                case 'title_update':
                    setTitlesMap(prev => {
                        const existingTitles = prev[message.job_id];
                        const found = existingTitles?.some(t => t.id === message.title_id);
                        if (!found && import.meta.env.DEV) {
                            console.warn('⚠️ title_update for unknown title_id:', message.title_id,
                                'existing ids:', existingTitles?.map(t => t.id) ?? 'NO_TITLES_FOR_JOB');
                        }
                        const updated = {
                            ...prev,
                            [message.job_id]: existingTitles?.map(title =>
                                title.id === message.title_id
                                    ? {
                                        ...title,
                                        ...message,
                                        // Map WebSocket 'error' field to title's error_message
                                        error_message: message.error ?? title.error_message,
                                    }
                                    : title
                            ) || []
                        };

                        // Check if all titles are terminal but job might still be active
                        const updatedTitles = updated[message.job_id];
                        if (updatedTitles && updatedTitles.length > 0) {
                            const allDone = updatedTitles.every(t => TERMINAL_TITLE_STATES.includes(t.state));
                            if (allDone) {
                                // Schedule a refresh to catch missed job_update messages
                                setTimeout(() => fetchRef.current?.(), 3000);
                            }
                        }

                        return updated;
                    });
                    break;

                case 'titles_discovered':
                    setTitlesMap(prev => ({
                        ...prev,
                        [message.job_id]: (message.titles as DiscTitle[]).map(t => ({
                            ...t,
                            state: t.state || 'pending' as const,
                        })),
                    }));

                    // Update job with discovered metadata
                    setJobs(prev => prev.map(job =>
                        job.id === message.job_id
                            ? {
                                ...job,
                                content_type: message.content_type,
                                detected_title: message.detected_title,
                                detected_season: message.detected_season
                            }
                            : job
                    ));
                    break;

                case 'drive_event':
                    if (import.meta.env.DEV) {
                        console.log('🔵 Drive event received:', {
                            event: message.event,
                            drive_id: message.drive_id,
                            volume_label: message.volume_label
                        });
                    }
                    fetchRef.current?.();
                    break;

                case 'subtitle_event':
                    setJobs(prev => prev.map(job =>
                        job.id === message.job_id
                            ? {
                                ...job,
                                subtitle_status: message.status,
                                subtitles_downloaded: message.downloaded,
                                subtitles_total: message.total,
                                subtitles_failed: message.failed_count
                            }
                            : job
                    ));
                    break;

                case 'update_status': {
                    setUpdateStatus(toUpdateStatus(message as UpdateStatusMessage));
                    break;
                }

                case 'fingerprint_disclosure_required': {
                    const msg = message as import('../../types').FingerprintDisclosureRequiredMessage;
                    setDisclosure(msg);
                    break;
                }

                default:
                    break;
            }
        });

        return unsubscribe;
    }, [addMessageListener, devMode, scheduleUnknownJobRefetch]);

    return {
        jobs,
        titlesMap,
        isConnected,
        updateStatus,
        cancelJob,
        advanceJob,
        clearCompleted,
        setJobName,
        reIdentifyJob,
        disclosure,
        clearDisclosure: () => setDisclosure(null),
    };
}
