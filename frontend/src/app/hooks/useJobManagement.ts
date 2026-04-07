/**
 * Job management hook with WebSocket integration
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useWebSocket } from '../../hooks/useWebSocket';
import type { Job, DiscTitle, WebSocketMessage } from '../../types';

export function useJobManagement(devMode: boolean = false) {
    const [jobs, setJobs] = useState<Job[]>([]);
    const [titlesMap, setTitlesMap] = useState<Record<number, DiscTitle[]>>({});

    // Use WebSocket URL that works with Vite proxy
    // When running on localhost:5173, connects to ws://localhost:5173/ws (proxied to backend)
    // In production, uses the same host as the frontend
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
    const { isConnected, addMessageListener } = useWebSocket(wsUrl);

    // Stable ref to fetchJobsAndTitles so the listener closure doesn't go stale
    const fetchRef = useRef<() => Promise<void>>();

    const fetchJobsAndTitles = useCallback(async () => {
        try {
            console.log('🔄 fetchJobsAndTitles called');
            const jobsRes = await fetch('/api/jobs');
            const jobsData: Job[] = await jobsRes.json();
            setJobs(jobsData);

            // Fetch titles for each job, merging with existing WebSocket state
            for (const job of jobsData) {
                const titlesRes = await fetch(`/api/jobs/${job.id}/titles`);
                const titlesData: DiscTitle[] = await titlesRes.json();
                setTitlesMap(prev => {
                    const existing = prev[job.id];
                    console.log('🔄 fetchJobsAndTitles merge:', {
                        job_id: job.id,
                        restTitleStates: titlesData.map(t => `${t.id}:${t.state}`),
                        existingTitleStates: existing?.map(t => `${t.id}:${t.state}`) ?? 'NONE',
                    });
                    if (!existing) {
                        return { ...prev, [job.id]: titlesData };
                    }
                    // Merge: for each title, keep the more-recent state.
                    // WebSocket-derived state (e.g. "ripping") is more current
                    // than the REST snapshot if the REST state is "pending".
                    const STATE_PRIORITY: Record<string, number> = {
                        pending: 0,
                        ripping: 1,
                        matching: 2,
                        matched: 3,
                        review: 3,
                        completed: 4,
                        failed: 4,
                    };
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
                    return { ...prev, [job.id]: merged };
                });
            }
        } catch (error) {
            console.error('Failed to fetch jobs:', error);
        }
    }, []);

    fetchRef.current = fetchJobsAndTitles;

    // Initial data fetch
    useEffect(() => {
        if (!devMode) {
            fetchJobsAndTitles();
        }
    }, [devMode, fetchJobsAndTitles]);

    async function cancelJob(jobId: string) {
        try {
            await fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
            // Job will update via WebSocket
        } catch (error) {
            console.error('Failed to cancel job:', error);
        }
    }

    async function clearCompleted() {
        try {
            const completedJobs = jobs.filter(j => j.state === 'completed');
            for (const job of completedJobs) {
                await fetch(`/api/jobs/${job.id}`, { method: 'DELETE' });
            }
            // Refresh jobs
            await fetchJobsAndTitles();
        } catch (error) {
            console.error('Failed to clear completed jobs:', error);
        }
    }

    async function setJobName(
        jobId: number,
        name: string,
        contentType: string,
        season?: number,
    ) {
        try {
            await fetch(`/api/jobs/${jobId}/set-name`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, content_type: contentType, season: season ?? null }),
            });
            // Job will update via WebSocket
        } catch (error) {
            console.error('Failed to set job name:', error);
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
            await fetch(`/api/jobs/${jobId}/re-identify`, {
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
                        // Unknown job — trigger a fetch
                        fetchRef.current?.();
                        return prev;
                    });
                    break;

                case 'title_update':
                    console.log('📡 WebSocket title_update:', {
                        title_id: message.title_id,
                        state: message.state,
                        match_stage: message.match_stage,
                        error: message.error,
                    });
                    setTitlesMap(prev => {
                        const existingTitles = prev[message.job_id];
                        const found = existingTitles?.some(t => t.id === message.title_id);
                        if (!found) {
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
                            const terminalStates = ['matched', 'completed', 'review', 'failed'];
                            const allDone = updatedTitles.every(t => terminalStates.includes(t.state));
                            if (allDone) {
                                // Schedule a refresh to catch missed job_update messages
                                setTimeout(() => fetchRef.current?.(), 3000);
                            }
                        }

                        return updated;
                    });
                    break;

                case 'titles_discovered':
                    console.log('📡 titles_discovered:', {
                        job_id: message.job_id,
                        title_count: message.titles?.length,
                        title_ids: message.titles?.map((t: { id: number }) => t.id),
                    });
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
                    console.log('🔵 Drive event received:', {
                        event: message.event,
                        drive_id: message.drive_id,
                        volume_label: message.volume_label
                    });
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

                default:
                    break;
            }
        });

        return unsubscribe;
    }, [addMessageListener, devMode]);

    return {
        jobs,
        titlesMap,
        isConnected,
        cancelJob,
        clearCompleted,
        setJobName,
        reIdentifyJob,
    };
}
