/**
 * Job management hook with WebSocket integration
 */

import { useState, useEffect } from 'react';
import { useWebSocket } from '../../hooks/useWebSocket';
import type { Job, DiscTitle, WebSocketMessage } from '../../types';

export function useJobManagement(devMode: boolean = false) {
    const [jobs, setJobs] = useState<Job[]>([]);
    const [titlesMap, setTitlesMap] = useState<Record<number, DiscTitle[]>>({});

    // Use WebSocket URL that works with Vite proxy
    // When running on localhost:5173, connects to ws://localhost:5173/ws (proxied to backend)
    // In production, uses the same host as the frontend
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
    const { lastMessage, isConnected } = useWebSocket(wsUrl);

    // Initial data fetch
    useEffect(() => {
        if (!devMode) {
            fetchJobsAndTitles();
        }
    }, [devMode]);

    async function fetchJobsAndTitles() {
        try {
            const jobsRes = await fetch('/api/jobs');
            const jobsData: Job[] = await jobsRes.json();
            setJobs(jobsData);

            // Fetch titles for each job
            for (const job of jobsData) {
                const titlesRes = await fetch(`/api/jobs/${job.id}/titles`);
                const titlesData: DiscTitle[] = await titlesRes.json();
                setTitlesMap(prev => ({ ...prev, [job.id]: titlesData }));
            }
        } catch (error) {
            console.error('Failed to fetch jobs:', error);
        }
    }

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

    // Handle WebSocket messages
    useEffect(() => {
        if (!lastMessage || devMode) return;

        const message = lastMessage as WebSocketMessage;

        switch (message.type) {
            case 'job_update':
                setJobs(prev => {
                    const exists = prev.some(j => j.id === message.job_id);
                    if (exists) {
                        return prev.map(job =>
                            job.id === message.job_id ? { ...job, ...message } : job
                        );
                    }
                    return prev.map(job =>
                        job.id === message.job_id ? { ...job, ...message } : job
                    );
                });

                // If we receive an update for a job we don't know about, fetch immediately
                setJobs(prev => {
                    if (!prev.find(j => j.id === message.job_id)) {
                        fetchJobsAndTitles();
                    }
                    return prev;
                });
                break;

            case 'title_update':
                console.log('📡 WebSocket title_update:', {
                    title_id: message.title_id,
                    state: message.state,
                    match_stage: message.match_stage,
                    full_message: message
                });
                setTitlesMap(prev => {
                    const updated = {
                        ...prev,
                        [message.job_id]: prev[message.job_id]?.map(title =>
                            title.id === message.title_id ? { ...title, ...message } : title
                        ) || []
                    };

                    // Check if all titles are terminal but job might still be active
                    const updatedTitles = updated[message.job_id];
                    if (updatedTitles && updatedTitles.length > 0) {
                        const terminalStates = ['matched', 'completed', 'review', 'failed'];
                        const allDone = updatedTitles.every(t => terminalStates.includes(t.state));
                        if (allDone) {
                            // Schedule a refresh to catch missed job_update messages
                            setTimeout(() => fetchJobsAndTitles(), 3000);
                        }
                    }

                    return updated;
                });
                break;

            case 'titles_discovered':
                setTitlesMap(prev => ({ ...prev, [message.job_id]: message.titles as DiscTitle[] }));

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
                fetchJobsAndTitles();
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
    }, [lastMessage, devMode]);

    return {
        jobs,
        titlesMap,
        isConnected,
        cancelJob,
        clearCompleted,
        setJobName,
    };
}
